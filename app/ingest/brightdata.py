"""Bright Data access: the data a plain script cannot reliably reach.

Three uses, each optional and cached:
  SERP API       discover X posts about the conference (Google: site:x.com "<hashtag>" after:/before:)
  Web Scraper    structured X post records (text, author, exact timestamp, likes) from the discovered URLs
  Web Unlocker   Bluesky's public AppView, which 403s from this network, and any blocked page

All calls go through https://api.brightdata.com/request with a zone; see https://docs.brightdata.com.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse

import httpx

from app import config
from app.ingest.posts import normalize_exa

BD_DIR = config.CACHE / "brightdata"
BD_DIR.mkdir(parents=True, exist_ok=True)
API = "https://api.brightdata.com/request"


def enabled() -> bool:
    return bool(config.BRIGHTDATA_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.BRIGHTDATA_API_KEY}", "Content-Type": "application/json"}


def _cached(key: str):
    p = BD_DIR / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _store(key: str, value) -> None:
    p = BD_DIR / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
    p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def unlock(url: str, fmt: str = "raw", zone: str | None = None, ttl_ok: bool = True) -> str | None:
    """Fetch a URL through Web Unlocker. Returns the body as text (raw) or markdown."""
    if not enabled():
        return None
    key = f"unlock:{fmt}:{url}"
    if ttl_ok and (c := _cached(key)) is not None:
        return c
    body = {"zone": zone or config.BRIGHTDATA_UNLOCKER_ZONE, "url": url, "format": "raw"}
    if fmt == "markdown":
        body["data_format"] = "markdown"
    try:
        r = httpx.post(API, headers=_headers(), json=body, timeout=90)
        if r.status_code != 200:
            return None
        _store(key, r.text)
        return r.text
    except Exception:
        return None


def serp(query: str, num: int = 20, tbs: str | None = None) -> list[dict]:
    """Google results through the SERP API as parsed JSON (brd_json=1). Returns [{title, link, snippet}]."""
    if not enabled():
        return []
    key = f"serp:{query}:{num}:{tbs}"
    if (c := _cached(key)) is not None:
        return c
    q = urllib.parse.urlencode({"q": query, "num": str(num), "brd_json": "1", **({"tbs": tbs} if tbs else {})})
    body = {"zone": config.BRIGHTDATA_SERP_ZONE, "url": f"https://www.google.com/search?{q}", "format": "raw"}
    out: list[dict] = []
    try:
        r = httpx.post(API, headers=_headers(), json=body, timeout=90)
        if r.status_code == 200:
            j = r.json()
            for it in j.get("organic", []):
                out.append({"title": it.get("title"), "link": it.get("link"), "snippet": it.get("description") or it.get("snippet")})
    except Exception:
        pass
    _store(key, out)
    return out


def discover_x_posts(conf: dict, day_from: str, day_to: str, per_query: int = 20) -> list[str]:
    """Use Google (via SERP API) to find X post URLs about the conference inside the date window."""
    urls: set[str] = set()
    for tag in conf["hashtags"]:
        for q in (f'site:x.com "{tag}"', f'site:x.com "{tag}" keynote OR session OR talk OR demo'):
            for it in serp(f"{q} after:{day_from} before:{day_to}", num=per_query):
                link = it.get("link") or ""
                if re.search(r"(x|twitter)\.com/[^/]+/status/\d+", link):
                    urls.add(link.split("?")[0])
    return sorted(urls)


# ----- Web Scraper API (datasets) ------------------------------------------------------------
X_POSTS_DATASET = "gd_lwxkxvnf1cynvib9co"  # X.com posts by URL (Bright Data dataset id)


def scrape_x_posts(urls: list[str], timeout_s: int = 240) -> list[dict]:
    """Trigger the X posts dataset for a batch of URLs and poll for the snapshot."""
    if not enabled() or not urls:
        return []
    key = "xposts:" + hashlib.sha1("|".join(sorted(urls)).encode()).hexdigest()
    if (c := _cached(key)) is not None:
        return c
    try:
        r = httpx.post(
            "https://api.brightdata.com/datasets/v3/trigger",
            headers=_headers(), params={"dataset_id": X_POSTS_DATASET, "include_errors": "true"},
            json=[{"url": u} for u in urls], timeout=60,
        )
        sid = r.json().get("snapshot_id")
        if not sid:
            return []
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            s = httpx.get(f"https://api.brightdata.com/datasets/v3/snapshot/{sid}", headers=_headers(), params={"format": "json"}, timeout=60)
            if s.status_code == 200 and s.headers.get("content-type", "").startswith("application/json"):
                data = s.json()
                if isinstance(data, list):
                    _store(key, data)
                    return data
            time.sleep(6)
    except Exception:
        pass
    return []


def x_record_to_post(rec: dict) -> dict | None:
    url = rec.get("url") or rec.get("post_url") or ""
    text = rec.get("description") or rec.get("text") or ""
    if not url or not text:
        return None
    base = normalize_exa({"url": url, "text": text, "_platform": "x", "publishedDate": rec.get("date_posted") or rec.get("timestamp") or ""})
    if not base:
        return None
    base.update({
        "author": rec.get("name") or rec.get("user_posted") or base.get("author"),
        "handle": rec.get("user_posted") or base.get("handle"),
        "likes": rec.get("likes"), "reposts": rec.get("reposts"), "replies": rec.get("replies"),
        "source": "brightdata", "_source": "brightdata",
    })
    return base


def harvest_x(conf_id: str, day_from: str, day_to: str) -> list[dict]:
    """Discover + scrape X posts for a conference window, cached as normalized posts for load_posts()."""
    conf = config.CONFERENCES[conf_id]
    urls = discover_x_posts(conf, day_from, day_to)
    recs = scrape_x_posts(urls[:150])
    posts = [p for p in (x_record_to_post(r) for r in recs) if p]
    if posts:
        (BD_DIR / f"posts_{conf_id}_x.json").write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    return posts


# ----- Bluesky through the Unlocker ---------------------------------------------------------
def bsky_search(query: str, since: str, until: str, limit: int = 100) -> list[dict]:
    """public.api.bsky.app searchPosts, fetched through Web Unlocker because the AppView blocks this network."""
    q = urllib.parse.urlencode({"q": query, "since": since, "until": until, "limit": str(limit), "sort": "latest"})
    raw = unlock(f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{q}")
    if not raw:
        return []
    try:
        return json.loads(raw).get("posts", [])
    except Exception:
        return []


def bsky_record_to_post(rec: dict) -> dict | None:
    uri = rec.get("uri") or ""  # at://did/app.bsky.feed.post/rkey
    handle = (rec.get("author") or {}).get("handle") or ""
    rkey = uri.rsplit("/", 1)[-1]
    if not rkey or not handle:
        return None
    url = f"https://bsky.app/profile/{handle}/post/{rkey}"
    text = (rec.get("record") or {}).get("text") or ""
    p = normalize_exa({"url": url, "text": "Follow " + text, "_platform": "bsky", "publishedDate": (rec.get("record") or {}).get("createdAt") or ""})
    if not p:
        return None
    p.update({"author": (rec.get("author") or {}).get("displayName") or handle, "likes": rec.get("likeCount"), "reposts": rec.get("repostCount"),
              "replies": rec.get("replyCount"), "source": "brightdata", "_source": "brightdata"})
    return p


def harvest_bsky(conf_id: str, since: str, until: str) -> list[dict]:
    conf = config.CONFERENCES[conf_id]
    out: dict[str, dict] = {}
    for tag in conf["hashtags"]:
        for rec in bsky_search(tag, since, until):
            p = bsky_record_to_post(rec)
            if p:
                out[p["id"]] = p
    posts = list(out.values())
    if posts:
        (BD_DIR / f"posts_{conf_id}_bsky.json").write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    return posts


if __name__ == "__main__":
    import sys

    conf_id = sys.argv[1] if len(sys.argv) > 1 else "universe25"
    conf = config.CONFERENCES[conf_id]
    print("bright data enabled:", enabled())
    xs = harvest_x(conf_id, conf["days"][0], conf["days"][-1])
    print("x posts:", len(xs))
    bs = harvest_bsky(conf_id, conf["days"][0] + "T00:00:00Z", conf["days"][-1] + "T23:59:59Z")
    print("bluesky posts (with likes):", len(bs))
