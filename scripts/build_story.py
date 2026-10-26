"""Build the tour story: one real post followed through every stage, computed once from the real graph.

    python -m scripts.build_story universe25 [post_id]

Writes data/timeline/<conf>_story.json. The tour page reads only this file, so it loads instantly and makes
no live calls. Every value in it comes from the record: the post, the ID decode, the resolver's candidate
scores, the edge written to FalkorDB, the Moment it fed, and the two-hop path to a repository.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import config
from app.agents.resolver import THRESHOLD, Resolver
from app.facets import repo_facets
from app.graph import Graph
from app.ingest.githubblog import load_announcements
from app.ingest.posts import load_posts
from app.ingest.rainfocus import load as load_agenda

DEFAULT_POST = {"universe25": "b8c53c06f6f1"}  # Kyle Daigle on LinkedIn, during the opening keynote he spoke at
REPO = "https://github.com/falkordb/falkordb"
SIGNALS = ["author", "mention", "title", "semantic", "product", "time", "keyword", "on_stage", "llm"]


def local(iso: str, tz: str, fmt: str = "%H:%M:%S") -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime(fmt)


def clean_text(t: str) -> str:
    t = re.sub(r"\*\*[^*]+\*\*:.*?\n", "", t or "")           # LinkedIn profile header lines
    t = re.sub(r"^#+ .*?\n", "", t.strip())                     # heading lines from the page render
    t = re.sub(r"(?m)^\s*-{3,}\s*$", "", t)                       # profile separators
    t = re.sub(r"(?m)^\s*Based in .*$", "", t)                    # profile location lines
    parts = re.split(r"(?<=[.!?])\s+", t.strip())
    while len(parts) > 1 and (parts[0][:1].islower() or len(parts[0]) < 24):  # fragments cut off at the front
        parts.pop(0)
    return re.sub(r"\s+\n", "\n", " ".join(parts)).strip()


def decode(post: dict) -> dict:
    nid = post.get("native_id") or ""
    if post["platform"] == "linkedin" and nid.isdigit():
        n = int(nid)
        bits = n.bit_length()
        shift = bits - 41
        ms = n >> shift
        return {"kind": "linkedin", "id": nid, "bits": bits, "shift": shift, "ms": ms,
                "iso": datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    if post["platform"] == "bsky" and nid:
        b32 = "234567abcdefghijklmnopqrstuvwxyz"
        v = 0
        for c in nid:
            v = v * 32 + b32.index(c)
        us = v >> 10
        return {"kind": "bsky", "id": nid, "int": v, "us": us,
                "iso": datetime.fromtimestamp(us / 1e6, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    return {"kind": "none"}


def main() -> None:
    conf_id = sys.argv[1] if len(sys.argv) > 1 else "universe25"
    conf = config.CONFERENCES[conf_id]
    tz = conf["tz"]
    tl = json.loads((config.TIMELINES / f"{conf_id}.json").read_text(encoding="utf-8"))
    pid = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_POST[conf_id]

    events = tl["events"]
    resolve = next(e for e in events if e["type"] == "resolve" and e["post_id"] == pid)
    posts = {p["id"]: p for p in load_posts(conf_id)}
    post = posts[pid]
    sessions, speakers = load_agenda(conf_id)
    by_session = {s["id"]: s for s in sessions}
    announcements = load_announcements(conf_id)
    by_ann = {a["id"]: a for a in announcements}

    # ---- resolver: re-score this one post against every session, with embeddings -----------
    r = Resolver(sessions, speakers, announcements, use_llm=bool(config.OPENAI_API_KEY))
    r.prepare()
    pemb = r.embed([post["text"]])[0] if r.use_llm else []
    scored = r.score(post, pemb)
    t = datetime.fromisoformat(post["ts"]).timestamp()
    in_progress = [s for s in sessions if s["start_utc"] and datetime.fromisoformat(s["start_utc"]).timestamp() - 1200 <= t <= datetime.fromisoformat(s["end_utc"]).timestamp() + 7200]
    winner = resolve["sessions"][0]
    cands = []
    for c in scored[:3]:
        s = by_session[c["session_id"]]
        cands.append({"session_id": s["id"], "title": s["title"], "room": s["room"].split("@")[0].strip(), "start_local": s["start_local"],
                      "score": c["score"], "signals": {k: round(v, 2) for k, v in c["signals"].items()}, "winner": s["id"] == winner["session_id"]})
    if not any(c["winner"] for c in cands):  # keep the chosen session visible even if re-scoring reorders
        s = by_session[winner["session_id"]]
        cands = [{"session_id": s["id"], "title": s["title"], "room": s["room"].split("@")[0].strip(), "start_local": s["start_local"],
                  "score": winner["confidence"], "signals": {k: 1.0 for k in winner["signals"]}, "winner": True}] + cands[:2]
    cands.sort(key=lambda c: -c["score"])

    # ---- the edge as written, and the path read back from FalkorDB -------------------------
    g = Graph(conf_id)
    why = g.why_post(pid)
    session = by_session[winner["session_id"]]
    write = {
        "cypher": "MATCH (p:Post {id:$pid}), (s:Session {id:$sid})\nMERGE (p)-[r:ABOUT_SESSION]->(s)\nSET r.confidence = $c, r.reason = $reason, r.signals = $signals",
        "params": {"pid": pid, "sid": session["id"], "c": winner["confidence"], "signals": winner["signals"]},
        "read": "MATCH (p:Post {id:$pid})-[:BY]->(:Author)-[:IS]->(sp:Speaker)<-[:PRESENTED_BY]-(s:Session)\nRETURN sp.name, s.title, s.room",
    }

    # ---- the Moment this post fed -----------------------------------------------------------
    moments = [e["moment"] for e in events if e["type"] == "moment" and pid in e["moment"].get("evidence", [])]
    moment = next((m for m in moments if m["kind"] == "wave"), moments[0] if moments else None)
    mdict = None
    if moment:
        ann = by_ann.get(moment.get("announcement_id") or "")
        ev = []
        for eid in moment.get("evidence", []):
            p = posts.get(eid)
            if not p:
                continue
            rr = next((e for e in events if e["type"] == "resolve" and e["post_id"] == eid), {})
            name = (rr.get("author_of") or [None])[0] or (p.get("author") or p.get("handle") or "someone")
            ev.append({"id": eid, "author": name, "platform": p["platform"], "time": local(p["ts"], tz, "%H:%M"),
                       "text": clean_text(p["text"])[:120], "self": eid == pid})
        mdict = {"kind": moment["kind"], "time": local(moment["ts"], tz, "%H:%M"),
                 "headline": (f"“{ann['title']}” is landing: {len(ev)} posts in the hours after it shipped" if ann else moment["headline"]),
                 "announcement": ann and {"title": ann["title"], "status": ann["status"], "time": local(ann["ts"], tz, "%H:%M"), "url": ann["url"]},
                 "evidence": ev}

    # ---- two hops to a repository ------------------------------------------------------------
    info = repo_facets(REPO)
    g.add_repo(info["url"], info["name"], info["facets"])
    mine = g.for_repo(info["url"], conf_id)
    facet_hit = set(resolve["facets"]) & set(info["facets"])
    repo = {"name": info["name"], "stars": info.get("stars"), "facets": [{"facet": f, "evidence": info["evidence"][f][:2]} for f in info["facets"]],
            "announcements": [{"title": a["title"], "status": a["status"], "via": a["via"]} for a in mine["announcements"][:4]],
            "sessions": [{"title": s["title"], "room": (s["room"] or "").split("@")[0].strip(), "start": s["start_local"], "via": s["via"]} for s in mine["sessions"][:3]],
            "shared_with_post": sorted(facet_hit)}

    story = {
        "conf": {"id": conf_id, "name": conf["name"], "tz": tz, "sessions": len(sessions), "posts": tl["meta"]["counts"]["posts"]},
        "post": {"id": pid, "url": post["url"], "platform": post["platform"], "handle": post.get("handle"),
                 "author": (resolve.get("author_of") or [post.get("author")])[0], "text": clean_text(post["text"])[:520],
                 "ts": post["ts"], "local": local(post["ts"], tz), "date": local(post["ts"], tz, "%a %d %b %Y"), "native_id": post.get("native_id")},
        "decode": decode(post),
        "resolve": {"in_progress": len(in_progress), "threshold": THRESHOLD, "candidates": cands, "signals": SIGNALS,
                    "edge": {"confidence": winner["confidence"], "reason": winner["reason"], "signals": winner["signals"]}},
        "session": {"id": session["id"], "title": session["title"], "room": session["room"].split("@")[0].strip(), "start": session["start_local"],
                    "end": session["end_local"], "replayable": session["replayable"], "capacity": session["capacity"],
                    "speakers": [sp["name"] for sp in r.session_speakers.get(session["id"], [])][:6]},
        "write": write,
        "why": why,
        "announcements": [{"title": by_ann[a]["title"], "status": by_ann[a]["status"], "time": local(by_ann[a]["ts"], tz, "%H:%M")} for a in resolve["announcements"] if a in by_ann],
        "facets": resolve["facets"],
        "moment": mdict,
        "repo": repo,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out = config.TIMELINES / f"{conf_id}_story.json"
    out.write_text(json.dumps(story, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"story -> {out}")
    print(" post:", story["post"]["author"], story["post"]["local"], "|", story["post"]["text"][:90])
    print(" decode:", story["decode"])
    print(" candidates:", [(c["title"][:32], c["score"], sorted(c["signals"]), c["winner"]) for c in cands], "| in progress:", len(in_progress))
    print(" why:", json.dumps(why)[:240])
    print(" moment:", mdict and (mdict["headline"][:80], len(mdict["evidence"])))
    print(" repo:", repo["name"], [f["facet"] for f in repo["facets"]], "| anns:", [a["title"][:40] for a in repo["announcements"]], "| shared:", repo["shared_with_post"])


if __name__ == "__main__":
    main()
