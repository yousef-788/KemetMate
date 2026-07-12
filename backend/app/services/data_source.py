"""
GitHub Data Source
-------------------
Fetches the project's silver-layer reference CSVs (beaches, hotels,
monuments, museums, periods, ancient sites, tickets, restaurants) straight
from the public GitHub repo instead of Azure Blob Storage. No Azure
credentials needed for any of this — just outbound network access to
raw.githubusercontent.com, which every standard host (Railway included)
allows by default.

Every service that used to call Azure's BlobServiceClient now calls
fetch_csv_from_github() instead, passing the file's path relative to the
repo root (e.g. "Data/silver/kemet_beaches_data.csv"). If the repo's Data/
folder ever moves or gets renamed, only the constants below need to change.
"""
import io

import pandas as pd
import requests

GITHUB_OWNER = "elsayedashraf05"
GITHUB_REPO = "kemet-assistant"
GITHUB_BRANCH = "main"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

REQUEST_TIMEOUT_SECONDS = 15


class GithubDataError(Exception):
    """Raised when a CSV can't be fetched or parsed from the GitHub repo."""


def fetch_csv_from_github(relative_path: str) -> pd.DataFrame:
    """relative_path is repo-root-relative, e.g. 'Data/silver/kemet_beaches_data.csv'
    (no leading slash). Raises GithubDataError on any failure — network,
    404, or a file that isn't valid CSV — so callers can turn that into
    whatever domain-specific error class they already raise (BeachesDataError,
    HotelsDataError, etc.) without duplicating the try/except everywhere."""
    url = f"{GITHUB_RAW_BASE}/{relative_path}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception as e:
        raise GithubDataError(f"Could not fetch '{relative_path}' from GitHub ({url}): {e}")
    try:
        return pd.read_csv(io.BytesIO(resp.content))
    except Exception as e:
        raise GithubDataError(f"'{relative_path}' was fetched but isn't valid CSV: {e}")
