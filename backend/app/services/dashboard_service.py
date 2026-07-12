"""
Dashboard Service
------------------
Weather (open-meteo) and currency (open-er-api) stay exactly as before: live data,
cached in memory for REFRESH_INTERVAL seconds.

Everything tourism-related below is real KEMET Gold-layer data — the Galaxy Schema
built on Azure Databricks (Bronze -> Silver -> Gold). The `kemetstorage` Azure
Storage account that used to serve these CSVs was permanently disabled, so the
Gold/Silver/Bronze exports now live directly in the GitHub repo instead
(elsayedashraf05/kemet-assistant, under Data/gold, Data/silver, Data/bronze) and are
fetched over plain HTTPS from raw.githubusercontent.com — no credentials needed since
it's a public repo. Kept in memory per process either way, so we don't re-download on
every request.

Two things are intentionally kept as constants because they were never tourism-catalog
data to begin with and have no Gold-layer equivalent: emergency phone numbers and the
useful-apps list. They're still served only from the backend, never hardcoded in the
frontend.

Every Gold getter below degrades to an empty/zeroed value instead of raising, so one
missing or half-written CSV only blanks a single chart instead of 500-ing the whole
/summary route (which also carries live weather/currency that have nothing to do with
the Gold export).
"""
import io
import logging
import os
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CITIES = {
    "Alexandria":      {"lat": 31.2001, "lon": 29.9187},
    "Aswan":           {"lat": 24.0889, "lon": 32.8998},
    "Cairo":           {"lat": 30.0444, "lon": 31.2357},
    "Dahab":           {"lat": 28.5010, "lon": 34.5160},
    "Fayoum":          {"lat": 29.3099, "lon": 30.8418},
    "Giza":            {"lat": 30.0131, "lon": 31.2089},
    "Hurghada":        {"lat": 27.2579, "lon": 33.8116},
    "Luxor":           {"lat": 25.6872, "lon": 32.6396},
    "Saint Catherine": {"lat": 28.5647, "lon": 33.9513},
    "Sharm El Sheikh":  {"lat": 27.9158, "lon": 34.3299},
}

REFRESH_INTERVAL = 300  # 5 minutes, same as before

_cache = {"weather": None, "weather_ts": 0, "currency": None, "currency_ts": 0}


# =========================================================================================
# LIVE WEATHER — unchanged
# =========================================================================================
def _fetch_weather_for_city(lat, lon):
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m"
        )
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            current = r.json()["current"]
            return current.get("temperature_2m"), current.get("relative_humidity_2m")
    except Exception:
        pass
    return None, None


def get_live_weather(force_refresh: bool = False):
    """Returns a list of {city, temperature, humidity} for all CITIES."""
    now = time.time()
    if not force_refresh and _cache["weather"] is not None and (now - _cache["weather_ts"] < REFRESH_INTERVAL):
        return _cache["weather"]

    results = []
    for city, coords in CITIES.items():
        temp, hum = _fetch_weather_for_city(coords["lat"], coords["lon"])
        results.append({"city": city, "temperature": temp, "humidity": hum})

    _cache["weather"] = results
    _cache["weather_ts"] = now
    return results


# =========================================================================================
# LIVE CURRENCY — unchanged
# =========================================================================================
def get_live_currency(force_refresh: bool = False):
    """Returns EGP rates for USD/EUR/GBP/SAR/AED, or None if the API failed."""
    now = time.time()
    if not force_refresh and _cache["currency"] is not None and (now - _cache["currency_ts"] < REFRESH_INTERVAL):
        return _cache["currency"]

    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        if r.status_code == 200:
            rates = r.json().get("rates", {})
            egp, eur, gbp, sar, aed = (
                rates.get("EGP"), rates.get("EUR"), rates.get("GBP"),
                rates.get("SAR"), rates.get("AED"),
            )
            if all([egp, eur, gbp, sar, aed]):
                result = {
                    "USD": round(egp, 2),
                    "EUR": round(egp / eur, 2),
                    "GBP": round(egp / gbp, 2),
                    "SAR": round(egp / sar, 2),
                    "AED": round(egp / aed, 2),
                }
                _cache["currency"] = result
                _cache["currency_ts"] = now
                return result
    except Exception:
        pass

    return _cache["currency"]  # last known value (or None) if the API is down right now


# =========================================================================================
# GOLD-LAYER DATA — read live over HTTPS from the GitHub repo's Data/gold folder
# (elsayedashraf05/kemet-assistant), which is where the weekly Databricks export now gets
# committed since the `kemetstorage` Azure Storage account was permanently disabled.
# raw.githubusercontent.com serves public-repo files with no auth needed, so no
# connection string / secret is required for this path at all.
#
# AZURE_DATALAKE_CONNECTION_STRING is kept as an optional path for the future (e.g. if a
# new storage account ever replaces this one) — set it and it takes priority over GitHub
# automatically. Leave it unset (the normal case now) and GitHub is used directly.
#
# Local-folder fallback (GOLD_DATA_DIR) only kicks in if neither GitHub nor Azure is
# reachable — handy for local dev with a local copy of Data/gold, not used in production.
# =========================================================================================
AZURE_DATALAKE_CONNECTION_STRING = os.environ.get("AZURE_DATALAKE_CONNECTION_STRING")
GOLD_CONTAINER = os.environ.get("GOLD_CONTAINER", "gold")
GOLD_BLOB_PREFIX = os.environ.get("GOLD_BLOB_PREFIX", "_csv_exports")

GITHUB_DATA_REPO = os.environ.get("GITHUB_DATA_REPO", "elsayedashraf05/kemet-assistant")
GITHUB_DATA_BRANCH = os.environ.get("GITHUB_DATA_BRANCH", "main")
GITHUB_GOLD_PATH = os.environ.get("GITHUB_GOLD_PATH", "Data/gold")
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_DATA_REPO}/{GITHUB_DATA_BRANCH}/{GITHUB_GOLD_PATH}"

GOLD_DATA_DIR = os.environ.get(
    "GOLD_DATA_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "gold",
    ),
)

_gold_cache: dict[str, pd.DataFrame] = {}
_blob_service_client = None  # lazily created, reused across requests/tables — only used if AZURE_DATALAKE_CONNECTION_STRING is set


def _get_blob_service_client():
    global _blob_service_client
    if _blob_service_client is None:
        from azure.storage.blob import BlobServiceClient  # already in requirements.txt
        _blob_service_client = BlobServiceClient.from_connection_string(AZURE_DATALAKE_CONNECTION_STRING)
    return _blob_service_client


def _read_gold_csv_from_azure(table: str) -> pd.DataFrame:
    blob_path = f"{GOLD_BLOB_PREFIX}/{table}.csv"
    client = _get_blob_service_client().get_blob_client(container=GOLD_CONTAINER, blob=blob_path)
    raw = client.download_blob().readall()
    return pd.read_csv(io.BytesIO(raw))


def _read_gold_csv_from_github(table: str) -> pd.DataFrame:
    url = f"{GITHUB_RAW_BASE}/{table}.csv"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content))


def _gold_source_for(table: str) -> str:
    """Human-readable source string, only used for logging/the debug endpoint."""
    if AZURE_DATALAKE_CONNECTION_STRING:
        return f"azure://{GOLD_CONTAINER}/{GOLD_BLOB_PREFIX}/{table}.csv"
    return f"{GITHUB_RAW_BASE}/{table}.csv"


def _load_gold(table: str) -> pd.DataFrame:
    """Loads a Gold CSV once per process and keeps it in memory. Never raises: a
    missing/unreadable table just logs a warning and serves empty, so it degrades
    that one chart instead of crashing /summary. Call POST /reload after a fresh
    weekly export lands (in the GitHub repo, or Azure if that's configured) to pick
    it up without a redeploy."""
    if table not in _gold_cache:
        try:
            if AZURE_DATALAKE_CONNECTION_STRING:
                _gold_cache[table] = _read_gold_csv_from_azure(table)
            elif GITHUB_DATA_REPO:
                _gold_cache[table] = _read_gold_csv_from_github(table)
            else:
                _gold_cache[table] = pd.read_csv(os.path.join(GOLD_DATA_DIR, f"{table}.csv"))
        except Exception as exc:
            source = _gold_source_for(table) if (AZURE_DATALAKE_CONNECTION_STRING or GITHUB_DATA_REPO) else os.path.join(GOLD_DATA_DIR, f"{table}.csv")
            logger.warning("Gold table '%s' unavailable at %s (%s) — serving empty.", table, source, exc)
            _gold_cache[table] = pd.DataFrame()
    return _gold_cache[table]


def _reload_gold_cache():
    """Call this after a fresh weekly export lands (GitHub commit, or Azure if that's
    configured), instead of restarting the process — clears the in-memory cache so
    the next request re-reads the CSVs."""
    _gold_cache.clear()


GOLD_TABLES = [
    "fact_attractions", "fact_hotels", "fact_restaurants", "fact_beaches",
    "dim_governorate", "dim_attraction_type", "dim_cuisine", "dim_price_tier",
    "ref_historical_timeline", "fact_national_stats",
]


def get_gold_debug_info() -> dict:
    """Diagnostic helper — tries loading every Gold table fresh (bypassing the cache)
    and reports exactly what happened: where it tried to read from, whether it
    worked, how many rows it got, and the real exception message if it didn't. Hit
    GET /api/dashboard/debug/gold directly when charts come back empty."""
    results = {}
    for table in GOLD_TABLES:
        source = _gold_source_for(table)
        try:
            if AZURE_DATALAKE_CONNECTION_STRING:
                df = _read_gold_csv_from_azure(table)
            elif GITHUB_DATA_REPO:
                df = _read_gold_csv_from_github(table)
            else:
                df = pd.read_csv(os.path.join(GOLD_DATA_DIR, f"{table}.csv"))
            results[table] = {
                "ok": True, "source": source, "rows": int(len(df)),
                "columns": list(df.columns), "error": None,
            }
        except Exception as exc:
            results[table] = {
                "ok": False, "source": source, "rows": 0,
                "columns": [], "error": f"{type(exc).__name__}: {exc}",
            }
    return {
        "data_source": "azure" if AZURE_DATALAKE_CONNECTION_STRING else ("github" if GITHUB_DATA_REPO else "local"),
        "container": GOLD_CONTAINER if AZURE_DATALAKE_CONNECTION_STRING else None,
        "prefix": GOLD_BLOB_PREFIX if AZURE_DATALAKE_CONNECTION_STRING else None,
        "github_raw_base": GITHUB_RAW_BASE if (GITHUB_DATA_REPO and not AZURE_DATALAKE_CONNECTION_STRING) else None,
        "local_fallback_dir": GOLD_DATA_DIR if not (AZURE_DATALAKE_CONNECTION_STRING or GITHUB_DATA_REPO) else None,
        "tables": results,
    }


def get_highlights() -> dict:
    """Headline counts for the KPI strip under the hero."""
    attractions = _load_gold("fact_attractions")
    hotels = _load_gold("fact_hotels")
    restaurants = _load_gold("fact_restaurants")
    beaches = _load_gold("fact_beaches")
    governorates = _load_gold("dim_governorate")
    return {
        "attractions": int(len(attractions)),
        "hotels": int(len(hotels)),
        "restaurants": int(len(restaurants)),
        "beaches": int(len(beaches)),
        "governorates": int(governorates["governorate_key"].nunique()) if "governorate_key" in governorates.columns else 0,
    }


def get_attractions_by_governorate() -> list[dict]:
    """Powers the 'Explore Egypt by Region' bar chart."""
    attractions, governorates = _load_gold("fact_attractions"), _load_gold("dim_governorate")
    if attractions.empty or governorates.empty:
        return []
    df = attractions.merge(governorates, on="governorate_key", how="left")
    counts = (
        df.dropna(subset=["governorate_name"])
        .groupby("governorate_name").size()
        .sort_values(ascending=False)
    )
    return [{"governorate": name, "count": int(count)} for name, count in counts.items()]


def get_attraction_types() -> list[dict]:
    """Powers the 'What Awaits You' donut chart (Ancient Sites / Monuments / Museums)."""
    attractions, types = _load_gold("fact_attractions"), _load_gold("dim_attraction_type")
    if attractions.empty or types.empty:
        return []
    df = attractions.merge(types, on="type_key", how="left")
    counts = df["type_name"].value_counts()
    return [{"type": name, "count": int(count)} for name, count in counts.items()]


def get_top_cuisines(limit: int = 8) -> list[dict]:
    """Powers the 'Flavors of Egypt' bar chart."""
    restaurants, cuisines = _load_gold("fact_restaurants"), _load_gold("dim_cuisine")
    if restaurants.empty or cuisines.empty:
        return []
    df = restaurants.merge(cuisines, on="cuisine_key", how="left")
    counts = df["cuisine_name"].value_counts().head(limit)
    return [{"cuisine": name, "count": int(count)} for name, count in counts.items()]


def get_hotel_price_tiers() -> list[dict]:
    """Powers the 'Hotels by Budget' breakdown (Budget / Mid-range / Luxury)."""
    order = ["Budget", "Mid-range", "Luxury"]
    hotels, tiers = _load_gold("fact_hotels"), _load_gold("dim_price_tier")
    if hotels.empty or tiers.empty:
        return [{"tier": tier, "count": 0} for tier in order]
    df = hotels.merge(tiers, on="tier_key", how="left")
    counts = df["tier_name"].value_counts()
    return [{"tier": tier, "count": int(counts.get(tier, 0))} for tier in order]


def get_beach_ratings_by_governorate(limit: int = 8) -> list[dict]:
    """Powers the 'Best-Rated Beaches' bar chart."""
    beaches, governorates = _load_gold("fact_beaches"), _load_gold("dim_governorate")
    if beaches.empty or governorates.empty:
        return []
    df = beaches.merge(governorates, on="governorate_key", how="left")
    avg = (
        df.dropna(subset=["governorate_name"])
        .groupby("governorate_name")["rating"].mean()
        .sort_values(ascending=False)
        .head(limit)
    )
    return [{"governorate": name, "rating": round(float(rating), 2)} for name, rating in avg.items()]


def get_historical_timeline() -> list[dict]:
    """Powers the '5,000 Years of History' timeline strip. start_year/end_year are
    signed (negative = BC) — already parsed this way in Silver, no conversion needed."""
    df = _load_gold("ref_historical_timeline")
    if df.empty:
        return []
    df = df.sort_values("start_year")
    out = []
    for _, row in df.iterrows():
        if pd.isna(row.get("start_year")) or pd.isna(row.get("end_year")):
            continue
        out.append({
            "period": row["collection"],
            "start_year": int(row["start_year"]),
            "end_year": int(row["end_year"]),
            "duration_years": int(row["duration_years"]),
        })
    return out


def get_national_stats() -> dict | None:
    """Powers the 'Egypt at a Glance' stat row. Returns None (frontend hides the
    section) rather than raising if the snapshot table isn't there yet."""
    df = _load_gold("fact_national_stats")
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "archaeological_sites": int(row["archaeological_sites"]),
        "museums": int(row["museums"]),
        "hotels_total": int(row["hotels_total"]),
        "diving_activity_centers": int(row["diving_activity_centers"]),
        "tourism_companies": int(row["tourism_companies"]),
        "souvenir_shops": int(row["souvenir_shops"]),
        "tourist_restaurants_cafes": int(row["tourist_restaurants_cafes"]),
        "tourist_vehicles": int(row["tourist_vehicles"]),
        "snapshot_date": str(row["snapshot_date"]),
    }


def _format_year(year: int) -> str:
    return f"{abs(year)} BC" if year < 0 else f"{year} AD"


# Google's own default pin/placeholder graphic — served whenever the scraper found a
# listing on Google Maps that has no real uploaded photo. Filtering this out is a data-
# quality step (not a code bug): the scrape correctly recorded what Google Maps actually
# showed for that listing, which happened to be its generic "no photo" placeholder.
_PLACEHOLDER_IMAGE_MARKERS = (
    "gstatic.com/tactile/pane/default_geocode",  # Google Maps' generic pin/no-photo image
    "maps.gstatic.com/mapfiles/",                 # other generic Maps UI graphics
)


def _is_placeholder_image(url) -> bool:
    if not isinstance(url, str) or not url.strip():
        return True
    low = url.lower()
    return any(marker in low for marker in _PLACEHOLDER_IMAGE_MARKERS)


SPOTLIGHT_LIMIT = 12


def get_spotlight_beaches(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    """Real beaches with real photos, for the rotating 'Discover Egypt' card that
    links to /beaches."""
    beaches, governorates = _load_gold("fact_beaches"), _load_gold("dim_governorate")
    if beaches.empty or "photo_url" not in beaches.columns:
        return []
    df = beaches.merge(governorates, on="governorate_key", how="left") if not governorates.empty else beaches
    df = df.dropna(subset=["photo_url", "name"])
    df = df[df["photo_url"].astype(str).str.strip() != ""]
    df = df[~df["photo_url"].apply(_is_placeholder_image)]
    if "rating" in df.columns:
        df = df.sort_values("rating", ascending=False)
    df = df.head(limit)
    out = []
    for _, r in df.iterrows():
        gov, rating = r.get("governorate_name"), r.get("rating")
        bits = [str(gov)] if pd.notna(gov) else []
        if pd.notna(rating):
            bits.append(f"{rating}★")
        out.append({"name": str(r["name"]), "subtitle": " · ".join(bits) or "Egypt", "image": str(r["photo_url"])})
    return out


def get_spotlight_restaurants(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    """Real restaurants with real photos, for the card that links to /restaurants."""
    restaurants, governorates = _load_gold("fact_restaurants"), _load_gold("dim_governorate")
    if restaurants.empty or "photo_url" not in restaurants.columns:
        return []
    df = restaurants.merge(governorates, on="governorate_key", how="left") if not governorates.empty else restaurants
    df = df.dropna(subset=["photo_url", "name"])
    df = df[df["photo_url"].astype(str).str.strip() != ""]
    df = df[~df["photo_url"].apply(_is_placeholder_image)]
    if "rating" in df.columns:
        df = df.sort_values("rating", ascending=False)
    df = df.head(limit)
    out = []
    for _, r in df.iterrows():
        cat, gov = r.get("category"), r.get("governorate_name")
        bits = [str(p) for p in (cat, gov) if pd.notna(p)]
        out.append({"name": str(r["name"]), "subtitle": " · ".join(bits) or "Egypt", "image": str(r["photo_url"])})
    return out


def get_spotlight_hotels(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    """Real hotels with real photos, for the card that links to /hotels."""
    hotels = _load_gold("fact_hotels")
    if hotels.empty or "image" not in hotels.columns:
        return []
    df = hotels
    governorates, tiers = _load_gold("dim_governorate"), _load_gold("dim_price_tier")
    if not governorates.empty:
        df = df.merge(governorates, on="governorate_key", how="left")
    if not tiers.empty:
        df = df.merge(tiers, on="tier_key", how="left")
    df = df.dropna(subset=["image", "name"])
    df = df[df["image"].astype(str).str.strip() != ""]
    df = df[~df["image"].apply(_is_placeholder_image)]
    if "rating_score" in df.columns:
        df = df.sort_values("rating_score", ascending=False)
    df = df.head(limit)
    out = []
    for _, r in df.iterrows():
        tier, gov = r.get("tier_name"), r.get("governorate_name")
        bits = [str(p) for p in (tier, gov) if pd.notna(p)]
        out.append({"name": str(r["name"]), "subtitle": " · ".join(bits) or "Egypt", "image": str(r["image"])})
    return out


def _spotlight_attractions_by_type(type_name: str, limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    """Shared logic for Ancient Sites / Monuments / Museums — all three live in
    fact_attractions, split by dim_attraction_type.type_name, and each has its own
    directory page on the site."""
    attractions, types = _load_gold("fact_attractions"), _load_gold("dim_attraction_type")
    if attractions.empty or types.empty or "image_url" not in attractions.columns:
        return []
    df = attractions.merge(types, on="type_key", how="left")
    governorates = _load_gold("dim_governorate")
    if not governorates.empty:
        df = df.merge(governorates, on="governorate_key", how="left")
    df = df[df["type_name"] == type_name]
    df = df.dropna(subset=["image_url", "place_name"])
    df = df[df["image_url"].astype(str).str.strip() != ""]
    df = df[~df["image_url"].apply(_is_placeholder_image)]
    df = df.head(limit)
    out = []
    for _, r in df.iterrows():
        gov = r.get("governorate_name")
        out.append({
            "name": str(r["place_name"]),
            "subtitle": str(gov) if pd.notna(gov) else "Egypt",
            "image": str(r["image_url"]),
        })
    return out


def get_spotlight_ancient_sites(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    return _spotlight_attractions_by_type("Ancient Site", limit)


def get_spotlight_monuments(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    return _spotlight_attractions_by_type("Monument", limit)


def get_spotlight_museums(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    return _spotlight_attractions_by_type("Museum", limit)


def get_spotlight_historical_periods(limit: int = SPOTLIGHT_LIMIT) -> list[dict]:
    """Real historical periods with real photos, for the card that links to
    /historical-periods."""
    df = _load_gold("ref_historical_timeline")
    if df.empty or "photo_url" not in df.columns:
        return []
    df = df.dropna(subset=["photo_url", "collection"])
    df = df[df["photo_url"].astype(str).str.strip() != ""]
    df = df.head(limit)
    out = []
    for _, r in df.iterrows():
        start, end = r.get("start_year"), r.get("end_year")
        subtitle = f"{_format_year(int(start))} – {_format_year(int(end))}" if pd.notna(start) and pd.notna(end) else "Ancient Egypt"
        out.append({"name": str(r["collection"]), "subtitle": subtitle, "image": str(r["photo_url"])})
    return out


def get_spotlights() -> dict:
    """One rotating, real-data card per major directory page — each links straight
    to that page on the site. The frontend rotates through `items` client-side (no
    extra network calls needed to change the card every minute); this endpoint just
    supplies a real, image-bearing pool per category to rotate through."""
    return {
        "beaches": {"label": "Beaches", "page": "/beaches", "items": get_spotlight_beaches()},
        "restaurants": {"label": "Restaurants", "page": "/restaurants", "items": get_spotlight_restaurants()},
        "hotels": {"label": "Hotels", "page": "/hotels", "items": get_spotlight_hotels()},
        "ancient_sites": {"label": "Ancient Sites", "page": "/ancient-sites", "items": get_spotlight_ancient_sites()},
        "monuments": {"label": "Monuments", "page": "/monuments", "items": get_spotlight_monuments()},
        "museums": {"label": "Museums", "page": "/museums", "items": get_spotlight_museums()},
        "historical_periods": {"label": "Historical Periods", "page": "/historical-periods", "items": get_spotlight_historical_periods()},
    }


def get_kemet_data_bundle() -> dict:
    """One call, every piece of Gold-derived content the dashboard needs — keeps the
    route handler in dashboard.py a thin pass-through instead of assembling this itself."""
    return {
        "highlights": get_highlights(),
        "attractions_by_governorate": get_attractions_by_governorate(),
        "attraction_types": get_attraction_types(),
        "top_cuisines": get_top_cuisines(),
        "hotel_price_tiers": get_hotel_price_tiers(),
        "beach_ratings_by_governorate": get_beach_ratings_by_governorate(),
        "historical_timeline": get_historical_timeline(),
        "national_stats": get_national_stats(),
        "spotlights": get_spotlights(),
    }


# =========================================================================================
# STATIC REFERENCE DATA — genuinely not tourism-catalog data (phone numbers, app links),
# so there's no Gold table for these and none is needed. Kept here, still served only
# from the backend (never hardcoded in the frontend). `icon` is a lucide-react icon
# name — no emoji, the frontend maps this to a real icon component.
# =========================================================================================
EMERGENCY_INFO = {
    "tourist_police": "126",
    "ambulance": "123",
    "fire": "180",
    "embassy_hotline": "+20 2 2797 3300",
    "general_emergency": "123",
}

USEFUL_APPS = [
    {"name": "Uber / Careem", "url": "https://www.uber.com", "icon": "car"},
    {"name": "Talabat", "url": "https://www.talabat.com", "icon": "utensils"},
    {"name": "Vezeeta", "url": "https://www.vezeeta.com", "icon": "heart-pulse"},
    {"name": "Google Maps", "url": "https://maps.google.com", "icon": "map"},
]