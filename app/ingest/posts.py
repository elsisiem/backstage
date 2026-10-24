"""Normalize discovered social posts into Backstage's Post shape.

Discovery sources write raw records to data/cache/*; this module cleans text, resolves author handles,
and recovers exact creation timestamps from platform post IDs:
  - Bluesky record keys are TIDs: 13 chars of sortable base32 encoding microseconds since the epoch.
  - LinkedIn activity IDs carry the millisecond timestamp in their top 41 bits.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import CACHE

B32 = "234567abcdefghijklmnopqrstuvwxyz"


def bsky_tid_to_dt(rkey: str) -> datetime | None:
    if not rkey or len(rkey) != 13 or any(c not in B32 for c in rkey):
        return None
    v = 0
    for c in rkey:
        v = v * 32 + B32.index(c)
    micros = v >> 10
    try:
        return datetime.fromtimestamp(micros / 1e6, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def linkedin_activity_to_dt(activity_id: str) -> datetime | None:
    if not activity_id or not activity_id.isdigit():
        return None
    n = int(activity_id)
    ms = n >> (n.bit_length() - 41)
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if 2015 <= dt.year <= 2035 else None


def post_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


_BSKY_URL = re.compile(r"bsky\.app/profile/([^/]+)/post/([a-z0-9]+)")
_LI_URL = re.compile(r"linkedin\.com/posts/([^_/?]+)[_-].*?activity-(\d+)")
_LI_URL2 = re.compile(r"linkedin\.com/(?:posts|feed/update)/.*?(\d{19})")
_X_URL = re.compile(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)")


def clean_text(platform: str, text: str, title: str | None) -> str:
    t = (text or "").replace("\r", "")
    if platform == "bsky":
        # Exa renders "Post by @handle — Bluesky  Post  Name's avatar  Follow  <body>"
        m = re.search(r"\bFollow\b\s*(.*)", t, re.S)
        if m:
            t = m.group(1)
        t = re.sub(r"^Post by @\S+ — Bluesky\s*Post\s*", "", t)
    elif platform == "linkedin":
        t = re.sub(r"^#\s.*?\n", "", t, count=1)  # "# Title · date" heading line
        t = re.sub(r"^\s*\S.*?· \d{4}-\d{2}-\d{2}T[^\n]*\n", "", t, count=1)
        if title and t.strip().startswith(title.strip()[:40]):
            t = t.strip()[len(title.strip()):]
    t = re.sub(r"\s+\n", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def is_post_url(url: str) -> bool:
    """Discovery also returns profile, company and event pages; only keep actual posts."""
    u = url.lower()
    if "bsky.app" in u:
        return "/post/" in u
    if "linkedin.com" in u:
        return "/posts/" in u or "/feed/update/" in u or "activity-" in u
    if "x.com" in u or "twitter.com" in u:
        return "/status/" in u
    return False


def normalize_exa(record: dict, window: tuple[str, str] | None = None) -> dict | None:
    url = record.get("url") or ""
    if not is_post_url(url):
        return None
    platform = record.get("_platform") or ("bsky" if "bsky.app" in url else "linkedin" if "linkedin.com" in url else "x" if "x.com" in url or "twitter.com" in url else "web")
    handle, dt, native_id = None, None, None
    if platform == "bsky":
        m = _BSKY_URL.search(url)
        if m:
            handle, native_id = m.group(1), m.group(2)
            dt = bsky_tid_to_dt(native_id)
    elif platform == "linkedin":
        m = _LI_URL.search(url)
        if m:
            handle, native_id = m.group(1), m.group(2)
        else:
            m2 = _LI_URL2.search(url)
            native_id = m2.group(1) if m2 else None
        dt = linkedin_activity_to_dt(native_id) if native_id else None
    elif platform == "x":
        m = _X_URL.search(url)
        if m:
            handle, native_id = m.group(1), m.group(2)
            # Twitter snowflake: ms since 2010-11-04T01:42:54.657Z in the top bits
            ms = (int(native_id) >> 22) + 1288834974657
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    pub = record.get("publishedDate") or ""
    exact = dt is not None
    if dt is None and pub:
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    text = clean_text(platform, record.get("text") or "", record.get("title"))
    if not text and not record.get("title"):
        return None
    day = dt.date().isoformat() if dt else (pub[:10] or None)
    if window and day and not (window[0] <= day <= window[1]):
        return None
    author_name = record.get("author") or None
    if platform == "bsky" and not author_name:
        m = re.search(r"Post by @\S+ — Bluesky\s*Post\s*(.+?)'s avatar", record.get("text") or "")
        if m:
            author_name = m.group(1).strip()
    if platform == "linkedin" and not author_name and handle:
        author_name = handle.replace("-", " ").title()
    return {
        "id": post_id(url),
        "url": url,
        "platform": platform,
        "native_id": native_id,
        "handle": handle,
        "author": author_name,
        "text": text[:1500],
        "title": (record.get("title") or "")[:200],
        "ts": dt.isoformat() if dt else None,
        "ts_exact": exact,
        "day": day,
        "hashtags": sorted({h.lower() for h in re.findall(r"#(\w+)", text)}),
        "links": sorted({u.rstrip(').,') for u in re.findall(r"https?://\S+", text)})[:8],
        "source": record.get("_source") or "exa",
        "query": record.get("_query"),
    }


def load_posts(conf_id: str, window: tuple[str, str] | None = None) -> list[dict]:
    """Load every cached discovery file for a conference and return deduplicated normalized posts.

    `window` is (first_day, last_day) inclusive, ISO dates; posts outside it are dropped.
    """
    out: dict[str, dict] = {}
    files = sorted((CACHE / "exa").glob(f"posts_{conf_id}*.json"))
    if (CACHE / "brightdata").exists():
        files += sorted((CACHE / "brightdata").glob(f"posts_{conf_id}*.json"))
    for path in files:
        for rec in json.loads(Path(path).read_text(encoding="utf-8")):
            if rec.get("_source", "exa") == "exa":
                p = normalize_exa(rec, window)
            else:  # already normalized (Bright Data pipeline writes Backstage's shape)
                p = rec if (not window or (rec.get("day") and window[0] <= rec["day"] <= window[1])) else None
            if p and p["id"] not in out:
                out[p["id"]] = p
    posts = list(out.values())
    posts.sort(key=lambda p: p["ts"] or "9")
    return posts


if __name__ == "__main__":
    import collections
    import sys

    conf = sys.argv[1] if len(sys.argv) > 1 else "universe25"
    posts = load_posts(conf, ("2025-10-26", "2025-10-31") if conf == "universe25" else None)
    c = collections.Counter((p["platform"], p["ts_exact"]) for p in posts)
    print(len(posts), "posts", dict(c))
    print(collections.Counter(p["day"] for p in posts))
    for p in posts[:3] + posts[-3:]:
        print("-", p["platform"], p["ts"], p["handle"], "|", p["text"][:120].replace("\n", " "))
