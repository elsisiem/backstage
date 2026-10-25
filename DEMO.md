# Demo script (under three minutes)

Open `https://backstage-live.fly.dev/console?play=keynote&speed=120` on the projector. The console starts at 8:55 AM on Day 1 of GitHub Universe 2025 and runs at two hours per minute.

**0:00 · The problem (15 s).** "Every conference has eight rooms and you are in one. Every conference has ten thousand people who care and a thousand who came. Hybrid is dying because livestreams are cost-prohibitive, visas are tightening, and recordings land weeks later. Backstage is the live layer for everyone who is not in the room."

**0:15 · The record is real (20 s).** Point at the clock. "This is last year's Universe, minute by minute. No API gave us this: Bluesky post keys and LinkedIn activity IDs encode their creation time, so we recovered exact timestamps for 600 posts. The agenda is the RainFocus catalog, 151 sessions, which is a JavaScript app with no public endpoint."

**0:35 · Watch the graph build (30 s).** "Left: every room, right now, and how loud it is. Middle: the FalkorDB graph growing as posts arrive. Right: four agents. Listener ingests. Resolver decides where a post belongs and writes the reason onto the edge. Wire parses announcements as they ship and tags the parts of a stack they touch. Curator turns spikes into Moments." Let the keynote wave hit around 9:10. "Twenty people are posting about the keynote. That is a Moment: walk over, or jump to that minute of the recording tonight."

**1:05 · Why, not just what (30 s).** Click a post node under the keynote. "Every placement is a path. This post sits under the Opening Keynote because its author is the speaker, it was posted twelve minutes into the slot, and it reacts to an announcement made on that stage. Here is the Cypher." Click the session. "Overlaps: the conflict graph. Live-only versus on-demand, straight from the catalog."

**1:35 · Your stack (30 s).** Scroll to Your Backstage. Paste `https://github.com/falkordb/falkordb`, press Build mine. "Manifests become facets: Actions workflows, a devcontainer, Cargo. Repo to Facet to Announcement: these shipped today and touch you. Repo to Facet to Session: these talks, in these rooms, are yours. Two hops in FalkorDB, with the evidence."

**2:05 · Ask the graph (20 s).** Ask tab, click "Which speakers posted about their own session?" then "Which sessions overlap with the loudest one?". "Templated and free-form questions compile to read-only Cypher."

**2:25 · Hard data, live Wednesday (20 s).** "Discovery runs on Exa; the hard parts run on Bright Data: SERP to find X posts, the Web Scraper for structured records, the Unlocker because Bluesky's own API blocks ordinary networks. Universe 2026 is loaded, 157 sessions, and the live loop starts pulling when the doors open Wednesday. Use it from your seat."

**2:45 · Close (10 s).** "Backstage: the live layer for everyone who is not in the room. Any conference, zero organizer effort, a graph you can question."

## Judge questions to expect

- *Why a graph?* Every answer is a multi-hop path (Post → Author → Speaker → Session; Repo → Facet → Announcement). The conflict graph (OVERLAPS) and the live mirror are native graph operations.
- *How accurate is resolution?* Rules first (author, mention, title, product, time, stage), embeddings and an LLM adjudicator when a key is set. Confidence and reason live on the edge. A wrong placement is one click away from a correction.
- *What about small conferences?* Sparse. Backstage is strongest from a few hundred attendees up, and the agenda layer works for any size.
