# Backstage

**The live layer for everyone who is not in the room.**

Every conference has eight rooms and you are in one. Every conference has ten thousand people who care and a thousand who came. Hybrid is dying because livestream platforms are "cost-prohibitive" (CSCW 2025), visas are tightening (NeurIPS added a second site over refusals), and recordings land weeks later, unindexed.

Backstage resolves the public internet to the agenda, **as it happens**, into a graph you can question:

- **Moments, not posts.** A spike of reactions in Room 2 at 2:41 becomes a Moment: who, what, links. Walk over now or watch exactly those minutes later.
- **Announcements as actions for your repo.** Every changelog entry and launch post is parsed, tagged with the parts of a stack it touches, and matched against your repo's manifests.
- **Artifacts where they belong.** Slides, repos and demo links posted by speakers attach to their session the minute they appear.
- **A permanent, queryable record.** Ask the graph: which sessions got loudest, which speakers posted about their own talk, what overlapped with the keynote, what shipped that touches `actions`.

Built in one day at **Patch the Conference** (WeMakeDevs × Bright Data, San Francisco, 24 Oct 2026).

## How it works

```
 Bright Data / Exa ──► Listener ──► Resolver ──► Curator ──► Wire
 (posts, SERP,          (raw posts)   (post→session    (moments)   (announcements
  Unlocker, X/LinkedIn/                edges with                    → facets → repos)
  Bluesky/YouTube)                     reasons)
                                 │
                                 ▼
                            FalkorDB graph  ◄──── console asks "why?" (Cypher paths)
```

**Graph (FalkorDB).** `Conference → Day → Session → Room/Speaker/Track/Product/Facet`, `Session -OVERLAPS- Session` (the conflict graph),
`Post -BY→ Author -IS→ Speaker`, `Post -ABOUT_SESSION{confidence, reason, signals}→ Session`, `Post -ABOUT_ANNOUNCEMENT→ Announcement -AFFECTS→ Facet ←USES- Repo`,
`Moment -OF→ Session|Announcement`, `Moment -EVIDENCE→ Post`. Every edge carries the reason it exists; every answer in the UI is a path.

**Resolver signals** (deterministic, explainable): the author is a speaker of the session; the post names a speaker; it quotes the title; semantic similarity to the abstract (OpenAI embeddings, optional); it names a product the session covers; it was posted inside the slot. An LLM only adjudicates between two close candidates.

**Exact timestamps without an API.** Bluesky record keys are TIDs (microseconds since the epoch in sortable base32) and LinkedIn activity IDs carry the millisecond timestamp in their top 41 bits, so the replay is the real minute-by-minute day. Verified against a live Bluesky record to the millisecond.

**Hard-to-get data (Bright Data).** The GitHub Universe catalog is a RainFocus JavaScript app; Bluesky's public AppView returns 403 from ordinary networks; X is pay-per-read and LinkedIn has no public read API. Backstage uses Bright Data's SERP API to discover X posts, the Web Scraper API for structured X records, and the Web Unlocker for Bluesky. Discovery of Bluesky and LinkedIn posts in a date window uses Exa.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                              # FalkorDB host/password, OPENAI_API_KEY, BRIGHTDATA_API_KEY, EXA_API_KEY

python -m app.ingest.rainfocus universe25 universe26   # agenda (151 + 157 sessions)
python -m scripts.build_timeline universe25            # resolve posts, curate moments, write the replay timeline, load FalkorDB
python -m app.server                                   # http://localhost:8080
```

FalkorDB: `docker run -p 6379:6379 -p 3000:3000 falkordb/falkordb:v4.22.2`, or any hosted instance. The demo deployment runs FalkorDB and the app on Fly.io (`infra/falkor/fly.toml`, `fly.toml`).

Live mode: `POST /api/conf/universe26/live/pull` pulls fresh posts (Exa + Bright Data), resolves them with the same agents, appends to the timeline and the graph. The console polls it during the conference.

## Repository map

| Path | What |
|---|---|
| `app/ingest/rainfocus.py` | RainFocus catalog API (GitHub Universe) → sessions, speakers |
| `app/ingest/posts.py` | Post normalization, TID / activity-ID timestamp recovery |
| `app/ingest/brightdata.py` | SERP discovery, X posts dataset, Unlocker for Bluesky |
| `app/ingest/githubblog.py` | Announcements with exact publish times and facets |
| `app/agents/resolver.py` | Post → session / speaker / announcement, with reasons |
| `app/agents/curator.py` | Moments: session spikes, announcement waves, venue surges |
| `app/agents/live.py` | Live pull loop for a conference happening now |
| `app/facets.py` | Stack facets: lexicon + repo manifest reader |
| `app/graph.py` | FalkorDB layer: schema, batched writes, path queries |
| `app/server.py` | FastAPI: SSE replay (mirrored into a live graph), why/session/ask/repo endpoints |
| `app/static/` | The console |
| `scripts/build_timeline.py` | End-to-end build for one conference |

## Honest limits

- Discovery is semantic search over the public web, so recall is partial and a relevance filter drops off-topic hits.
- Without an OpenAI key the resolver runs rules only (author, mention, title, product, time); embeddings roughly double session linking.
- Small conferences are sparse. Backstage is strongest from a few hundred attendees up.
