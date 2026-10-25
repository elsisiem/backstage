"""Curator agent: turns a stream of resolved posts into Moments.

A Moment is the thing you would walk to another room for, or jump to in the recording later:
  - a session spike: several people posting about one session inside a short window
  - an announcement wave: an announcement goes out and posts start quoting it
  - a conference-wide surge: the whole venue reacting at once (keynote)
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone

WINDOW = 30 * 60       # seconds
COOLDOWN = 45 * 60     # per-session gap between moments
SESSION_MIN = 3        # posts in window to fire
SURGE_MIN = 12         # conference-wide posts in window to fire


def _ep(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _mid(*parts: str) -> str:
    return "m_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def build_moments(posts: list[dict], resolutions: list[dict], sessions: list[dict], announcements: list[dict]) -> list[dict]:
    by_id = {p["id"]: p for p in posts}
    sess = {s["id"]: s for s in sessions}
    res_by_post = {r["post_id"]: r for r in resolutions}
    exact = [p for p in posts if p.get("ts_exact") and p.get("ts")]
    exact.sort(key=lambda p: p["ts"])
    moments: list[dict] = []

    # ---- session spikes -------------------------------------------------------------------
    per_session: dict[str, list[dict]] = defaultdict(list)
    for p in exact:
        for s in res_by_post.get(p["id"], {}).get("sessions", []):
            per_session[s["session_id"]].append(p)
    for sid, plist in per_session.items():
        last_fire = -1e12
        for i, p in enumerate(plist):
            t = _ep(p["ts"])
            window = [q for q in plist[max(0, i - 40):i + 1] if t - WINDOW <= _ep(q["ts"]) <= t]
            speaker_posting = any(res_by_post[q["id"]].get("author_of") for q in window)
            need = SESSION_MIN - (1 if speaker_posting else 0)
            if len(window) >= need and t - last_fire >= COOLDOWN:
                last_fire = t
                s = sess[sid]
                authors = sorted({(q.get("author") or q.get("handle") or "someone") for q in window})
                lead = max(window, key=lambda q: len(q.get("text") or ""))
                moments.append({
                    "id": _mid("s", sid, p["ts"][:16]), "kind": "spike", "ts": p["ts"], "session_id": sid,
                    "headline": f"{len(window)} people are posting about “{s['title'][:70]}” right now",
                    "detail": (lead.get("text") or "")[:220], "room": s.get("room"), "score": len(window) + (2 if speaker_posting else 0),
                    "evidence": [q["id"] for q in window][-6:], "authors": authors[:6],
                })

    # ---- announcement waves ---------------------------------------------------------------
    ann_posts: dict[str, list[dict]] = defaultdict(list)
    for p in exact:
        for aid in res_by_post.get(p["id"], {}).get("announcements", []):
            ann_posts[aid].append(p)
    for a in announcements:
        plist = [p for p in ann_posts.get(a["id"], []) if 0 <= _ep(p["ts"]) - _ep(a["ts"]) <= 6 * 3600]
        if len(plist) >= 3:
            moments.append({
                "id": _mid("a", a["id"]), "kind": "wave", "ts": plist[min(2, len(plist) - 1)]["ts"], "announcement_id": a["id"],
                "headline": f"“{a['title'][:70]}” is landing: {len(plist)} posts in the hours after it shipped",
                "detail": a.get("summary", "")[:220], "score": len(plist) + 1, "evidence": [p["id"] for p in plist][:6],
                "authors": sorted({(p.get("author") or p.get("handle") or "") for p in plist})[:6],
            })

    # ---- conference surges ----------------------------------------------------------------
    last = -1e12
    for i, p in enumerate(exact):
        t = _ep(p["ts"])
        window = [q for q in exact[max(0, i - 200):i + 1] if t - WINDOW <= _ep(q["ts"]) <= t]
        if len(window) >= SURGE_MIN and t - last >= 2 * 3600:
            last = t
            moments.append({
                "id": _mid("c", p["ts"][:16]), "kind": "surge", "ts": p["ts"],
                "headline": f"The whole venue is posting: {len(window)} posts in 30 minutes",
                "detail": (max(window, key=lambda q: len(q.get('text') or '')).get("text") or "")[:220],
                "score": len(window) // 2, "evidence": [q["id"] for q in window][-8:], "authors": [],
            })

    moments.sort(key=lambda m: m["ts"])
    return moments
