"""Announcements: GitHub blog posts and changelog entries published during the conference.

Discovery comes from the Exa cache (site:github.blog in the date window); exact publish times are read from
each page's article:published_time meta tag so the Wire can place announcements on the replay clock.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone

from app.config import CACHE
from app.facets import facets_for_text

BLOG_DIR = CACHE / "githubblog"
BLOG_DIR.mkdir(parents=True, exist_ok=True)

_TIME_PATTERNS = [
    re.compile(r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"'),
    re.compile(r'<meta[^>]+content="([^"]+)"[^>]+property="article:published_time"'),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'<time[^>]+datetime="([^"]+)"'),
]
_DESC = re.compile(r'<meta[^>]+(?:name="description"|property="og:description")[^>]+content="([^"]*)"')


def _fetch(url: str) -> tuple[str | None, str | None]:
    """Exact publish time + description for a github.blog URL (cached). Uses httpx so the CA bundle is certifi's."""
    cache = BLOG_DIR / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".json")
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("ts"):
            return d.get("ts"), d.get("desc")
    ts = desc = None
    try:
        import httpx

        html = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (Backstage)"}, timeout=30, follow_redirects=True).text
        for pat in _TIME_PATTERNS:
            m = pat.search(html)
            if m:
                raw = m.group(1).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.astimezone(timezone.utc).isoformat()
                break
        m2 = _DESC.search(html)
        if m2:
            desc = m2.group(1)
    except Exception:
        pass
    cache.write_text(json.dumps({"ts": ts, "desc": desc}), encoding="utf-8")
    return ts, desc


def _is_article(url: str, title: str) -> bool:
    u = url.lower()
    if any(x in u for x in ("/author/", "/tag/", "/page/", "/category/", "github.blog/jp/", "/ja/", "/de/", "/fr/")):
        return False
    if "archives" in title.lower() or "author at" in title.lower():
        return False
    return "/changelog/" in u or "/news-insights/" in u or "/ai-and-ml/" in u or "/engineering/" in u or "/open-source/" in u or "/enterprise-software/" in u or "/developer-skills/" in u


def _status(title: str, text: str) -> str:
    t = (title + " " + text).lower()
    if "generally available" in t or " ga " in t or "now available" in t:
        return "GA"
    if "public preview" in t or "preview" in t:
        return "Preview"
    if "beta" in t:
        return "Beta"
    if "announc" in t or "introducing" in t:
        return "Announced"
    return "Update"


def _product(title: str) -> str:
    t = title.lower()
    for name, keys in [
        ("Copilot coding agent", ["coding agent", "mission control", "agent hq"]), ("Copilot CLI", ["copilot cli"]),
        ("Copilot code review", ["code review"]), ("GitHub Code Quality", ["code quality"]), ("Copilot", ["copilot"]),
        ("GitHub Actions", ["actions", "runner"]), ("Secret scanning", ["secret scanning"]), ("Code scanning", ["code scanning"]),
        ("Releases", ["releases"]), ("MCP", ["mcp"]), ("Octoverse", ["octoverse"]), ("Dependabot", ["dependabot"]),
        ("GitHub Enterprise", ["enterprise"]), ("Home dashboard", ["dashboard"]), ("GitHub Models", ["models"]),
    ]:
        if any(k in t for k in keys):
            return name
    return "GitHub"


def load_announcements(conf_id: str) -> list[dict]:
    src = CACHE / "exa" / f"githubblog_{conf_id}.json"
    if not src.exists():
        return []
    out = []
    for rec in json.loads(src.read_text(encoding="utf-8")):
        url = rec.get("url") or ""
        title = re.sub(r"\s*-\s*(The GitHub Blog|GitHub Changelog)\s*$", "", rec.get("title") or "").strip()
        if not _is_article(url, title):
            continue
        ts, desc = _fetch(url)
        ts = ts or rec.get("publishedDate")
        text = (rec.get("text") or "")[:2500]
        kind = "changelog" if "/changelog/" in url else "blog"
        out.append({
            "id": "ann_" + hashlib.sha1(url.encode()).hexdigest()[:10], "title": title, "url": url, "ts": ts,
            "summary": (desc or text[:400]).strip(), "status": _status(title, text), "product": _product(title),
            "kind": kind, "facets": facets_for_text(title + " " + title + " " + text, limit=5),
            "keyphrases": keyphrases(title),
        })
    out = [a for a in out if a["ts"]]
    out.sort(key=lambda a: a["ts"])
    return out


_STOP = {"the", "a", "an", "and", "for", "in", "of", "to", "with", "now", "is", "are", "on", "your", "any", "way", "you", "work", "new", "github", "changelog", "blog", "introducing", "announcing", "generally", "available", "public", "preview", "gets", "upgraded", "how", "what", "from", "into"}


def keyphrases(title: str) -> list[str]:
    """Short phrases that identify an announcement inside a social post ("Agent HQ", "mission control")."""
    t = re.sub(r"[^\w\s@#&.-]", " ", title)
    words = [w for w in t.split() if w.lower() not in _STOP]
    phrases = set()
    # capitalized multiword runs
    for m in re.finditer(r"((?:[A-Z][\w.&-]*\s+){1,3}[A-Z][\w.&-]*)", title):
        ph = m.group(1).strip()
        if len(ph) > 5 and ph.lower() not in ("github blog", "github changelog", "the github"):
            phrases.add(ph.lower())
    if len(words) >= 2:
        phrases.add(" ".join(words[:2]).lower())
    for w in words:
        if len(w) > 7 and w.lower() not in ("copilot", "github"):
            phrases.add(w.lower())
    return sorted(phrases)[:8]


if __name__ == "__main__":
    anns = load_announcements("universe25")
    print(len(anns), "announcements with timestamps")
    for a in anns[:40]:
        print(a["ts"][:16], a["status"].ljust(9), a["product"].ljust(22), a["title"][:70], a["facets"], a["keyphrases"][:3])
