import os
import re
import html
import hashlib
from datetime import datetime, timezone

import feedparser
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # e.g. "@yourchannelusername" or numeric chat id

# RSS feeds (you can add/remove sources freely)
FEEDS = {
    "Singapore": [
        # Channel NewsAsia
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
        # The Business Times (SG)
        "https://www.businesstimes.com.sg/rss.xml",
        # Yahoo SG
        "https://sg.news.yahoo.com/rss/",
    ],
    "US": [
        # Reuters US (domestic)
        "https://feeds.reuters.com/reuters/domesticNews",
        # AP News
        "https://apnews.com/rss",
        # NPR
        "https://feeds.npr.org/1001/rss.xml",
    ],
    "Global": [
        # Reuters World
        "https://feeds.reuters.com/reuters/worldNews",
        # BBC World
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        # Al Jazeera
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
}

MAX_ITEMS_PER_REGION = 20
DISABLE_LINK_PREVIEW = True

# Optional: store a tiny "seen" cache between runs via GitHub Actions cache
SEEN_FILE = "seen_hashes.txt"
MAX_SEEN = 2000


def clean_text(s: str) -> str:
    s = s or ""
    s = re.sub(r"<[^>]+>", " ", s)      # strip HTML tags
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def pick_description(entry) -> str:
    # Many feeds have summary; some have description/content
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


def fetch_region_items(feed_urls: list[str], limit: int, seen: set[str]) -> list[dict]:
    items: list[dict] = []

    for url in feed_urls:
        d = feedparser.parse(url)
        for e in getattr(d, "entries", []):
            title = clean_text(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            if not title or not link:
                continue

            desc = pick_description(e)
            if len(desc) > 220:
                desc = desc[:217].rstrip() + "..."

            key = stable_hash(title, link)
            if key in seen:
                continue

            items.append({"title": title, "desc": desc, "link": link, "key": key})

            if len(items) >= limit:
                return items

    return items[:limit]


def chunk_message(text: str, max_len: int = 3800) -> list[str]:
    # Telegram hard limit ~4096 chars; keep margin
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": DISABLE_LINK_PREVIEW,
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def format_region(region: str, items: list[dict]) -> str:
    lines = [f"<b>{region} — Top {len(items)}</b>"]
    for i, it in enumerate(items, 1):
        # Escape title/desc for HTML safety, but keep link as-is
        title = html.escape(it["title"])
        desc = html.escape(it["desc"]) if it["desc"] else ""
        link = it["link"]

        lines.append(f"{i}. <a href=\"{link}\">{title}</a>")
        if desc:
            lines.append(f"   <i>{desc}</i>")
    return "\n".join(lines)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Missing BOT_TOKEN or CHAT_ID env vars.")

    seen = load_seen()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    header = f"<b>Daily News Digest</b>\n<i>{today} (UTC)</i>\n"
    for region, urls in FEEDS.items():
        items = fetch_region_items(urls, MAX_ITEMS_PER_REGION, seen)
        for it in items:
            seen.add(it["key"])

        msg = header + "\n" + format_region(region, items)
        for part in chunk_message(msg):
            telegram_send(part)

    save_seen(seen)


if __name__ == "__main__":
    main()
