"""Fetch and normalize a RainFocus session catalog (GitHub Universe uses RainFocus).

The catalog page is a JavaScript app; the data lives behind events.githubuniverse.com/api/search
and needs the widget's API profile token and widget id, which we read from the catalog page config.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from app.config import CACHE

RF_DIR = CACHE / "rainfocus"
RF_DIR.mkdir(parents=True, exist_ok=True)
API = "https://events.githubuniverse.com/api/search"


def _headers(tokens: dict) -> dict:
    return {
        "rfapiprofileid": tokens["apiProfileToken"],
        "rfwidgetid": tokens["widgetId"],
        "User-Agent": "Mozilla/5.0 (Backstage)",
        "Origin": "https://reg.githubuniverse.com",
        "Referer": "https://reg.githubuniverse.com/",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def fetch_sessions(event: str, force: bool = False) -> list[dict]:
    """Page through the catalog (50 per page) and cache the raw items."""
    raw_path = RF_DIR / f"{event}_sessions.json"
    tokens = json.loads((RF_DIR / f"{event}_tokens.json").read_text())
    if raw_path.exists() and not force:
        items = json.loads(raw_path.read_text(encoding="utf-8"))
        if len(items) > 50:
            return items
    items: list[dict] = []
    seen: set[str] = set()
    frm = 0
    while True:
        body = urllib.parse.urlencode({"type": "session", "size": "50", "from": str(frm)}).encode()
        req = urllib.request.Request(API, data=body, headers=_headers(tokens))
        j = json.load(urllib.request.urlopen(req, timeout=60))
        # first page comes back as sectionList[].items, later pages as a flat items[]
        page = j.get("items") or [it for s in j.get("sectionList", []) for it in s.get("items", [])]
        new = [it for it in page if it.get("sessionID") not in seen]
        for it in new:
            seen.add(it.get("sessionID"))
        items += new
        if len(page) < 50 or not new:
            break
        frm += 50
        time.sleep(0.25)
    raw_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return items


def _attr(item: dict, *names: str) -> list[str]:
    out = []
    for a in item.get("attributevalues", []) or []:
        key = (a.get("attribute_id") or a.get("attribute") or "").lower()
        if key in [n.lower() for n in names]:
            v = a.get("value")
            if v and v not in out:
                out.append(v)
    return out


def _utc(s: str | None) -> str | None:
    # RainFocus gives 'YYYY/MM/DD HH:MM:SS' in UTC
    if not s:
        return None
    return datetime.strptime(s, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()


def normalize(items: list[dict], conf_id: str) -> tuple[list[dict], list[dict]]:
    """Return (sessions, speakers) in Backstage's shape."""
    sessions, speakers = [], {}
    for it in items:
        times = it.get("times") or []
        t0 = times[0] if times else {}
        sp_ids = []
        for p in it.get("participants", []) or []:
            sid = p.get("speakerId") or p.get("userRef") or p.get("globalFullName")
            if not sid:
                continue
            sp_ids.append(sid)
            if sid not in speakers:
                speakers[sid] = {
                    "id": sid,
                    "name": p.get("globalFullName") or p.get("fullName") or "",
                    "first": p.get("globalFirstname") or p.get("firstName") or "",
                    "last": p.get("globalLastname") or p.get("lastName") or "",
                    "company": p.get("globalCompany") or p.get("companyName") or "",
                    "title": p.get("globalJobtitle") or p.get("jobTitle") or "",
                    "twitter": (p.get("twitter") or "").strip().lstrip("@"),
                    "linkedin": (p.get("linkedIn") or "").strip(),
                    "bio": (p.get("globalBio") or p.get("bio") or "")[:600],
                    "photo": p.get("photoURL") or "",
                }
        sessions.append(
            {
                "id": it.get("sessionID"),
                "conf": conf_id,
                "code": it.get("code"),
                "title": (it.get("title") or "").strip(),
                "abstract": (it.get("abstract") or "").strip(),
                "type": it.get("type") or "",
                "tracks": _attr(it, "Track"),
                "level": (_attr(it, "Level") or [""])[0],
                "products": _attr(it, "GitHubProduct", "GitHubproduct"),
                "audience": _attr(it, "TargetAudience"),
                "room": t0.get("room") or "",
                "day": t0.get("date") or "",
                "start_utc": _utc(t0.get("utcStartTime")),
                "end_utc": _utc(t0.get("utcEndTime")),
                "start_local": t0.get("startTimeFormatted") or "",
                "end_local": t0.get("endTimeFormatted") or "",
                "length_min": t0.get("length"),
                "capacity": int(t0.get("capacity") or 0),
                "replayable": bool(t0.get("replayable")),
                "virtual": bool(t0.get("virtualTime")),
                "showings": len(times),
                "speakers": sp_ids,
                "url": f"https://reg.githubuniverse.com/flow/github/{conf_id}/attendee-portal/page/sessioncatalog/session/{it.get('sessionID')}",
            }
        )
    sessions.sort(key=lambda s: (s["start_utc"] or "9", s["room"]))
    return sessions, list(speakers.values())


def load(conf_id: str, force: bool = False) -> tuple[list[dict], list[dict]]:
    items = fetch_sessions(conf_id, force=force)
    sessions, speakers = normalize(items, conf_id)
    (RF_DIR / f"{conf_id}_normalized.json").write_text(
        json.dumps({"sessions": sessions, "speakers": speakers}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return sessions, speakers


if __name__ == "__main__":
    import sys

    for ev in [a for a in sys.argv[1:] if not a.startswith("--")] or ["universe25", "universe26"]:
        s, sp = load(ev, force="--force" in sys.argv)
        rooms = sorted({x["room"] for x in s if x["room"]})
        print(f"{ev}: {len(s)} sessions, {len(sp)} speakers, {len(rooms)} rooms, "
              f"{sum(1 for x in s if x['replayable'])} replayable, days={sorted({x['day'] for x in s})}")
