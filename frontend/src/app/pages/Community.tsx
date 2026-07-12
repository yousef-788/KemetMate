import { useState, useEffect, useRef, useCallback, useMemo, memo } from "react";
import {
  Heart, MessageCircle, Bookmark, Camera, MapPin, Send,
  Search, Sparkles, Globe, Filter, Image as ImageIcon, X,
  Loader2, AlertCircle, Trash2, User as UserIcon, Trophy, Smile,
} from "lucide-react";
import { API_BASE_URL } from "../lib/api";

// Same backend the Account page talks to.
const API_BASE = API_BASE_URL;
const TOKEN_KEY = "kemet_token";
const GUEST_NAME_KEY = "kemet_guest_name";

const AVATAR_COLORS = [
  "from-yellow-400 to-orange-500",
  "from-blue-400 to-purple-500",
  "from-green-400 to-teal-500",
  "from-pink-400 to-rose-500",
  "from-indigo-400 to-blue-500",
  "from-amber-400 to-yellow-500",
];

function gradientFor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function initialsFor(name: string) {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return parts[0].slice(0, 2).toUpperCase();
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

const HASHTAG_RE = /(#[\p{L}\p{N}_]+)/gu;

/** Renders post/comment text with #hashtags as clickable spans. */
function renderWithHashtags(text: string, onHashtagClick: (tag: string) => void) {
  const parts = text.split(HASHTAG_RE);
  return parts.map((part, i) =>
    part.startsWith("#") ? (
      <span
        key={i}
        onClick={(e) => { e.stopPropagation(); onHashtagClick(part); }}
        className="text-[#D4AF37] font-medium cursor-pointer hover:underline"
      >
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

const EMOJI_PICKS = [
  "😀", "😂", "😍", "🥰", "😎", "🤩", "😅", "🙌",
  "👍", "🙏", "❤️", "🔥", "✨", "🎉", "🌍", "🏜️",
  "🐫", "☀️", "📸", "✈️", "🗺️", "🏛️", "🌅", "😢",
];

// --- Identity helpers (shared with Account.tsx via localStorage) ---

function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
function getGuestName(): string {
  return localStorage.getItem(GUEST_NAME_KEY) || "";
}
function setGuestName(name: string) {
  localStorage.setItem(GUEST_NAME_KEY, name);
}
function getCurrentIdentity(): { name: string; loggedIn: boolean } {
  const token = getToken();
  if (token) {
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      if (payload?.username) return { name: payload.username, loggedIn: true };
    } catch {
      /* fall through to guest */
    }
  }
  return { name: getGuestName(), loggedIn: false };
}

async function accountApiRequest(path: string, options: RequestInit = {}) {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/account${path}`, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

// Same endpoint Account.tsx uses to get the real profile_pic_url — this is
// the authoritative source (set via avatar upload), unlike the JWT payload
// which only carries the username.
const apiAccountMe = () =>
  accountApiRequest("/me").then(
    (d) =>
      d.user as {
        username: string;
        email: string;
        profile_pic_url: string;
        full_name: string;
        country: string;
        language: string;
        travel_preferences: string[];
        created_at: string;
      }
  );

// Common country names -> 3-letter abbreviation shown next to the poster's
// name in the composer, e.g. "Egypt" -> "EGY". Falls back to the first 3
// letters of whatever string the account has, so unlisted countries still
// show *something* short instead of overflowing the header.
const COUNTRY_ABBR: Record<string, string> = {
  Egypt: "EGY", "United States": "USA", "United Kingdom": "UK", Canada: "CAN",
  Germany: "GER", France: "FRA", Italy: "ITA", Spain: "ESP", Netherlands: "NED",
  "Saudi Arabia": "KSA", "United Arab Emirates": "UAE", Jordan: "JOR", Morocco: "MAR",
  Tunisia: "TUN", Algeria: "ALG", Qatar: "QAT", Kuwait: "KUW", Lebanon: "LEB",
  China: "CHN", Japan: "JPN", India: "IND", Brazil: "BRA", Australia: "AUS",
  Russia: "RUS", Turkey: "TUR", Greece: "GRE", Portugal: "POR",
};
function countryAbbr(country: string): string {
  if (!country) return "";
  return COUNTRY_ABBR[country] || country.slice(0, 3).toUpperCase();
}

// --- API layer ---

interface Reply {
  author: string;
  text: string;
  timestamp: string;
  profile_pic_url: string;
}

interface Comment {
  id: number;
  author: string;
  text: string;
  timestamp: string;
  profile_pic_url: string;
  likes: number;
  liked_by_me: boolean;
  replies: Reply[];
  replies_count: number;
}

interface Post {
  owner_username: string;
  content_index: number;
  text: string;
  image_url: string | null;
  image_urls: string[];
  rating: number | null;
  timestamp: string;
  profile_pic_url: string;
  likes: number;
  liked_by_me: boolean;
  saves: number;
  saved_by_me: boolean;
  comments: Comment[];
  comments_count: number;
}

async function postsApiRequest(path: string, options: RequestInit = {}) {
  const token = getToken();
  const guestName = getGuestName();
  const res = await fetch(`${API_BASE}/api/posts${path}`, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(!token && guestName ? { "X-Guest-Name": guestName } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

const apiListPosts = () => postsApiRequest("").then((d) => d.posts as Post[]);

// Module-level cache: lives outside React state, so it survives the
// Community component unmounting/remounting (e.g. switching to Account and
// back). We show cached data instantly with no spinner, then silently
// re-fetch in the background to keep it fresh. It only resets on a full
// browser reload — that's intentional, it's a session cache, not storage.
let cachedPosts: Post[] | null = null;

const apiCreatePost = (text: string, files: File[], rating: number | null) => {
  const formData = new FormData();
  formData.append("text", text);
  for (const f of files) formData.append("files", f);
  if (rating) formData.append("rating", String(rating));
  return postsApiRequest("", { method: "POST", body: formData }).then((d) => d.post as Post);
};

const apiToggleLike = (owner: string, idx: number) =>
  postsApiRequest(`/${owner}/${idx}/like`, { method: "POST" }) as Promise<{ liked_by_me: boolean; likes: number }>;

const apiToggleSave = (owner: string, idx: number) =>
  postsApiRequest(`/${owner}/${idx}/save`, { method: "POST" }) as Promise<{ saved_by_me: boolean; saves: number }>;

const apiComment = (owner: string, idx: number, text: string) =>
  postsApiRequest(`/${owner}/${idx}/comment`, {
    method: "POST",
    body: JSON.stringify({ text }),
  }) as Promise<{ comment: Comment; comments_count: number }>;

const apiToggleCommentLike = (owner: string, idx: number, commentId: number) =>
  postsApiRequest(`/${owner}/${idx}/comment/${commentId}/like`, { method: "POST" }) as Promise<{
    liked_by_me: boolean;
    likes: number;
  }>;

const apiReplyToComment = (owner: string, idx: number, commentId: number, text: string) =>
  postsApiRequest(`/${owner}/${idx}/comment/${commentId}/reply`, {
    method: "POST",
    body: JSON.stringify({ text }),
  }) as Promise<{ reply: Reply; replies_count: number }>;

const apiDeletePost = (owner: string, idx: number) =>
  postsApiRequest(`/${owner}/${idx}`, { method: "DELETE" });

// --- Small shared UI bits ---

function Avatar({ name, imageUrl, size = "md" }: { name: string; imageUrl?: string | null; size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: "w-8 h-8 text-xs", md: "w-10 h-10 text-sm", lg: "w-12 h-12 text-base" };
  if (imageUrl) {
    return <img src={imageUrl} alt={name} loading="lazy" decoding="async" className={`${sizes[size]} rounded-full object-cover flex-shrink-0`} />;
  }
  return (
    <div className={`${sizes[size]} rounded-full bg-gradient-to-br ${gradientFor(name)} flex items-center justify-center font-bold text-white flex-shrink-0`}>
      {initialsFor(name)}
    </div>
  );
}

function StarRating({
  value, onChange, size = 18, readOnly = false,
}: { value: number; onChange?: (v: number) => void; size?: number; readOnly?: boolean }) {
  const [hover, setHover] = useState(0);
  const shown = hover || value;
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          disabled={readOnly}
          onClick={() => onChange?.(n === value ? 0 : n)}
          onMouseEnter={() => !readOnly && setHover(n)}
          onMouseLeave={() => !readOnly && setHover(0)}
          className={`transition-transform ${readOnly ? "cursor-default" : "cursor-pointer hover:scale-110"}`}
        >
          <svg
            width={size} height={size} viewBox="0 0 24 24"
            fill={n <= shown ? "#D4AF37" : "none"}
            stroke="#D4AF37" strokeWidth={1.5}
          >
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        </button>
      ))}
    </div>
  );
}

/** Emoji picker popover. Clicking an emoji inserts it and gives it a quick
 * pop/bounce animation so the interaction feels alive. */
function EmojiPicker({ onPick, onClose }: { onPick: (emoji: string) => void; onClose: () => void }) {
  const [popped, setPopped] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="absolute z-20 bottom-full mb-2 left-0 bg-[#12152B] border border-white/10 rounded-2xl p-3 shadow-xl grid grid-cols-8 gap-1 w-64"
    >
      {EMOJI_PICKS.map((emoji) => (
        <button
          key={emoji}
          type="button"
          onClick={() => {
            setPopped(emoji);
            onPick(emoji);
            setTimeout(() => setPopped(null), 300);
          }}
          className={`text-lg leading-none w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/10 transition-transform ${
            popped === emoji ? "scale-150" : "scale-100"
          }`}
          style={{ transitionDuration: popped === emoji ? "150ms" : "200ms" }}
        >
          {emoji}
        </button>
      ))}
    </div>
  );
}

/** Asks a guest (once) for the name they want to post/react/comment/save under.
 * Logged-in users never see this — their account name is used automatically. */
function GuestNameModal({ onSubmit, onCancel }: { onSubmit: (name: string) => void; onCancel: () => void }) {
  const [name, setName] = useState(getGuestName());
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-sm bg-[#12152B] border border-white/10 rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <UserIcon size={16} style={{ color: "#D4AF37" }} />
            What's your name?
          </h3>
          <button onClick={onCancel} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>
        <p className="text-sm text-white/50">
          You're not signed in. Enter a name to post and interact as a guest — you'll see this again only if you clear it.
        </p>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && onSubmit(name.trim())}
          placeholder="Your name"
          className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-[#D4AF37]/40 transition-colors"
        />
        <button
          onClick={() => name.trim() && onSubmit(name.trim())}
          disabled={!name.trim()}
          className="w-full py-2.5 rounded-xl font-semibold text-sm bg-[#D4AF37] text-black hover:bg-[#C9A84C] transition-all disabled:opacity-40"
        >
          Continue
        </button>
      </div>
    </div>
  );
}

function CommentRow({
  postOwner, contentIndex, comment, identity, myProfilePicUrl, onCommentChanged, requireIdentity, onHashtagClick,
}: {
  postOwner: string;
  contentIndex: number;
  comment: Comment;
  identity: { name: string; loggedIn: boolean };
  myProfilePicUrl: string | null;
  onCommentChanged: (updated: Comment) => void;
  requireIdentity: (action: () => void) => void;
  onHashtagClick: (tag: string) => void;
}) {
  const [replying, setReplying] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [busy, setBusy] = useState(false);

  const likeComment = () =>
    requireIdentity(() => {
      const optimisticLiked = !comment.liked_by_me;
      const optimisticLikes = comment.likes + (optimisticLiked ? 1 : -1);
      onCommentChanged({ ...comment, liked_by_me: optimisticLiked, likes: optimisticLikes });
      apiToggleCommentLike(postOwner, contentIndex, comment.id)
        .then((res) => onCommentChanged({ ...comment, liked_by_me: res.liked_by_me, likes: res.likes }))
        .catch(() => onCommentChanged({ ...comment, liked_by_me: comment.liked_by_me, likes: comment.likes }));
    });

  const submitReply = () => {
    if (!replyText.trim()) return;
    requireIdentity(async () => {
      setBusy(true);
      try {
        const res = await apiReplyToComment(postOwner, contentIndex, comment.id, replyText.trim());
        onCommentChanged({ ...comment, replies: [...comment.replies, res.reply], replies_count: res.replies_count });
        setReplyText("");
        setReplying(false);
      } catch {
        /* ignore */
      } finally {
        setBusy(false);
      }
    });
  };

  return (
    <div className="flex gap-3">
      <Avatar name={comment.author} imageUrl={comment.profile_pic_url} size="sm" />
      <div className="flex-1 min-w-0">
        <div className="bg-white/5 rounded-xl px-3 py-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-semibold text-white">{comment.author}</span>
            <span className="text-xs text-gray-500">{timeAgo(comment.timestamp)}</span>
          </div>
          <p className="text-xs text-gray-300 leading-relaxed">{renderWithHashtags(comment.text, onHashtagClick)}</p>
        </div>

        <div className="flex items-center gap-4 mt-1 pl-1">
          <button
            onClick={likeComment}
            className={`flex items-center gap-1 text-xs font-medium transition-colors ${
              comment.liked_by_me ? "text-rose-400" : "text-gray-500 hover:text-rose-400"
            }`}
          >
            <Heart size={11} fill={comment.liked_by_me ? "currentColor" : "none"} />
            {comment.likes > 0 ? comment.likes : "Love"}
          </button>
          <button
            onClick={() => setReplying((r) => !r)}
            className="text-xs font-medium text-gray-500 hover:text-blue-400 transition-colors"
          >
            Reply
          </button>
        </div>

        {comment.replies.length > 0 && (
          <div className="mt-2 space-y-2 pl-4 border-l border-white/10">
            {comment.replies.map((r, idx) => (
              <div key={idx} className="flex gap-2">
                <Avatar name={r.author} imageUrl={r.profile_pic_url} size="sm" />
                <div className="flex-1 bg-white/5 rounded-xl px-3 py-1.5">
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="text-xs font-semibold text-white">{r.author}</span>
                    <span className="text-xs text-gray-500">{timeAgo(r.timestamp)}</span>
                  </div>
                  <p className="text-xs text-gray-300 leading-relaxed">{renderWithHashtags(r.text, onHashtagClick)}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {replying && (
          <div className="flex gap-2 mt-2 pl-4">
            <Avatar name={identity.name || "?"} imageUrl={myProfilePicUrl} size="sm" />
            <div className="flex-1 flex gap-2">
              <input
                autoFocus
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitReply()}
                placeholder={`Reply to ${comment.author}...`}
                className="flex-1 bg-white/10 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none focus:border-[#D4AF37]/50 transition-colors"
              />
              <button
                onClick={submitReply}
                disabled={!replyText.trim() || busy}
                className="w-7 h-7 rounded-full bg-[#D4AF37] text-black flex items-center justify-center hover:bg-[#C9A84C] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0 self-center"
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const PostCard = memo(function PostCard({
  post, identity, myProfilePicUrl, onChanged, onDeleted, requireIdentity, onHashtagClick,
}: {
  post: Post;
  identity: { name: string; loggedIn: boolean };
  myProfilePicUrl: string | null;
  onChanged: (updated: Post) => void;
  onDeleted: (owner: string, idx: number) => void;
  requireIdentity: (action: () => void) => void;
  onHashtagClick: (tag: string) => void;
}) {
  const [showComments, setShowComments] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [busy, setBusy] = useState(false);
  const [imgIndex, setImgIndex] = useState(0);
  const isOwn = identity.name !== "" && identity.name === post.owner_username;
  const images = post.image_urls?.length ? post.image_urls : post.image_url ? [post.image_url] : [];

  const like = () =>
    requireIdentity(() => {
      // Flip instantly so the tap feels immediate — the network call
      // happens in the background and only corrects things on failure.
      const optimisticLiked = !post.liked_by_me;
      const optimisticLikes = post.likes + (optimisticLiked ? 1 : -1);
      onChanged({ ...post, liked_by_me: optimisticLiked, likes: optimisticLikes });
      apiToggleLike(post.owner_username, post.content_index)
        .then((res) => onChanged({ ...post, liked_by_me: res.liked_by_me, likes: res.likes }))
        .catch(() => onChanged({ ...post, liked_by_me: post.liked_by_me, likes: post.likes }));
    });

  const save = () =>
    requireIdentity(() => {
      const optimisticSaved = !post.saved_by_me;
      const optimisticSaves = post.saves + (optimisticSaved ? 1 : -1);
      onChanged({ ...post, saved_by_me: optimisticSaved, saves: optimisticSaves });
      apiToggleSave(post.owner_username, post.content_index)
        .then((res) => onChanged({ ...post, saved_by_me: res.saved_by_me, saves: res.saves }))
        .catch(() => onChanged({ ...post, saved_by_me: post.saved_by_me, saves: post.saves }));
    });

  const submitComment = () => {
    if (!commentText.trim()) return;
    requireIdentity(async () => {
      setBusy(true);
      try {
        const res = await apiComment(post.owner_username, post.content_index, commentText.trim());
        onChanged({ ...post, comments: [...post.comments, res.comment], comments_count: res.comments_count });
        setCommentText("");
      } catch {
        /* ignore */
      } finally {
        setBusy(false);
      }
    });
  };

  const updateComment = (updated: Comment) =>
    onChanged({ ...post, comments: post.comments.map((c) => (c.id === updated.id ? updated : c)) });

  const remove = async () => {
    if (!window.confirm("Delete this post?")) return;
    try {
      await apiDeletePost(post.owner_username, post.content_index);
      onDeleted(post.owner_username, post.content_index);
    } catch {
      /* ignore */
    }
  };

  return (
    <div
      className="bg-[#12152B] border border-white/10 rounded-2xl overflow-hidden hover:border-[#D4AF37]/20 transition-colors duration-300"
      style={{ contentVisibility: "auto", containIntrinsicSize: "0 400px" }}
    >
      {/* Header */}
      <div className="p-5 pb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Avatar name={post.owner_username} imageUrl={post.profile_pic_url} size="md" />
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-white text-sm">{post.owner_username}</span>
            </div>
            <span className="text-xs text-gray-500">{timeAgo(post.timestamp)}</span>
          </div>
        </div>
        {isOwn && (
          <button onClick={remove} className="text-gray-500 hover:text-red-400 transition-colors" title="Delete post">
            <Trash2 size={16} />
          </button>
        )}
      </div>

      {/* Rating */}
      {!!post.rating && (
        <div className="px-5 pb-2">
          <StarRating value={post.rating} readOnly size={14} />
        </div>
      )}

      {/* Content */}
      {post.text && (
        <div className="px-5 pb-3">
          <p className="text-gray-200 text-sm leading-relaxed whitespace-pre-wrap">
            {renderWithHashtags(post.text, onHashtagClick)}
          </p>
        </div>
      )}

      {/* Image(s) */}
      {images.length > 0 && (
        <div className="mx-5 mb-4 rounded-xl overflow-hidden relative">
          <img
            src={images[imgIndex]}
            alt="post"
            loading="lazy"
            decoding="async"
            className="w-full max-h-[420px] object-cover"
          />
          {images.length > 1 && (
            <>
              <div className="absolute top-2 right-2 bg-black/60 text-white text-xs px-2 py-0.5 rounded-full">
                {imgIndex + 1}/{images.length}
              </div>
              {imgIndex > 0 && (
                <button
                  onClick={() => setImgIndex((i) => i - 1)}
                  className="absolute left-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70"
                >
                  ‹
                </button>
              )}
              {imgIndex < images.length - 1 && (
                <button
                  onClick={() => setImgIndex((i) => i + 1)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70"
                >
                  ›
                </button>
              )}
              <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1">
                {images.map((_, i) => (
                  <span key={i} className={`w-1.5 h-1.5 rounded-full ${i === imgIndex ? "bg-[#D4AF37]" : "bg-white/40"}`} />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Stats bar — same text size/weight for all three so they read as one row */}
      <div className="px-5 py-2 border-t border-white/5 flex items-center justify-between text-xs font-normal text-gray-500">
        <span>{post.likes.toLocaleString()} likes</span>
        <div className="flex gap-3">
          <button onClick={() => setShowComments(!showComments)} className="text-xs font-normal hover:text-gray-300 transition-colors">
            {post.comments_count} comments
          </button>
          <span className="text-xs font-normal">{post.saves} saves</span>
        </div>
      </div>

      {/* Action buttons */}
      <div className="px-5 py-2 border-t border-white/5 flex items-center justify-between">
        <button
          onClick={like}
          className={`flex items-center gap-1.5 text-sm font-medium transition-all px-3 py-1.5 rounded-lg ${
            post.liked_by_me ? "text-rose-400 bg-rose-400/10" : "text-gray-400 hover:text-rose-400 hover:bg-rose-400/10"
          }`}
        >
          <Heart size={16} fill={post.liked_by_me ? "currentColor" : "none"} />
          Like
        </button>
        <button
          onClick={() => setShowComments(!showComments)}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-blue-400 hover:bg-blue-400/10 transition-all px-3 py-1.5 rounded-lg"
        >
          <MessageCircle size={16} />
          Comment
        </button>
        <button
          onClick={save}
          className={`flex items-center gap-1.5 text-sm font-medium transition-all px-3 py-1.5 rounded-lg ${
            post.saved_by_me ? "text-[#D4AF37] bg-[#D4AF37]/10" : "text-gray-400 hover:text-[#D4AF37] hover:bg-[#D4AF37]/10"
          }`}
        >
          <Bookmark size={16} fill={post.saved_by_me ? "currentColor" : "none"} />
          Save
        </button>
      </div>

      {/* Latest 2 comments — visible without expanding anything */}
      {!showComments && post.comments.length > 0 && (
        <div className="border-t border-white/5 px-5 py-3 space-y-3">
          {post.comments.slice(-2).map((c) => (
            <CommentRow
              key={c.id}
              postOwner={post.owner_username}
              contentIndex={post.content_index}
              comment={c}
              identity={identity}
              myProfilePicUrl={myProfilePicUrl}
              onCommentChanged={updateComment}
              requireIdentity={requireIdentity}
              onHashtagClick={onHashtagClick}
            />
          ))}
          {post.comments_count > 2 && (
            <button
              onClick={() => setShowComments(true)}
              className="text-xs font-medium text-[#D4AF37] hover:opacity-80 transition-opacity pl-10"
            >
              View all {post.comments_count} comments
            </button>
          )}
        </div>
      )}

      {/* Full comments thread + reply box */}
      {showComments && (
        <div className="border-t border-white/10 bg-white/3 px-5 py-4 space-y-4">
          {post.comments.length === 0 && (
            <p className="text-xs text-gray-500">No comments yet — be the first.</p>
          )}
          {post.comments.map((c) => (
            <CommentRow
              key={c.id}
              postOwner={post.owner_username}
              contentIndex={post.content_index}
              comment={c}
              identity={identity}
              myProfilePicUrl={myProfilePicUrl}
              onCommentChanged={updateComment}
              requireIdentity={requireIdentity}
              onHashtagClick={onHashtagClick}
            />
          ))}

          <div className="flex gap-3 mt-2">
            <Avatar name={identity.name || "?"} imageUrl={myProfilePicUrl} size="sm" />
            <div className="flex-1 flex gap-2">
              <input
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitComment()}
                placeholder="Share your thoughts..."
                className="flex-1 bg-white/10 border border-white/10 rounded-full px-4 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-[#D4AF37]/50 transition-colors"
              />
              <button
                onClick={submitComment}
                disabled={!commentText.trim() || busy}
                className="w-8 h-8 rounded-full bg-[#D4AF37] text-black flex items-center justify-center hover:bg-[#C9A84C] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0 self-center"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

export function Community() {
  const [posts, setPosts] = useState<Post[]>(cachedPosts ?? []);
  const [loading, setLoading] = useState(cachedPosts === null);
  const [loadError, setLoadError] = useState("");
  const [identity, setIdentity] = useState(getCurrentIdentity());

  const [composerOpen, setComposerOpen] = useState(false);
  const [newPostText, setNewPostText] = useState("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [newRating, setNewRating] = useState(0);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [myCountry, setMyCountry] = useState("");
  const [myFullName, setMyFullName] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<"Latest" | "Photos">("Latest");

  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);

  const loadPosts = async () => {
    // Only show the big spinner the very first time, when there's no
    // cached data yet. On every later visit we already have something on
    // screen, so we just refresh quietly behind the scenes.
    if (cachedPosts === null) setLoading(true);
    setLoadError("");
    try {
      const data = await apiListPosts();
      cachedPosts = data;
      setPosts(data);
    } catch (e) {
      // If we have stale cached data, keep showing it rather than
      // replacing the feed with an error on a background refresh failure.
      if (cachedPosts === null) {
        setLoadError(e instanceof Error ? e.message : "Failed to load posts.");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPosts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Runs `action` immediately if we already know who's posting (logged in,
  // or a guest name was typed before). Otherwise asks for a name first.
  const requireIdentity = useCallback((action: () => void) => {
    const current = getCurrentIdentity();
    setIdentity(current);
    if (current.name) {
      action();
    } else {
      setPendingAction(() => action);
    }
  }, []);

  const handleGuestNameSubmit = (name: string) => {
    setGuestName(name);
    setIdentity({ name, loggedIn: false });
    setPendingAction(null);
    pendingAction?.();
  };

  // Every mutation goes through this so the module-level cache and the
  // on-screen list never drift apart from each other.
  const applyPosts = useCallback((updater: (prev: Post[]) => Post[]) => {
    setPosts((prev) => {
      const next = updater(prev);
      cachedPosts = next;
      return next;
    });
  }, []);

  const updatePostInList = useCallback(
    (updated: Post) =>
      applyPosts((prev) =>
        prev.map((p) =>
          p.owner_username === updated.owner_username && p.content_index === updated.content_index ? updated : p
        )
      ),
    [applyPosts]
  );

  const removePostFromList = useCallback(
    (owner: string, idx: number) =>
      applyPosts((prev) => prev.filter((p) => !(p.owner_username === owner && p.content_index === idx))),
    [applyPosts]
  );

  const publishPost = () => {
    if (!newPostText.trim() && uploadFiles.length === 0) return;
    requireIdentity(async () => {
      setPublishing(true);
      setPublishError("");
      try {
        const post = await apiCreatePost(newPostText.trim(), uploadFiles, newRating || null);
        applyPosts((prev) => [post, ...prev]);
        setNewPostText("");
        setUploadFiles([]);
        setNewRating(0);
        setShowEmojiPicker(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
        setComposerOpen(false);
      } catch (e) {
        setPublishError(e instanceof Error ? e.message : "Failed to publish post.");
      } finally {
        setPublishing(false);
      }
    });
  };

  const insertEmoji = (emoji: string) => {
    const el = textareaRef.current;
    if (!el) {
      setNewPostText((t) => t + emoji);
      return;
    }
    const start = el.selectionStart ?? newPostText.length;
    const end = el.selectionEnd ?? newPostText.length;
    const next = newPostText.slice(0, start) + emoji + newPostText.slice(end);
    setNewPostText(next);
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = start + emoji.length;
    });
  };

  const filteredPosts = posts.filter((p) => {
    if (activeFilter === "Photos" && !(p.image_urls?.length || p.image_url)) return false;
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return p.owner_username.toLowerCase().includes(q) || p.text.toLowerCase().includes(q);
  });

  // `identity` only carries {name, loggedIn} straight from the JWT — it never
  // had a profile picture. So every "this is me" avatar (composer, comment
  // box) was always falling back to initials, even for users who *do* have a
  // profile pic. We fetch the real one from /api/account/me — the same
  // authoritative source Account.tsx uses (set via avatar upload) — rather
  // than guessing from posts, since a user with no posts yet would have no
  // way to surface their picture that way.
  const [myProfilePicUrl, setMyProfilePicUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!identity.loggedIn) {
      setMyProfilePicUrl(null);
      setMyCountry("");
      setMyFullName("");
      return;
    }
    let cancelled = false;
    apiAccountMe()
      .then((u) => {
        if (cancelled) return;
        setMyProfilePicUrl(u.profile_pic_url || null);
        setMyCountry(u.country || "");
        setMyFullName(u.full_name || "");
      })
      .catch(() => { if (!cancelled) { setMyProfilePicUrl(null); setMyCountry(""); setMyFullName(""); } });
    return () => { cancelled = true; };
  }, [identity.loggedIn, identity.name]);

  // Top 10 most active travelers, ranked purely by how many posts they've
  // shared — real data straight from what's already loaded, no fake numbers.
  const topTravelers = useMemo(() => {
    const counts = new Map<string, { count: number; profile_pic_url: string }>();
    for (const p of posts) {
      const existing = counts.get(p.owner_username);
      if (existing) {
        existing.count += 1;
      } else {
        counts.set(p.owner_username, { count: 1, profile_pic_url: p.profile_pic_url });
      }
    }
    return Array.from(counts.entries())
      .map(([username, data]) => ({ username, ...data }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);
  }, [posts]);

  return (
    <div className="space-y-6">
      {pendingAction && (
        <GuestNameModal onSubmit={handleGuestNameSubmit} onCancel={() => setPendingAction(null)} />
      )}

      {/* Hero Banner */}
      <div className="relative rounded-3xl overflow-hidden border border-white/10">
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: "url(https://images.unsplash.com/photo-1754400534733-06ba59423253?w=1600&q=80)" }}
        />
        <div className="absolute inset-0 bg-gradient-to-r from-[#0A0B1E]/95 via-[#0A0B1E]/70 to-transparent" />
        <div className="relative px-8 py-10">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="text-[#D4AF37]" size={20} />
            <span className="text-[#D4AF37] text-sm font-medium tracking-wider uppercase">Traveler Community</span>
          </div>
          <h1 className="text-4xl font-bold text-white mb-2">Share Your Egypt Story</h1>
          <p className="text-gray-300 max-w-xl mb-6">
            Connect with fellow explorers, share your experiences, discover hidden gems, and inspire the next generation of Egypt travelers.
          </p>
          <button
            onClick={() => setComposerOpen(true)}
            className="px-6 py-3 bg-[#D4AF37] hover:bg-[#C9A84C] text-black font-semibold rounded-xl transition-all shadow-lg shadow-[#D4AF37]/20 flex items-center gap-2"
          >
            <Camera size={18} />
            Share Your Experience
          </button>
        </div>
      </div>

      {/* Main layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Feed - left/main */}
        <div className="lg:col-span-2 space-y-5">
          {/* Search + filters */}
          <div className="space-y-3">
            <div className="relative">
              <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search travelers..."
                className="w-full bg-white/5 border border-white/10 rounded-xl pl-10 pr-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-[#D4AF37]/40 transition-colors"
              />
            </div>
            <div className="flex items-center gap-2">
              <Filter size={14} className="text-gray-400 flex-shrink-0" />
              {(["Latest", "Photos"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setActiveFilter(f)}
                  className={`px-4 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
                    activeFilter === f ? "bg-[#D4AF37] text-black" : "bg-white/10 text-gray-400 hover:bg-white/15 hover:text-white"
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          {/* Compose teaser (collapsed) */}
          {!composerOpen && (
            <button
              onClick={() => setComposerOpen(true)}
              className="w-full bg-white/5 border border-white/10 hover:border-[#D4AF37]/30 rounded-2xl p-4 flex items-center gap-3 transition-all group text-left"
            >
              <Avatar name={identity.name || "?"} imageUrl={myProfilePicUrl} size="md" />
              <span className="text-gray-500 group-hover:text-gray-400 text-sm transition-colors flex-1">
                {"What's your Egypt story? Share it with the community..."}
              </span>
              <Camera size={18} className="text-gray-500 group-hover:text-[#D4AF37] transition-colors" />
            </button>
          )}

          {/* Compose expanded */}
          {composerOpen && (
            <div className="bg-white/5 backdrop-blur-sm border border-[#D4AF37]/30 rounded-2xl p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Avatar name={identity.name || "?"} imageUrl={myProfilePicUrl} size="md" />
                  <div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <p className="text-sm font-semibold text-white">
                        {identity.name ? myFullName || identity.name : "Share Your Experience"}
                      </p>
                      {identity.name && <span className="text-xs text-gray-400">@{identity.name}</span>}
                      {myCountry && (
                        <span className="text-[10px] font-semibold text-[#D4AF37] bg-[#D4AF37]/10 border border-[#D4AF37]/30 rounded-full px-1.5 py-0.5">
                          {countryAbbr(myCountry)}
                        </span>
                      )}
                      {!identity.loggedIn && identity.name && (
                        <span className="text-[10px] text-gray-500">(guest)</span>
                      )}
                    </div>
                    <p className="text-xs text-gray-400">Visible to all KEMET travelers</p>
                  </div>
                </div>
                <button onClick={() => setComposerOpen(false)} className="text-gray-500 hover:text-white transition-colors">
                  <X size={18} />
                </button>
              </div>

              <textarea
                ref={textareaRef}
                value={newPostText}
                onChange={(e) => setNewPostText(e.target.value)}
                placeholder="Describe your experience, tips, hidden gems, honest thoughts... #hashtags welcome"
                rows={4}
                maxLength={500}
                className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-[#D4AF37]/40 transition-colors resize-none"
              />

              {uploadFiles.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {uploadFiles.map((f, i) => (
                    <div key={i} className="relative w-16 h-16 rounded-lg overflow-hidden border border-white/10 group">
                      <img src={URL.createObjectURL(f)} alt={f.name} className="w-full h-full object-cover" />
                      <button
                        onClick={() => setUploadFiles((prev) => prev.filter((_, idx) => idx !== i))}
                        className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity"
                      >
                        <X size={14} className="text-white" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex items-center justify-between flex-wrap gap-3">
                <div className="flex items-center gap-4 relative">
                  <label className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-[#D4AF37] transition-colors cursor-pointer">
                    <ImageIcon size={16} />
                    {uploadFiles.length > 0 ? `${uploadFiles.length} photo${uploadFiles.length > 1 ? "s" : ""}` : "Add photos"}
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/jpeg,image/png,image/jpg"
                      multiple
                      className="hidden"
                      onChange={(e) => setUploadFiles((prev) => [...prev, ...Array.from(e.target.files || [])].slice(0, 6))}
                    />
                  </label>

                  <button
                    type="button"
                    onClick={() => setShowEmojiPicker((v) => !v)}
                    className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-[#D4AF37] transition-colors"
                  >
                    <Smile size={16} />
                    Emoji
                  </button>
                  {showEmojiPicker && (
                    <EmojiPicker onPick={insertEmoji} onClose={() => setShowEmojiPicker(false)} />
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500">Your rating</span>
                  <StarRating value={newRating} onChange={setNewRating} size={16} />
                </div>
              </div>

              {publishError && (
                <div className="flex items-center gap-2 text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-xl px-3 py-2">
                  <AlertCircle size={14} />
                  {publishError}
                </div>
              )}

              <div className="flex items-center justify-between pt-1">
                <span className={`text-xs ${newPostText.length > 480 ? "text-rose-400" : "text-gray-500"}`}>
                  {newPostText.length}/500
                </span>
                <button
                  onClick={publishPost}
                  disabled={(!newPostText.trim() && uploadFiles.length === 0) || publishing}
                  className="px-5 py-2 bg-[#D4AF37] hover:bg-[#C9A84C] text-black text-sm font-semibold rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {publishing ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  Publish
                </button>
              </div>
            </div>
          )}

          {/* Posts feed */}
          {loading ? (
            <div className="flex justify-center py-16">
              <Loader2 size={28} className="animate-spin text-[#D4AF37]" />
            </div>
          ) : loadError ? (
            <div className="text-center py-16 text-red-400 text-sm">{loadError}</div>
          ) : filteredPosts.length === 0 ? (
            <div className="text-center py-16 text-gray-500">
              <Globe size={40} className="mx-auto mb-3 opacity-30" />
              <p>{posts.length === 0 ? "No posts yet. Be the first to share your experience!" : "No posts match your search."}</p>
            </div>
          ) : (
            filteredPosts.map((post) => (
              <PostCard
                key={`${post.owner_username}-${post.content_index}`}
                post={post}
                identity={identity}
                myProfilePicUrl={myProfilePicUrl}
                onChanged={updatePostInList}
                onDeleted={removePostFromList}
                requireIdentity={requireIdentity}
                onHashtagClick={(tag) => setSearchQuery(tag)}
              />
            ))
          )}
        </div>

        {/* Sidebar - right */}
        <div className="space-y-5">
          {/* Top Travelers — real ranking by post count */}
          {topTravelers.length > 0 && (
            <div className="bg-white/5 border border-white/10 rounded-2xl p-5">
              <h3 className="text-sm font-semibold text-white mb-4 flex items-center gap-2">
                <Trophy size={15} className="text-[#D4AF37]" />
                Top Travelers
              </h3>
              <ul className="space-y-3">
                {topTravelers.map((t, idx) => (
                  <li key={t.username} className="flex items-center gap-3">
                    <span className="w-4 text-xs font-semibold text-gray-500 flex-shrink-0">{idx + 1}</span>
                    <Avatar name={t.username} imageUrl={t.profile_pic_url} size="sm" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">@{t.username}</p>
                    </div>
                    <span className="text-xs text-gray-400 flex-shrink-0">
                      {t.count} post{t.count !== 1 ? "s" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Community Guidelines card */}
          <div className="bg-gradient-to-br from-[#D4AF37]/15 to-[#8B7355]/10 border border-[#D4AF37]/30 rounded-2xl p-5">
            <h3 className="text-sm font-semibold text-[#D4AF37] mb-3 flex items-center gap-2">
              <Sparkles size={15} />
              Community Guidelines
            </h3>
            <ul className="space-y-2 text-xs text-gray-400">
              {[
                "Share honest, first-hand experiences",
                "Be respectful of local culture & customs",
                "Include helpful tips for fellow travelers",
                "No spam or promotional content",
              ].map((rule) => (
                <li key={rule} className="flex items-start gap-2">
                  <MapPin size={12} className="text-[#D4AF37] mt-0.5 flex-shrink-0" />
                  {rule}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}