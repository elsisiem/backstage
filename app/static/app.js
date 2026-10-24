/* Backstage console: streams the conference timeline, grows the graph, and asks FalkorDB why. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtTime = (epoch, tz) => new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz }).format(new Date(epoch * 1000));
  const fmtDay = (epoch, tz) => new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: tz }).format(new Date(epoch * 1000));
  const COLORS = { session: '#7c5cff', speaker: '#22d3ee', bsky: '#3b82f6', linkedin: '#60a5fa', x: '#e5e7eb', ann: '#f59e0b', moment: '#fb7185', facet: '#10b981', post: '#94a3b8' };

  const S = {
    conf: null, meta: null, confs: [], clock: 0, speed: 120, playing: false, es: null,
    counts: { posts: 0, linked: 0, moments: 0, anns: 0 },
    posts: new Map(), sessions: new Map(), activity: new Map(), hot: new Map(), moments: [], anns: new Map(),
    g: null, gd: { nodes: [], links: [] }, nodeIx: new Map(), linkIx: new Set(), queue: [], flushTimer: null,
  };

  // ---------- boot ---------------------------------------------------------------------
  async function boot() {
    S.confs = await (await fetch('/api/conferences')).json();
    const sel = $('#conf-select');
    sel.innerHTML = S.confs.map((c) => `<option value="${c.id}" ${c.built ? '' : 'disabled'}>${esc(c.name)} · ${c.mode === 'replay' ? 'replay' : 'live'}</option>`).join('');
    sel.onchange = () => loadConf(sel.value);
    initGraph();
    bindUI();
    await loadConf(S.confs.find((c) => c.built)?.id || 'universe25');
    const t = await (await fetch('/api/ask/templates')).json();
    $('#ask-chips').innerHTML = t.map((x) => `<button data-t="${x.id}">${esc(x.q)}</button>`).join('');
    $$('#ask-chips button').forEach((b) => (b.onclick = () => ask({ template: b.dataset.t })));
  }

  async function loadConf(id) {
    stop();
    S.conf = id;
    $('#conf-select').value = id;
    S.meta = await (await fetch(`/api/conf/${id}/bootstrap`)).json();
    S.sessions = new Map(S.meta.sessions.map((s) => [s.id, s]));
    S.anns = new Map((S.meta.announcements || []).map((a) => [a.id, a]));
    const c = S.meta.counts;
    $('#hs-posts').textContent = c.posts.toLocaleString();
    $('#hs-sessions').textContent = S.meta.sessions.length;
    $('#hs-moments').textContent = c.moments;
    $('#hs-anns').textContent = c.announcements;
    const live = S.meta.conf.mode === 'live';
    $('#mode-pill').textContent = live ? 'LIVE' : 'REPLAY';
    $('#mode-pill').classList.toggle('live', live);
    resetView();
    S.clock = S.meta.clock.start || dayEpoch(0, 15, 30);
    renderClock();
    renderRooms();
    if (live) log('listener', 'agenda loaded: ' + S.meta.sessions.length + ' sessions · polling Bluesky, LinkedIn and X every 2 min once the conference starts');
  }

  function dayEpoch(dayIdx, hUtc, mUtc) {
    const d = S.meta.conf.days[Math.min(dayIdx, S.meta.conf.days.length - 1)];
    return Math.floor(Date.parse(`${d}T${String(hUtc).padStart(2, '0')}:${String(mUtc).padStart(2, '0')}:00Z`) / 1000);
  }

  function resetView() {
    S.counts = { posts: 0, linked: 0, moments: 0, anns: 0 };
    S.posts.clear(); S.activity.clear(); S.hot.clear(); S.moments = [];
    S.gd = { nodes: [], links: [] }; S.nodeIx.clear(); S.linkIx.clear(); S.queue = [];
    if (S.g) S.g.graphData(S.gd);
    $('#log').innerHTML = ''; $('#moments').innerHTML = ''; $('#wire').innerHTML = '';
    $('#t-moments').textContent = '0'; $('#t-wire').textContent = '0';
    $('#graph-empty').hidden = false;
    renderCounters();
  }

  // ---------- stream -------------------------------------------------------------------
  function play(startAt) {
    stop();
    const start = startAt ?? S.clock;
    S.es = new EventSource(`/api/conf/${S.conf}/stream?speed=${S.speed}&start=${start}`);
    S.playing = true;
    $('#btn-play').textContent = '❚❚';
    $('#graph-empty').hidden = true;
    S.es.addEventListener('hello', (e) => { const d = JSON.parse(e.data); log('graph', `connected · ${d.total} events on the timeline · mirroring into FalkorDB graph "${S.conf}_live": ${d.mirror ? 'on' : 'off'}`); });
    S.es.addEventListener('catchup', (e) => { const d = JSON.parse(e.data); if (d.posts) log('listener', `${d.posts} posts already on the record before this point`); });
    S.es.addEventListener('tick', (e) => { S.clock = JSON.parse(e.data).clock; renderClock(); if (S.clock % 60 < 2 || true) renderRooms(); });
    S.es.addEventListener('post', (e) => onPost(JSON.parse(e.data)));
    S.es.addEventListener('resolve', (e) => onResolve(JSON.parse(e.data)));
    S.es.addEventListener('announcement', (e) => onAnnouncement(JSON.parse(e.data)));
    S.es.addEventListener('moment', (e) => onMoment(JSON.parse(e.data)));
    S.es.addEventListener('done', () => { log('graph', 'end of the record'); stop(); });
    S.es.onerror = () => { log('graph', 'stream interrupted, reconnecting…'); };
  }
  function stop() { if (S.es) { S.es.close(); S.es = null; } S.playing = false; $('#btn-play').textContent = '▶'; }

  // ---------- events -------------------------------------------------------------------
  function onPost(ev) {
    const p = ev.post; S.posts.set(p.id, p); S.counts.posts++;
    const who = p.author || (p.handle ? '@' + p.handle : 'someone');
    log('listener', `${esc(p.platform)} · ${esc(who)}: “${esc(p.text.slice(0, 110))}${p.text.length > 110 ? '…' : ''}”`, ev.t);
    addNode({ id: 'p:' + p.id, type: 'post', platform: p.platform, label: who, val: 1.2, post: p });
    renderCounters();
  }

  function onResolve(ev) {
    const p = S.posts.get(ev.post_id) || {}; const who = p.author || (p.handle ? '@' + p.handle : 'post');
    let linked = false;
    for (const s of ev.sessions) {
      linked = true;
      const sess = S.sessions.get(s.session_id);
      addNode({ id: 's:' + s.session_id, type: 'session', label: s.title, val: 5, session: sess });
      bump('s:' + s.session_id, 0.8);
      addLink('p:' + ev.post_id, 's:' + s.session_id, 'about', true);
      track(s.session_id, ev.t);
      log('resolver', `linked ${esc(who)} → <b>“${esc(s.title.slice(0, 70))}”</b> <span class="conf">${s.confidence.toFixed(2)}</span> · ${esc(s.reason)}`, ev.t);
      for (const name of ev.author_of || []) {
        addNode({ id: 'sp:' + name, type: 'speaker', label: name, val: 3.5 });
        addLink('p:' + ev.post_id, 'sp:' + name, 'by', false);
        addLink('sp:' + name, 's:' + s.session_id, 'presents', false);
      }
    }
    if (!linked && ev.author_of?.length) {
      for (const name of ev.author_of) { addNode({ id: 'sp:' + name, type: 'speaker', label: name, val: 3.5 }); addLink('p:' + ev.post_id, 'sp:' + name, 'by', false); }
      log('resolver', `${esc(who)} is speaker <b>${esc(ev.author_of[0])}</b>; post is not about one specific session`, ev.t);
    }
    for (const name of ev.mentions || []) {
      if (ev.author_of?.includes(name)) continue;
      addNode({ id: 'sp:' + name, type: 'speaker', label: name, val: 3.5 });
      addLink('p:' + ev.post_id, 'sp:' + name, 'mentions', false);
      if (!linked) log('resolver', `${esc(who)} mentions speaker <b>${esc(name)}</b>`, ev.t);
    }
    for (const aid of ev.announcements || []) {
      const a = S.anns.get(aid); if (!a) continue;
      addNode({ id: 'a:' + aid, type: 'ann', label: a.title, val: 4.5, ann: a });
      addLink('p:' + ev.post_id, 'a:' + aid, 'reacts', true);
      bump('a:' + aid, 0.6);
      if (!linked) log('resolver', `${esc(who)} is reacting to <b>“${esc(a.title.slice(0, 60))}”</b>`, ev.t);
      linked = true;
    }
    if (!linked && ev.facets?.length) {
      const f = ev.facets[0];
      addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 });
      addLink('p:' + ev.post_id, 'f:' + f, 'tags', false);
    }
    if (linked) S.counts.linked++;
    const n = S.nodeIx.get('p:' + ev.post_id); if (n) n.linked = linked;
    renderCounters();
  }

  function onAnnouncement(ev) {
    const a = ev.announcement; S.anns.set(a.id, a); S.counts.anns++;
    addNode({ id: 'a:' + a.id, type: 'ann', label: a.title, val: 4.5, ann: a });
    for (const f of (a.facets || []).slice(0, 2)) { addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 }); addLink('a:' + a.id, 'f:' + f, 'affects', false); }
    log('wire', `shipped <b>${esc(a.title.slice(0, 80))}</b> [${esc(a.status)}] · touches ${(a.facets || []).slice(0, 4).map(esc).join(', ')}`, ev.t);
    const card = document.createElement('div');
    card.className = 'card wire';
    card.innerHTML = `<div class="h">${esc(a.title)}</div><div class="m">${esc(a.product)} · ${esc(a.status)} · ${esc((a.summary || '').slice(0, 140))}</div><div class="facets">${(a.facets || []).map((f) => `<span class="facet">${esc(f)}</span>`).join('')}</div><div class="t"><span>${fmtTime(ev.t, S.meta.conf.tz)}</span><span>${esc(a.kind)}</span></div>`;
    card.onclick = () => window.open(a.url, '_blank');
    $('#wire').prepend(card); $('#t-wire').textContent = S.counts.anns;
    renderCounters();
  }

  function onMoment(ev) {
    const m = ev.moment; S.counts.moments++; S.moments.push(m);
    const target = m.session_id ? 's:' + m.session_id : m.announcement_id ? 'a:' + m.announcement_id : null;
    addNode({ id: 'm:' + m.id, type: 'moment', label: m.headline, val: 7, moment: m });
    if (target) {
      if (m.session_id) { addNode({ id: target, type: 'session', label: S.sessions.get(m.session_id)?.title || '', val: 5, session: S.sessions.get(m.session_id) }); S.hot.set(m.session_id, ev.t); }
      addLink('m:' + m.id, target, 'of', true);
    }
    for (const pid of (m.evidence || []).slice(-4)) if (S.nodeIx.has('p:' + pid)) addLink('m:' + m.id, 'p:' + pid, 'evidence', false);
    log('curator', `<b>moment</b> · ${esc(m.headline)}${m.room ? ' · ' + esc(m.room.split('@')[0]) : ''}`, ev.t);
    const card = document.createElement('div');
    card.className = 'card moment';
    card.innerHTML = `<div class="h">🔥 ${esc(m.headline)}</div><div class="m">${esc((m.detail || '').slice(0, 160))}</div><div class="t"><span>${fmtTime(ev.t, S.meta.conf.tz)}</span>${m.room ? `<span>${esc(m.room.split('@')[0])}</span>` : ''}<span>${esc(m.kind)}</span></div>`;
    card.onclick = () => (m.session_id ? openSession(m.session_id) : m.announcement_id ? openAnn(m.announcement_id) : null);
    $('#moments').prepend(card); $('#t-moments').textContent = S.counts.moments;
    renderCounters();
  }

  function track(sid, t) { const a = S.activity.get(sid) || []; a.push(t); S.activity.set(sid, a); }
  const recent = (sid, win = 1800) => (S.activity.get(sid) || []).filter((t) => t >= S.clock - win && t <= S.clock + 60).length;

  // ---------- rooms --------------------------------------------------------------------
  function renderRooms() {
    if (!S.meta) return;
    const now = S.clock; const tz = S.meta.conf.tz;
    const live = S.meta.sessions.filter((s) => s.start_epoch && s.start_epoch - 900 <= now && s.end_epoch + 300 >= now);
    const byRoom = new Map();
    for (const s of live) { const r = s.room || 'TBA'; if (!byRoom.has(r)) byRoom.set(r, []); byRoom.get(r).push(s); }
    const rooms = [...byRoom.entries()].map(([room, ss]) => ({ room, ss, act: Math.max(...ss.map((s) => recent(s.id))), hot: ss.some((s) => (S.hot.get(s.id) || 0) >= now - 2400) }))
      .sort((a, b) => b.hot - a.hot || b.act - a.act || a.room.localeCompare(b.room));
    $('#rooms-sub').textContent = rooms.length ? `${live.length} sessions in ${rooms.length} rooms` : 'nothing scheduled at this minute';
    $('#rooms').innerHTML = rooms.slice(0, 14).map(({ room, ss, act, hot }) => ss.slice(0, 2).map((s) => {
      const starts = s.start_epoch > now ? `starts in ${Math.ceil((s.start_epoch - now) / 60)} min` : s.end_epoch < now ? 'just ended' : `${Math.floor((now - s.start_epoch) / 60)} min in`;
      return `<div class="room ${hot ? 'hot' : ''}" data-sid="${s.id}"><div class="rn"><span>${esc(room.split('@')[0].trim())}</span><span class="ago">${starts}</span></div>
        <div class="st">${esc(s.title)}${s.replayable ? '<span class="badge rec">on-demand later</span>' : '<span class="badge only">live only</span>'}${hot ? '<span class="badge hot">moment</span>' : ''}</div>
        <div class="sp">${esc((s.speakers || []).slice(0, 3).join(', '))}${s.speakers?.length > 3 ? ' +' + (s.speakers.length - 3) : ''} · ${esc(s.start_local)}–${esc(s.end_local)}</div>
        <div class="meter"><i style="width:${Math.min(100, recent(s.id) * 14)}%"></i></div></div>`;
    }).join('')).join('');
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
  }

  // ---------- log ----------------------------------------------------------------------
  function log(agent, html, t) {
    const el = $('#log'); const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const ln = document.createElement('div'); ln.className = 'ln ' + agent;
    ln.innerHTML = `<span class="t">${t ? fmtTime(t, S.meta.conf.tz) : ''}</span><span class="ag">${agent.toUpperCase()}</span>${html}`;
    el.appendChild(ln);
    while (el.children.length > 350) el.removeChild(el.firstChild);
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // ---------- graph --------------------------------------------------------------------
  function initGraph() {
    const el = $('#graph');
    S.g = ForceGraph()(el)
      .graphData(S.gd).nodeId('id').nodeVal((n) => n.val || 1).nodeRelSize(3.2)
      .nodeColor((n) => n.type === 'post' ? (COLORS[n.platform] || COLORS.post) : COLORS[n.type])
      .nodeLabel((n) => n.type === 'post' ? `${n.label}: ${(n.post?.text || '').slice(0, 140)}` : n.label)
      .linkColor((l) => l.kind === 'about' ? 'rgba(124,92,255,.55)' : l.kind === 'of' || l.kind === 'evidence' ? 'rgba(251,113,133,.5)' : l.kind === 'reacts' ? 'rgba(245,158,11,.5)' : 'rgba(148,163,184,.25)')
      .linkWidth((l) => (l.kind === 'about' ? 1.4 : 0.8))
      .linkDirectionalParticles((l) => (l.fresh ? 3 : 0)).linkDirectionalParticleWidth(2.2).linkDirectionalParticleSpeed(0.012)
      .nodeCanvasObjectMode(() => 'after')
      .nodeCanvasObject((n, ctx, scale) => {
        if (!['session', 'ann', 'moment', 'speaker', 'facet'].includes(n.type)) return;
        if (scale < 0.9 && n.type !== 'moment') return;
        const label = n.type === 'moment' ? '🔥 ' + n.label.slice(0, 48) : n.label.slice(0, n.type === 'session' ? 44 : 28);
        const size = Math.max(10 / scale, 2.6);
        ctx.font = `${n.type === 'speaker' || n.type === 'facet' ? 500 : 600} ${size}px Inter, sans-serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        ctx.fillStyle = 'rgba(11,15,23,.75)';
        const w = ctx.measureText(label).width + 6;
        ctx.fillRect(n.x - w / 2, n.y + (n.val || 1) + 3, w, size + 3);
        ctx.fillStyle = n.type === 'session' ? '#ddd6fe' : n.type === 'ann' ? '#fde68a' : n.type === 'moment' ? '#fecdd3' : n.type === 'facet' ? '#a7f3d0' : '#a5f3fc';
        ctx.fillText(label, n.x, n.y + (n.val || 1) + 4.5);
      })
      .onNodeClick((n) => openNode(n))
      .d3AlphaDecay(0.03).d3VelocityDecay(0.3).cooldownTicks(200).warmupTicks(0)
      .backgroundColor('rgba(0,0,0,0)');
    S.g.d3Force('charge').strength(-45);
    new ResizeObserver(() => S.g.width(el.clientWidth).height(el.clientHeight)).observe(el);
    S.g.width(el.clientWidth).height(el.clientHeight);
  }
  function addNode(n) {
    if (S.nodeIx.has(n.id)) return S.nodeIx.get(n.id);
    S.nodeIx.set(n.id, n); S.gd.nodes.push(n); scheduleFlush(); return n;
  }
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
      // keep the picture readable: cap floating (unlinked) posts
      const floating = S.gd.nodes.filter((n) => n.type === 'post' && !n.linked);
      if (floating.length > 160) { const drop = new Set(floating.slice(0, floating.length - 160).map((n) => n.id)); S.gd.nodes = S.gd.nodes.filter((n) => !drop.has(n.id)); drop.forEach((id) => S.nodeIx.delete(id)); S.gd.links = S.gd.links.filter((l) => !drop.has(l.source.id || l.source) && !drop.has(l.target.id || l.target)); }
      S.g.graphData(S.gd); renderCounters();
    }, 120);
  }

  // ---------- details ------------------------------------------------------------------
  function showDetail(html) { $('#detail-body').innerHTML = html; $('#detail').hidden = false; }
  $('#detail-close').onclick = () => ($('#detail').hidden = true);
  function openNode(n) {
    if (n.type === 'post') return openPost(n.post);
    if (n.type === 'session') return openSession(n.id.slice(2));
    if (n.type === 'ann') return openAnn(n.id.slice(2));
    if (n.type === 'moment') return showDetail(`<h3>🔥 ${esc(n.moment.headline)}</h3><div class="k">why it fired</div><div>${esc(n.moment.detail || '')}</div><div class="k">evidence</div>${(n.moment.evidence || []).map((id) => S.posts.get(id)).filter(Boolean).map((p) => `<div class="post">${esc(p.text.slice(0, 160))}<div class="m">${esc(p.author || '@' + p.handle)} · ${esc(p.platform)}</div></div>`).join('')}${n.moment.session_id ? `<p><button class="btn small" onclick="window.__openSession('${n.moment.session_id}')">open session</button></p>` : ''}`);
    if (n.type === 'speaker') { const ss = S.meta.sessions.filter((s) => (s.speakers || []).includes(n.label)); return showDetail(`<h3>${esc(n.label)}</h3><div class="k">speaking at</div>${ss.map((s) => `<div class="post"><b>${esc(s.title)}</b><div class="m">${esc(s.room.split('@')[0])} · ${esc(s.day)} ${esc(s.start_local)}</div></div>`).join('') || '<div class="ev">no session found in the agenda</div>'}`); }
    if (n.type === 'facet') return showDetail(`<h3>#${esc(n.label)}</h3><div class="ev">A stack facet: the shared vocabulary that joins repos, sessions, announcements and posts. Paste a repo below to see what reaches you through it.</div>`);
  }
  window.__openSession = (id) => openSession(id);

  async function openPost(p) {
    showDetail(`<h3>${esc(p.author || '@' + p.handle)} on ${esc(p.platform)}</h3><div>${esc(p.text.slice(0, 500))}</div><div class="k">asking the graph…</div>`);
    const w = await (await fetch(`/api/conf/${S.conf}/why/${p.id}`)).json();
    let html = `<h3>${esc(p.author || '@' + p.handle)} on ${esc(p.platform)} <a href="${esc(p.url)}" target="_blank" rel="noopener">↗</a></h3><div>${esc(p.text.slice(0, 420))}</div>`;
    if (!w.sessions.length && !w.mentions.length && !w.announcements.length) html += `<div class="k">resolution</div><div class="ev">Not tied to one session. ${w.facets.length ? 'Tagged ' + w.facets.map((f) => `<span class="facet">${esc(f)}</span>`).join('') : ''}</div>`;
    for (const s of w.sessions) {
      html += `<div class="k">why it sits under this session</div>`;
      html += `<div class="hop"><span class="n post">Post</span><span class="e">—ABOUT_SESSION ${s.confidence.toFixed(2)}→</span><span class="n session">${esc(s.title.slice(0, 50))}</span></div>`;
      if (s.author_is_speaker?.length) html += `<div class="hop"><span class="n post">Post</span><span class="e">—BY→</span><span class="n author">Author</span><span class="e">—IS→</span><span class="n speaker">${esc(s.author_is_speaker[0])}</span><span class="e">←PRESENTED_BY—</span><span class="n session">Session</span></div>`;
      html += `<div>${(s.signals || []).map((x) => `<span class="sig">${esc(x)}</span>`).join('')}</div><div class="ev" style="margin-top:4px">${esc(s.reason)}</div>`;
      html += `<div class="ev">${esc(s.room?.split('@')[0] || '')} · ${esc(s.start_local || '')} · speakers: ${esc((s.speakers || []).join(', '))}</div>`;
    }
    if (w.mentions.length) html += `<div class="k">mentions</div><div>${w.mentions.map((m) => `<span class="n speaker sig">${esc(m)}</span>`).join(' ')}</div>`;
    if (w.announcements.length) html += `<div class="k">reacting to</div><div>${w.announcements.map((m) => `<div class="hop"><span class="n post">Post</span><span class="e">—ABOUT_ANNOUNCEMENT→</span><span class="n ann">${esc(m.slice(0, 50))}</span></div>`).join('')}</div>`;
    html += `<div class="k">cypher</div><div class="path">${esc(w.cypher)}</div>`;
    showDetail(html);
  }

  async function openSession(id) {
    const s = S.sessions.get(id);
    showDetail(`<h3>${esc(s?.title || '')}</h3><div class="k">asking the graph…</div>`);
    const d = await (await fetch(`/api/conf/${S.conf}/session/${id}`)).json();
    let html = `<h3>${esc(d.title)}</h3><div class="ev">${esc(d.room?.split('@')[0] || '')} · ${esc(d.day)} ${esc(d.start_local)}–${esc(d.end_local)} ${d.replayable ? '<span class="badge rec">on-demand later</span>' : '<span class="badge only">live only</span>'}</div>`;
    html += `<div class="ev" style="margin-top:4px">${d.speakers.map((x) => `<b>${esc(x.name)}</b> (${esc(x.company)})`).join(', ')}</div>`;
    if (d.facets?.length) html += `<div style="margin-top:6px">${d.facets.map((f) => `<span class="facet">${esc(f)}</span>`).join('')}</div>`;
    if (d.moments?.length) html += `<div class="k">moments</div>${d.moments.map((m) => `<div class="post">🔥 ${esc(m.headline)}<div class="m">${esc((m.ts || '').slice(11, 16))} UTC</div></div>`).join('')}`;
    html += `<div class="k">${d.posts.length} posts resolved here</div>` + d.posts.slice(0, 8).map((p) => `<div class="post">${esc((p.text || '').slice(0, 150))}<div class="m">${esc(p.author || '')} · ${esc(p.platform)} · <span class="conf">${(p.confidence || 0).toFixed(2)}</span> ${esc(p.reason || '')}</div></div>`).join('');
    if (d.overlaps?.length) html += `<div class="k">overlaps with (the conflict graph)</div>${d.overlaps.slice(0, 6).map((o) => `<div class="post"><b>${esc(o.title)}</b><div class="m">${esc(o.room?.split('@')[0] || '')} · ${esc(o.start_local || '')}</div></div>`).join('')}`;
    if (d.artifacts?.length) html += `<div class="k">artifacts shared</div>${d.artifacts.map((a) => `<div class="post"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.kind)} · ${esc(a.url.slice(0, 60))}</a></div>`).join('')}`;
    html += `<div class="k">cypher</div><div class="path">${esc(d.cypher)}</div><p><a href="${esc(d.url)}" target="_blank" rel="noopener">session page ↗</a></p>`;
    showDetail(html);
  }

  function openAnn(id) {
    const a = S.anns.get(id); if (!a) return;
    showDetail(`<h3>${esc(a.title)}</h3><div class="ev">${esc(a.product)} · ${esc(a.status)} · ${esc((a.ts || '').slice(0, 16))} UTC</div><div style="margin-top:8px">${esc(a.summary || '')}</div><div class="k">touches</div><div>${(a.facets || []).map((f) => `<span class="facet">${esc(f)}</span>`).join('')}</div><div class="k">path to you</div><div class="hop"><span class="n ann">Announcement</span><span class="e">—AFFECTS→</span><span class="n facet">facet</span><span class="e">←USES—</span><span class="n">your Repo</span></div><p><a href="${esc(a.url)}" target="_blank" rel="noopener">read it ↗</a></p>`);
  }

  // ---------- ask ----------------------------------------------------------------------
  async function ask(body) {
    const out = $('#ask-out'); out.innerHTML = '<div class="ev">asking FalkorDB…</div>';
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
    const out = $('#yours-out'); out.innerHTML = '<div class="ev">reading the repo’s manifests, tagging facets, walking the graph…</div>';
    const res = await fetch(`/api/conf/${S.conf}/repo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
    const r = await res.json();
    if (!res.ok) { out.innerHTML = `<div class="ev">${esc(r.detail || 'failed')}</div>`; return; }
    const repo = r.repo;
    addNode({ id: 'r:' + repo.name, type: 'facet', label: repo.name, val: 6 });
    for (const f of repo.facets) { addNode({ id: 'f:' + f, type: 'facet', label: f, val: 3 }); addLink('r:' + repo.name, 'f:' + f, 'uses', true); }
    log('graph', `repo <b>${esc(repo.name)}</b> → facets ${repo.facets.map(esc).join(', ')} · ${r.sessions.length} sessions and ${r.announcements.length} announcements reach it`);
    out.innerHTML = `
      <div class="col"><h4>${esc(repo.name)} · stack facets</h4>${repo.facets.map((f) => `<div class="item"><div class="h"><span class="facet">${esc(f)}</span></div><div class="m">${esc((repo.evidence[f] || []).slice(0, 3).join(' · '))}</div></div>`).join('')}</div>
      <div class="col"><h4>sessions for you</h4>${r.sessions.map((s) => `<div class="item" onclick="window.__openSession('${s.id}')"><div class="h">${esc(s.title)}</div><div class="m">${esc(s.room?.split('@')[0] || '')} · ${esc(s.day)} ${esc(s.start_local)} · ${s.replayable ? 'on-demand later' : 'live only'} · ${s.posts} posts</div><div class="m">via ${(s.via || []).map((f) => `<span class="facet">${esc(f)}</span>`).join('')}</div></div>`).join('') || '<div class="ev">nothing matched</div>'}</div>
      <div class="col"><h4>announcements that touch your stack</h4>${r.announcements.map((a) => `<div class="item" onclick="window.open('${esc(a.url)}','_blank')"><div class="h">${esc(a.title)}</div><div class="m">${esc(a.status)} · ${a.posts} reactions · via ${(a.via || []).map((f) => `<span class="facet">${esc(f)}</span>`).join('')}</div></div>`).join('') || '<div class="ev">nothing matched</div>'}</div>`;
  }

  // ---------- ui -----------------------------------------------------------------------
  function bindUI() {
    $('#btn-play').onclick = () => (S.playing ? stop() : play());
    $$('#speeds button').forEach((b) => (b.onclick = () => { $$('#speeds button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); S.speed = +b.dataset.speed; if (S.playing) play(S.clock); }));
    $$('#jumps button').forEach((b) => (b.onclick = () => {
      const j = b.dataset.jump; const at = j === 'day1' ? dayEpoch(0, 15, 30) : j === 'keynote' ? dayEpoch(0, 15, 55) : j === 'day1pm' ? dayEpoch(0, 21, 0) : dayEpoch(1, 15, 30);
      resetView(); S.clock = at; renderClock(); renderRooms(); play(at);
    }));
    $$('#tabs button').forEach((b) => (b.onclick = () => { $$('#tabs button').forEach((x) => x.classList.remove('on')); b.classList.add('on'); $$('.tabpane').forEach((p) => p.classList.remove('on')); $('#tab-' + b.dataset.tab).classList.add('on'); }));
    $('#ask-form').onsubmit = (e) => { e.preventDefault(); const q = $('#ask-input').value.trim(); if (q) ask({ q }); };
    $('#repo-form').onsubmit = (e) => { e.preventDefault(); buildMine($('#repo-input').value.trim()); };
    $('#btn-replay').onclick = () => { $('#console').scrollIntoView({ behavior: 'smooth' }); loadConf('universe25').then(() => { const at = dayEpoch(0, 15, 30); resetView(); S.clock = at; renderClock(); play(at); }); };
    $('#btn-live').onclick = () => { $('#console').scrollIntoView({ behavior: 'smooth' }); const c = S.confs.find((x) => x.id === 'universe26'); if (c?.built) loadConf('universe26'); else alert('Universe 2026 opens Wednesday 28 October. The agenda is loaded; posts start flowing when the doors open.'); };
  }

  boot();
})();
