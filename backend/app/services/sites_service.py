"""
Ancient Sites data service — same pattern as `museums_service.py`.

Update: the data now loads straight from the GitHub repo (Data/silver/
ancient_sites_en.csv) instead of Azure Blob Storage — no Azure credentials
needed for this file anymore. Columns unchanged:
    id, place_name, government, description, open, close, image_url, map
(no tickets_price column in this export — see tickets_service.py for
pricing, which comes from a separate dataset.)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from threading import Lock

import pandas as pd

from app.services.data_source import fetch_csv_from_github, GithubDataError

GITHUB_PATH = "Data/silver/ancient_sites_en.csv"
CACHE_TTL_SECONDS = 15 * 60


class SitesDataError(Exception):
    """Raised when the ancient-sites dataset can't be loaded."""


def strip_html(text):
    if not isinstance(text, str):
        return text
    clean = re.sub(r"<[^>]+>", "", text)
    for ent, rep in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')]:
        clean = clean.replace(ent, rep)
    return clean.strip()


@dataclass
class _TTLCache:
    ttl_seconds: int
    _value: list | None = field(default=None, init=False)
    _expires_at: float = field(default=0.0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def get_or_set(self, factory):
        with self._lock:
            now = time.time()
            if self._value is not None and now < self._expires_at:
                return self._value
            value = factory()
            self._value = value
            self._expires_at = now + self.ttl_seconds
            return value


_cache = _TTLCache(ttl_seconds=CACHE_TTL_SECONDS)


def _fetch_dataframe() -> pd.DataFrame:
    try:
        df = fetch_csv_from_github(GITHUB_PATH)
    except GithubDataError as exc:
        raise SitesDataError(str(exc)) from exc

    df.columns = df.columns.str.strip()
    for col in ["description", "place_name", "government"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: strip_html(x) if pd.notna(x) else x)
    return df


def _record_from_row(row, full_description: bool = False) -> dict:
    name = strip_html(str(row.get("place_name", "")))
    location = strip_html(str(row.get("government", "")))
    full_desc = strip_html(str(row.get("description", ""))) if pd.notna(row.get("description")) else ""
    if full_description:
        desc = full_desc
    else:
        desc = next((line.strip() for line in full_desc.split("\n") if line.strip()), full_desc)
    img = (
        str(row.get("image_url", ""))
        if pd.notna(row.get("image_url"))
        else "https://images.unsplash.com/photo-1568322445389-f64ac2515020?w=600"
    )
    open_time = row.get("open")
    close_time = row.get("close")
    hours = f"{open_time} \u2013 {close_time}" if pd.notna(open_time) and pd.notna(close_time) else "Not Available"
    maps_url = str(row.get("map", "")) if pd.notna(row.get("map")) else None

    return {
        "name": name,
        "location": location,
        "desc": desc,
        "full_desc": full_desc,
        "img": img,
        "hours": hours,
        "maps_url": maps_url,
    }


def _load_dataframe_cached() -> pd.DataFrame:
    return _cache.get_or_set(_fetch_dataframe)


def get_sites(location: str | None = None, search: str | None = None) -> list[dict]:
    """All sites, optionally filtered.

    Filtering is supported server-side too, mirroring museums_service, but
    the frontend currently filters client-side for instant results.
    """
    df = _load_dataframe_cached()
    records = [_record_from_row(row) for _, row in df.iterrows()]

    if location and location != "All Locations":
        records = [r for r in records if r["location"] == location]
    if search:
        q = search.lower()
        records = [r for r in records if q in r["name"].lower()]
    return records


def get_locations() -> list[str]:
    df = _load_dataframe_cached()
    locations = {strip_html(str(loc)) for loc in df["government"].dropna()}
    return ["All Locations"] + sorted(locations)
