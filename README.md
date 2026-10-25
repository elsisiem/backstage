<p align="center">
  <img src="app/static/logo.svg" width="64" height="64" alt="Backstage">
</p>

<h1 align="center">Backstage</h1>

<p align="center"><strong>The live layer for everyone who is not in the room.</strong><br>
The public internet, resolved to a conference agenda as it happens, into a graph you can question.</p>

<p align="center">
  <a href="https://backstage-live.fly.dev">backstage-live.fly.dev</a> &nbsp;·&nbsp;
  <a href="https://backstage-live.fly.dev/console?tour=1">guided demo</a> &nbsp;·&nbsp;
  <a href="https://backstage-live.fly.dev/console?play=keynote&speed=120">replay Universe 2025</a>
</p>

<p align="center"><img src="app/static/docs/console.png" width="920" alt="The Backstage console mid-replay: rooms on the left, the graph in the middle, the agent log on the right"></p>

---

A conference has eight rooms and you are in one. It has ten thousand people who care and a thousand who came. Organizers are cutting remote access because livestreaming has become cost-prohibitive, research conferences are opening second sites because speakers cannot get visas, and recordings land weeks later without an index. Inside the venue the problem is the same in miniature: at any minute every attendee is outside seven of eight rooms.

Backstage reads the public record of a conference while it is happening, decides which session every post is about, and keeps the result in a graph. From that graph it answers the questions the people outside the room actually have: which room is loud right now, what just shipped that touches my repository, and which minute did I miss.

## What it does

**Moments, not posts.** A spike of reactions in one room, an announcement starting to land, the whole venue posting at once. Each Moment carries its evidence and its time, so you can walk over now or jump to those minutes in the recording later.

**Announcements as actions for your repository.** Every changelog entry and launch post is parsed and tagged with the parts of a stack it touches. Paste a repository and the graph answers which of today's announcements change something you depend on, and which sessions are yours.

**A line back from inside.** Anyone in a room can send a single sentence, no account needed. It is resolved and attributed like any post and reaches every open console the moment it lands. That is what makes the layer two-sided.

**Why, not just what.** Every placement in the graph carries the signals that produced it. Click a post and the console shows the path: the author is the speaker, the post landed twelve minutes into the slot, it reacts to an announcement made on that stage. Every question in the console compiles to read-only Cypher that is shown with the answer.

## How it works

```
 public internet ──► Listener ──► Resolver ──► Curator ──► Wire ──► you
 Bluesky · LinkedIn    posts with    post → session   Moments     announcements
 X · github.blog       exact times   with reasons                  → facets → repo
                                          │
                                          ▼
                                   FalkorDB graph  ◄── the console asks "why" (Cypher paths)
```

Four agents share one FalkorDB graph.

| Agent | Writes | How |
|---|---|---|
| Listener | `Post`, `Author` | Discovery through Exa and Bright Data; exact timestamps recovered from platform post IDs |
| Resolver | `Post -ABOUT_SESSION{confidence, reason, signals}-> Session` | Author is a speaker, names a speaker, quotes the title, embedding similarity, names a product, inside the slot, said on stage. An LLM adjudicates only between two close candidates |
| Curator | `Moment -OF-> Session \| Announcement`, `Moment -EVIDENCE-> Post` | Session spikes, announcement waves, venue-wide surges |
| Wire | `Announcement -AFFECTS-> Facet` | Changelog and launch posts with exact publish times, tagged by stack facet |

The agenda itself is `Conference → Day → Session → Room / Speaker / Track / Product / Facet`, with `Session -OVERLAPS- Session` as the conflict graph. Repositories enter as `Repo -USES-> Facet`, so "what touches my stack" is a two-hop path.

**Exact timestamps without an API.** Bluesky record keys are TIDs: microseconds since the epoch in sortable base32. LinkedIn activity IDs carry the millisecond timestamp in their top 41 bits. The replay is therefore the real minute-by-minute day, verified against a live Bluesky record to the millisecond.

**Hard-to-get data.** The GitHub Universe catalog is a RainFocus JavaScript application with no public endpoint. Bluesky's public AppView returns 403 from ordinary networks. X is pay-per-read and LinkedIn has no public read API. Backstage uses Bright Data's SERP API to discover X posts, the Web Scraper API for structured records, and the Web Unlocker for Bluesky; Exa handles Bluesky and LinkedIn discovery inside a date window.

## The console

<p align="center"><img src="app/static/docs/console-dark.png" width="920" alt="The console in dark mode"></p>

- **Right now across the venue.** One card that summarizes every room before you look at any of them.
- **Rooms, right now.** Live only or on-demand later, straight from the catalog; the bar is public signal in the last thirty minutes.
- **The graph.** Builds itself as the day unfolds and is mirrored into FalkorDB while it does. Expand it to the whole viewport.
- **Agents, Moments, Wire, Ask.** The log is the agents' reasoning; Moments and Wire are their outputs; Ask compiles plain questions to Cypher.
- **Your Backstage.** A repository becomes facets; facets reach announcements and sessions.
- **Report from the room.** The one line back.
- **Guided tour.** Nine steps that spotlight each part of the console in order. `?tour=1`, the sidebar, or the `?` key.

Light and dark themes, keyboard control (`space` play/pause, `esc` close, `?` tour), and deep links for demos: `/console?play=keynote&speed=120`.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # FalkorDB host and password, OPENAI_API_KEY, BRIGHTDATA_API_KEY, EXA_API_KEY

python -m app.ingest.rainfocus universe25 universe26  # agenda: 151 + 157 sessions
python -m scripts.build_timeline universe25           # resolve posts, curate moments, load FalkorDB, write the replay timeline
python -m app.server                                  # http://localhost:8080
```

FalkorDB: `docker run -p 6379:6379 -p 3000:3000 falkordb/falkordb:v4.22.2`, or any hosted instance. The demo deployment runs FalkorDB and the app on Fly.io (`infra/falkor/fly.toml`, `fly.toml`).

Live mode: while a live-mode conference is inside its dates, the server pulls fresh posts every three minutes (`app/agents/live.py`), resolves them with the same agents, and appends to the timeline and the graph. GitHub Universe 2026 is loaded and armed for 28 October.

Without an OpenAI key the Resolver runs on deterministic signals only and free-form questions are disabled; templated questions still work. Without a Bright Data key, X and Bluesky harvesting are skipped and discovery runs on Exa alone.

## Repository

| Path | What |
|---|---|
| `app/ingest/rainfocus.py` | RainFocus catalog API → sessions, speakers |
| `app/ingest/posts.py` | Post normalization, TID and activity-ID timestamp recovery |
| `app/ingest/brightdata.py` | SERP discovery, X posts dataset, Unlocker for Bluesky |
| `app/ingest/githubblog.py` | Announcements with exact publish times and facets |
| `app/agents/resolver.py` | Post → session, speaker, announcement, with reasons |
| `app/agents/curator.py` | Moments: spikes, waves, surges |
| `app/agents/live.py` | Live pull for a conference happening now |
| `app/facets.py` | Stack facets: lexicon and repository manifest reader |
| `app/graph.py` | FalkorDB layer: schema, batched writes, path queries |
| `app/server.py` | FastAPI: SSE replay mirrored into a live graph, why / session / ask / repo / note endpoints |
| `app/static/` | Landing page and console |
| `scripts/build_timeline.py` | End-to-end build for one conference |
| `DEMO.md` | The three-minute script |

## Limits

Discovery is semantic search over the public web, so recall is partial and a relevance filter drops off-topic hits. Resolution is only as good as its signals; confidence and reason sit on every edge so a wrong placement is visible rather than silent. Small conferences are sparse; Backstage is strongest from a few hundred attendees up.

Built in one day at Patch the Conference, San Francisco, 24 October 2026. Graph: FalkorDB. Hard-to-get data: Bright Data. Discovery: Exa.
