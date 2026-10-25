"""Resolver agent: decides where each post belongs in the conference graph and why.

Signals (all explainable, all written onto the edge):
  author     the post's author is a speaker of the session            (name / handle match)
  mention    the post names a speaker of the session
  title      the post quotes or closely paraphrases the session title
  semantic   embedding similarity between post and session abstract    (OpenAI, optional)
  product    the post names a product the session covers
  time       the post was published inside or right after the slot     (exact timestamps only)
An LLM adjudicates only when two sessions are close; everything else is deterministic.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from app import config
from app.facets import facets_for_text

THRESHOLD = 0.42


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


class Resolver:
    def __init__(self, sessions: list[dict], speakers: list[dict], announcements: list[dict], use_llm: bool = True):
        self.sessions = sessions
        self.speakers = {s["id"]: s for s in speakers}
        self.announcements = announcements
        self.by_id = {s["id"]: s for s in sessions}
        self.session_speakers = {s["id"]: [self.speakers[i] for i in s["speakers"] if i in self.speakers] for s in sessions}
        self.use_llm = bool(use_llm and config.OPENAI_API_KEY)
        self._client = None
        self._emb: dict[str, list[float]] = {}
        self.llm_calls = 0

    # ----- optional OpenAI ----------------------------------------------------------------
    @property
    def client(self):
        if self._client is None and self.use_llm:
            from openai import OpenAI
            self._client = OpenAI(api_key=config.OPENAI_API_KEY)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.use_llm:
            return [[] for _ in texts]
        out: list[list[float]] = []
        batch = 96
        for i in range(0, len(texts), batch):
            chunk = [t[:2000] or " " for t in texts[i:i + batch]]
            r = self.client.embeddings.create(model=config.OPENAI_EMBED_MODEL, input=chunk)
            out += [d.embedding for d in r.data]
        return out

    def prepare(self) -> None:
        if not self.use_llm:
            return
        texts = [f"{s['title']}. {s['abstract'][:800]}" for s in self.sessions]
        for s, e in zip(self.sessions, self.embed(texts)):
            self._emb[s["id"]] = e

    @staticmethod
    def _cos(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        num = sum(x * y for x, y in zip(a, b))
        den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return num / den if den else 0.0

    # ----- signal helpers -----------------------------------------------------------------
    def author_matches(self, post: dict, sp: dict) -> bool:
        name = _norm(post.get("author") or "")
        handle = (post.get("handle") or "").lower()
        spname = _norm(sp.get("name"))
        if name and spname and fuzz.token_set_ratio(name, spname) >= 92:
            return True
        if handle:
            tw = (sp.get("twitter") or "").lower()
            if tw and handle == tw:
                return True
            li = (sp.get("linkedin") or "").lower()
            if li and handle and handle in li:
                return True
            last = _norm(sp.get("last")).replace(" ", "")
            first = _norm(sp.get("first")).replace(" ", "")
            h = re.sub(r"[^a-z0-9]", "", handle.split(".")[0])
            if last and len(last) >= 5 and last in h and (first[:3] in h if first else True):
                return True
        return False

    def mentioned(self, text_norm: str, sp: dict) -> bool:
        spname = _norm(sp.get("name")).strip()
        return bool(spname) and len(spname) > 6 and f" {spname} " in f" {text_norm} "

    @staticmethod
    def in_window(post: dict, s: dict) -> float:
        if not post.get("ts_exact") or not s.get("start_utc"):
            return 0.0
        t = datetime.fromisoformat(post["ts"]).timestamp()
        a = datetime.fromisoformat(s["start_utc"]).timestamp() - 20 * 60
        b = datetime.fromisoformat(s["end_utc"]).timestamp() + 120 * 60
        if a <= t <= b:
            return 1.0
        return 0.3 if post.get("day") == s.get("day") else 0.0

    # ----- main ---------------------------------------------------------------------------
    def score(self, post: dict, pemb: list[float]) -> list[dict]:
        text = post.get("text") or ""
        tn = _norm(text)
        out = []
        for s in self.sessions:
            sig: dict[str, float] = {}
            sps = self.session_speakers.get(s["id"], [])
            if any(self.author_matches(post, sp) for sp in sps):
                sig["author"] = 1.0
            if any(self.mentioned(tn, sp) for sp in sps):
                sig["mention"] = 1.0
            title = _norm(s["title"]).strip()
            if len(title) >= 18:
                pr = fuzz.partial_ratio(title, tn) / 100
                if title in tn:
                    sig["title"] = 1.0
                elif pr >= 0.72 and len(title) >= 25:
                    sig["title"] = round((pr - 0.6) / 0.4, 2)
            if pemb and self._emb.get(s["id"]):
                c = self._cos(pemb, self._emb[s["id"]])
                if c >= 0.42:
                    sig["semantic"] = round(min(1.0, (c - 0.35) / 0.3), 2)
            prods = [p for p in s.get("products", []) if len(p) > 3 and _norm(p).strip() in tn]
            if prods and len(prods) >= 1 and any(p.lower() not in ("github", "copilot") for p in prods):
                sig["product"] = 0.5
            # stage keywords: "keynote" / "main stage" posts inside the keynote slot belong to the keynote
            if ("keynote" in title or s.get("type") == "Keynote") and ("keynote" in tn or "main stage" in tn or "opening" in tn) and self.in_window(post, s) == 1.0:
                sig["keyword"] = 1.0
            score = (0.5 * sig.get("author", 0) + 0.35 * sig.get("mention", 0) + 0.35 * sig.get("title", 0) + 0.35 * sig.get("semantic", 0)
                     + 0.12 * sig.get("product", 0) + 0.4 * sig.get("keyword", 0))
            if score > 0.15:
                tw = self.in_window(post, s)
                if tw:
                    sig["time"] = tw
                    score += 0.15 * tw
            if score >= 0.2:
                out.append({"session_id": s["id"], "score": round(min(score, 1.0), 3), "signals": sig})
        out.sort(key=lambda r: -r["score"])
        return out[:5]

    def adjudicate(self, post: dict, cands: list[dict]) -> str | None:
        """Ask the LLM which of two close candidates the post is about (or neither)."""
        if not self.use_llm:
            return None
        opts = "\n".join(f"{i+1}. {self.by_id[c['session_id']]['title']} — speakers: {', '.join(sp['name'] for sp in self.session_speakers[c['session_id']])}" for i, c in enumerate(cands))
        prompt = (f"A social post from a conference:\n\"\"\"{post['text'][:900]}\"\"\"\n\nWhich session is it about?\n{opts}\n0. none of these\n"
                  "Answer with the number only.")
        try:
            from app.llm import chat

            self.llm_calls += 1
            n = int((re.findall(r"\d+", chat(prompt, max_tokens=40)) or ["0"])[0])
            return cands[n - 1]["session_id"] if 1 <= n <= len(cands) else None
        except Exception:
            return None

    def reason_text(self, post: dict, s: dict, sig: dict) -> str:
        parts = []
        sps = self.session_speakers.get(s["id"], [])
        if "author" in sig:
            who = next((sp["name"] for sp in sps if self.author_matches(post, sp)), "the author")
            parts.append(f"author {who} is a speaker of this session")
        if "mention" in sig:
            who = [sp["name"] for sp in sps if self.mentioned(_norm(post.get("text")), sp)]
            parts.append(f"names {', '.join(who)}")
        if "title" in sig:
            parts.append("quotes the session title" if sig["title"] >= 1 else f"paraphrases the title ({int(sig['title']*100)}%)")
        if "semantic" in sig:
            parts.append(f"semantically close to the abstract ({int(sig['semantic']*100)}%)")
        if "product" in sig:
            parts.append("names a product the session covers")
        if "keyword" in sig:
            parts.append("talks about the keynote while it is on stage")
        if "on_stage" in sig:
            parts.append("reacts to an announcement made on this stage")
        if sig.get("time") == 1.0 and post.get("ts_exact"):
            t = datetime.fromisoformat(post["ts"]); st = datetime.fromisoformat(s["start_utc"])
            dm = int((t - st).total_seconds() // 60)
            parts.append(f"posted {abs(dm)} min {'after the start' if dm >= 0 else 'before the start'} of the slot")
        return "; ".join(parts) or "weak match"

    def resolve_all(self, posts: list[dict]) -> list[dict]:
        """Return a list of resolution records, one per post."""
        self.prepare()
        pembs = self.embed([p["text"] for p in posts]) if self.use_llm else [[] for _ in posts]
        ann_phrases = [(a, [kp for kp in a.get("keyphrases", []) if len(kp) >= 6]) for a in self.announcements]
        results = []
        for post, pemb in zip(posts, pembs):
            tn = _norm(post.get("text"))
            cands = self.score(post, pemb)
            chosen = []
            if cands and cands[0]["score"] >= THRESHOLD:
                top = cands[0]
                second = cands[1] if len(cands) > 1 else None
                if second and second["score"] >= THRESHOLD and top["score"] - second["score"] < 0.12 and "author" not in top["signals"]:
                    pick = self.adjudicate(post, [top, second])
                    if pick:
                        chosen = [c for c in cands[:2] if c["session_id"] == pick]
                        chosen[0]["signals"]["llm"] = 1.0
                    else:
                        chosen = [top]
                else:
                    chosen = [top]
                # a speaker-authored post can legitimately be about more than one of their sessions
                if "author" in top["signals"]:
                    chosen += [c for c in cands[1:3] if "author" in c["signals"] and c["score"] >= THRESHOLD]
            mentions = sorted({sp["id"] for s in self.sessions for sp in self.session_speakers[s["id"]] if self.mentioned(tn, sp)})
            author_of = sorted({sp["id"] for s in self.sessions for sp in self.session_speakers[s["id"]] if self.author_matches(post, sp)})
            # announcements: longest distinctive phrase wins; at most two per post
            hits = []
            for a, phrases in ann_phrases:
                best = max((len(ph) for ph in phrases if f" {ph} " in f" {tn} "), default=0)
                if best:
                    hits.append((best, a["id"]))
            anns = [aid for _, aid in sorted(hits, reverse=True)[:2]]
            # announcements made from the stage: a post reacting to one, inside that keynote's slot, is about the keynote too
            if anns and not chosen and post.get("ts_exact"):
                for s in self.sessions:
                    if ("keynote" in _norm(s["title"]) or s.get("type") == "Keynote") and self.in_window(post, s) == 1.0:
                        a_ts = {a["id"]: a["ts"] for a in self.announcements}
                        if any(abs(datetime.fromisoformat(a_ts[aid]).timestamp() - datetime.fromisoformat(s["start_utc"]).timestamp()) < 3 * 3600 for aid in anns if aid in a_ts):
                            chosen = [{"session_id": s["id"], "score": 0.55, "signals": {"on_stage": 1.0, "time": 1.0}}]
                        break
            links = []
            for u in post.get("links", []):
                ul = u.lower()
                kind = "repo" if "github.com/" in ul and "/pull/" not in ul else "slides" if any(k in ul for k in ("speakerdeck", "slideshare", "slides", ".pdf")) else "video" if any(k in ul for k in ("youtu", "vimeo")) else "link"
                if kind != "link":
                    links.append({"url": u, "kind": kind})
            kind = self.kind(post, bool(chosen), bool(anns))
            results.append({
                "post_id": post["id"],
                "sessions": [{"session_id": c["session_id"], "confidence": c["score"], "signals": sorted(c["signals"]),
                              "reason": self.reason_text(post, self.by_id[c["session_id"]], c["signals"])} for c in chosen],
                "mentions": mentions, "author_of": author_of, "announcements": anns[:3],
                "facets": facets_for_text(post.get("text") or "", limit=4), "artifacts": links[:4], "kind": kind,
            })
        return results

    @staticmethod
    def kind(post: dict, has_session: bool, has_ann: bool) -> str:
        t = (post.get("text") or "").lower()
        if "?" in t and len(t) < 400 and any(w in t for w in ("anyone", "how do", "does ", "why ", "what ")):
            return "question"
        if has_ann or any(w in t for w in ("announc", "introducing", "now available", "generally available", "public preview", "launch")):
            return "announcement"
        if any(w in t for w in ("recap", "takeaways", "day 1", "day 2", "wrap", "summary", "highlights")):
            return "recap"
        if any(w in t for w in ("slides", "repo", "github.com/", "demo link", "code is")) and post.get("links"):
            return "artifact"
        if has_session:
            return "reaction"
        return "post"
