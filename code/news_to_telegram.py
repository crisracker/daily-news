#!/usr/bin/env python3
"""
CT Daily News Digest (RSS -> Telegram)

- Pulls top 20 items for: Singapore, US, Global from RSS feeds
- Deduplicates using a small "seen" cache (persistable via GitHub Actions cache)
- Posts a neat, compact, user-friendly Telegram message (titles clickable)
"""

import os
import re
import html
import hashlib
from datetime import datetime, timezone
from typing import Dict, List

import feedparser
import requests

# ====== REQUIRED ENV VARS (set in GitHub Secrets / local env) ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # e.g. "@yourchannelusername" or numeric id

# Optional: set DRY_RUN=1 to print message to logs instead of posting
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

# ====== CONFIG ======
MAX_ITEMS_PER_REGION = 20
DISABLE_LINK_PREVIEW = True

# Keep a small cache of already-posted items to reduce repeats across days.
# (GitHub Actions can cache this file between runs.)
SEEN_FILE = "seen_hashes.txt"
MAX_SEEN = 2000

# RSS feeds (edit as you like)
FEEDS: Dict[str, List[str]] = {
    "Singapore": [
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
        "https://www.businesstimes.com.sg/rss.xml",
        "https://sg.news.yahoo.com/rss/",
    ],
    "US": [
        "https://feeds.reuters.com/reuters/domesticNews",
        "https://apnews.com/rss",
        "https://feeds.npr.org/1001/rss.xml",
    ],
    "Global": [
        "https://feeds.reuters.com/reuters/worldNews",
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
}

DIVIDER = "\n\n──────────\n\n"


# ====== HELPERS ======
def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def clean_text(text: str) -> str:
    text = strip_tags(text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pick_description(entry) -> str:
    # RSS entries may have summary/description/content; pick best available.
    if getattr(entry, "summary", None):
        return clean_text(entry.summary)
    if getattr(entry, "description", None):
        return clean_text(entry.description)
    return ""


def stable_hash(title: str, link: str) -> str:
    h = hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()
    return h[:24]


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(seen: set[str]) -> None:
    # Keep file bounded
    items = list(seen)[-MAX_SEEN:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(items) + ("\n" if items else ""))


def fetch_region_items(feed_urls: List[str], limit: int, seen: set[str]) -> List[dict]:
    items: List[dict] = []

    for url in feed_urls:
        d = feedparser.parse(url)
        for e in getattr(d, "entries", []):
            title = clean_text(getattr(e, "title", ""))
            link = getattr(e, "link", "")

            if not title or not link:
                continue

            desc = pick_description(e)

            key = stable_hash(title, link)
            if key in seen:
                continue

            items.append({"title": title, "desc": desc, "link": link, "key": key})

            if len(items) >= limit:
                return items

    return items[:limit]


def chunk_message(text: str, max_len: int = 3800) -> List[str]:
    # Telegram limit is ~4096 chars; keep a safety margin.
    parts = []
    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def telegram_send(text: str) -> None:
    if DRY_RUN:
        print("\n----- DRY RUN MESSAGE START -----\n")
        print(text)
        print("\n----- DRY RUN MESSAGE END -----\n")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": DISABLE_LINK_PREVIEW,
    }
    r = requests.post(url, json=payload, timeout=30)
    # Helpful error if secrets/chat id wrong
    if r.status_code != 200:
        raise RuntimeError(f"Telegram API error {r.status_code}: {r.text}")
    r.raise_for_status()


def format_region(region: str, items: List[dict]) -> str:
    lines = [f"🗞️ <b>{html.escape(region)}</b>  <i>(Top {len(items)})</i>"]

    for i, it in enumerate(items, 1):
        title = html.escape(it["title"])
        link = it["link"]
        desc = clean_text(it.get("desc", ""))

        # Keep description to a single neat line (optional)
        if desc:
            if len(desc) > 140:
                desc = desc[:137].rstrip() + "..."
            desc = html.escape(desc)
            lines.append(f'{i}. <a href="{link}">{title}</a>\n   {desc}')
        else:
            lines.append(f'{i}. <a href="{link}">{title}</a>')

    return "\n".join(lines)


# ====== MAIN ======
def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Missing BOT_TOKEN or CHAT_ID environment variables.")

    seen = load_seen()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build ONE clean message containing all regions (with dividers)
    blocks: List[str] = []
    for region, urls in FEEDS.items():
        items = fetch_region_items(urls, MAX_ITEMS_PER_REGION, seen)
        for it in items:
            seen.add(it["key"])
        blocks.append(format_region(region, items))

    msg = f"<b>CT Daily News Digest</b>\n<i>{today} (UTC)</i>\n\n" + DIVIDER.join(blocks)

    # Send (chunk if needed)
    for part in chunk_message(msg):
        telegram_send(part)

    save_seen(seen)


if __name__ == "__main__":
    main()
