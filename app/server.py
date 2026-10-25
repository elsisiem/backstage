"""Backstage server: serves the console, streams the replay/live timeline over SSE, and answers graph questions.

Every replayed event is also written into FalkorDB (graph "<conf>_live") so the graph on stage is being built
in front of the judges, and every "why" button is a live Cypher path query.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from app import config
from app.facets import repo_facets
from app.graph import Graph

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = FastAPI(title="Backstage", default_response_class=ORJSONResponse)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

_timelines: dict[str, dict] = {}
_graphs: dict[str, Graph] = {}
_live_graphs: dict[str, Graph] = {}
_resolvers: dict[str, Any] = {}
# field notes reported from inside the venue, pushed to every open stream the moment they land
_live_bus: dict[str, list[dict]] = {}


# control characters except tab and newline, built at runtime so the source stays plain text
_CTRL = re.compile("[%s]" % "".join(map(chr, [*range(0, 9), 11, 12, *range(14, 32), 127])))
_note_hits: dict[str, list[float]] = {}
NOTE_LIMIT, NOTE_WINDOW = 5, 60.0


def _note_allowed(ip: str) -> bool:
    """Simple per-IP budget for room reports: NOTE_LIMIT per NOTE_WINDOW seconds, in memory."""
    now = time.time()
    hits = [t for t in _note_hits.get(ip, []) if now - t < NOTE_WINDOW]
    if len(hits) >= NOTE_LIMIT:
        _note_hits[ip] = hits
        return False
    hits.append(now)
    _note_hits[ip] = hits
    return True


def notes_path(conf_id: str) -> Path:
    return config.TIMELINES / f"{conf_id}_notes.json"


def load_notes(conf_id: str) -> list[dict]:
    p = notes_path(conf_id)
    return orjson.loads(p.read_bytes()) if p.exists() else []


def timeline(conf_id: str) -> dict:
    if conf_id not in _timelines:
        path = config.TIMELINES / f"{conf_id}.json"
        if not path.exists():
            raise HTTPException(404, f"no timeline built for {conf_id}")
        _timelines[conf_id] = orjson.loads(path.read_bytes())
    return _timelines[conf_id]


def graph(conf_id: str) -> Graph:
    if conf_id not in _graphs:
        _graphs[conf_id] = Graph(conf_id)
    return _graphs[conf_id]


def live_graph(conf_id: str) -> Graph:
    if conf_id not in _live_graphs:
        _live_graphs[conf_id] = Graph(f"{conf_id}_live")
    return _live_graphs[conf_id]


# ----- pages ------------------------------------------------------------------------------
@app.get("/")
async def landing():
    return FileResponse(STATIC / "landing.html")


@app.get("/console")
async def console():
    return FileResponse(STATIC / "console.html")


@app.get("/health")
async def health():
    out = {"ok": True, "timelines": sorted(p.stem for p in config.TIMELINES.glob("*.json"))}
    try:
        out["graphs"] = Graph("universe25").db.list_graphs()
    except Exception as e:  # pragma: no cover
        out["graph_error"] = str(e)[:200]
    return out


# ----- bootstrap ----------------------------------------------------------------------------
@app.get("/api/conferences")
async def conferences():
    out = []
    for cid, c in config.CONFERENCES.items():
        built = (config.TIMELINES / f"{cid}.json").exists()
        meta = timeline(cid)["meta"] if built else None
        out.append({**c, "built": built, "counts": meta["counts"] if meta else None, "clock": meta["clock"] if meta else None})
    return out


@app.get("/api/conf/{conf_id}/bootstrap")
async def bootstrap(conf_id: str):
    tl = timeline(conf_id)
    return tl["meta"]


# ----- replay stream ------------------------------------------------------------------------
def _mirror_to_live(g: Graph, ev: dict, by_session: dict) -> None:
    """Write a replayed event into the live graph (idempotent MERGEs). Sessions are merged on first contact."""
    try:
        if ev["type"] == "post":
            g.add_post({**ev["post"], "hashtags": ev["post"].get("hashtags", [])})
        elif ev["type"] == "resolve":
            for s in ev["sessions"]:
                meta_s = by_session.get(s["session_id"], {})
                g.q("MERGE (s:Session {id:$id}) SET s.title=$title, s.room=$room, s.start_local=$sl, s.replayable=$rp",
                    {"id": s["session_id"], "title": s.get("title", ""), "room": s.get("room", ""), "sl": meta_s.get("start_local", ""), "rp": bool(meta_s.get("replayable"))})
                for name in ev.get("author_of", []):
                    g.q("MATCH (p:Post {id:$pid})-[:BY]->(a:Author), (s:Session {id:$sid}) MERGE (sp:Speaker {id:$spid}) SET sp.name=$name "
                        "MERGE (a)-[:IS]->(sp) MERGE (s)-[:PRESENTED_BY]->(sp)", {"pid": ev["post_id"], "sid": s["session_id"], "spid": "live:" + name, "name": name})
                g.link_post_session(ev["post_id"], s["session_id"], s["confidence"], s["reason"], s["signals"])
            for f in ev.get("facets", []):
                g.tag_post_facets(ev["post_id"], [f])
            for aid in ev.get("announcements", []):
                g.link_post_announcement(ev["post_id"], aid)
        elif ev["type"] == "announcement":
            g.add_announcement(ev["announcement"])
        elif ev["type"] == "moment":
            g.add_moment(ev["moment"])
    except Exception:
        pass


@app.get("/api/conf/{conf_id}/stream")
async def stream(request: Request, conf_id: str, speed: float = Query(60.0, ge=1, le=3600), start: int | None = None,
                 mirror: bool = True):
    """Server-sent replay. `speed` is replay seconds per wall second; `start` is a unix timestamp to begin from."""
    tl = timeline(conf_id)
    events: list[dict] = tl["events"]
    meta = tl["meta"]
    by_session = {s["id"]: s for s in meta["sessions"]}
    t_begin = start or meta["clock"]["start"]
    lg = None
    if mirror:
        try:
            lg = live_graph(conf_id)
            lg.ensure_indices()
        except Exception:
            lg = None
    mirror_q: asyncio.Queue = asyncio.Queue(maxsize=2000)

    async def mirror_worker():
        loop = asyncio.get_running_loop()
        while True:
            ev = await mirror_q.get()
            if ev is None:
                return
            await loop.run_in_executor(None, _mirror_to_live, lg, ev, by_session)

    async def gen():
        worker = asyncio.create_task(mirror_worker()) if lg is not None else None
        try:
            async for item in _gen_inner():
                yield item
        finally:
            if worker:
                await mirror_q.put(None)

    async def _gen_inner():
        yield {"event": "hello", "data": orjson.dumps({"conf": conf_id, "clock": t_begin, "speed": speed, "mirror": lg is not None, "total": len(events)}).decode()}
        idx = next((i for i, e in enumerate(events) if e["t"] >= t_begin), len(events))
        # catch-up summary for everything before the start point
        before = events[:idx]
        yield {"event": "catchup", "data": orjson.dumps({"posts": sum(1 for e in before if e["type"] == "post"), "moments": [e["moment"] for e in before if e["type"] == "moment"][-5:],
                                                         "announcements": [e["announcement"] for e in before if e["type"] == "announcement"],
                                                         "notes": load_notes(conf_id)[-20:]}).decode()}
        wall0 = time.time()
        clock0 = t_begin
        last_tick = 0.0
        bus = _live_bus.setdefault(conf_id, [])
        bus_sent = len(bus)
        while idx < len(events) or True:
            if await request.is_disconnected():
                break
            now_clock = clock0 + (time.time() - wall0) * speed
            if time.time() - last_tick > 1.0:
                last_tick = time.time()
                yield {"event": "tick", "data": orjson.dumps({"clock": int(now_clock)}).decode()}
            while bus_sent < len(bus):  # field notes reported from the venue, delivered immediately
                yield {"event": "note", "data": orjson.dumps(bus[bus_sent]).decode()}
                bus_sent += 1
            if idx >= len(events):
                await asyncio.sleep(0.5)
                continue
            ev = events[idx]
            if ev["t"] <= now_clock:
                if lg is not None and not mirror_q.full():
                    mirror_q.put_nowait(ev)
                yield {"event": ev["type"], "data": orjson.dumps(ev).decode()}
                idx += 1
                continue
            await asyncio.sleep(min(0.25, max(0.01, (ev["t"] - now_clock) / speed)))
        yield {"event": "done", "data": orjson.dumps({"clock": int(events[-1]["t"]) if events else 0}).decode()}

    return EventSourceResponse(gen(), ping=15)


def _sessions_for_graph(meta: dict) -> list[dict]:
    out = []
    for s in meta["sessions"]:
        out.append({**s, "abstract": "", "speakers": [], "products": s.get("products", []), "tracks": s.get("tracks", []),
                    "start_utc": s.get("start_utc"), "end_utc": s.get("end_utc")})
    return out


def _speakers_for_graph(meta: dict) -> list[dict]:
    return [{**sp, "first": "", "last": "", "twitter": "", "linkedin": ""} for sp in meta["speakers"]]


# ----- graph questions ----------------------------------------------------------------------
@app.get("/api/conf/{conf_id}/why/{post_id}")
async def why(conf_id: str, post_id: str):
    g = graph(conf_id)
    return await asyncio.get_running_loop().run_in_executor(None, g.why_post, post_id)


@app.get("/api/conf/{conf_id}/session/{session_id}")
async def session_detail(conf_id: str, session_id: str):
    g = graph(conf_id)

    def run():
        rows = g.ro(
            """
            MATCH (s:Session {id:$id})
            OPTIONAL MATCH (s)-[:PRESENTED_BY]->(sp:Speaker)
            OPTIONAL MATCH (p:Post)-[r:ABOUT_SESSION]->(s)
            OPTIONAL MATCH (s)-[:OVERLAPS]-(o:Session)
            OPTIONAL MATCH (s)-[:ABOUT]->(f:Facet)
            OPTIONAL MATCH (a:Artifact)-[:FOR]->(s)
            OPTIONAL MATCH (m:Moment)-[:OF]->(s)
            RETURN s.title, s.room, s.start_local, s.end_local, s.day, s.replayable, s.url, s.abstract,
                   collect(DISTINCT {name: sp.name, company: sp.company, title: sp.title}),
                   collect(DISTINCT {id: p.id, text: p.text, url: p.url, platform: p.platform, ts: p.ts, confidence: r.confidence, reason: r.reason, author: p.author}),
                   collect(DISTINCT {id: o.id, title: o.title, room: o.room, start_local: o.start_local}),
                   collect(DISTINCT f.name), collect(DISTINCT {url: a.url, kind: a.kind}),
                   collect(DISTINCT {id: m.id, headline: m.headline, ts: m.ts})
            """,
            {"id": session_id},
        )
        if not rows:
            raise HTTPException(404, "unknown session")
        r = rows[0]
        posts = sorted([p for p in r[9] if p.get("id")], key=lambda p: -(p.get("confidence") or 0))
        return {"id": session_id, "title": r[0], "room": r[1], "start_local": r[2], "end_local": r[3], "day": r[4], "replayable": r[5],
                "url": r[6], "abstract": r[7], "speakers": [s for s in r[8] if s.get("name")], "posts": posts[:40],
                "overlaps": [o for o in r[10] if o.get("id")], "facets": [f for f in r[11] if f], "artifacts": [a for a in r[12] if a.get("url")],
                "moments": [m for m in r[13] if m.get("id")],
                "cypher": "MATCH (s:Session {id:$id})<-[r:ABOUT_SESSION]-(p:Post), (s)-[:OVERLAPS]-(o:Session) RETURN s, p, r, o"}

    return await asyncio.get_running_loop().run_in_executor(None, run)


@app.get("/api/conf/{conf_id}/now")
async def now_playing(conf_id: str, at: int):
    """Sessions in progress at a clock value, with how loud each one is in the last 30 minutes."""
    g = graph(conf_id)

    def run():
        rows = g.ro(
            """
            MATCH (s:Session {conf:$cid})
            WHERE s.start_epoch <= $at AND s.end_epoch >= $at - 600
            OPTIONAL MATCH (p:Post)-[:ABOUT_SESSION]->(s) WHERE p.ts_epoch >= $at - 1800 AND p.ts_epoch <= $at
            RETURN s.id, s.title, s.room, s.start_local, s.end_local, s.replayable, count(p) ORDER BY count(p) DESC, s.room
            """,
            {"cid": conf_id, "at": at},
        )
        return [dict(zip(["id", "title", "room", "start_local", "end_local", "replayable", "posts_30m"], r)) for r in rows]

    return await asyncio.get_running_loop().run_in_executor(None, run)


@app.post("/api/conf/{conf_id}/repo")
async def repo(conf_id: str, body: dict):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, repo_facets, url)
    if info.get("error"):
        raise HTTPException(400, info["error"])
    g = graph(conf_id)
    await loop.run_in_executor(None, g.add_repo, info["url"], info["name"], info["facets"])
    matches = await loop.run_in_executor(None, g.for_repo, info["url"], conf_id)
    return {"repo": info, **matches}


# ----- field notes: people inside the venue reporting for everyone who is not ------------------
def _resolver(conf_id: str):
    if conf_id not in _resolvers:
        from app.agents.resolver import Resolver
        from app.ingest.githubblog import load_announcements
        from app.ingest.rainfocus import load as load_agenda

        sessions, speakers = load_agenda(conf_id)
        r = Resolver(sessions, speakers, load_announcements(conf_id), use_llm=True)
        try:
            r.prepare()
        except Exception:
            r.use_llm = False
        _resolvers[conf_id] = r
    return _resolvers[conf_id]


@app.post("/api/conf/{conf_id}/note")
async def note(conf_id: str, body: dict, request: Request):
    """A one-line report from inside a room. Becomes a Post in the graph, resolved like any other, and is
    pushed to every open console. No account needed: this is the nudge that makes the layer two-sided."""
    import hashlib

    ip = request.client.host if request.client else "unknown"
    if not _note_allowed(ip):
        raise HTTPException(429, "easy. five reports a minute per person is plenty")
    text = _CTRL.sub("", body.get("text") or "")
    text = re.sub(r"\s+", " ", text).strip()[:600]
    if len(text) < 3 or not re.search(r"[A-Za-z]{2,}", text):
        raise HTTPException(400, "say something with words in it")
    handle = (body.get("handle") or "someone in the room").strip()[:60]
    session_id = (body.get("session_id") or "").strip() or None
    ts = datetime.now(timezone.utc)
    meta = timeline(conf_id)["meta"]
    by_session = {s["id"]: s for s in meta["sessions"]}
    pid = "n_" + hashlib.sha1(f"{conf_id}|{ts.isoformat()}|{text}".encode()).hexdigest()[:10]
    post = {"id": pid, "url": "", "platform": "backstage", "handle": handle, "author": handle, "text": text, "ts": ts.isoformat(),
            "ts_exact": True, "day": ts.date().isoformat(), "kind": "note", "hashtags": [], "links": []}
    loop = asyncio.get_running_loop()

    def resolve_and_store():
        r = _resolver(conf_id)
        res = r.resolve_all([post])[0]
        if session_id and session_id in by_session:
            s = by_session[session_id]
            res["sessions"] = [{"session_id": session_id, "confidence": 0.95, "signals": ["reported_from_room"],
                                "reason": f"reported from {s['room'].split('@')[0].strip()} by {handle}"}]
        g = graph(conf_id)
        g.add_post(post)
        for s in res["sessions"]:
            g.link_post_session(pid, s["session_id"], s["confidence"], s["reason"], s["signals"])
        for aid in res["announcements"]:
            g.link_post_announcement(pid, aid)
        if res["facets"]:
            g.tag_post_facets(pid, res["facets"])
        return res

    res = await loop.run_in_executor(None, resolve_and_store)
    ev = {"t": int(ts.timestamp()), "type": "note", "post": post, "resolve": {
        "post_id": pid, "sessions": [{**s, "title": by_session[s["session_id"]]["title"], "room": by_session[s["session_id"]]["room"]} for s in res["sessions"] if s["session_id"] in by_session],
        "mentions": [_resolver(conf_id).speakers[i]["name"] for i in res["mentions"] if i in _resolver(conf_id).speakers],
        "author_of": [], "announcements": res["announcements"], "facets": res["facets"], "artifacts": res["artifacts"]}}
    notes = load_notes(conf_id)
    notes.append(ev)
    notes_path(conf_id).write_bytes(orjson.dumps(notes))
    _live_bus.setdefault(conf_id, []).append(ev)
    return ev


@app.get("/api/conf/{conf_id}/notes")
async def notes(conf_id: str):
    return load_notes(conf_id)[-50:]


# ----- ask ------------------------------------------------------------------------------------
SCHEMA_DOC = """
Nodes: Conference{id,name}, Day{date}, Room{name}, Session{id,title,abstract,type,level,room,day,start,end,start_epoch,end_epoch,start_local,end_local,replayable,virtual,capacity,url,products,tracks},
Speaker{id,name,company,title}, Track{name}, Product{name}, Facet{name}, Author{handle,name,platform}, Post{id,url,platform,text,ts,ts_epoch,day,kind},
Announcement{id,title,url,ts,status,product,summary}, Moment{id,ts,headline,score,kind}, Artifact{url,kind}, Repo{url,name}
Edges: (Conference)-[:HAS_DAY]->(Day)-[:HAS_SESSION]->(Session)-[:IN_ROOM]->(Room); (Session)-[:PRESENTED_BY]->(Speaker); (Session)-[:ON_TRACK]->(Track);
(Session)-[:COVERS]->(Product); (Session)-[:ABOUT]->(Facet); (Session)-[:OVERLAPS]->(Session); (Post)-[:BY]->(Author)-[:IS]->(Speaker);
(Post)-[:ABOUT_SESSION {confidence,reason,signals}]->(Session); (Post)-[:MENTIONS]->(Speaker); (Post)-[:TAGS]->(Facet); (Post)-[:ABOUT_ANNOUNCEMENT]->(Announcement);
(Post)-[:LINKS]->(Artifact)-[:FOR]->(Session); (Announcement)-[:AFFECTS]->(Facet); (Moment)-[:OF]->(Session|Announcement); (Moment)-[:EVIDENCE]->(Post); (Repo)-[:USES]->(Facet)
"""

TEMPLATES = {
    "loudest": ("Which sessions got the most posts?",
                "MATCH (p:Post)-[:ABOUT_SESSION]->(s:Session) RETURN s.title AS session, s.room AS room, s.start_local AS starts, count(p) AS posts ORDER BY posts DESC LIMIT 8"),
    "speakers_posting": ("Which speakers posted about their own session?",
                         "MATCH (p:Post)-[:BY]->(a:Author)-[:IS]->(sp:Speaker)<-[:PRESENTED_BY]-(s:Session)<-[:ABOUT_SESSION]-(p) RETURN sp.name AS speaker, s.title AS session, count(p) AS posts ORDER BY posts DESC LIMIT 10"),
    "conflicts": ("Which sessions overlap with the loudest one?",
                  "MATCH (p:Post)-[:ABOUT_SESSION]->(s:Session) WITH s, count(p) AS n ORDER BY n DESC LIMIT 1 MATCH (s)-[:OVERLAPS]-(o:Session) RETURN s.title AS loudest, o.title AS overlaps_with, o.room AS room, o.replayable AS recorded_later ORDER BY room"),
    "announcement_waves": ("Which announcements did people react to most?",
                           "MATCH (p:Post)-[:ABOUT_ANNOUNCEMENT]->(a:Announcement) RETURN a.title AS announcement, a.status AS status, count(p) AS posts ORDER BY posts DESC LIMIT 8"),
    "mcp": ("Everything about MCP: sessions, announcements and how many people posted",
            "MATCH (f:Facet {id:'mcp'}) OPTIONAL MATCH (s:Session)-[:ABOUT]->(f) OPTIONAL MATCH (a:Announcement)-[:AFFECTS]->(f) OPTIONAL MATCH (p:Post)-[:TAGS]->(f) RETURN collect(DISTINCT s.title)[0..6] AS sessions, collect(DISTINCT a.title)[0..6] AS announcements, count(DISTINCT p) AS posts"),
    "artifacts": ("Which slides, repos and videos were shared, and for which session?",
                  "MATCH (p:Post)-[:LINKS]->(a:Artifact) OPTIONAL MATCH (a)-[:FOR]->(s:Session) RETURN a.kind AS kind, a.url AS url, s.title AS session LIMIT 15"),
    "unrecorded": ("Which sessions were in-person only (not recorded) and still got attention?",
                   "MATCH (p:Post)-[:ABOUT_SESSION]->(s:Session) WHERE s.replayable = false RETURN s.title AS session, s.type AS type, s.room AS room, count(p) AS posts ORDER BY posts DESC LIMIT 8"),
}


@app.get("/api/ask/templates")
async def ask_templates():
    return [{"id": k, "q": v[0]} for k, v in TEMPLATES.items()]


@app.post("/api/conf/{conf_id}/ask")
async def ask(conf_id: str, body: dict):
    q = (body.get("q") or "").strip()
    template = body.get("template")
    g = graph(conf_id)
    loop = asyncio.get_running_loop()

    def run(cy: str):
        res = g.g.ro_query(cy)
        cols = res.header and [h[1] for h in res.header] or []
        rows = [[_plain(v) for v in r] for r in res.result_set[:25]]
        return cols, rows

    def safe(cy: str | None) -> bool:
        return bool(cy) and bool(re.match(r"^\s*MATCH", cy, re.I)) and not re.search(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", cy, re.I)

    if template in TEMPLATES:
        cypher = TEMPLATES[template][1]
        q = TEMPLATES[template][0]
        try:
            cols, rows = await loop.run_in_executor(None, run, cypher)
        except Exception as e:
            return {"q": q, "cypher": cypher, "error": str(e)[:300]}
    elif config.OPENAI_API_KEY and q:
        # generate, run, and repair once or twice if the query errors or comes back empty
        feedback, cypher, cols, rows, err = None, None, [], [], None
        for _ in range(3):
            cypher = await loop.run_in_executor(None, _llm_cypher, q, feedback)
            if not safe(cypher):
                feedback = "That was not a single read-only MATCH query. Output only one MATCH ... RETURN query."
                continue
            try:
                cols, rows = await loop.run_in_executor(None, run, cypher)
                err = None
            except Exception as e:
                err = str(e)[:300]
                feedback = f"That query failed with: {err}. Fix it. Query:\n{cypher}"
                continue
            if rows:
                break
            feedback = (f"That query ran but returned zero rows. Loosen it: use toLower(x) CONTAINS 'word' instead of equality, "
                        f"drop optional constraints, and check edge directions against the schema. Query:\n{cypher}")
        if err:
            return {"q": q, "cypher": cypher, "error": err}
    else:
        raise HTTPException(400, "no question, and no OpenAI key for free-form questions")
    answer = None
    if config.OPENAI_API_KEY and rows:
        answer = await loop.run_in_executor(None, _llm_answer, q, cols, rows)
    return {"q": q, "cypher": cypher, "columns": cols, "rows": rows, "answer": answer}


def _plain(v: Any) -> Any:
    if hasattr(v, "properties"):
        return v.properties
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


FEWSHOT = """
Examples of good queries against this graph:
-- posts in a room, with speakers
MATCH (p:Post)-[:ABOUT_SESSION]->(s:Session) WHERE toLower(s.room) CONTAINS 'bats'
OPTIONAL MATCH (s)-[:PRESENTED_BY]->(sp:Speaker)
RETURN s.title AS session, collect(DISTINCT sp.name) AS speakers, count(DISTINCT p) AS posts ORDER BY posts DESC LIMIT 12
-- what people said about a topic during the keynote
MATCH (p:Post)-[:ABOUT_SESSION]->(s:Session) WHERE toLower(s.title) CONTAINS 'keynote' AND toLower(p.text) CONTAINS 'mcp'
RETURN p.author AS who, p.platform AS platform, left(p.text, 160) AS said, p.ts AS at ORDER BY p.ts LIMIT 12
-- announcements reacted to, with facets
MATCH (p:Post)-[:ABOUT_ANNOUNCEMENT]->(a:Announcement)-[:AFFECTS]->(f:Facet)
RETURN a.title AS announcement, collect(DISTINCT f.name) AS touches, count(DISTINCT p) AS reactions ORDER BY reactions DESC LIMIT 10
Rules: edge directions are exactly as in the schema (Session -[:IN_ROOM]-> Room, Day -[:HAS_SESSION]-> Session, Post -[:BY]-> Author);
sessions also carry s.room and s.day as strings, so prefer string CONTAINS filters over extra hops; always use toLower() for text filters;
no comments in the query; one statement only; no APOC.
"""


def _llm_cypher(q: str, feedback: str | None = None) -> str | None:
    from app.llm import chat

    prompt = (f"You write Cypher for FalkorDB (openCypher).\nSchema:{SCHEMA_DOC}\n{FEWSHOT}\n"
              f"Write ONE read-only MATCH query that answers: \"{q}\". Return at most 12 rows with readable column aliases. Output only the query.")
    if feedback:
        prompt += f"\n\nFeedback on your previous attempt: {feedback}"
    try:
        text = chat(prompt, max_tokens=700)
        text = re.sub(r"^```(?:cypher)?|```$", "", text, flags=re.M).strip()
        text = re.sub(r"//.*$", "", text, flags=re.M).strip()
        return text or None
    except Exception:
        return None


def _llm_answer(q: str, cols: list[str], rows: list[list[Any]]) -> str | None:
    from app.llm import chat

    data = json.dumps({"columns": cols, "rows": rows[:12]}, default=str)[:6000]
    try:
        return chat(f"Question: {q}\nGraph result: {data}\nAnswer in two short sentences, concrete, no preamble.", max_tokens=300) or None
    except Exception:
        return None


# ----- live ingest (Universe 2026 on the day, any conference with hashtags) ------------------
@app.post("/api/conf/{conf_id}/live/pull")
async def live_pull(conf_id: str):
    from app.agents.live import pull_once

    result = await asyncio.get_running_loop().run_in_executor(None, pull_once, conf_id)
    _timelines.pop(conf_id, None)  # reload the appended timeline on next read
    return result


async def _live_loop():
    """While a live-mode conference is in its date window, pull fresh posts every few minutes."""
    from datetime import date

    while True:
        try:
            today = date.today().isoformat()
            for cid, c in config.CONFERENCES.items():
                if c["mode"] == "live" and c["days"][0] <= today <= c["days"][-1] and (config.TIMELINES / f"{cid}.json").exists():
                    await live_pull(cid)
        except Exception:
            pass
        await asyncio.sleep(180)


@app.on_event("startup")
async def _start_live_loop():
    if os.getenv("BACKSTAGE_LIVE", "1") == "1":
        asyncio.create_task(_live_loop())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
