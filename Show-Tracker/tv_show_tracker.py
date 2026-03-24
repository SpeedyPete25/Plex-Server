import csv
import time
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://api.tvmaze.com"
INPUT_CSV = "shows.csv"
OUTPUT_CSV = "shows_updated.csv"
REQUEST_DELAY_SECONDS = 0.25

def safe_get(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Fetch JSON from TVmaze and return None on failure."""
    try:
        response = requests.get(url, params=params, timeout=20)
        if response.status_code == 200:
            return response.json()
        return None
    except requests.RequestException:
        return None

def _normalize_name_with_year(show_name: str) -> (str, Optional[int]):
    """Extract base name and optional year string from values like 'Doctor Who (2023)'."""
    import re

    candidate = show_name.strip()
    match = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", candidate)
    if match:
        return match.group(1).strip(), int(match.group(2))
    return candidate, None

def search_show(show_name: str) -> Optional[Dict[str, Any]]:
    """
    Search for a show by name.
    Uses TVmaze search endpoint and returns the first strong match if available.
    """
    data = safe_get(f"{BASE_URL}/search/shows", params={"q": show_name})
    if not data:
        return None

    normalized_name, target_year = _normalize_name_with_year(show_name)

    shows = [item.get("show") for item in data if isinstance(item.get("show"), dict)]

    # Prefer exact title + year match where possible
    if target_year is not None:
        year_matches = [
            show
            for show in shows
            if show.get("name", "").strip().lower() == normalized_name.lower()
            and show.get("premiered", "").startswith(str(target_year))
        ]
        if year_matches:
            return year_matches[0]

    # Prefer exact title match where possible (with or without year suffix)
    exact_matches = [
        show
        for show in shows
        if show.get("name", "").strip().lower() == normalized_name.lower()
        or show.get("name", "").strip().lower() == show_name.strip().lower()
    ]
    if exact_matches:
        return exact_matches[0]

    # Otherwise use the first result
    return shows[0] if shows else None

def get_show_details(show_id: int) -> Optional[Dict[str, Any]]:
    """
    Get show details including embedded next and previous episode.
    """
    return safe_get(f"{BASE_URL}/shows/{show_id}", params={"embed[]": ["nextepisode", "previousepisode"]})

def get_show_seasons(show_id: int) -> List[Dict[str, Any]]:
    """
    Get all seasons for a show.
    """
    data = safe_get(f"{BASE_URL}/shows/{show_id}/seasons")
    return data if isinstance(data, list) else []

def find_next_season_airdate(
    seasons: List[Dict[str, Any]],
    previous_season_number: Optional[int],
    next_episode: Optional[Dict[str, Any]]
) -> Optional[str]:
    """
    Determine the next season airdate if we can infer it.

    Logic:
    - If next episode exists and its season number is greater than the previous episode's season,
      then the next episode is likely the start of a new season, so use its airdate.
    - Otherwise, if season metadata contains a future season with a premiereDate, use that.
    """
    if next_episode:
        next_season_num = next_episode.get("season")
        next_episode_num = next_episode.get("number")
        next_airdate = next_episode.get("airdate")

        if next_season_num is not None and next_episode_num == 1:
            return next_airdate

        if (
            previous_season_number is not None
            and next_season_num is not None
            and next_season_num > previous_season_number
        ):
            return next_airdate

    # Fallback: look for future season metadata
    if previous_season_number is not None:
        future_seasons = [
            s for s in seasons
            if s.get("number") is not None and s.get("number") > previous_season_number
        ]
        future_seasons.sort(key=lambda s: s.get("number", 0))
        for season in future_seasons:
            premiere = season.get("premiereDate")
            if premiere:
                return premiere

    return None

def normalize_status(
    api_status: Optional[str],
    next_episode: Optional[Dict[str, Any]]
) -> str:
    """
    Convert TVmaze-style status into the categories:
    cancelled, renewed, running, unconfirmed
    """
    status = (api_status or "").strip().lower()

    if status == "ended":
        return "cancelled"

    if status == "to be determined":
        return "unconfirmed"

    if status == "running":
        # If a next episode exists, treat it as renewed/upcoming.
        if next_episode:
            return "renewed"
        return "running"

    # Unknown statuses fall back to unconfirmed
    return "unconfirmed"

def process_show(show_name: str) -> Dict[str, str]:
    """
    Build one output row for a show.
    """
    row = {
        "show_name": show_name,
        "tvmaze_status": "",
        "next_known_airdate": "",
    }

    show = search_show(show_name)
    time.sleep(REQUEST_DELAY_SECONDS)

    if not show:
        return row

    show_id = show.get("id")
    if not show_id:
        row["notes"] = "Show found but missing ID"
        return row

    details = get_show_details(show_id)
    time.sleep(REQUEST_DELAY_SECONDS)

    if not details:
        row["notes"] = "Could not retrieve show details"
        return row

    seasons = get_show_seasons(show_id)
    time.sleep(REQUEST_DELAY_SECONDS)

    next_episode = details.get("_embedded", {}).get("nextepisode")
    previous_episode = details.get("_embedded", {}).get("previousepisode")

    previous_season_number = previous_episode.get("season") if previous_episode else None
    next_known_airdate = next_episode.get("airdate") if next_episode else None
    next_season_airdate = find_next_season_airdate(seasons, previous_season_number, next_episode)

    row["tvmaze_status"] = details.get("status", "")
    row["next_known_airdate"] = next_known_airdate or ""

    return row

def read_input_csv(filename: str) -> List[str]:
    """
    Read show names from a CSV with a 'show_name' column.
    """
    show_names: List[str] = []
    with open(filename, "r", newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for record in reader:
            name = (record.get("show_name") or "").strip()
            if name:
                show_names.append(name)
    return show_names

def write_output_csv(filename: str, rows: List[Dict[str, str]]) -> None:
    """
    Write updated show data to CSV.
    """
    fieldnames = [
        "show_name",
        "tvmaze_status",
        "next_known_airdate",
    ]

    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main() -> None:
    show_names = read_input_csv(INPUT_CSV)
    results: List[Dict[str, str]] = []

    for show_name in show_names:
        print(f"Updating: {show_name}")
        result = process_show(show_name)
        results.append(result)

    write_output_csv(OUTPUT_CSV, results)
    print(f"\nDone. Updated file written to: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()