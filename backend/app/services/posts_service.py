"""
Posts Service
-------------
Community posts logic, extracted from the old Streamlit pages (home.py +
your.py) into framework-agnostic functions, same style as accounts_service.py.

Storage layout (Cosmos DB "kemetcosmos" / "Posts" container, partition key
/Username):
    { id, Username, Content: [ {...post item...}, ... ] }

Each post item now looks like:
  {
    "text": str,
    "image_urls": [str, ...],                         # 0-or-more photos
    "rating": int | None,                              # 1-5 stars, optional
    "timestamp": iso string (UTC, "...Z"),
    "reactions": { "<emoji>": [reactor_id, ...] },   # "❤️" bucket == Likes
    "saves": [reactor_id, ...],                       # Bookmarks
    "comments": [
        {
          "author": str,
          "text": str,
          "timestamp": iso string,
          "likes": [reactor_id, ...],
          "replies": [ {"author": str, "text": str, "timestamp": iso string}, ... ]
        }, ...
    ]
  }

Old documents created by the Streamlit app only had text/image_url/timestamp
(sometimes reactions). _normalize_item() / _normalize_comment() fill in the
missing fields on read so nothing breaks. A comment's / reply's position in
its list is used as its stable id (comment_index / reply_index) — comments
are only ever appended to or mutated in place, never reordered or removed
individually, so this stays stable.

Author profile pictures on comments/replies are NOT stored on the comment —
they're resolved at read time from the Users container (same as the post
owner's picture), so a user's picture on old comments updates automatically
whenever they change their avatar, and nothing needs backfilling.
"""
import datetime

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.storage.blob import BlobServiceClient

from app.utils import get_secret

DATABASE_NAME = "kemetcosmos"
POSTS_CONTAINER_NAME = "Posts"
USERS_CONTAINER_NAME = "Users"
IMAGES_CONTAINER_NAME = "posts"  # Azure Blob container (same one home.py used)

LIKE_EMOJI = "❤️"  # the "Like" button maps onto this reaction bucket
MAX_IMAGES_PER_POST = 6


class PostsError(Exception):
    pass


def _now_iso():
    """UTC, with an explicit 'Z' — the frontend's `new Date(iso)` treats a
    bare (no-timezone) timestamp as *local* time, so a server running in a
    different timezone than the viewer used to make every post look posted
    at the wrong time. Stamping UTC explicitly fixes that everywhere at once."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _get_containers():
    cosmos_endpoint = get_secret("COSMOS_ENDPOINT")
    cosmos_key = get_secret("COSMOS_KEY")
    if not cosmos_endpoint or not cosmos_key:
        raise PostsError("COSMOS_ENDPOINT / COSMOS_KEY are not configured.")

    client = CosmosClient(cosmos_endpoint, cosmos_key)
    database = client.create_database_if_not_exists(id=DATABASE_NAME)
    container_posts = database.create_container_if_not_exists(
        id=POSTS_CONTAINER_NAME, partition_key=PartitionKey(path="/Username")
    )
    container_users = database.create_container_if_not_exists(
        id=USERS_CONTAINER_NAME, partition_key=PartitionKey(path="/Username")
    )
    return container_posts, container_users


def _normalize_comment(raw_comment):
    if not isinstance(raw_comment, dict):
        raw_comment = {"author": "", "text": str(raw_comment)}
    raw_comment.setdefault("author", "")
    raw_comment.setdefault("text", "")
    raw_comment.setdefault("timestamp", "")
    raw_comment.setdefault("likes", [])
    raw_comment.setdefault("replies", [])
    raw_comment["replies"] = [
        {
            "author": r.get("author", "") if isinstance(r, dict) else "",
            "text": r.get("text", "") if isinstance(r, dict) else str(r),
            "timestamp": r.get("timestamp", "") if isinstance(r, dict) else "",
        }
        for r in (raw_comment.get("replies") or [])
    ]
    return raw_comment


def _normalize_item(raw_item):
    """Backfills fields on posts that predate reactions/saves/comments/
    multi-image/rating."""
    if not isinstance(raw_item, dict):
        raw_item = {"text": str(raw_item), "image_urls": []}
    raw_item.setdefault("text", "")
    raw_item.setdefault("timestamp", "")
    raw_item.setdefault("reactions", {})
    raw_item.setdefault("saves", [])
    raw_item.setdefault("comments", [])
    raw_item.setdefault("rating", None)

    # Legacy documents stored a single "image_url"; new ones store a list.
    if "image_urls" not in raw_item:
        legacy_url = raw_item.get("image_url")
        raw_item["image_urls"] = [legacy_url] if legacy_url else []
    raw_item["image_urls"] = [u for u in (raw_item.get("image_urls") or []) if u]

    raw_item["comments"] = [_normalize_comment(c) for c in raw_item.get("comments", [])]
    return raw_item


def _profile_pics(container_users):
    profile_pics = {}
    try:
        for u in container_users.read_all_items():
            pic_url = u.get("ProfilePicUrl")
            if pic_url:
                profile_pics[u.get("Username", "")] = pic_url
    except Exception:
        pass
    return profile_pics


def _serialize_comment(comment_index, comment, profile_pics, viewer_id):
    likes = comment.get("likes", []) or []
    replies = [
        {
            "author": r.get("author", ""),
            "text": r.get("text", ""),
            "timestamp": r.get("timestamp", ""),
            "profile_pic_url": profile_pics.get(r.get("author", ""), ""),
        }
        for r in comment.get("replies", [])
    ]
    return {
        "id": comment_index,
        "author": comment.get("author", ""),
        "text": comment.get("text", ""),
        "timestamp": comment.get("timestamp", ""),
        "profile_pic_url": profile_pics.get(comment.get("author", ""), ""),
        "likes": len(likes),
        "liked_by_me": bool(viewer_id) and viewer_id in likes,
        "replies": replies,
        "replies_count": len(replies),
    }


def _serialize_item(owner_username, content_index, item, profile_pics, viewer_id):
    likers = (item.get("reactions") or {}).get(LIKE_EMOJI, []) or []
    saves = item.get("saves", []) or []
    comments = item.get("comments", []) or []
    image_urls = item.get("image_urls", []) or []
    return {
        "owner_username": owner_username,
        "content_index": content_index,
        "text": item.get("text", ""),
        "image_urls": image_urls,
        "image_url": image_urls[0] if image_urls else None,  # back-compat for older clients
        "rating": item.get("rating"),
        "timestamp": item.get("timestamp", ""),
        "profile_pic_url": profile_pics.get(owner_username, ""),
        "likes": len(likers),
        "liked_by_me": bool(viewer_id) and viewer_id in likers,
        "saves": len(saves),
        "saved_by_me": bool(viewer_id) and viewer_id in saves,
        "comments": [_serialize_comment(idx, c, profile_pics, viewer_id) for idx, c in enumerate(comments)],
        "comments_count": len(comments),
    }


def list_posts(viewer_id=None):
    """All posts from every user, newest first (feeds the Community page)."""
    container_posts, container_users = _get_containers()
    profile_pics = _profile_pics(container_users)

    try:
        docs = list(container_posts.read_all_items())
    except Exception as e:
        raise PostsError(f"Error reading posts: {e}")

    all_posts = []
    for doc in docs:
        owner_username = doc.get("Username", "")
        content = doc.get("Content", [])
        if not isinstance(content, list):
            continue
        for idx, raw_item in enumerate(content):
            item = _normalize_item(raw_item)
            all_posts.append(_serialize_item(owner_username, idx, item, profile_pics, viewer_id))

    all_posts.sort(key=lambda p: p["timestamp"], reverse=True)
    return all_posts


def _upload_images(username, image_files):
    """image_files: list of (bytes, filename) tuples. Returns list of URLs."""
    if not image_files:
        return []
    storage_conn = get_secret("AZURE_STORAGE_CONNECTION_STRING")
    if not storage_conn:
        raise PostsError("Storage connection not configured.")
    try:
        blob_service_client = BlobServiceClient.from_connection_string(storage_conn)
    except Exception as e:
        raise PostsError(f"Error uploading image: {e}")

    urls = []
    for i, (image_bytes, image_filename) in enumerate(image_files):
        try:
            file_name = f"{username}_{datetime.datetime.now().timestamp()}_{i}_{image_filename or 'image'}"
            blob_client = blob_service_client.get_blob_client(container=IMAGES_CONTAINER_NAME, blob=file_name)
            blob_client.upload_blob(image_bytes, overwrite=True)
            urls.append(blob_client.url)
        except Exception as e:
            raise PostsError(f"Error uploading image: {e}")
    return urls


def create_post(username, text, image_files=None, rating=None):
    """image_files: list of (bytes, filename) tuples (0 or more)."""
    username = (username or "").strip()
    text = (text or "").strip()
    image_files = [f for f in (image_files or []) if f and f[0]][:MAX_IMAGES_PER_POST]

    if not username:
        raise PostsError("A name is required to post.")
    if not text and not image_files:
        raise PostsError("Post cannot be empty.")

    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = None
        else:
            if rating < 1 or rating > 5:
                rating = None

    container_posts, _ = _get_containers()
    image_urls = _upload_images(username, image_files)

    post_item = {
        "text": text,
        "image_urls": image_urls,
        "rating": rating,
        "timestamp": _now_iso(),
        "reactions": {},
        "saves": [],
        "comments": [],
    }

    try:
        user_doc = container_posts.read_item(item=username, partition_key=username)
        content = user_doc.get("Content", [])
        if not isinstance(content, list):
            content = []
        content.append(post_item)
        user_doc["Content"] = content
        container_posts.upsert_item(body=user_doc)
        content_index = len(content) - 1
    except exceptions.CosmosResourceNotFoundError:
        new_doc = {"id": username, "Username": username, "Content": [post_item]}
        container_posts.create_item(body=new_doc)
        content_index = 0
    except Exception as e:
        raise PostsError(f"Error saving post: {e}")

    return _serialize_item(username, content_index, post_item, {}, username)


def _load_item(container_posts, owner_username, content_index):
    try:
        doc = container_posts.read_item(item=owner_username, partition_key=owner_username)
    except exceptions.CosmosResourceNotFoundError:
        raise PostsError("Post not found.")
    except Exception as e:
        raise PostsError(f"Error loading post: {e}")

    content = doc.get("Content", [])
    if content_index < 0 or content_index >= len(content):
        raise PostsError("Post not found.")
    item = _normalize_item(content[content_index])
    return doc, content, item


def toggle_like(owner_username, content_index, reactor_id):
    reactor_id = (reactor_id or "").strip()
    if not reactor_id:
        raise PostsError("A name is required to react.")

    container_posts, _ = _get_containers()
    doc, content, item = _load_item(container_posts, owner_username, content_index)

    likers = item["reactions"].get(LIKE_EMOJI, [])
    if reactor_id in likers:
        likers.remove(reactor_id)
        liked = False
    else:
        likers.append(reactor_id)
        liked = True
    if likers:
        item["reactions"][LIKE_EMOJI] = likers
    else:
        item["reactions"].pop(LIKE_EMOJI, None)

    content[content_index] = item
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error saving reaction: {e}")

    return {"liked_by_me": liked, "likes": len(likers)}


def toggle_save(owner_username, content_index, reactor_id):
    reactor_id = (reactor_id or "").strip()
    if not reactor_id:
        raise PostsError("A name is required to save posts.")

    container_posts, _ = _get_containers()
    doc, content, item = _load_item(container_posts, owner_username, content_index)

    saves = item["saves"]
    if reactor_id in saves:
        saves.remove(reactor_id)
        saved = False
    else:
        saves.append(reactor_id)
        saved = True
    item["saves"] = saves

    content[content_index] = item
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error saving bookmark: {e}")

    return {"saved_by_me": saved, "saves": len(saves)}


def _profile_pic_for(container_users, username):
    try:
        u = container_users.read_item(item=username, partition_key=username)
        return u.get("ProfilePicUrl", "") or ""
    except Exception:
        return ""


def add_comment(owner_username, content_index, author, text):
    author = (author or "").strip()
    text = (text or "").strip()
    if not author:
        raise PostsError("A name is required to comment.")
    if not text:
        raise PostsError("Comment cannot be empty.")

    container_posts, container_users = _get_containers()
    doc, content, item = _load_item(container_posts, owner_username, content_index)

    comment = {
        "author": author,
        "text": text,
        "timestamp": _now_iso(),
        "likes": [],
        "replies": [],
    }
    item["comments"].append(comment)
    comment_index = len(item["comments"]) - 1
    content[content_index] = item
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error saving comment: {e}")

    profile_pic = _profile_pic_for(container_users, author)
    return {
        "comment": _serialize_comment(comment_index, comment, {author: profile_pic}, author),
        "comments_count": len(item["comments"]),
    }


def toggle_comment_like(owner_username, content_index, comment_index, reactor_id):
    reactor_id = (reactor_id or "").strip()
    if not reactor_id:
        raise PostsError("A name is required to react.")

    container_posts, _ = _get_containers()
    doc, content, item = _load_item(container_posts, owner_username, content_index)

    comments = item["comments"]
    if comment_index < 0 or comment_index >= len(comments):
        raise PostsError("Comment not found.")
    comment = comments[comment_index]

    likes = comment.setdefault("likes", [])
    if reactor_id in likes:
        likes.remove(reactor_id)
        liked = False
    else:
        likes.append(reactor_id)
        liked = True

    content[content_index] = item
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error saving reaction: {e}")

    return {"liked_by_me": liked, "likes": len(likes)}


def add_reply(owner_username, content_index, comment_index, author, text):
    author = (author or "").strip()
    text = (text or "").strip()
    if not author:
        raise PostsError("A name is required to reply.")
    if not text:
        raise PostsError("Reply cannot be empty.")

    container_posts, container_users = _get_containers()
    doc, content, item = _load_item(container_posts, owner_username, content_index)

    comments = item["comments"]
    if comment_index < 0 or comment_index >= len(comments):
        raise PostsError("Comment not found.")
    comment = comments[comment_index]

    reply = {"author": author, "text": text, "timestamp": _now_iso()}
    comment.setdefault("replies", []).append(reply)

    content[content_index] = item
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error saving reply: {e}")

    profile_pic = _profile_pic_for(container_users, author)
    return {
        "reply": {**reply, "profile_pic_url": profile_pic},
        "replies_count": len(comment["replies"]),
    }


def delete_post(owner_username, content_index, requester_username):
    if (requester_username or "").strip() != (owner_username or "").strip():
        raise PostsError("You can only delete your own posts.")

    container_posts, _ = _get_containers()
    try:
        doc = container_posts.read_item(item=owner_username, partition_key=owner_username)
    except exceptions.CosmosResourceNotFoundError:
        raise PostsError("Post not found.")
    except Exception as e:
        raise PostsError(f"Error loading post: {e}")

    content = doc.get("Content", [])
    if content_index < 0 or content_index >= len(content):
        raise PostsError("Post not found.")

    content.pop(content_index)
    doc["Content"] = content
    try:
        container_posts.upsert_item(body=doc)
    except Exception as e:
        raise PostsError(f"Error deleting post: {e}")

    return True


def get_user_posts(username):
    """Powers the 'Your Posts' tab: every post by this exact identity
    (account username, or the name a guest typed), newest first."""
    username = (username or "").strip()
    if not username:
        return []

    container_posts, container_users = _get_containers()
    profile_pics = _profile_pics(container_users)

    try:
        doc = container_posts.read_item(item=username, partition_key=username)
    except exceptions.CosmosResourceNotFoundError:
        return []
    except Exception as e:
        raise PostsError(f"Error loading posts: {e}")

    content = doc.get("Content", [])
    posts = []
    for idx, raw_item in enumerate(content):
        item = _normalize_item(raw_item)
        posts.append(_serialize_item(username, idx, item, profile_pics, username))

    posts.sort(key=lambda p: p["timestamp"], reverse=True)
    return posts


def get_saved_posts(reactor_id):
    """Powers the 'Saved' tab: every post (by anyone) this identity bookmarked."""
    reactor_id = (reactor_id or "").strip()
    if not reactor_id:
        return []
    all_posts = list_posts(viewer_id=reactor_id)
    return [p for p in all_posts if p["saved_by_me"]]