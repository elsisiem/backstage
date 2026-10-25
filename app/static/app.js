/* Backstage console: streams the conference timeline, grows the graph, summarizes the venue, and asks FalkorDB why. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tag = (t, cls = '') => `<em class="tag ${cls}">[${esc(t)}]</em>`;
  const fmtTime = (epoch, tz) => new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }).format(new Date(epoch * 1000));
  const fmtDay = (epoch, tz) => new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: tz }).format(new Date(epoch * 1000));
  const roomShort = (r) => (r || '').split('@')[0].trim();
  const COLORS = { session: '#111318', speaker: '#1d4ed8', bsky: '#60a5fa', linkedin: '#64748b', x: '#374151', backstage: '#ff4f00', ann: '#d97706', moment: '#ff4f00', facet: '#0f766e', post: '#94a3b8', repo: '#0f766e' };
  const DARK = { session: '#e8eaed', speaker: '#7c9cff', bsky: '#60a5fa', linkedin: '#8c94a3', x: '#c3c8cf', backstage: '#ff5a14', ann: '#f59e0b', moment: '#ff5a14', facet: '#2dd4bf', post: '#606974', repo: '#2dd4bf' };
  const isDark = () => document.documentElement.getAttribute('data-theme') === 'dark';
  const color = (k) => (isDark() ? DARK : COLORS)[k];

  const S = {
    conf: null, meta: null, confs: [], clock: 0, speed: 120, playing: false, es: null,
    counts: { posts: 0, linked: 0, moments: 0, anns: 0, notes: 0, edges: 0 },
    posts: new Map(), sessions: new Map(), activity: new Map(), hot: new Map(), moments: [], anns: new Map(), shipped: [], notes: new Map(),
    g: null, gd: { nodes: [], links: [] }, nodeIx: new Map(), linkIx: new Set(), flushTimer: null, lastLinked: null,
  };

  // ---------- boot ---------------------------------------------------------------------
  async function boot() {
    S.confs = await (await fetch('/api/conferences')).json();
    const sel = $('#conf-select');
    sel.innerHTML = S.confs.map((c) => `<option value="${c.id}" ${c.built ? '' : 'disabled'}>${esc(c.short)} · ${c.mode}</option>`).join('');
    sel.onchange = () => loadConf(sel.value);
    initGraph();
    bindUI();
    health();
    await loadConf(S.confs.find((c) => c.built)?.id || 'universe25');
    const t = await (await fetch('/api/ask/templates')).json();
    $('#ask-chips').innerHTML = t.map((x) => `<button data-t="${x.id}">${esc(x.q)}</button>`).join('');
    $$('#ask-chips button').forEach((b) => (b.onclick = () => ask({ template: b.dataset.t })));
    const u = new URL(location.href);
    if (u.searchParams.get('speed')) { S.speed = +u.searchParams.get('speed'); $$('#speeds button').forEach((x) => x.classList.toggle('on', +x.dataset.speed === S.speed)); }
    const jump = u.searchParams.get('play');
    if (jump) { const at = jumpEpoch(jump); resetView(); S.clock = at; renderClock(); renderRooms(); play(at); go('now'); }
    if (u.searchParams.get('tour')) setTimeout(startTour, 600);
  }

  // ---------- theme / expand / tour ----------------------------------------------------
  function initTheme() {
    const seg = $('#theme-seg'); let cur = document.documentElement.getAttribute('data-theme') || 'light';
    const paint = () => $$('button', seg).forEach((b) => b.classList.toggle('on', b.dataset.theme === cur));
    seg.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; cur = b.dataset.theme; document.documentElement.setAttribute('data-theme', cur); try { localStorage.setItem('bs-theme', cur); } catch { /* private mode */ } paint(); if (S.g) S.g.graphData({ nodes: [...S.gd.nodes], links: [...S.gd.links] }); };
    paint();
  }
  function toggleExpand(sel, force) {
    const card = $(sel); if (!card) return;
    const on = force ?? !card.classList.contains('expanded');
    $$('.card.expanded').forEach((c) => { c.classList.remove('expanded'); const b = c.querySelector('.xp'); if (b) b.textContent = 'EXPAND'; });
    if (on) { card.classList.add('expanded'); const b = card.querySelector('.xp'); if (b) b.textContent = 'CLOSE'; }
    document.body.classList.toggle('has-expanded', on);
    setTimeout(() => { const el = $('#graph'); S.g.width(el.clientWidth).height(el.clientHeight); if (!S.userZoomed && S.gd.nodes.length) S.g.zoomToFit(300, 36); }, 230);
  }

  const TOUR = [
    { t: '.topbar', title: 'The clock is the real day', text: 'Day 1 of GitHub Universe 2025 at 08:55, replayed at two hours per minute. Every timestamp was recovered from a post ID, not an API. Speeds and jumps live here.', run: () => { if (!S.playing) { const at = jumpEpoch('keynote'); resetView(); S.clock = at; renderClock(); renderRooms(); play(at); } } },
    { t: '#flow', title: 'The trajectory of one post', text: 'Public internet, Listener, Resolver, Graph, you. The counters under each stage move as the day unfolds. This is the whole product in one row.' },
    { t: '#now', title: 'Read the venue before any room', text: 'Which room is loud, what just shipped, the moment you missed, the latest line from inside. One glance, then you decide where to look.' },
    { t: '#rooms-card', title: 'Every room, right now', text: 'Live only or on-demand later, straight from the catalog. The bar is public signal in the last thirty minutes. Click a room for its posts, overlaps and moments.', run: () => go('rooms') },
    { t: '#graph-card', title: 'The graph builds itself', text: 'Sessions are black, speakers blue, posts slate, announcements amber, moments orange. Click any node and the graph answers why it sits there, as a path.', run: () => { const pid = S.lastLinked; if (pid) openPost(S.posts.get(pid)); } },
    { t: '#side', title: 'Four agents, one graph', text: 'Listener, Resolver, Wire and Curator write to the same FalkorDB graph. The log is their reasoning. Moments and Wire are their outputs.', run: () => setTab('moments') },
    { t: '#report', title: 'One line back from the room', text: 'Anyone inside can report without an account. It is resolved and attributed like any post and reaches every open console the moment it lands.', run: () => { $('#note-text').value = 'Speaker just demoed the coding agent opening a PR from Slack. Room is full, overflow at the back.'; } },
    { t: '#mine', title: 'Your stack, your Backstage', text: 'A repository becomes facets. Facets reach announcements and sessions in two hops. That is the personal layer, with evidence on every item.', run: () => buildMine($('#repo-input').value.trim()) },
    { t: '#side', title: 'Ask in plain words', text: 'Templated questions and free-form ones compile to read-only Cypher against the same graph. The query is always shown.', run: () => { setTab('ask'); ask({ template: 'speakers_posting' }); } },
  ];
  let tourI = -1, tourEls = null;
  function startTour() { if (tourEls) endTour(); tourEls = {}; tourEls.veil = document.createElement('div'); tourEls.veil.className = 'tour-veil'; tourEls.ring = document.createElement('div'); tourEls.ring.className = 'tour-ring'; tourEls.card = document.createElement('div'); tourEls.card.className = 'tour-card'; tourEls.track = document.createElement('div'); tourEls.track.className = 'tour-track'; tourEls.track.innerHTML = TOUR.map(() => '<i></i>').join(''); document.body.append(tourEls.veil, tourEls.ring, tourEls.card, tourEls.track); tourStep(0); }
  function endTour() { if (!tourEls) return; Object.values(tourEls).forEach((e) => e.remove()); tourEls = null; tourI = -1; }
  function tourStep(i) {
    if (!tourEls) return;
    if (i < 0 || i >= TOUR.length) return endTour();
    tourI = i; const st = TOUR[i];
    try { st.run && st.run(); } catch { /* step action is best effort */ }
    $$('#nav button').forEach((b) => b.classList.remove('on'));
    $$('i', tourEls.track).forEach((d, k) => { d.className = k < i ? 'done' : k === i ? 'on' : ''; });
    const target = $(st.t);
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    tourEls.card.innerHTML = `<span class="lbl">STEP ${String(i + 1).padStart(2, '0')} / ${String(TOUR.length).padStart(2, '0')}</span><h3>${esc(st.title)}</h3><p>${esc(st.text)}</p><div class="row"><button class="btn" data-tb="back" ${i === 0 ? 'disabled' : ''}>BACK</button><button class="btn primary" data-tb="next">${i === TOUR.length - 1 ? 'FINISH' : 'NEXT'}</button><span class="sp">ESC TO LEAVE</span></div>`;
    $$('[data-tb]', tourEls.card).forEach((b) => (b.onclick = () => tourStep(b.dataset.tb === 'back' ? i - 1 : i + 1)));
    setTimeout(() => {
      if (!tourEls) return;
      const r = target.getBoundingClientRect();
      Object.assign(tourEls.ring.style, { left: r.left - 6 + 'px', top: r.top - 6 + 'px', width: r.width + 12 + 'px', height: r.height + 12 + 'px' });
      const cw = 360, ch = tourEls.card.offsetHeight || 180;
      let left = r.right + 14, top = r.top;
      if (left + cw > innerWidth - 12) { left = Math.max(12, r.left); top = r.bottom + 14; }
      if (top + ch > innerHeight - 12) top = Math.max(12, innerHeight - ch - 12);
      if (left + cw > innerWidth - 12) left = innerWidth - cw - 12;
      Object.assign(tourEls.card.style, { left: left + 'px', top: top + 'px' });
    }, 420);
  }

  async function health() {
    try { const h = await (await fetch('/health')).json(); const g = h.graph || {}; setStatus('st-graph', g.reachable ? `CONNECTED · ${(g.graphs || []).length} GRAPHS · ${g.ping_ms} MS` : 'UNREACHABLE', g.reachable ? 'ok' : ''); }
    catch { setStatus('st-graph', 'UNREACHABLE'); }
  }
  function setStatus(id, text, cls = '') { const el = $('#' + id); el.textContent = `[${text}]`; el.className = cls; }

  async function loadConf(id) {
    stop();
    S.conf = id;
    $('#conf-select').value = id;
    S.meta = await (await fetch(`/api/conf/${id}/bootstrap`)).json();
    S.sessions = new Map(S.meta.sessions.map((s) => [s.id, s]));
    S.anns = new Map((S.meta.announcements || []).map((a) => [a.id, a]));
    $('#conf-name').textContent = S.meta.conf.name.toUpperCase();
    const live = S.meta.conf.mode === 'live';
    $('#mode-tag').textContent = live ? '[LIVE]' : '[REPLAY]';
    $('#mode-tag').className = 'tag ' + (live ? 'accent' : '');
    setStatus('st-live', live ? `ARMED · ${S.meta.conf.days[0].slice(5).replace('-', '/')}` : 'NOT THIS EVENT', live ? 'ok' : '');
    resetView();
    S.clock = dayEpoch(0, 15, 30);
    renderClock(); renderRooms(); renderDigest(); renderFlow();
    try { for (const n of await (await fetch(`/api/conf/${id}/notes`)).json()) onNote(n, true); } catch { /* none yet */ }
    if (live) log('graph', `agenda loaded: ${S.meta.sessions.length} sessions. The live loop pulls Bluesky, LinkedIn and X every three minutes once the doors open.`);
  }

  function dayEpoch(dayIdx, hUtc, mUtc) {
    const d = S.meta.conf.days[Math.min(dayIdx, S.meta.conf.days.length - 1)];
    return Math.floor(Date.parse(`${d}T${String(hUtc).padStart(2, '0')}:${String(mUtc).padStart(2, '0')}:00Z`) / 1000);
  }
  function jumpEpoch(j) {
    return j === 'day1' ? dayEpoch(0, 15, 30) : j === 'keynote' ? dayEpoch(0, 15, 55) : j === 'day1pm' ? dayEpoch(0, 21, 0) : j === 'day2' ? dayEpoch(1, 15, 30) : S.meta.clock.start;
  }

  function resetView() {
    S.counts = { posts: 0, linked: 0, moments: 0, anns: 0, notes: S.counts.notes || 0, edges: 0 };
    S.posts.clear(); S.activity.clear(); S.hot.clear(); S.moments = []; S.shipped = []; S.lastLinked = null;
    S.gd = { nodes: [], links: [] }; S.nodeIx.clear(); S.linkIx.clear(); S.roomsSig = null;
    if (S.g) S.g.graphData({ nodes: [], links: [] });
    $('#log').innerHTML = ''; $('#moments').innerHTML = ''; $('#wire').innerHTML = '';
    $('#t-moments').textContent = '0'; $('#t-wire').textContent = '0'; $('#n-moments').textContent = '0'; $('#n-wire').textContent = '0';
    $('#graph-empty').hidden = false;
    renderCounters();
  }

  // ---------- stream -------------------------------------------------------------------
  function play(startAt) {
    stop();
    const start = startAt ?? S.clock;
    S.es = new EventSource(`/api/conf/${S.conf}/stream?speed=${S.speed}&start=${start}`);
    S.playing = true;
    $('#btn-play').textContent = 'PAUSE';
    $('#graph-empty').hidden = true;
    setStatus('st-stream', S.meta.conf.mode === 'live' ? 'LIVE' : `REPLAY · ${S.speed}X`, S.meta.conf.mode === 'live' ? 'live' : 'ok');
    S.es.addEventListener('hello', (e) => { const d = JSON.parse(e.data); log('graph', `connected. ${d.total} events on the record. mirroring into FalkorDB graph "${S.conf}_live": ${d.mirror ? 'on' : 'off'}`); });
    S.es.addEventListener('catchup', (e) => { const d = JSON.parse(e.data); if (d.posts) log('listener', `${d.posts} posts already on the record before this point`); });
    S.es.addEventListener('tick', (e) => { S.clock = JSON.parse(e.data).clock; renderClock(); renderRooms(); if (S.clock % 120 < 2) renderDigest(); });
    S.es.addEventListener('post', (e) => onPost(JSON.parse(e.data)));
    S.es.addEventListener('resolve', (e) => onResolve(JSON.parse(e.data)));
    S.es.addEventListener('announcement', (e) => onAnnouncement(JSON.parse(e.data)));
    S.es.addEventListener('moment', (e) => onMoment(JSON.parse(e.data)));
    S.es.addEventListener('note', (e) => onNote(JSON.parse(e.data)));
    S.es.addEventListener('done', () => { log('graph', 'end of the record'); stop(); });
    S.es.onerror = () => { setStatus('st-stream', 'RECONNECTING'); };
  }
  function stop() { if (S.es) { S.es.close(); S.es = null; } S.playing = false; $('#btn-play').textContent = 'PLAY'; setStatus('st-stream', 'IDLE'); }

  // ---------- events -------------------------------------------------------------------
  function who(p) { return p.author || (p.handle ? '@' + p.handle : 'someone'); }

  function onPost(ev) {
    const p = ev.post; S.posts.set(p.id, p); S.counts.posts++;
    log('listener', `${esc(p.platform)} · ${esc(who(p))}: ${esc(p.text.slice(0, 110))}${p.text.length > 110 ? '…' : ''}`, ev.t, p.id);
    addNode({ id: 'p:' + p.id, type: 'post', platform: p.platform, label: who(p), val: 1.2, post: p });
    renderCounters();
  }

  function applyResolution(ev, t, postId, fromRoom) {
    const p = S.posts.get(postId) || {}; const name = who(p);
    let linked = false;
    for (const s of ev.sessions) {
      linked = true;
      addNode({ id: 's:' + s.session_id, type: 'session', label: s.title, val: 5, session: S.sessions.get(s.session_id) });
      bump('s:' + s.session_id, 0.8);
      addLink('p:' + postId, 's:' + s.session_id, 'about', true);
      track(s.session_id, t);
      S.lastLinked = postId;
      log(fromRoom ? 'room' : 'resolver', `${esc(name)} → <b>${esc(s.title.slice(0, 70))}</b> ${tag(s.confidence.toFixed(2))} ${esc(s.reason)}`, t, postId);
      for (const n of ev.author_of || []) { addNode({ id: 'sp:' + n, type: 'speaker', label: n, val: 3.5 }); addLink('p:' + postId, 'sp:' + n, 'by', false); addLink('sp:' + n, 's:' + s.session_id, 'presents', false); }
    }
    if (!linked && ev.author_of?.length) {
      for (const n of ev.author_of) { addNode({ id: 'sp:' + n, type: 'speaker', label: n, val: 3.5 }); addLink('p:' + postId, 'sp:' + n, 'by', false); }
      log('resolver', `${esc(name)} is speaker <b>${esc(ev.author_of[0])}</b>; not about one specific session`, t);
    }
    for (const n of ev.mentions || []) {
      if (ev.author_of?.includes(n)) continue;
      addNode({ id: 'sp:' + n, type: 'speaker', label: n, val: 3.5 }); addLink('p:' + postId, 'sp:' + n, 'mentions', false);
      if (!linked) log('resolver', `${esc(name)} mentions speaker <b>${esc(n)}</b>`, t);
    }
    for (const aid of ev.announcements || []) {
      const a = S.anns.get(aid); if (!a) continue;
      addNode({ id: 'a:' + aid, type: 'ann', label: a.title, val: 4.5, ann: a });
      addLink('p:' + postId, 'a:' + aid, 'reacts', true); bump('a:' + aid, 0.6);
      if (!linked) log('resolver', `${esc(name)} is reacting to <b>${esc(a.title.slice(0, 60))}</b>`, t);
      linked = true;
    }
    if (!linked && ev.facets?.length) { const f = ev.facets[0]; addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 }); addLink('p:' + postId, 'f:' + f, 'tags', false); }
    if (linked) S.counts.linked++;
    const n = S.nodeIx.get('p:' + postId); if (n) n.linked = linked;
    renderCounters();
  }
  function onResolve(ev) { applyResolution(ev, ev.t, ev.post_id, false); }

  function onAnnouncement(ev) {
    const a = ev.announcement; S.anns.set(a.id, a); S.counts.anns++; S.shipped.push({ ...a, t: ev.t });
    addNode({ id: 'a:' + a.id, type: 'ann', label: a.title, val: 4.5, ann: a });
    for (const f of (a.facets || []).slice(0, 2)) { addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 }); addLink('a:' + a.id, 'f:' + f, 'affects', false); }
    log('wire', `shipped <b>${esc(a.title.slice(0, 80))}</b> ${tag(a.status)} touches ${(a.facets || []).slice(0, 4).map(esc).join(', ')}`, ev.t);
    const card = document.createElement('div');
    card.className = 'cardi wire';
    card.innerHTML = `<div class="h">${esc(a.title)}</div><div class="m">${esc(a.product)} · ${esc((a.summary || '').slice(0, 140))}</div><div class="facets">${tag(a.status, 'blue')}${(a.facets || []).map((f) => tag(f)).join('')}</div><div class="t"><span>${fmtTime(ev.t, S.meta.conf.tz)}</span><span>${esc(a.kind)}</span></div>`;
    card.onclick = () => window.open(a.url, '_blank');
    $('#wire').prepend(card); $('#t-wire').textContent = S.counts.anns; $('#n-wire').textContent = S.counts.anns;
    renderCounters(); renderDigest();
  }

  function onMoment(ev) {
    const m = ev.moment; S.counts.moments++; S.moments.push({ ...m, t: ev.t });
    const target = m.session_id ? 's:' + m.session_id : m.announcement_id ? 'a:' + m.announcement_id : null;
    addNode({ id: 'm:' + m.id, type: 'moment', label: m.headline, val: 7, moment: m, order: S.counts.moments });
    if (target) {
      if (m.session_id) { addNode({ id: target, type: 'session', label: S.sessions.get(m.session_id)?.title || '', val: 5, session: S.sessions.get(m.session_id) }); S.hot.set(m.session_id, ev.t); }
      addLink('m:' + m.id, target, 'of', true);
    }
    for (const pid of (m.evidence || []).slice(-4)) if (S.nodeIx.has('p:' + pid)) addLink('m:' + m.id, 'p:' + pid, 'evidence', false);
    log('curator', `${tag('MOMENT', 'accent')} ${esc(m.headline)}${m.room ? ' · ' + esc(roomShort(m.room)) : ''}`, ev.t);
    const card = document.createElement('div');
    card.className = 'cardi moment';
    card.innerHTML = `<div class="h">${esc(m.headline)}</div><div class="m">${esc((m.detail || '').slice(0, 160))}</div><div class="t"><span>${fmtTime(ev.t, S.meta.conf.tz)}</span>${m.room ? `<span>${esc(roomShort(m.room))}</span>` : ''}<span>${esc(m.kind)}</span></div>`;
    card.onclick = () => (m.session_id ? openSession(m.session_id) : m.announcement_id ? openAnn(m.announcement_id) : null);
    $('#moments').prepend(card); $('#t-moments').textContent = S.counts.moments; $('#n-moments').textContent = S.counts.moments;
    renderCounters(); renderDigest();
  }

  function onNote(ev, quiet) {
    const p = ev.post; if (S.notes.has(p.id)) return;
    S.notes.set(p.id, ev); S.posts.set(p.id, p); S.counts.notes = S.notes.size;
    setStatus('st-notes', `${S.counts.notes} FROM THE ROOM`, S.counts.notes ? 'ok' : '');
    if (quiet) return;
    S.counts.posts++;
    addNode({ id: 'p:' + p.id, type: 'post', platform: 'backstage', label: who(p), val: 2.2, post: p });
    log('room', `${tag('FROM THE ROOM', 'accent')} ${esc(who(p))}: ${esc(p.text.slice(0, 160))}`, ev.t, p.id);
    applyResolution(ev.resolve, ev.t, p.id, true);
    renderDigest();
  }

  function track(sid, t) { const a = S.activity.get(sid) || []; a.push(t); S.activity.set(sid, a); }
  const recent = (sid, win = 1800) => (S.activity.get(sid) || []).filter((t) => t >= S.clock - win && t <= S.clock + 60).length;
  const liveSessions = () => S.meta.sessions.filter((s) => s.start_epoch && s.start_epoch - 900 <= S.clock && s.end_epoch + 300 >= S.clock);

  // ---------- digest: the venue, before any one room ------------------------------------
  function renderDigest() {
    if (!S.meta) return;
    const now = S.clock, tz = S.meta.conf.tz;
    const live = liveSessions();
    const lines = [];
    const loud = live.map((s) => ({ s, n: recent(s.id) })).filter((x) => x.n > 0).sort((a, b) => b.n - a.n);
    const recentMoments = S.moments.filter((m) => now - m.t <= 2700);
    const recentShipped = S.shipped.filter((a) => now - a.t <= 3600);
    const notes = [...S.notes.values()].filter((n) => now - n.t <= 7200 || S.meta.conf.mode === 'live');
    let head;
    if (!S.playing && !S.counts.posts) head = 'Press play. The venue summarizes itself before you look at any one room.';
    else if (recentMoments.length) head = recentMoments[recentMoments.length - 1].headline + '.';
    else if (loud.length) head = `${roomShort(loud[0].s.room)} is the loudest room right now: ${loud[0].s.title}.`;
    else if (live.length) head = `${live.length} sessions in progress and the internet is quiet about all of them.`;
    else head = 'Nothing scheduled at this minute. The record keeps filling between sessions.';
    $('#now-head').textContent = head;
    $('#now-tag').textContent = `[${fmtTime(now, tz)} · ${live.length} IN PROGRESS]`;
    for (const x of loud.slice(0, 3)) lines.push({ k: roomShort(x.s.room), v: `${x.s.title} <small>· ${x.n} post${x.n > 1 ? 's' : ''} in 30 min · ${x.s.replayable ? 'on-demand later' : 'live only'}</small>`, hot: (S.hot.get(x.s.id) || 0) >= now - 2400 });
    const quiet = live.filter((s) => recent(s.id) === 0);
    if (quiet.length) lines.push({ k: 'QUIET', v: `${quiet.length} session${quiet.length > 1 ? 's' : ''} with no public signal <small>· ${quiet.filter((s) => !s.replayable).length} of them will not be recorded · e.g. ${esc(quiet[0].title.slice(0, 44))}</small>` });
    if (recentShipped.length) lines.push({ k: 'SHIPPED', v: `${recentShipped.slice(-3).map((a) => esc(a.title.slice(0, 48))).join(' · ')} <small>· ${recentShipped.length} in the last hour</small>` });
    for (const m of recentMoments.slice(-2).reverse()) lines.push({ k: 'MOMENT', v: `${esc(m.headline)} <small>· ${fmtTime(m.t, tz)}</small>`, hot: true });
    if (notes.length) lines.push({ k: 'FROM THE ROOM', v: `${esc(who(notes[notes.length - 1].post))}: ${esc(notes[notes.length - 1].post.text.slice(0, 120))} <small>· ${notes.length} report${notes.length > 1 ? 's' : ''}</small>`, hot: true });
    $('#digest').innerHTML = lines.map((l) => `<li><span class="k ${l.hot ? 'hot' : ''}">${l.k}</span><span class="v">${l.v}</span></li>`).join('');
    // the report form offers the rooms that are live now
    const sel = $('#note-session'); const cur = sel.value;
    sel.innerHTML = '<option value="">Which room are you in?</option>' + live.map((s) => `<option value="${s.id}">${esc(roomShort(s.room))} · ${esc(s.title.slice(0, 48))}</option>`).join('');
    if (cur) sel.value = cur;
  }

  // ---------- rooms --------------------------------------------------------------------
  function renderRooms() {
    if (!S.meta) return;
    const now = S.clock;
    const live = liveSessions();
    const byRoom = new Map();
    for (const s of live) { const r = s.room || 'TBA'; if (!byRoom.has(r)) byRoom.set(r, []); byRoom.get(r).push(s); }
    const rooms = [...byRoom.entries()].map(([room, ss]) => ({ room, ss, act: Math.max(...ss.map((s) => recent(s.id))), hot: ss.some((s) => (S.hot.get(s.id) || 0) >= now - 2400) }))
      .sort((a, b) => b.hot - a.hot || b.act - a.act || a.room.localeCompare(b.room));
    $('#rooms-sub').textContent = rooms.length ? `${live.length} sessions in ${rooms.length} rooms` : 'nothing scheduled at this minute';
    const sig = rooms.slice(0, 14).map(({ room, ss, hot }) => room + ':' + ss.map((s) => s.id + '/' + Math.min(100, recent(s.id) * 14)).join(',') + (hot ? '!' : '')).join('|') + '#' + Math.floor(now / 60);
    if (sig === S.roomsSig) return;
    S.roomsSig = sig;
    $('#rooms').innerHTML = rooms.slice(0, 14).map(({ room, ss, hot }) => ss.slice(0, 2).map((s) => {
      const starts = s.start_epoch > now ? `starts in ${Math.ceil((s.start_epoch - now) / 60)} min` : s.end_epoch < now ? 'just ended' : `${Math.floor((now - s.start_epoch) / 60)} min in`;
      return `<div class="room ${hot ? 'hot' : ''}" data-sid="${s.id}"><div class="rn"><span>${esc(roomShort(room))}</span><span class="ago">${starts}</span></div>
        <span class="st">${esc(s.title)}</span>
        <div class="sp">${esc((s.speakers || []).slice(0, 3).join(', '))}${s.speakers?.length > 3 ? ' +' + (s.speakers.length - 3) : ''} · ${esc(s.start_local)}–${esc(s.end_local)}</div>
        <div class="tags">${s.replayable ? tag('ON-DEMAND LATER', 'dim') : tag('LIVE ONLY')}${hot ? tag('MOMENT', 'accent') : ''}${recent(s.id) ? tag(recent(s.id) + ' POSTS · 30 MIN', 'blue') : ''}</div>
        <div class="meter"><i style="width:${Math.min(100, recent(s.id) * 14)}%"></i></div></div>`;
    }).join('')).join('') || '<div class="empty">No room is live at this minute.</div>';
    $$('#rooms .room').forEach((el) => (el.onclick = () => openSession(el.dataset.sid)));
  }

  function renderClock() {
    if (!S.meta) return;
    $('#clock-time').textContent = fmtTime(S.clock, S.meta.conf.tz);
    $('#clock-day').textContent = fmtDay(S.clock, S.meta.conf.tz);
  }
  function renderCounters() {
    $('#c-posts').textContent = S.counts.posts; $('#c-linked').textContent = S.counts.linked;
    $('#c-moments').textContent = S.counts.moments; $('#c-anns').textContent = S.counts.anns; $('#c-nodes').textContent = S.gd.nodes.length;
    renderFlow();
  }
  function renderFlow() {
    $('#f-ingested').textContent = `${S.counts.posts} ingested`;
    $('#f-placed').textContent = `${S.counts.linked} placed`;
    $('#f-nodes').innerHTML = `${S.gd.nodes.length} nodes &middot; ${S.gd.links.length} edges`;
    $('#f-you').innerHTML = `${S.counts.moments} moments &middot; ${S.counts.anns} shipped`;
  }

  // ---------- log ----------------------------------------------------------------------
  function log(agent, html, t, pid) {
    const el = $('#log'); const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const ln = document.createElement('div'); ln.className = 'ln ' + agent;
    ln.innerHTML = `<span class="t">${t ? fmtTime(t, S.meta.conf.tz) : ''}</span><span class="ag">${agent.toUpperCase()}</span>${html}`;
    if (pid) { ln.title = 'open this post in the graph'; ln.onclick = () => { const p = S.posts.get(pid); if (p) { openPost(p); go('graph'); } }; }
    el.appendChild(ln);
    while (el.children.length > 350) el.removeChild(el.firstChild);
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // ---------- graph --------------------------------------------------------------------
  function initGraph() {
    const el = $('#graph');
    S.g = ForceGraph()(el)
      .graphData({ nodes: [], links: [] }).nodeId('id').nodeVal((n) => n.val || 1).nodeRelSize(3.2)
      .nodeColor((n) => n.type === 'post' ? (color(n.platform) || color('post')) : color(n.type))
      .nodeLabel((n) => n.type === 'post' ? `${n.label}: ${(n.post?.text || '').slice(0, 140)}` : n.label)
      .linkColor((l) => l.kind === 'about' ? 'rgba(29,78,216,.45)' : l.kind === 'of' || l.kind === 'evidence' ? 'rgba(255,79,0,.55)' : l.kind === 'reacts' ? 'rgba(217,119,6,.5)' : 'rgba(100,116,139,.28)')
      .linkWidth((l) => (l.kind === 'about' ? 1.4 : 0.8))
      .linkDirectionalParticles((l) => (l.fresh ? 3 : 0)).linkDirectionalParticleWidth(2.2).linkDirectionalParticleSpeed(0.012).linkDirectionalParticleColor(() => '#ff4f00')
      .nodeCanvasObjectMode(() => 'after')
      .nodeCanvasObject((n, ctx, scale) => {
        if (!['session', 'ann', 'moment', 'speaker', 'facet', 'repo'].includes(n.type)) return;
        // label density: moments and big sessions always, announcements when zoomed a little, people and facets only when close
        if (n.type === 'session' && (n.val || 0) < 6 && scale < 1.2) return;
        if (n.type === 'ann' && scale < 1.0) return;
        if ((n.type === 'speaker' || n.type === 'facet') && scale < 1.5) return;
        if (n.type === 'moment' && S.counts.moments - (n.order || 0) > 4 && scale < 1.4) return;
        const label = n.label.slice(0, n.type === 'session' ? 32 : n.type === 'moment' ? 30 : 22);
        const size = Math.max(9 / scale, 2.4);
        ctx.font = `${n.type === 'speaker' || n.type === 'facet' ? 500 : 600} ${size}px "JetBrains Mono", monospace`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        const w = ctx.measureText(label).width + 6;
        const dark = isDark();
        ctx.fillStyle = dark ? 'rgba(12,14,17,.86)' : 'rgba(248,249,250,.88)';
        ctx.fillRect(n.x - w / 2, n.y + (n.val || 1) + 3, w, size + 3);
        ctx.fillStyle = dark ? (n.type === 'session' ? '#e8eaed' : n.type === 'ann' ? '#f59e0b' : n.type === 'moment' ? '#ff8146' : n.type === 'facet' || n.type === 'repo' ? '#2dd4bf' : '#7c9cff')
          : (n.type === 'session' ? '#111318' : n.type === 'ann' ? '#b45309' : n.type === 'moment' ? '#d63f00' : n.type === 'facet' || n.type === 'repo' ? '#0f766e' : '#1d4ed8');
        ctx.fillText(label, n.x, n.y + (n.val || 1) + 4.5);
      })
      .onNodeClick((n) => openNode(n))
      .onBackgroundClick(() => { S.userZoomed = false; })
      .d3AlphaDecay(0.03).d3VelocityDecay(0.3).cooldownTicks(300).warmupTicks(0)
      .backgroundColor('rgba(0,0,0,0)');
    S.g.d3Force('charge').strength(-40);
    S.g.d3Force('pull', (alpha) => { for (const n of S.gd.nodes) { if (Number.isFinite(n.x)) { n.vx -= n.x * 0.015 * alpha; n.vy -= n.y * 0.015 * alpha; } } });
    el.addEventListener('wheel', () => { S.userZoomed = true; }, { passive: true });
    el.addEventListener('mousedown', () => { S.userZoomed = true; });
    setInterval(() => { if (!S.userZoomed && S.gd.nodes.length) S.g.zoomToFit(500, 36, (n) => Number.isFinite(n.x)); }, 2000);
    const fit = () => S.g.width(el.clientWidth).height(el.clientHeight);
    new ResizeObserver(fit).observe(el); fit();
  }
  function addNode(n) { if (S.nodeIx.has(n.id)) return S.nodeIx.get(n.id); S.nodeIx.set(n.id, n); S.gd.nodes.push(n); scheduleFlush(); return n; }
  function bump(id, by) { const n = S.nodeIx.get(id); if (n) n.val = Math.min(22, (n.val || 1) + by); }
  function addLink(a, b, kind, fresh) {
    if (!S.nodeIx.has(a) || !S.nodeIx.has(b)) return;
    const key = a + '>' + b + ':' + kind; if (S.linkIx.has(key)) return;
    S.linkIx.add(key); const l = { source: a, target: b, kind, fresh: !!fresh }; S.gd.links.push(l);
    if (fresh) setTimeout(() => { l.fresh = false; }, 9000);
    scheduleFlush();
  }
  function scheduleFlush() {
    if (S.flushTimer) return;
    S.flushTimer = setTimeout(() => {
      S.flushTimer = null;
      const floating = S.gd.nodes.filter((n) => n.type === 'post' && !n.linked);
      if (floating.length > 160) { const drop = new Set(floating.slice(0, floating.length - 160).map((n) => n.id)); S.gd.nodes = S.gd.nodes.filter((n) => !drop.has(n.id)); drop.forEach((id) => S.nodeIx.delete(id)); S.gd.links = S.gd.links.filter((l) => !drop.has(l.source.id || l.source) && !drop.has(l.target.id || l.target)); }
      S.g.graphData({ nodes: [...S.gd.nodes], links: [...S.gd.links] }); renderCounters();
    }, 120);
  }

  // ---------- details ------------------------------------------------------------------
  function showDetail(html) { $('#detail-body').innerHTML = html; $('#detail').hidden = false; }
  $('#detail-close').onclick = () => ($('#detail').hidden = true);
  function openNode(n) {
    if (n.type === 'post') return openPost(n.post);
    if (n.type === 'session') return openSession(n.id.slice(2));
    if (n.type === 'ann') return openAnn(n.id.slice(2));
    if (n.type === 'moment') return showDetail(`<h3>${esc(n.moment.headline)}</h3>${tag('MOMENT', 'accent')}<span class="k">why it fired</span><div>${esc(n.moment.detail || '')}</div><span class="k">evidence</span>${(n.moment.evidence || []).map((id) => S.posts.get(id)).filter(Boolean).map((p) => `<div class="post">${esc(p.text.slice(0, 160))}<div class="m">${esc(who(p))} · ${esc(p.platform)}</div></div>`).join('')}${n.moment.session_id ? `<p><button class="btn" onclick="window.__openSession('${n.moment.session_id}')">OPEN SESSION</button></p>` : ''}`);
    if (n.type === 'speaker') { const ss = S.meta.sessions.filter((s) => (s.speakers || []).includes(n.label)); return showDetail(`<h3>${esc(n.label)}</h3>${tag('SPEAKER', 'blue')}<span class="k">speaking at</span>${ss.map((s) => `<div class="post"><b>${esc(s.title)}</b><div class="m">${esc(roomShort(s.room))} · ${esc(s.day)} ${esc(s.start_local)}</div></div>`).join('') || '<div class="ev">no session found in the agenda</div>'}`); }
    if (n.type === 'facet' || n.type === 'repo') return showDetail(`<h3>${esc(n.label)}</h3>${tag(n.type === 'repo' ? 'REPO' : 'FACET')}<div class="ev" style="margin-top:8px">A stack facet is the shared vocabulary that joins repos, sessions, announcements and posts. Paste a repo in Your Backstage to see what reaches you through it.</div>`);
  }
  window.__openSession = (id) => openSession(id);

  async function openPost(p) {
    showDetail(`<h3>${esc(who(p))} on ${esc(p.platform)}</h3><div>${esc(p.text.slice(0, 500))}</div><span class="k">asking the graph</span><div class="skeleton"></div>`);
    const w = await (await fetch(`/api/conf/${S.conf}/why/${p.id}`)).json();
    let html = `<h3>${esc(who(p))} on ${esc(p.platform)}${p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noopener">open</a>` : ''}</h3><div>${esc(p.text.slice(0, 420))}</div>`;
    if (!w.sessions.length && !w.mentions.length && !w.announcements.length) html += `<span class="k">resolution</span><div class="ev">Not tied to one session. ${w.facets.length ? 'Tagged ' + w.facets.map((f) => tag(f)).join(' ') : ''}</div>`;
    for (const s of w.sessions) {
      html += `<span class="k">why it sits under this session</span>`;
      html += `<div class="hop"><span class="n post">Post</span><span class="e">—ABOUT_SESSION ${s.confidence.toFixed(2)}→</span><span class="n session">${esc(s.title.slice(0, 50))}</span></div>`;
      if (s.author_is_speaker?.length) html += `<div class="hop"><span class="n post">Post</span><span class="e">—BY→</span><span class="n">Author</span><span class="e">—IS→</span><span class="n speaker">${esc(s.author_is_speaker[0])}</span><span class="e">←PRESENTED_BY—</span><span class="n session">Session</span></div>`;
      html += `<div>${(s.signals || []).map((x) => tag(x)).join(' ')}</div><div class="ev" style="margin-top:4px">${esc(s.reason)}</div>`;
      html += `<div class="ev">${esc(roomShort(s.room))} · ${esc(s.start_local || '')} · speakers: ${esc((s.speakers || []).join(', '))}</div>`;
    }
    if (w.mentions.length) html += `<span class="k">mentions</span><div>${w.mentions.map((m) => tag(m, 'blue')).join(' ')}</div>`;
    if (w.announcements.length) html += `<span class="k">reacting to</span>${w.announcements.map((m) => `<div class="hop"><span class="n post">Post</span><span class="e">—ABOUT_ANNOUNCEMENT→</span><span class="n ann">${esc(m.slice(0, 50))}</span></div>`).join('')}`;
    html += `<span class="k">cypher</span><div class="path">${esc(w.cypher)}</div>`;
    showDetail(html);
  }

  async function openSession(id) {
    const s = S.sessions.get(id);
    showDetail(`<h3>${esc(s?.title || '')}</h3><span class="k">asking the graph</span><div class="skeleton"></div>`);
    const d = await (await fetch(`/api/conf/${S.conf}/session/${id}`)).json();
    let html = `<h3>${esc(d.title)}</h3><div>${d.replayable ? tag('ON-DEMAND LATER', 'dim') : tag('LIVE ONLY')} ${tag(roomShort(d.room))}</div><div class="ev" style="margin-top:6px">${esc(d.day)} ${esc(d.start_local)}–${esc(d.end_local)} · ${d.speakers.map((x) => `<b>${esc(x.name)}</b> (${esc(x.company)})`).join(', ')}</div>`;
    if (d.facets?.length) html += `<div style="margin-top:6px">${d.facets.map((f) => tag(f)).join(' ')}</div>`;
    if (d.moments?.length) html += `<span class="k">moments</span>${d.moments.map((m) => `<div class="post">${esc(m.headline)}<div class="m">${esc((m.ts || '').slice(11, 16))} UTC</div></div>`).join('')}`;
    html += `<span class="k">${d.posts.length} posts resolved here</span>` + d.posts.slice(0, 8).map((p) => `<div class="post">${esc((p.text || '').slice(0, 150))}<div class="m">${esc(p.author || '')} · ${esc(p.platform)} · ${tag((p.confidence || 0).toFixed(2))} ${esc(p.reason || '')}</div></div>`).join('');
    if (d.overlaps?.length) html += `<span class="k">overlaps with (the conflict graph)</span>${d.overlaps.slice(0, 6).map((o) => `<div class="post"><b>${esc(o.title)}</b><div class="m">${esc(roomShort(o.room))} · ${esc(o.start_local || '')}</div></div>`).join('')}`;
    if (d.artifacts?.length) html += `<span class="k">artifacts shared</span>${d.artifacts.map((a) => `<div class="post"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.kind)} · ${esc(a.url.slice(0, 60))}</a></div>`).join('')}`;
    html += `<span class="k">cypher</span><div class="path">${esc(d.cypher)}</div><p><a href="${esc(d.url)}" target="_blank" rel="noopener">session page</a></p>`;
    showDetail(html);
  }

  function openAnn(id) {
    const a = S.anns.get(id); if (!a) return;
    showDetail(`<h3>${esc(a.title)}</h3><div>${tag(a.status, 'blue')} ${tag(a.product)}</div><div class="ev" style="margin-top:6px">${esc((a.ts || '').slice(0, 16))} UTC</div><div style="margin-top:8px">${esc(a.summary || '')}</div><span class="k">touches</span><div>${(a.facets || []).map((f) => tag(f)).join(' ')}</div><span class="k">path to you</span><div class="hop"><span class="n ann">Announcement</span><span class="e">—AFFECTS→</span><span class="n facet">facet</span><span class="e">←USES—</span><span class="n">your Repo</span></div><p><a href="${esc(a.url)}" target="_blank" rel="noopener">read it</a></p>`);
  }

  // ---------- ask ----------------------------------------------------------------------
  async function ask(body) {
    const out = $('#ask-out'); out.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:70%;margin-top:6px"></div>';
    const r = await (await fetch(`/api/conf/${S.conf}/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    if (r.error || r.detail) { out.innerHTML = `<div class="ev">${esc(r.error || r.detail)}</div>${r.cypher ? `<div class="cy">${esc(r.cypher)}</div>` : ''}`; return; }
    let html = `<div class="ev"><b>${esc(r.q)}</b></div>`;
    if (r.answer) html += `<div class="answer">${esc(r.answer)}</div>`;
    if (r.rows?.length) html += `<table class="res"><thead><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${r.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    else html += '<div class="ev">no rows</div>';
    html += `<div class="cy">${esc(r.cypher)}</div>`;
    out.innerHTML = html;
  }

  // ---------- your backstage -----------------------------------------------------------
  async function buildMine(url) {
    const out = $('#yours-out'); out.innerHTML = '<div class="ev">reading the repo’s manifests, tagging facets, walking the graph</div><div class="skeleton"></div><div class="skeleton"></div>';
    const res = await fetch(`/api/conf/${S.conf}/repo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
    const r = await res.json();
    if (!res.ok) { out.innerHTML = `<div class="ev">${esc(r.detail || 'failed')}</div>`; return; }
    const repo = r.repo;
    addNode({ id: 'r:' + repo.name, type: 'repo', label: repo.name, val: 6 });
    for (const f of repo.facets) { addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 }); addLink('r:' + repo.name, 'f:' + f, 'uses', true); }
    $('#f-you2').textContent = `${r.sessions.length} sessions · ${r.announcements.length} announcements for ${repo.name}`;
    log('graph', `repo <b>${esc(repo.name)}</b> → facets ${repo.facets.map(esc).join(', ')} · ${r.sessions.length} sessions and ${r.announcements.length} announcements reach it`);
    out.innerHTML = `
      <div class="col"><h4>${esc(repo.name)} · stack facets</h4>${repo.facets.map((f) => `<div class="item"><div class="h">${tag(f)}</div><div class="m">${esc((repo.evidence[f] || []).slice(0, 3).join(' · '))}</div></div>`).join('')}</div>
      <div class="col"><h4>sessions for you</h4>${r.sessions.map((s) => `<div class="item" onclick="window.__openSession('${s.id}')"><div class="h">${esc(s.title)}</div><div class="m">${esc(roomShort(s.room))} · ${esc(s.day)} ${esc(s.start_local)} · ${s.posts} posts</div><div class="tags">${s.replayable ? tag('ON-DEMAND LATER', 'dim') : tag('LIVE ONLY')}${(s.via || []).map((f) => tag(f)).join('')}</div></div>`).join('') || '<div class="empty">nothing matched</div>'}</div>
      <div class="col"><h4>announcements that touch your stack</h4>${r.announcements.map((a) => `<div class="item" onclick="window.open('${esc(a.url)}','_blank')"><div class="h">${esc(a.title)}</div><div class="m">${a.posts} reactions</div><div class="tags">${tag(a.status, 'blue')}${(a.via || []).map((f) => tag(f)).join('')}</div></div>`).join('') || '<div class="empty">nothing matched</div>'}</div>`;
  }

  // ---------- report from the room -----------------------------------------------------
  async function sendNote() {
    const text = $('#note-text').value.trim(); if (text.length < 3) return;
    const btn = $('#note-send'); btn.disabled = true; btn.textContent = 'SENDING';
    try {
      const r = await fetch(`/api/conf/${S.conf}/note`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, handle: $('#note-handle').value.trim(), session_id: $('#note-session').value }) });
      const ev = await r.json();
      if (!r.ok) throw new Error(ev.detail || 'failed');
      if (!S.playing) onNote(ev);
      const ok = $('#note-ok'); ok.hidden = false; ok.textContent = `[DELIVERED] ${ev.resolve.sessions.length ? 'placed under “' + ev.resolve.sessions[0].title.slice(0, 50) + '”' : 'on the record'} · every open console has it now`;
      $('#note-text').value = '';
    } catch (e) { const ok = $('#note-ok'); ok.hidden = false; ok.textContent = `[NOT SENT] ${e.message}`; }
    finally { btn.disabled = false; btn.textContent = 'SEND TO EVERYONE'; }
  }

  // ---------- navigation / ui -----------------------------------------------------------
  function go(where) {
    $$('#nav button').forEach((b) => b.classList.toggle('on', b.dataset.go === where));
    const map = { now: '#now', rooms: '#rooms-card', graph: '#graph-card', moments: '#side', wire: '#side', ask: '#side', mine: '#mine', guide: '#guide' };
    if (['moments', 'wire', 'ask', 'agents'].includes(where)) setTab(where);
    const el = $(map[where] || '#now'); if (el) el.scrollIntoView({ behavior: 'smooth', block: where === 'now' ? 'start' : 'nearest' });
  }
  function setTab(t) { $$('#tabs button').forEach((x) => x.classList.toggle('on', x.dataset.tab === t)); $$('.tabpane').forEach((p) => p.classList.toggle('on', p.id === 'tab-' + t)); }

  function runStep(step) {
    if (step === 'play') { const at = jumpEpoch('keynote'); resetView(); S.clock = at; renderClock(); renderRooms(); play(at); go('now'); }
    if (step === 'now') { renderDigest(); go('now'); }
    if (step === 'why') { const pid = S.lastLinked || [...S.posts.keys()][0]; if (pid) { openPost(S.posts.get(pid)); go('graph'); } else { go('graph'); } }
    if (step === 'mine') { go('mine'); buildMine($('#repo-input').value.trim()); }
    if (step === 'ask') { go('ask'); ask({ template: 'speakers_posting' }); }
    if (step === 'note') { go('now'); $('#note-text').value = 'Speaker just demoed the coding agent opening a PR from Slack. Room is full, overflow at the back.'; $('#note-text').focus(); }
  }

  function bindUI() {
    $('#btn-play').onclick = () => (S.playing ? stop() : play());
    $$('#speeds button').forEach((b) => (b.onclick = () => { $$('#speeds button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); S.speed = +b.dataset.speed; if (S.playing) play(S.clock); }));
    $$('#jumps button').forEach((b) => (b.onclick = () => { const at = jumpEpoch(b.dataset.jump); resetView(); S.clock = at; renderClock(); renderRooms(); play(at); }));
    $$('#tabs button').forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
    $$('[data-go]').forEach((b) => (b.onclick = (e) => { e.preventDefault(); go(b.dataset.go); }));
    $$('[data-step]').forEach((b) => (b.onclick = () => runStep(b.dataset.step)));
    $('#collapse').onclick = () => { $('#shell').classList.toggle('collapsed'); setTimeout(() => S.g.width($('#graph').clientWidth).height($('#graph').clientHeight), 220); };
    $('#ask-form').onsubmit = (e) => { e.preventDefault(); const q = $('#ask-input').value.trim(); if (q) ask({ q }); };
    $('#repo-form').onsubmit = (e) => { e.preventDefault(); buildMine($('#repo-input').value.trim()); };
    $('#note-form').onsubmit = (e) => { e.preventDefault(); sendNote(); };
    $$('[data-expand]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleExpand(b.dataset.expand); }));
    $$('[data-tour]').forEach((b) => (b.onclick = (e) => { e.preventDefault(); startTour(); }));
    initTheme();
    document.addEventListener('keydown', (e) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
      if (e.key === 'Escape') { if (tourEls) return endTour(); if ($('.card.expanded')) return toggleExpand('.card.expanded', false); $('#detail').hidden = true; return; }
      if (typing) return;
      if (e.key === ' ') { e.preventDefault(); S.playing ? stop() : play(); }
      if (e.key === '?') startTour();
      if (tourEls && (e.key === 'ArrowRight' || e.key === 'Enter')) tourStep(tourI + 1);
      if (tourEls && e.key === 'ArrowLeft') tourStep(tourI - 1);
    });
  }

  window.__S = S;
  boot();
})();
