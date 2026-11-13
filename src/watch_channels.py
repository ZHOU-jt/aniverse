"""Reviewed official destinations with deterministic platform-search fallbacks."""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit

CATALOGUE_PATH = Path(__file__).resolve().parent / "data/watch_channels.json"
MAX_REVIEW_AGE_DAYS = 180
logger = logging.getLogger("aniverse.watch")
PLATFORMS = (
    {"id": "bilibili", "name": "哔哩哔哩", "mark": "B站", "search": "https://search.bilibili.com/all?keyword="},
    {"id": "tencent", "name": "腾讯视频", "mark": "腾", "search": "https://v.qq.com/x/search/?q="},
    {"id": "iqiyi", "name": "爱奇艺", "mark": "爱", "search": "https://www.iqiyi.com/so/q_"},
    {"id": "youku", "name": "优酷", "mark": "优", "search": "https://so.youku.com/search_video/q_"},
)
DESTINATIONS = {
    "bilibili": {"www.bilibili.com": ((r"/bangumi/play/(?:ss|ep)[0-9]+/?", "play"), (r"/bangumi/media/md[0-9]+/?", "detail"))},
    "tencent": {"v.qq.com": ((r"/x/cover/[A-Za-z0-9]+(?:/[A-Za-z0-9]+)?\.html", "play"), (r"/detail/[A-Za-z0-9]/[A-Za-z0-9]+\.html", "detail"))},
    "iqiyi": {"www.iqiyi.com": ((r"/v_[A-Za-z0-9]+\.html", "play"), (r"/a_[A-Za-z0-9]+\.html", "detail"))},
    "youku": {"v.youku.com": ((r"/v_show/id_[A-Za-z0-9=]+\.html", "play"),), "list.youku.com": ((r"/show/id_z[A-Za-z0-9]+\.html", "detail"),)},
}


@lru_cache(maxsize=1)
def load_catalogue():
    try:
        data = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), dict):
            raise ValueError("Invalid watch catalogue")
        return data["entries"]
    except (OSError, ValueError):
        logger.warning("watch_catalogue_unavailable; using platform search links")
        return {}


def reviewed_destination(entry, today):
    if not isinstance(entry, dict) or entry.get("enabled", True) is not True:
        return None
    platform = entry.get("platform")
    if not isinstance(platform, str) or platform not in DESTINATIONS or not isinstance(entry.get("verified_title"), str) or not entry["verified_title"].strip():
        return None
    try:
        checked = date.fromisoformat(entry.get("verified_on", ""))
        if not 0 <= (today - checked).days <= MAX_REVIEW_AGE_DAYS:
            return None
        url = entry.get("url", "")
        if not isinstance(url, str) or not url or any(ch.isspace() or ord(ch) < 32 for ch in url) or "\\" in url:
            return None
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment:
            return None
        for pattern, kind in DESTINATIONS[platform].get(parsed.hostname, ()):
            if re.fullmatch(pattern, parsed.path):
                return {"url": url, "kind": kind, "verified_on": checked.isoformat()}
    except (TypeError, ValueError):
        return None
    return None


def build_watch_channels(anime, *, catalogue=None, today=None):
    def value(key, default=None):
        try:
            return anime[key] or default
        except (KeyError, IndexError, TypeError):
            return default
    title = next((str(value(key)).strip() for key in ("title_zh", "title", "title_japanese") if value(key) and str(value(key)).strip()), "番剧")
    mal_id = str(value("mal_id", ""))
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    catalogue = load_catalogue() if catalogue is None else catalogue
    candidates = catalogue.get(mal_id, []) if isinstance(catalogue, dict) and mal_id.isdigit() else []
    candidates = candidates if isinstance(candidates, list) else []
    reviewed = {}
    for entry in candidates:
        destination = reviewed_destination(entry, today)
        if destination:
            previous = reviewed.get(entry["platform"])
            # Prefer a verified player page over an album/detail page.
            if previous is None or (previous["kind"] == "detail" and destination["kind"] == "play"):
                reviewed[entry["platform"]] = destination
    channels = []
    for platform in PLATFORMS:
        direct = reviewed.get(platform["id"])
        channels.append({
            **platform, "title": title, "search_url": platform["search"] + quote(title, safe=""),
            "direct_url": direct["url"] if direct else None,
            "kind": direct["kind"] if direct else "search",
            "verified_on": direct["verified_on"] if direct else None,
            "label": ("官方播放页" if direct["kind"] == "play" else "官方番剧页") if direct else "平台搜索",
        })
    return sorted(channels, key=lambda channel: channel["direct_url"] is None)
