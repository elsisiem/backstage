"""Live mode: pull fresh posts for a conference that is happening now, resolve them with the same agents,
append them to the timeline on disk and to the graph.

Sources: Exa (Bluesky + LinkedIn discovery with date filters), Bright Data (X via SERP + Web Scraper,
Bluesky via Unlocker). Everything is idempotent, so this can run every couple of minutes.
"""
from __future__ import annotations

import json
import threading
import time
import time
from datetime import datetime, timedelta, timezone

import httpx

from app import config
from app.agents.curator import build_moments
from app.agents.resolver import Resolver
from app.graph import Graph, epoch
from app.ingest import brightdata
from app.ingest.githubblog import load_announcements
from app.ingest.posts import normalize_exa
from app.ingest.rainfocus import load as load_agenda


def exa_recent(conf: dict, since_iso: str, until_iso: str) -> list[dict]:
    if not config.EXA_API_KEY:
        return []
    out = []
    for dom in (["bsky.app"], ["linkedin.com"], ["x.com", "twitter.com"]):
        for q in conf["hashtags"][:2]:
            try:
                r = httpx.post(
                    "https://api.exa.ai/search", headers={"x-api-key": config.EXA_API_KEY},
                    json={"query": q, "type": "auto", "numResults": 50, "includeDomains": dom, "startPublishedDate": since_iso,
                          "endPublishedDate": until_iso, "contents": {"text": {"maxCharacters": 1200}}}, timeout=90,
                ).json()
                out += [{**x, "_platform": dom[0].split(".")[0].replace("bsky", "bsky"), "_source": "exa", "_query": q} for x in r.get("results", [])]
            except Exception:
                continue
    return out


_resolvers: dict[str, tuple[Resolver, float]] = {}
_resolver_lock = threading.Lock()
RESOLVER_TTL = 15 * 60  # seconds; the agenda and announcement files can change during a live day


def get_resolver(conf_id: str) -> Resolver:
    """One Resolver per conference, shared by the server and the live loop.

    Session embeddings are computed once (prepare() is idempotent). If embedding fails the resolver stays
    LLM-enabled and simply retries on the next call instead of being cached in a degraded state. The instance
    is rebuilt after RESOLVER_TTL so a refreshed agenda or new announcements are picked up without a restart.
    """
    with _resolver_lock:
        cached = _resolvers.get(conf_id)
        if cached and time.time() - cached[1] < RESOLVER_TTL:
            r = cached[0]
        else:
            sessions, speakers = load_agenda(conf_id)
            r = Resolver(sessions, speakers, load_announcements(conf_id), use_llm=True)
            _resolvers[conf_id] = (r, time.time())
        try:
            r.prepare()
        except Exception:
            pass  # rules-only this call; the next call retries the embeddings
        return r


def pull_once(conf_id: str) -> dict:
    conf = config.CONFERENCES[conf_id]
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=36)).isoformat()
    until = now.isoformat()
    tl_path = config.TIMELINES / f"{conf_id}.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.exists() else {"meta": {}, "events": []}
    known = {e["post"]["id"] for e in tl["events"] if e["type"] == "post"}

    raw = exa_recent(conf, since, until)
    fresh: dict[str, dict] = {}
    window = (conf["days"][0], conf["days"][-1])
    for rec in raw:
        p = normalize_exa(rec, None)
        if p and p["id"] not in known:
            fresh[p["id"]] = p
    if brightdata.enabled():
        for p in brightdata.harvest_bsky(conf_id, since, until) + brightdata.harvest_x(conf_id, window[0], window[1]):
            if p["id"] not in known:
                fresh[p["id"]] = p
    posts = sorted(fresh.values(), key=lambda p: p["ts"] or "9")
    if not posts:
        return {"pulled": 0, "new": 0}

    resolver = get_resolver(conf_id)
    sessions, announcements = resolver.sessions, resolver.announcements
    calls_before = resolver.llm_calls
    resolutions = resolver.resolve_all(posts)
    llm_calls_now = resolver.llm_calls - calls_before
    res_by_post = {r["post_id"]: r for r in resolutions}
    kw = [h.lower().lstrip("#") for h in conf["hashtags"]]
    posts = [p for p in posts if res_by_post[p["id"]]["sessions"] or res_by_post[p["id"]]["mentions"] or res_by_post[p["id"]]["announcements"]
             or any(k in (p.get("text") or "").lower() for k in kw)]

    g = Graph(conf_id)
    by_id = {s["id"]: s for s in sessions}
    new_events = []
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
        t = epoch(p["ts"])
        new_events.append({"t": t, "type": "post", "post": {k: p.get(k) for k in ("id", "url", "platform", "handle", "author", "text", "ts", "ts_exact", "kind", "hashtags")}})
        if r["sessions"] or r["mentions"] or r["announcements"] or r["facets"]:
            new_events.append({"t": t + 2, "type": "resolve", "post_id": p["id"],
                               "sessions": [{**s, "title": by_id[s["session_id"]]["title"], "room": by_id[s["session_id"]]["room"]} for s in r["sessions"]],
                               "mentions": [resolver.speakers[i]["name"] for i in r["mentions"] if i in resolver.speakers],
                               "author_of": [resolver.speakers[i]["name"] for i in r["author_of"] if i in resolver.speakers],
                               "announcements": r["announcements"], "facets": r["facets"], "artifacts": r["artifacts"]})
    all_posts = [e["post"] for e in tl["events"] + new_events if e["type"] == "post"]
    all_res = [r for r in resolutions] + []
    moments = build_moments([p for p in posts], resolutions, sessions, announcements)
    for m in moments:
        g.add_moment(m)
        new_events.append({"t": epoch(m["ts"]) + 3, "type": "moment", "moment": m})
    tl["events"] = sorted(tl["events"] + new_events, key=lambda e: e["t"])
    meta = tl.setdefault("meta", {})
    counts = meta.setdefault("counts", {"posts": 0, "linked": 0, "moments": 0, "announcements": 0})
    counts["posts"] += len(posts)
    counts["linked"] += sum(1 for p in posts if res_by_post[p["id"]]["sessions"])
    counts["moments"] += len(moments)
    meta["clock"] = {"start": tl["events"][0]["t"], "end": tl["events"][-1]["t"]} if tl["events"] else meta.get("clock")
    tl_path.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
    linked_now = sum(1 for p in posts if res_by_post[p["id"]]["sessions"])
    return {"pulled": len(raw), "new": len(posts), "linked": linked_now, "linked_total": counts["linked"], "moments_added": len(moments),
            "llm_calls": llm_calls_now}


if __name__ == "__main__":
    import sys

    print(pull_once(sys.argv[1] if len(sys.argv) > 1 else "universe26"))
