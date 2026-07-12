"""
Community Stats Service
-------------------------
Real, aggregate numbers about KEMET's own community — powers the "KEMET
Community" cards on the dashboard homepage. Everything here is computed
from the same Cosmos DB containers accounts_service.py, posts_service.py,
and trip_planner_service.py already use; nothing is invented or hardcoded.

Both stats that were missing the first time this was built are now real,
since the underlying data now exists:
  - "Top country" — accounts_service now stores a Country field at signup
    (added via update_profile too), so this is a genuine most-common value,
    not a guess. Accounts that never set a country are excluded from the
    count entirely rather than counted as an empty string.
  - "Trips created" — trip_planner_service now persists every generated
    itinerary to the TripPlans container, so this is a real cross-user count.
"""
from collections import Counter

from app.services import accounts_service, posts_service, trip_planner_service


def get_community_stats() -> dict:
    account_stats = accounts_service.get_community_account_stats()

    posts = posts_service.list_posts()
    total_posts = len(posts)
    total_likes = sum(p["likes"] for p in posts)
    total_comments = sum(p["comments_count"] for p in posts)
    total_saves = sum(p["saves"] for p in posts)

    author_counts = Counter(p["owner_username"] for p in posts if p.get("owner_username"))
    most_active_author, most_active_count = (
        author_counts.most_common(1)[0] if author_counts else (None, 0)
    )

    total_trips = trip_planner_service.get_total_trip_count()

    return {
        "total_users": account_stats["total_users"],
        "top_country": account_stats["top_country"],
        "top_country_count": account_stats["top_country_count"],
        "total_posts": total_posts,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_saves": total_saves,
        "most_active_author": most_active_author,
        "most_active_author_posts": most_active_count,
        "total_trips": total_trips,
    }
