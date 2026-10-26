# Demo script (three minutes)

Light theme, browser full screen. Two tabs ready: `https://backstage-live.fly.dev/tour` and `https://backstage-live.fly.dev/console?play=keynote&speed=120`.

**0:00 · Landing (15 s).** Open `https://backstage-live.fly.dev`. "A conference runs eight rooms at once, and most people who care about it are not in any of them. The day is narrated in public by the people who are. Backstage reads that record as it is written and keeps it in a graph." Click **Take the tour**.

**0:15 · Tour, steps 01 and 02 (30 s).** Let it autoplay. "This is one real post. Kyle Daigle, who spoke at the opening keynote, on LinkedIn at 10:06. Discovery gives us a date and no time. The Listener reads the time out of the post ID itself: LinkedIn activity IDs carry the millisecond in their top 41 bits." Point at the decode table on the right.

**0:45 · Step 03, Resolve (30 s).** "The Resolver scores the post against every session. Author is a speaker, the text matches the abstract, it names a product the talk covered, it landed in the window. 0.97 against 0.46. The reason is written on the edge."

**1:15 · Steps 04 and 05, Write and Moment (30 s).** "The edge goes into FalkorDB, and here it is read back: Post, Author, Speaker, Session, Room. Five other people were posting about the same announcement, so the Curator made it a Moment." Point at the evidence list.

**1:45 · Step 06, You (15 s).** "Paste a repository and its manifests become facets. Two hops later you know which announcements and sessions from that morning touch your stack."

**2:00 · Console (45 s).** Switch to the console tab, already replaying. "This is the same graph for the whole day. Every room, right now. The venue summarized before you pick one. Agents writing to FalkorDB as posts arrive." Click a post node: "every placement is a path." Click **Ask**, then "Which speakers posted about their own session?". Type one line into **Report from the room** and send: "anyone inside can send one line, no account, and it reaches every open console."

**2:45 · Close (15 s).** "Bright Data reaches what a plain script cannot: X through the SERP API and Bluesky through the Browser API. Universe 2026 is loaded and goes live Wednesday. Backstage: the live layer for everyone who is not in the room."

## Questions to expect

- **Why a graph?** Every answer is a path: Post to Author to Speaker to Session, Repo to Facet to Announcement. The conflict map is `OVERLAPS` edges computed inside FalkorDB.
- **How accurate is placement?** Deterministic signals first, embeddings and an LLM tie-break when a key is set. Confidence and reason live on the edge, so a wrong placement is visible.
- **Small conferences?** Sparse. Strongest from a few hundred attendees up; the agenda layer works at any size.
