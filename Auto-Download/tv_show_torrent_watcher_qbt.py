#!/usr/bin/env python3
"""TV Show Torrent Watcher using qBittorrent Web UI API

Requirements:
    pip install feedparser pyyaml requests

qBittorrent setup:
 1. Install qBittorrent (https://www.qbittorrent.org/download.php)
 2. Enable Web UI (Tools -> Options -> Web UI), choose port (e.g., 8080)
 3. Set username/password and (optionally) local host privileges

Config: Auto-Download/config_qbt.yaml

shows:
  - name: "Rick and Morty"
  - name: "The Expanse"

rss_feeds:
  - "https://torrentfeed.example.com/rss"

qbt:
  host: "127.0.0.1"
  port: 8080
  username: "admin"
  password: "adminadmin"
  category: "TV"

save_path: "./downloads"
history_path: "./downloaded_history_qbt.json"
interval: 1800
run_once: false
no_download: false

IMPORTANT:
- Download only legally authorized content.
- Perform seed/share etiquette and respect copyright.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Dict, List

import feedparser
import requests
import yaml

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)


def parse_args():
    p = argparse.ArgumentParser(description="Watch torrent RSS for new 1080p episodes and add to qBittorrent")
    p.add_argument("--config", default="Auto-Download/config_qbt.yaml", help="Path to YAML config")
    p.add_argument("--run-once", action="store_true", help="Fetch feed once and exit")
    p.add_argument("--no-download", action="store_true", help="Only print matches without adding torrents")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("shows", [])
    cfg.setdefault("rss_feeds", [])
    cfg.setdefault("save_path", "./downloads")
    cfg.setdefault("history_path", "./downloaded_history_qbt.json")
    cfg.setdefault("interval", 1800)
    return cfg


def load_history(path: str) -> Dict[str, bool]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_history(path: str, history: Dict[str, bool]):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def title_matches_show(title: str, show_names: List[str]) -> List[str]:
    title_lower = title.lower()
    matches = []
    for show in show_names:
        if show.lower() in title_lower:
            matches.append(show)
    return matches


def is_1080p(title: str) -> bool:
    return "1080p" in title.lower()


def find_magnet(entry) -> str:
    href = entry.get("link", "")
    if isinstance(href, str) and href.startswith("magnet:"):
        return href

    for link in entry.get("links", []):
        if isinstance(link, dict):
            l = link.get("href", "")
            if isinstance(l, str) and l.startswith("magnet:"):
                return l
    return ""


def filter_entries(entries, show_names):
    result = []
    for entry in entries:
        title = entry.get("title", "")
        if not title:
            continue
        if not is_1080p(title):
            continue
        matched = title_matches_show(title, show_names)
        if not matched:
            continue

        magnet = find_magnet(entry)
        if not magnet:
            logging.debug("Skipping entry without magnet: %s", title)
            continue

        result.append({"title": title, "magnet": magnet, "shows": matched})
    return result


def qbt_login(base_url: str, username: str, password: str, session: requests.Session):
    login_url = f"{base_url}/api/v2/auth/login"
    response = session.post(login_url, data={"username": username, "password": password}, timeout=15)
    if response.status_code != 200 or response.text != "Ok.":
        raise RuntimeError(f"qBittorrent login failed: {response.status_code} {response.text}")


def qbt_add_magnet(base_url: str, magnet_uri: str, save_path: str, category: str, session: requests.Session):
    add_url = f"{base_url}/api/v2/torrents/add"
    data = {
        "urls": magnet_uri,
        "savepath": save_path,
        "category": category,
        "autoTMM": "false",
    }
    response = session.post(add_url, data=data, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to add torrent: {response.status_code} {response.text}")


def process_feed(feed_url: str, cfg: dict, show_names: List[str], history: Dict[str, bool], session: requests.Session):
    logging.info("Checking feed: %s", feed_url)
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        logging.warning("RSS feed error: %s", getattr(feed, "bozo_exception", "unknown"))

    entries = filter_entries(feed.entries, show_names)
    logging.info("Found %d 1080p candidates", len(entries))

    new_items = 0
    for item in entries:
        key = normalize_title(item["title"])
        if history.get(key):
            continue

        logging.info("New episode match: %s", item["title"])
        if not cfg.get("no_download"):
            qbt_add_magnet(
                cfg["qbt_base_url"], item["magnet"], cfg["save_path"], cfg.get("qbt_category", ""), session
            )
            logging.info("Added to qBittorrent: %s", item["title"])
        else:
            logging.info("No-download mode: would add %s", item["title"])

        history[key] = True
        new_items += 1

    return new_items


def main():
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = load_config(args.config)
    cfg["run_once"] = cfg.get("run_once", False) or args.run_once
    cfg["no_download"] = cfg.get("no_download", False) or args.no_download

    show_names = [show["name"] if isinstance(show, dict) else str(show) for show in cfg.get("shows", [])]
    if not show_names:
        logging.error("No shows configured in config file")
        sys.exit(1)

    if not cfg.get("rss_feeds"):
        logging.error("No rss_feeds configured in config file")
        sys.exit(1)

    qbt_cfg = cfg.get("qbt", {})
    host = qbt_cfg.get("host", "127.0.0.1")
    port = qbt_cfg.get("port", 8080)
    username = qbt_cfg.get("username", "admin")
    password = qbt_cfg.get("password", "adminadmin")
    category = qbt_cfg.get("category", "")
    base_url = f"http://{host}:{port}"

    cfg.update({
        "qbt_base_url": base_url,
        "qbt_category": category,
    })

    history = load_history(cfg["history_path"])

    session = requests.Session()
    qbt_login(base_url, username, password, session)
    logging.info("Logged into qBittorrent Web UI at %s", base_url)

    while True:
        total_new = 0
        for feed_url in cfg["rss_feeds"]:
            total_new += process_feed(feed_url, cfg, show_names, history, session)

        save_history(cfg["history_path"], history)

        if total_new > 0:
            logging.info("Finished checking feeds. %d new items added." , total_new)
        else:
            logging.info("Finished checking feeds. No new items.")

        if cfg["run_once"]:
            break

        logging.info("Sleeping %d seconds before next check...", cfg["interval"])
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
