"""Build a conference: load the agenda into FalkorDB, resolve every post, curate moments, and write the
replay timeline the server streams to the console.

    python -m scripts.build_timeline universe25 [--no-llm] [--reset]
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from app import config
from app.agents.curator import build_moments
from app.agents.resolver import Resolver
from app.facets import facets_for_text
from app.graph import Graph, epoch
from app.ingest.githubblog import load_announcements
from app.ingest.posts import load_posts
from app.ingest.rainfocus import load as load_agenda


def spread_inexact(posts: list[dict], conf: dict) -> None:
    """Posts whose platform gave us only a date get a plausible slot inside that day (flagged inexact)."""
    by_day: dict[str, list[dict]] = {}
    for p in posts:
        if not p.get("ts_exact") and p.get("day"):
            by_day.setdefault(p["day"], []).append(p)
    for day, plist in by_day.items():
        start = datetime.fromisoformat(day + "T17:00:00+00:00")  # 10:00 PT
        step = timedelta(hours=8) / max(1, len(plist))
        for i, p in enumerate(plist):
            p["ts"] = (start + step * i).isoformat()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    conf_id = args[0] if args else "universe25"
    conf = config.CONFERENCES[conf_id]
    use_llm = "--no-llm" not in sys.argv
    t0 = time.time()

    sessions, speakers = load_agenda(conf_id)
    window = (
        (datetime.fromisoformat(conf["days"][0]) - timedelta(days=2)).date().isoformat(),
        (datetime.fromisoformat(conf["days"][-1]) + timedelta(days=2)).date().isoformat(),
    )
    posts = load_posts(conf_id, window)
    spread_inexact(posts, conf)
    posts.sort(key=lambda p: p["ts"] or "9")
    announcements = [a for a in load_announcements(conf_id) if window[0] <= a["ts"][:10] <= window[1]]
    print(f"[{conf_id}] {len(sessions)} sessions, {len(speakers)} speakers, {len(posts)} posts, {len(announcements)} announcements")

    # ---- graph ---------------------------------------------------------------------------
    g = Graph(conf_id)
    if "--reset" in sys.argv:
        g.delete()
        g = Graph(conf_id)
    g.load_agenda(conf, sessions, speakers)
    session_facets = {}
    for s in sessions:
        f = facets_for_text(f"{s['title']} {s['title']} {s['abstract']} {' '.join(s['products'])} {' '.join(s['tracks'])}", limit=5)
        session_facets[s["id"]] = f
        g.tag_session_facets(s["id"], f)
    for a in announcements:
        g.add_announcement(a)
    print(f"  agenda + announcements in graph ({time.time()-t0:.0f}s)")

    # ---- resolve -------------------------------------------------------------------------
    resolver = Resolver(sessions, speakers, announcements, use_llm=use_llm)
    resolutions = resolver.resolve_all(posts)
    res_by_post = {r["post_id"]: r for r in resolutions}

    # discovery is semantic search, so it also returns noise: keep a post only if it is evidently about the
    # conference, or the resolver tied it to a session, speaker or announcement
    kw = [h.lower().lstrip("#") for h in conf["hashtags"]] + ["githubuniverse", "universe25", "universe 2025", "fort mason", "octoverse", "agent hq"]
    def relevant(p: dict) -> bool:
        r = res_by_post[p["id"]]
        if r["sessions"] or r["mentions"] or r["author_of"] or r["announcements"]:
            return True
        t = (p.get("text") or "").lower() + " " + " ".join(p.get("hashtags") or [])
        return any(k in t for k in kw)
    dropped = [p for p in posts if not relevant(p)]
    posts = [p for p in posts if relevant(p)]
    print(f"  relevance filter: kept {len(posts)}, dropped {len(dropped)} off-topic discovery hits")
    for p in posts:
        r = res_by_post[p["id"]]
        p["kind"] = r["kind"]
        g.add_post(p)
        for s in r["sessions"]:
            g.link_post_session(p["id"], s["session_id"], s["confidence"], s["reason"], s["signals"])
        for spid in r["mentions"]:
            g.link_post_speaker(p["id"], spid)
        for spid in r["author_of"]:
            g.link_author_speaker(p["id"], spid)
        if r["facets"]:
            g.tag_post_facets(p["id"], r["facets"])
        for aid in r["announcements"]:
            g.link_post_announcement(p["id"], aid)
        for art in r["artifacts"]:
            g.add_artifact(p["id"], art["url"], art["kind"], r["sessions"][0]["session_id"] if r["sessions"] else None)
    linked = sum(1 for r in resolutions if r["sessions"])
    print(f"  resolved: {linked}/{len(posts)} posts linked to sessions, "
          f"{sum(1 for r in resolutions if r['announcements'])} to announcements, "
          f"{sum(1 for r in resolutions if r['author_of'])} authored by speakers, llm calls={resolver.llm_calls} ({time.time()-t0:.0f}s)")
    print("  signals:", Counter(sig for r in resolutions for s in r["sessions"] for sig in s["signals"]))

    # ---- curate --------------------------------------------------------------------------
    moments = build_moments(posts, resolutions, sessions, announcements)
    for m in moments:
        g.add_moment(m)
    print(f"  moments: {len(moments)} {Counter(m['kind'] for m in moments)}")

    # ---- timeline ------------------------------------------------------------------------
    by_id = {s["id"]: s for s in sessions}
    events = []
    for p in posts:
        r = res_by_post[p["id"]]
        events.append({"t": epoch(p["ts"]), "type": "post", "post": {k: p[k] for k in ("id", "url", "platform", "handle", "author", "text", "ts", "ts_exact", "kind", "hashtags")}})
        if r["sessions"] or r["mentions"] or r["announcements"] or r["facets"]:
            events.append({
                "t": epoch(p["ts"]) + 2, "type": "resolve", "post_id": p["id"],
                "sessions": [{**s, "title": by_id[s["session_id"]]["title"], "room": by_id[s["session_id"]]["room"]} for s in r["sessions"]],
                "mentions": [resolver.speakers[i]["name"] for i in r["mentions"] if i in resolver.speakers],
                "author_of": [resolver.speakers[i]["name"] for i in r["author_of"] if i in resolver.speakers],
                "announcements": r["announcements"], "facets": r["facets"], "artifacts": r["artifacts"],
            })
    for a in announcements:
        events.append({"t": epoch(a["ts"]), "type": "announcement", "announcement": {k: a[k] for k in ("id", "title", "url", "ts", "status", "product", "summary", "kind", "facets")}})
    for m in moments:
        events.append({"t": epoch(m["ts"]) + 3, "type": "moment", "moment": m})
    events.sort(key=lambda e: e["t"])

    rooms = sorted({s["room"] for s in sessions if s["room"]})
    meta = {
        "conf": conf, "rooms": rooms,
        "sessions": [{**{k: s[k] for k in ("id", "title", "type", "room", "day", "start_utc", "end_utc", "start_local", "end_local", "replayable", "virtual", "capacity", "url", "tracks", "products", "level")},
                      "start_epoch": epoch(s["start_utc"]), "end_epoch": epoch(s["end_utc"]),
                      "speakers": [resolver.speakers[i]["name"] for i in s["speakers"] if i in resolver.speakers],
                      "facets": session_facets[s["id"]]} for s in sessions],
        "speakers": [{k: sp.get(k, "") for k in ("id", "name", "company", "title", "photo")} for sp in speakers],
        "announcements": announcements,
        "counts": {"posts": len(posts), "linked": linked, "moments": len(moments), "announcements": len(announcements)},
        "clock": {"start": min(e["t"] for e in events) if events else min((epoch(s["start_utc"]) for s in sessions if s["start_utc"]), default=0),
                  "end": max(e["t"] for e in events) if events else max((epoch(s["end_utc"]) for s in sessions if s["end_utc"]), default=0)},
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (config.TIMELINES / f"{conf_id}.json").write_text(json.dumps({"meta": meta, "events": events}, ensure_ascii=False), encoding="utf-8")
    print(f"  timeline: {len(events)} events -> data/timeline/{conf_id}.json ({time.time()-t0:.0f}s)")
    print("  graph:", g.stats())


if __name__ == "__main__":
    main()
