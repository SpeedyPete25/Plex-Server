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


def search_show(show_name: str) -> Optional[Dict[str, Any]]:
    """
    Search for a show by name.
    Uses TVmaze search endpoint and returns the first strong match if available.
    """
    data = safe_get(f"{BASE_URL}/search/shows", params={"q": show_name})
    if not data:
        return None

    # Prefer exact title match where possible
    exact_matches = [
        item["show"]
        for item in data
        if item.get("show", {}).get("name", "").strip().lower() == show_name.strip().lower()
    ]
    if exact_matches:
        return exact_matches[0]

    # Otherwise use the first result
    return data[0].get("show")


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
        "found_title": "",
        "tvmaze_status": "",
        "status_bucket": "",
        "next_known_airdate": "",
        "next_season_airdate": "",
        "official_site": "",
        "network": "",
        "notes": "",
    }

    show = search_show(show_name)
    time.sleep(REQUEST_DELAY_SECONDS)

    if not show:
        row["notes"] = "Show not found"
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

    network_name = ""
    if details.get("network"):
        network_name = details["network"].get("name", "")
    elif details.get("webChannel"):
        network_name = details["webChannel"].get("name", "")

    row["found_title"] = details.get("name", "")
    row["tvmaze_status"] = details.get("status", "")
    row["status_bucket"] = normalize_status(details.get("status"), next_episode)
    row["next_known_airdate"] = next_known_airdate or ""
    row["next_season_airdate"] = next_season_airdate or ""
    row["official_site"] = details.get("officialSite", "") or ""
    row["network"] = network_name

    if row["status_bucket"] == "Running" and not row["next_known_airdate"]:
        row["notes"] = "Show is marked running, but no next episode is currently listed"
    elif row["status_bucket"] == "Renewed" and not row["next_season_airdate"]:
        row["notes"] = "Upcoming episode exists, but next season premiere is not clearly identifiable"
    elif row["status_bucket"] == "To Be Determined":
        row["notes"] = "Future season/episode not yet confirmed"
    elif row["status_bucket"] == "Ended":
        row["notes"] = "Show appears ended"

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
        "found_title",
        "tvmaze_status",
        "status_bucket",
        "next_known_airdate",
        "next_season_airdate",
        "official_site",
        "network",
        "notes",
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