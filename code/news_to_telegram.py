#!/usr/bin/env python3
"""
Daily News Digest (RSS -> Telegram) - resilient for GitHub Actions

Fixes common CI failures where some RSS hosts close connections:
- Fetch RSS with requests (custom User-Agent, timeout, retries)
- Feed parsing uses feedparser on response content
- Skips failing feeds instead of crashing the whole run
"""

import os
import re
import html
import time
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

import feedparser
import requests

# ====== REQUIRED ENV VARS ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # e.g. "@yourchannelusername" or numeric id
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# ====== CONFIG ======
MAX_ITEMS_PER_REGION = 20
DISABLE_LINK_PREVIEW = True

SEEN_FILE = "seen_hashes.txt"
MAX_SEEN = 2000

# Use feeds that are generally stable; still may occasionally fail, but we won't crash.
FEEDS: Dict[str, List[str]] = {
    "Singapore": [
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
        "https://www.businesstimes.com.sg/rss.xml",
        "https://sg.news.yahoo.com/rss/",
    ],
    "US": [
        "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "https://feeds.npr.org/1001/rss.xml",
        "https://www.usnews.com/rss/news",
        "https://www.politico.com/rss/politics08.xml",
    ],
    "Global": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.france24.com/en/rss",
    ],
}

DIVIDER = "\n\n──────────\n\n"

# Pretend to be a normal browser (helps a lot)
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)

# Requests settings
TIMEOUT = 20
RETRIES = 2
BACKOFF_SECONDS = 2


# ====== TEXT HELPERS ======
def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def clean_text(text: str) -> str:
    text = strip_tags(text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pick_description(entry) -> str:
    if getattr(entry, "summary", None):
        return clean_text(entry.summary)
    if getattr(entry, "description", None):
        return clean_text(entry.description)
    return ""


def stable_hash(title: str, link: str) -> str:
    h = hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()
    return h[:24]


# ====== SEEN CACHE ======
def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(seen: set[str]) -> None:
    items = list(seen)[-MAX_SEEN:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(items) + ("\n" if items else ""))


# ====== FETCH + PARSE (RESILIENT) ======
def fetch_feed_content(url: str) -> Optional[bytes]:
    headers = {"User-Agent": UA, "Accept": "a
