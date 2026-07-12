"""
Beaches Service
----------------
نفس منطق hotels_service.py بالظبط (نفس أسلوب الـ cache والـ BeachesDataError)
لكن للداتا بتاعة الشواطئ (kemet_beaches_data.csv) بدل الفنادق.

تحديث: الداتا بقت بتتقرأ من ملف الريبو على GitHub مباشرة (Data/silver/) بدل
Azure Blob Storage — مفيش أي Azure credentials مطلوبة للملف ده خالص دلوقتي.
"""
import re
from functools import lru_cache

import pandas as pd

from app.services.data_source import fetch_csv_from_github, GithubDataError

GITHUB_PATH = "Data/silver/kemet_beaches_data.csv"


def upscale_photo_url(url: str) -> str:
    """
    صور الشواطئ جايه من Google (googleusercontent.com) بروابط فيها
    حجم محدد زي =w408-h306-k-no وده جودة واطية للعرض الكبير.
    بنستبدل جزء الحجم بحجم أعلى (w1200-h900) من غير ما نلمس باقي الرابط،
    بالظبط زي فكرة upscale_image في hotels_service.py.
    """
    if not isinstance(url, str) or "googleusercontent.com" not in url:
        return url
    return re.sub(r"=w\d+-h\d+(-k-no)?", "=w1200-h900-k-no", url)


class BeachesDataError(Exception):
    """بترفع لما تحميل الداتا من GitHub يفشل أو الداتا متبقاش موجودة."""
    pass


@lru_cache(maxsize=1)
def _load_raw_dataframe() -> pd.DataFrame:
    """
    تحميل الـ CSV من GitHub مرة واحدة وتخزينه في الذاكرة.
    لإعادة التحميل يدوياً استخدم _load_raw_dataframe.cache_clear()
    """
    try:
        df = fetch_csv_from_github(GITHUB_PATH)
    except GithubDataError as e:
        raise BeachesDataError(str(e))
    df.columns = df.columns.str.strip()
    return df


def _dataframe_to_records(df: pd.DataFrame) -> list[dict]:
    df = df[df["photo_url"].notna() & (df["photo_url"].str.strip() != "")]

    records = []
    for _, row in df.iterrows():
        rating = float(row["rating"]) if pd.notna(row.get("rating")) else 0
        records.append({
            "id": str(row.get("id", "")).strip(),
            "name": str(row.get("name", "")).strip(),
            "government": str(row.get("government", "")).strip() if pd.notna(row.get("government")) else "",
            "rating": rating,
            "photo_url": upscale_photo_url(str(row.get("photo_url", "")).strip()),
            "maps_url": str(row.get("maps_url", "")).strip() if pd.notna(row.get("maps_url")) else "#",
        })
    return records


def get_governments() -> list[str]:
    df = _load_raw_dataframe()
    return ["All"] + sorted(df["government"].dropna().unique().tolist())


def get_beaches(government: str | None = None, search: str | None = None,
                min_rating: float = 0) -> list[dict]:
    """
    فلاتر البحث بتتنفذ في الـ backend بحيث الفرونت يبعت query params بس
    (?government=Red+Sea&search=dahab&min_rating=4.5), زي نفس فكرة get_hotels.
    """
    df = _load_raw_dataframe()
    records = _dataframe_to_records(df)

    def matches(r):
        if government and government != "All" and r["government"] != government:
            return False
        if search and search.lower() not in r["name"].lower() and search.lower() not in r["government"].lower():
            return False
        if r["rating"] < min_rating:
            return False
        return True

    return sorted([r for r in records if matches(r)], key=lambda r: r["rating"], reverse=True)
