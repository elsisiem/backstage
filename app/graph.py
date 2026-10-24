"""FalkorDB layer. One graph per conference; every write is a MERGE so replays are idempotent.

The graph is the product: posts are resolved to sessions through multi-hop paths
(Post -> Author -> Speaker -> Session, Post -> Announcement -> Facet <- Repo, ...), and every edge
carries the reason it exists so the UI can show why.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from falkordb import FalkorDB

from app import config


def epoch(iso: str | None) -> int:
    if not iso:
        return 0
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class Graph:
    def __init__(self, name: str):
        self.name = name
        self.db = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, password=config.FALKORDB_PASSWORD)
        self.g = self.db.select_graph(name)

    # ----- plumbing -------------------------------------------------------------------------
    def q(self, cypher: str, params: dict | None = None) -> list[list[Any]]:
        return self.g.query(cypher, params or {}).result_set

    def ro(self, cypher: str, params: dict | None = None) -> list[list[Any]]:
        return self.g.ro_query(cypher, params or {}).result_set

    def exists(self) -> bool:
        return self.name in self.db.list_graphs()

    def delete(self) -> None:
        if self.exists():
            self.g.delete()

    def ensure_indices(self) -> None:
        for label, prop in [
            ("Session", "id"), ("Speaker", "id"), ("Post", "id"), ("Author", "id"), ("Room", "id"),
            ("Facet", "id"), ("Announcement", "id"), ("Moment", "id"), ("Repo", "id"), ("Track", "id"),
            ("Product", "id"), ("Post", "ts_epoch"), ("Session", "start_epoch"),
        ]:
            try:
                self.q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                pass  # already exists

    # ----- agenda ---------------------------------------------------------------------------
    def load_agenda(self, conf: dict, sessions: list[dict], speakers: list[dict]) -> None:
        self.ensure_indices()
        self.q(
            "MERGE (c:Conference {id:$id}) SET c.name=$name, c.venue=$venue, c.days=$days, c.tz=$tz",
            {"id": conf["id"], "name": conf["name"], "venue": conf["venue"], "days": conf["days"], "tz": conf["tz"]},
        )
        for sp in speakers:
            self.q(
                "MERGE (s:Speaker {id:$id}) SET s.name=$name, s.company=$company, s.title=$title, "
                "s.twitter=$twitter, s.linkedin=$linkedin, s.photo=$photo, s.first=$first, s.last=$last",
                {k: sp.get(k, "") for k in ["id", "name", "company", "title", "twitter", "linkedin", "photo", "first", "last"]},
            )
        for s in sessions:
            params = {
                "cid": conf["id"], "id": s["id"], "title": s["title"], "abstract": s["abstract"][:2000], "type": s["type"],
                "level": s["level"], "room": s["room"], "rid": f"{conf['id']}:{s['room']}", "day": s["day"],
                "did": f"{conf['id']}:{s['day']}", "start": s["start_utc"] or "", "end": s["end_utc"] or "",
                "start_epoch": epoch(s["start_utc"]), "end_epoch": epoch(s["end_utc"]), "start_local": s["start_local"],
                "end_local": s["end_local"], "replayable": s["replayable"], "virtual": s["virtual"], "capacity": s["capacity"],
                "url": s["url"], "products": s["products"], "tracks": s["tracks"], "speakers": s["speakers"],
            }
            self.q(
                """
                MATCH (c:Conference {id:$cid})
                MERGE (d:Day {id:$did}) SET d.date=$day
                MERGE (c)-[:HAS_DAY]->(d)
                MERGE (r:Room {id:$rid}) SET r.name=$room
                MERGE (s:Session {id:$id})
                SET s.conf=$cid, s.title=$title, s.abstract=$abstract, s.type=$type, s.level=$level, s.room=$room,
                    s.day=$day, s.start=$start, s.end=$end, s.start_epoch=$start_epoch, s.end_epoch=$end_epoch,
                    s.start_local=$start_local, s.end_local=$end_local, s.replayable=$replayable, s.virtual=$virtual,
                    s.capacity=$capacity, s.url=$url, s.products=$products, s.tracks=$tracks
                MERGE (d)-[:HAS_SESSION]->(s)
                MERGE (s)-[:IN_ROOM]->(r)
                """,
                params,
            )
            for spid in s["speakers"]:
                self.q("MATCH (s:Session {id:$id}),(sp:Speaker {id:$sp}) MERGE (s)-[:PRESENTED_BY]->(sp)", {"id": s["id"], "sp": spid})
            for t in s["tracks"]:
                self.q("MATCH (s:Session {id:$id}) MERGE (t:Track {id:$tid}) SET t.name=$t MERGE (s)-[:ON_TRACK]->(t)",
                       {"id": s["id"], "tid": f"{conf['id']}:{t}", "t": t})
            for p in s["products"]:
                self.q("MATCH (s:Session {id:$id}) MERGE (p:Product {id:$pid}) SET p.name=$p MERGE (s)-[:COVERS]->(p)",
                       {"id": s["id"], "pid": p.lower(), "p": p})
        # time overlaps inside the conference (the conflict graph)
        self.q(
            """
            MATCH (a:Session {conf:$cid}),(b:Session {conf:$cid})
            WHERE a.id < b.id AND a.start_epoch > 0 AND b.start_epoch > 0
              AND a.start_epoch < b.end_epoch AND b.start_epoch < a.end_epoch
            MERGE (a)-[:OVERLAPS]->(b)
            """,
            {"cid": conf["id"]},
        )

    def tag_session_facets(self, session_id: str, facets: list[str]) -> None:
        for f in facets:
            self.q("MATCH (s:Session {id:$id}) MERGE (f:Facet {id:$f}) SET f.name=$f MERGE (s)-[:ABOUT]->(f)", {"id": session_id, "f": f})

    # ----- posts ----------------------------------------------------------------------------
    def add_post(self, p: dict) -> None:
        self.q(
            """
            MERGE (po:Post {id:$id})
            SET po.url=$url, po.platform=$platform, po.text=$text, po.ts=$ts, po.ts_epoch=$ts_epoch, po.exact=$exact,
                po.day=$day, po.kind=$kind, po.title=$title, po.hashtags=$hashtags
            MERGE (a:Author {id:$aid}) SET a.handle=$handle, a.name=$author, a.platform=$platform
            MERGE (po)-[:BY]->(a)
            """,
            {
                "id": p["id"], "url": p["url"], "platform": p["platform"], "text": p["text"], "ts": p.get("ts") or "",
                "ts_epoch": epoch(p.get("ts")), "exact": bool(p.get("ts_exact")), "day": p.get("day") or "",
                "kind": p.get("kind") or "post", "title": p.get("title") or "", "hashtags": p.get("hashtags") or [],
                "aid": f"{p['platform']}:{(p.get('handle') or p.get('author') or 'unknown').lower()}",
                "handle": p.get("handle") or "", "author": p.get("author") or "",
            },
        )

    def link_post_session(self, post_id: str, session_id: str, confidence: float, reason: str, signals: list[str]) -> None:
        self.q(
            "MATCH (p:Post {id:$pid}),(s:Session {id:$sid}) MERGE (p)-[r:ABOUT_SESSION]->(s) "
            "SET r.confidence=$c, r.reason=$reason, r.signals=$signals",
            {"pid": post_id, "sid": session_id, "c": confidence, "reason": reason, "signals": signals},
        )

    def link_author_speaker(self, post_id: str, speaker_id: str) -> None:
        self.q("MATCH (p:Post {id:$pid})-[:BY]->(a:Author), (sp:Speaker {id:$sid}) MERGE (a)-[:IS]->(sp)", {"pid": post_id, "sid": speaker_id})

    def link_post_speaker(self, post_id: str, speaker_id: str) -> None:
        self.q("MATCH (p:Post {id:$pid}),(sp:Speaker {id:$sid}) MERGE (p)-[:MENTIONS]->(sp)", {"pid": post_id, "sid": speaker_id})

    def tag_post_facets(self, post_id: str, facets: list[str]) -> None:
        for f in facets:
            self.q("MATCH (p:Post {id:$id}) MERGE (f:Facet {id:$f}) SET f.name=$f MERGE (p)-[:TAGS]->(f)", {"id": post_id, "f": f})

    def link_post_announcement(self, post_id: str, ann_id: str) -> None:
        self.q("MATCH (p:Post {id:$pid}),(a:Announcement {id:$aid}) MERGE (p)-[:ABOUT_ANNOUNCEMENT]->(a)", {"pid": post_id, "aid": ann_id})

    def add_artifact(self, post_id: str, url: str, kind: str, session_id: str | None) -> None:
        self.q("MATCH (p:Post {id:$pid}) MERGE (a:Artifact {url:$url}) SET a.kind=$kind MERGE (p)-[:LINKS]->(a)",
               {"pid": post_id, "url": url, "kind": kind})
        if session_id:
            self.q("MATCH (a:Artifact {url:$url}),(s:Session {id:$sid}) MERGE (a)-[:FOR]->(s)", {"url": url, "sid": session_id})

    # ----- announcements / moments ----------------------------------------------------------
    def add_announcement(self, a: dict) -> None:
        self.q(
            """
            MERGE (an:Announcement {id:$id})
            SET an.title=$title, an.url=$url, an.ts=$ts, an.ts_epoch=$ts_epoch, an.status=$status, an.product=$product,
                an.summary=$summary, an.kind=$kind
            """,
            {"id": a["id"], "title": a["title"], "url": a["url"], "ts": a.get("ts") or "", "ts_epoch": epoch(a.get("ts")),
             "status": a.get("status") or "", "product": a.get("product") or "", "summary": (a.get("summary") or "")[:800],
             "kind": a.get("kind") or "changelog"},
        )
        for f in a.get("facets", []):
            self.q("MATCH (a:Announcement {id:$id}) MERGE (f:Facet {id:$f}) SET f.name=$f MERGE (a)-[:AFFECTS]->(f)", {"id": a["id"], "f": f})

    def add_moment(self, m: dict) -> None:
        self.q(
            "MERGE (m:Moment {id:$id}) SET m.ts=$ts, m.ts_epoch=$ts_epoch, m.headline=$headline, m.score=$score, m.kind=$kind",
            {"id": m["id"], "ts": m["ts"], "ts_epoch": epoch(m["ts"]), "headline": m["headline"], "score": m["score"], "kind": m.get("kind", "spike")},
        )
        if m.get("session_id"):
            self.q("MATCH (m:Moment {id:$id}),(s:Session {id:$sid}) MERGE (m)-[:OF]->(s)", {"id": m["id"], "sid": m["session_id"]})
        if m.get("announcement_id"):
            self.q("MATCH (m:Moment {id:$id}),(a:Announcement {id:$aid}) MERGE (m)-[:OF]->(a)", {"id": m["id"], "aid": m["announcement_id"]})
        for pid in m.get("evidence", []):
            self.q("MATCH (m:Moment {id:$id}),(p:Post {id:$pid}) MERGE (m)-[:EVIDENCE]->(p)", {"id": m["id"], "pid": pid})

    # ----- repos ----------------------------------------------------------------------------
    def add_repo(self, url: str, name: str, facets: list[str]) -> None:
        self.q("MERGE (r:Repo {id:$id}) SET r.url=$url, r.name=$name", {"id": url.lower(), "url": url, "name": name})
        for f in facets:
            self.q("MATCH (r:Repo {id:$id}) MERGE (f:Facet {id:$f}) SET f.name=$f MERGE (r)-[:USES]->(f)", {"id": url.lower(), "f": f})

    # ----- questions the UI asks ------------------------------------------------------------
    def why_post(self, post_id: str) -> dict:
        """Everything the graph knows about why a post sits where it sits."""
        rows = self.ro(
            """
            MATCH (p:Post {id:$id})-[r:ABOUT_SESSION]->(s:Session)
            OPTIONAL MATCH (s)-[:PRESENTED_BY]->(sp:Speaker)
            OPTIONAL MATCH (p)-[:BY]->(a:Author)-[:IS]->(sp2:Speaker)
            RETURN s.id, s.title, s.room, s.start_local, r.confidence, r.reason, r.signals,
                   collect(DISTINCT sp.name), collect(DISTINCT sp2.name)
            """,
            {"id": post_id},
        )
        out = {"post_id": post_id, "sessions": []}
        for row in rows:
            out["sessions"].append({
                "session_id": row[0], "title": row[1], "room": row[2], "start_local": row[3], "confidence": row[4],
                "reason": row[5], "signals": row[6], "speakers": row[7], "author_is_speaker": row[8],
            })
        out["mentions"] = [r[0] for r in self.ro("MATCH (p:Post {id:$id})-[:MENTIONS]->(sp:Speaker) RETURN sp.name", {"id": post_id})]
        out["facets"] = [r[0] for r in self.ro("MATCH (p:Post {id:$id})-[:TAGS]->(f:Facet) RETURN f.name", {"id": post_id})]
        out["announcements"] = [r[0] for r in self.ro("MATCH (p:Post {id:$id})-[:ABOUT_ANNOUNCEMENT]->(a:Announcement) RETURN a.title", {"id": post_id})]
        out["cypher"] = "MATCH (p:Post {id:$id})-[r:ABOUT_SESSION]->(s:Session)-[:PRESENTED_BY]->(sp:Speaker) RETURN p, r, s, sp"
        return out

    def for_repo(self, repo_url: str, conf_id: str) -> dict:
        """Two multi-hop questions: which sessions and which announcements touch this repo's stack."""
        sessions = self.ro(
            """
            MATCH (r:Repo {id:$rid})-[:USES]->(f:Facet)<-[:ABOUT]-(s:Session {conf:$cid})
            WITH s, collect(DISTINCT f.name) AS via
            OPTIONAL MATCH (s)-[:PRESENTED_BY]->(sp:Speaker)
            OPTIONAL MATCH (p:Post)-[:ABOUT_SESSION]->(s)
            RETURN s.id, s.title, s.room, s.day, s.start_local, s.replayable, via, collect(DISTINCT sp.name), count(DISTINCT p)
            ORDER BY size(via) DESC, count(DISTINCT p) DESC LIMIT 12
            """,
            {"rid": repo_url.lower(), "cid": conf_id},
        )
        anns = self.ro(
            """
            MATCH (r:Repo {id:$rid})-[:USES]->(f:Facet)<-[:AFFECTS]-(a:Announcement)
            WITH a, collect(DISTINCT f.name) AS via
            OPTIONAL MATCH (p:Post)-[:ABOUT_ANNOUNCEMENT]->(a)
            RETURN a.id, a.title, a.url, a.status, a.ts, via, count(DISTINCT p)
            ORDER BY size(via) DESC, count(DISTINCT p) DESC LIMIT 12
            """,
            {"rid": repo_url.lower()},
        )
        return {
            "sessions": [dict(zip(["id", "title", "room", "day", "start_local", "replayable", "via", "speakers", "posts"], r)) for r in sessions],
            "announcements": [dict(zip(["id", "title", "url", "status", "ts", "via", "posts"], r)) for r in anns],
            "cypher": "MATCH (r:Repo)-[:USES]->(f:Facet)<-[:ABOUT]-(s:Session) RETURN s, collect(f)   /   MATCH (r:Repo)-[:USES]->(f:Facet)<-[:AFFECTS]-(a:Announcement) RETURN a, collect(f)",
        }

    def stats(self) -> dict:
        rows = self.ro("MATCH (n) RETURN labels(n)[0] AS l, count(n) ORDER BY l")
        edges = self.ro("MATCH ()-[r]->() RETURN type(r), count(r) ORDER BY type(r)")
        return {"nodes": {r[0]: r[1] for r in rows}, "edges": {r[0]: r[1] for r in edges}}
