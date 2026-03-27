#!/usr/bin/env python3
"""TV Show Torrent Watcher + Downloader

This script monitors a torrent RSS feed and automatically downloads new 1080p episodes for configured shows.

Requirements:
    pip install python-libtorrent feedparser

Usage:
    python tv_show_torrent_watcher.py --config config.yaml
    python tv_show_torrent_watcher.py --config config.yaml --run-once

Config example (YAML):

shows:
  - name: "Rick and Morty"
  - name: "The Expanse"

rss_feeds:
  - "https://torrentkingrss.example.com/rss"

save_path: "./downloads"
history_path: "./downloaded_history.json"
interval: 1800  # seconds
max_download_rate_kb: 0
max_upload_rate_kb: 0

IMPORTANT:
- Only download content you are legally entitled to access.
- Use with public domain / authorized sources.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional

try:
    import feedparser
except ImportError:
    print("ERROR: feedparser is required. Run: pip install feedparser")
    sys.exit(1)

try:
    import libtorrent as lt
except ImportError:
    print("ERROR: python-libtorrent is required. Run: pip install python-libtorrent")
    sys.exit(1)


logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

DEFAULT_CONFIG_PATH = "Auto-Download/config.yaml"


def parse_args():
    p = argparse.ArgumentParser(description="Watch torrent RSS for new TV episodes and auto-download 1080p")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to YAML config")
    p.add_argument("--run-once", action="store_true", help="Fetch feed once and exit")
    p.add_argument("--no-download", action="store_true", help="Only print matches without downloading")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


def load_config(path: str) -> Dict:
    try:
        import yaml
    except ImportError:
        print("ERROR: pyyaml is required. Run: pip install pyyaml")
        sys.exit(1)

    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg is None:
        cfg = {}

    cfg.setdefault("shows", [])
    cfg.setdefault("rss_feeds", [])
    cfg.setdefault("save_path", "./downloads")
    cfg.setdefault("history_path", "./downloaded_history.json")
    cfg.setdefault("interval", 1800)
    cfg.setdefault("max_download_rate_kb", 0)
    cfg.setdefault("max_upload_rate_kb", 0)

    return cfg


def load_history(history_path: str) -> Dict[str, bool]:
    if not os.path.exists(history_path):
        return {}
    with open(history_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history_path: str, history: Dict[str, bool]) -> None:
    os.makedirs(os.path.dirname(history_path) or ".", exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def title_matches_show(title: str, show_names: List[str]) -> Optional[str]:
    lower_title = title.lower()
    for show in show_names:
        if show.lower() in lower_title:
            return show
    return None


def filter_items(entries, show_names):
    filtered = []
    for entry in entries:
        title = entry.get("title", "")
        if "1080p" not in title.lower():
            continue

        matched = title_matches_show(title, show_names)
        if not matched:
            continue

        magnet = None
        if entry.get("links"):
            for link in entry.links:
                if link.get("rel") == "alternate" and link.get("type") == "application/x-bittorrent":
                    magnet = link.get("href")
                    break

        # fallback: maybe just link with magnet URI
        if magnet is None:
            for link in entry.links:
                href = link.get("href", "")
                if href.startswith("magnet:"):
                    magnet = href
                    break

        if magnet is None:
            # Some feeds provide it in entry.link immediately as magnet or torrent URL
            link = entry.get("link", "")
            if isinstance(link, str) and link.startswith("magnet:"):
                magnet = link

        if magnet is None:
            logging.debug("Skipping due to no magnet/torrent URL: %s", title)
            continue

        filtered.append({"title": title, "magnet": magnet, "show": matched})
    return filtered


def add_torrent(magnet_uri: str, save_path: str, max_dl: float, max_ul: float):
    session = lt.session()
    session.listen_on(6881, 6891)

    session.add_dht_router("router.bittorrent.com", 6881)
    session.add_dht_router("router.utorrent.com", 6881)
    session.add_dht_router("dht.transmissionbt.com", 6881)
    session.start_dht()

    params = {
        "save_path": os.path.abspath(save_path),
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        "paused": False,
        "auto_managed": True,
        "duplicate_is_error": True,
    }

    handle = lt.add_magnet_uri(session, magnet_uri, params)

    if max_dl > 0:
        handle.set_download_limit(int(max_dl * 1024))
    if max_ul > 0:
        handle.set_upload_limit(int(max_ul * 1024))

    logging.info("Started download for magnet: %s", magnet_uri)

    # Wait for metadata then detach. This script intentionally does not stay alive until full completion by default.
    timeout = 120
    start = time.time()
    while not handle.has_metadata() and time.time() - start < timeout:
        time.sleep(1)

    if not handle.has_metadata():
        logging.warning("Warning: metadata not available within %ds for %s", timeout, magnet_uri)
    else:
        logging.info("Metadata acquired for %s", magnet_uri)

    return handle


def process_feed(feed_url, show_names, history, cfg, no_download=False):
    logging.info("Checking feed: %s", feed_url)
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        logging.warning("Malformed RSS feed (bozo): %s", feed.bozo_exception)

    matches = filter_items(feed.entries, show_names)
    logging.info("Found %d candidate 1080p episodes", len(matches))

    for item in matches:
        key = normalize_title(item["title"])
        if key in history:
            logging.debug("Already processed: %s", item["title"])
            continue

        logging.info("New match: [%s] %s", item["show"], item["title"])

        if not no_download:
            os.makedirs(cfg["save_path"], exist_ok=True)
            handle = add_torrent(
                item["magnet"], cfg["save_path"], cfg["max_download_rate_kb"], cfg["max_upload_rate_kb"]
            )
            # for now we don't keep handle alive; cyt seeds in the background while process is running
            # In production, a flow to keep runs and enforce completion is better.

        history[key] = True

    return history


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = load_config(args.config)
    show_names = [show["name"] if isinstance(show, dict) else str(show) for show in cfg["shows"]]

    if not show_names:
        logging.error("No shows configured in config file")
        sys.exit(1)
    if not cfg["rss_feeds"]:
        logging.error("No rss_feeds configured in config file")
        sys.exit(1)

    history = load_history(cfg["history_path"])

    while True:
        for feed in cfg["rss_feeds"]:
            history = process_feed(feed, show_names, history, cfg, no_download=args.no_download)

        save_history(cfg["history_path"], history)

        if args.run_once:
            break

        logging.info("Sleeping for %d seconds before next check...", cfg["interval"])
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
