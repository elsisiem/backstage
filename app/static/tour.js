/* Backstage tour: one real post followed through every stage.
   Reads /api/conf/universe25/story once (precomputed from the graph), then runs entirely in the page:
   an SVG trajectory where the active edge marches and the post travels, a step rail with progress,
   and the real artifact for each step on the right. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tag = (t, cls = '') => `<em class="tag ${cls}">${esc(t)}</em>`;
  const cut = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));
  const SIG = [['author', 'author is a speaker'], ['mention', 'names a speaker'], ['title', 'quotes the title'], ['semantic', 'close to the abstract'],
    ['product', 'names a product the talk covers'], ['time', 'posted inside the slot'], ['stage', 'said on stage']];
  const hasSig = (sig, k) => (k === 'stage' ? !!(sig.keyword || sig.on_stage) : !!sig[k]);

  const STEPS = [
    { n: '01', t: 'Published', ms: 6500 },
    { n: '02', t: 'Listen', ms: 7000 },
    { n: '03', t: 'Resolve', ms: 9000 },
    { n: '04', t: 'Write', ms: 7000 },
    { n: '05', t: 'Moment', ms: 8500 },
    { n: '06', t: 'You', ms: 8000 },
    { n: '07', t: 'The room', ms: 9000 },
  ];
  const LAST = STEPS.length - 1;
  let D = null, step = 0, playing = true, timer = null;

  // ---------- geometry (viewBox units) -------------------------------------------------
  const VB = { w: 1000, h: 420 };
  const N = {
    bsky: { x: 78, y: 112, w: 112, h: 32, l: 'BLUESKY' },
    linkedin: { x: 78, y: 172, w: 112, h: 32, l: 'LINKEDIN' },
    x: { x: 78, y: 232, w: 112, h: 32, l: 'X' },
    blog: { x: 78, y: 292, w: 112, h: 32, l: 'GITHUB.BLOG' },
    listener: { x: 246, y: 172, w: 124, h: 56, l: 'LISTENER', s: 'time from the ID' },
    resolver: { x: 412, y: 172, w: 124, h: 56, l: 'RESOLVER', s: '7 signals' },
    c0: { x: 594, y: 172, w: 196, h: 48 },
    c1: { x: 594, y: 94, w: 196, h: 48 },
    c2: { x: 594, y: 250, w: 196, h: 48 },
    graph: { x: 800, y: 172, w: 150, h: 108, l: 'FALKORDB', s: 'graph · universe25', top: true },
    moment: { x: 800, y: 52, w: 170, h: 46, l: 'MOMENT' },
    you: { x: 942, y: 172, w: 96, h: 56, l: 'YOU' },
    room: { x: 412, y: 362, w: 160, h: 44, l: 'THE ROOM', s: 'one line back' },
  };
  const E = {
    'bsky-l': 'M134 112 C158 112 166 160 184 166',
    'linkedin-l': 'M134 172 H184',
    'x-l': 'M134 232 C158 232 166 184 184 178',
    'l-r': 'M308 172 H350',
    'r-c1': 'M474 172 C484 172 484 94 496 94',
    'r-c0': 'M474 172 H496',
    'r-c2': 'M474 172 C484 172 484 250 496 250',
    'c0-g': 'M692 172 H730',
    'g-m': 'M800 120 V75',
    'blog-g': 'M134 292 C330 330 640 330 748 224',
    'g-y': 'M870 172 H894',
    'room-l': 'M332 362 C276 362 246 330 246 200',
  };

  const svgNode = (k, label, sub, extra = '') => {
    const n = N[k];
    return `<g class="node" id="n-${k}">
      <rect x="${n.x - n.w / 2}" y="${n.y - n.h / 2}" width="${n.w}" height="${n.h}"/>
      <text class="nl" x="${n.x}" y="${n.top ? n.y - n.h / 2 + 22 : n.y + (sub ? -3 : 5)}" text-anchor="middle">${esc(label)}</text>
      ${sub ? `<text class="ns" x="${n.x}" y="${n.top ? n.y - n.h / 2 + 37 : n.y + 14}" text-anchor="middle">${esc(sub)}</text>` : ''}${extra}</g>`;
  };

  function buildSvg() {
    const r = D.resolve;
    const cands = r.candidates.slice(0, 3);
    const candSvg = cands.map((c, i) => {
      const k = 'c' + i, n = N[k], w = n.w - 20;
      return `<g class="node cand ${c.winner ? 'win' : 'lose'}" id="n-${k}">
        <rect x="${n.x - n.w / 2}" y="${n.y - n.h / 2}" width="${n.w}" height="${n.h}"/>
        <text class="nl" x="${n.x - n.w / 2 + 10}" y="${n.y - 4}" style="font-size:12px;letter-spacing:0">${esc(cut(c.title, 24))}</text>
        <rect class="bar-bg" x="${n.x - n.w / 2 + 10}" y="${n.y + 8}" width="${w - 44}" height="4"/>
        <rect class="bar-fg" x="${n.x - n.w / 2 + 10}" y="${n.y + 8}" width="0" height="4" data-w="${((w - 44) * Math.min(1, c.score)).toFixed(1)}"/>
        <text class="ns" x="${n.x + n.w / 2 - 10}" y="${n.y + 13}" text-anchor="end" style="font-size:11.5px;font-weight:600">${c.score.toFixed(2)}</text></g>`;
    }).join('');
    // a small constellation inside the graph node: the post joins it at step 04
    const g = N.graph, cx = g.x, cy = g.y + 20;
    const pts = [[-44, -8], [-24, 8], [-8, -12], [10, 2], [30, -10], [46, 8], [-34, 22], [24, 22]];
    const lines = [[0, 1], [1, 3], [2, 3], [3, 4], [4, 5], [1, 6], [3, 7], [5, 7]];
    const mini = `<g class="mini" id="mini">${lines.map(([a, b]) => `<line x1="${cx + pts[a][0]}" y1="${cy + pts[a][1]}" x2="${cx + pts[b][0]}" y2="${cy + pts[b][1]}"/>`).join('')}
      ${pts.map(([x, y], i) => `<circle cx="${cx + x}" cy="${cy + y}" r="${i === 3 ? 4.5 : 3}" style="fill:${i === 3 ? 'var(--ink)' : 'var(--faint)'}"/>`).join('')}
      <line id="mini-link" x1="${cx + 10}" y1="${cy + 2}" x2="${cx - 4}" y2="${cy + 28}" style="opacity:0;stroke:var(--accent)"/>
      <circle id="mini-post" cx="${cx - 4}" cy="${cy + 28}" r="4" style="opacity:0;fill:var(--accent)"/></g>`;
    const callout = (id, x, y, w, l1, l2) => `<g class="callout" id="${id}" style="opacity:0"><rect x="${x}" y="${y}" width="${w}" height="40"/>
      <text class="c1" x="${x + 10}" y="${y + 16}">${esc(l1)}</text><text class="c2" x="${x + 10}" y="${y + 31}">${esc(l2)}</text></g>`;
    const dec = D.decode;
    const decodeLine = dec.kind === 'linkedin' ? `activity ${dec.id} >> ${dec.shift}` : dec.kind === 'bsky' ? `rkey ${dec.id} >> 10` : 'timestamp from the payload';
    const win = cands.find((c) => c.winner) || cands[0];
    const runner = cands.find((c) => !c.winner);
    return `<svg viewBox="0 0 ${VB.w} ${VB.h}" role="img" aria-label="Trajectory of one post through Backstage">
      <defs><pattern id="tg" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" class="tg-grid" stroke-width="0.6"/></pattern>
        <marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" style="fill:var(--accent)"/></marker></defs>
      <rect width="${VB.w}" height="${VB.h}" fill="url(#tg)" opacity=".6"/>
      ${Object.entries(E).map(([k, d]) => `<path class="edge" id="e-${k}" d="${d}"/>`).join('')}
      ${svgNode('bsky', 'BLUESKY')}${svgNode('linkedin', 'LINKEDIN')}${svgNode('x', 'X')}${svgNode('blog', 'GITHUB.BLOG')}
      ${svgNode('listener', 'LISTENER', 'time from the ID')}${svgNode('resolver', 'RESOLVER', `${r.in_progress} in progress`)}
      ${candSvg}
      ${svgNode('graph', 'FALKORDB', 'universe25', mini)}
      ${svgNode('moment', 'MOMENT', D.moment ? `${D.moment.evidence.length} posts · ${D.moment.kind}` : 'curator')}
      ${svgNode('you', 'YOU', cut(D.repo.name.split('/')[1] || D.repo.name, 14))}
      ${svgNode('room', 'THE ROOM', 'one line back')}
      ${callout('co-1', 160, 222, 268, decodeLine, `= ${dec.ms || ''} ms · ${D.post.local} PT`.replace('=  ms · ', '= '))}
      ${callout('co-2', 496, 292, 264, `threshold ${r.threshold.toFixed(2)} · ${r.in_progress} sessions live`, `${cut(win.title, 18)} ${win.score.toFixed(2)}${runner ? ` vs ${runner.score.toFixed(2)}` : ''}`)}
      ${callout('co-3', 700, 244, 224, 'MERGE (p)-[:ABOUT_SESSION]->(s)', `confidence ${r.edge.confidence.toFixed(2)} · ${r.edge.signals.length} signals`)}
      <g class="pkt" id="pkt"><rect width="104" height="20"/><text x="52" y="14" text-anchor="middle" id="pkt-t">POST</text></g>
      <g class="pkt hl" id="note" style="opacity:0"><rect width="104" height="20"/><text x="52" y="14" text-anchor="middle">NOTE</text></g>
    </svg>`;
  }

  // where the post sits at each step (top-left of the packet)
  function pkt(i) {
    const src = N[D.post.platform] || N.linkedin;
    const at = (n, dy) => ({ x: n.x - 52, y: n.y - n.h / 2 - 10 + dy });
    const p = [at(src, -12), at(N.listener, -14), at(N.resolver, -14), at(N.graph, -14), { x: N.graph.x - 52, y: 87 }, at(N.you, -14), at(N.listener, -14)][i];
    return { ...p, label: i === 0 ? 'POST' : D.post.local.replace(/:(\d\d)$/, ':$1') + (D.decode.ms ? '.' + String(D.decode.ms % 1000).padStart(3, '0') : ''), hl: i >= 3 };
  }

  // ---------- per-step state ----------------------------------------------------------
  const ACTIVE = (src) => [
    { on: [src], edges: [] },
    { on: ['listener'], edges: [src + '-l'], co: 'co-1' },
    { on: ['resolver', 'c0', 'c1', 'c2'], edges: ['l-r', 'r-c0', 'r-c1', 'r-c2'], co: 'co-2' },
    { on: ['c0', 'graph'], edges: ['c0-g'], co: 'co-3' },
    { on: ['graph', 'moment', 'blog'], edges: ['g-m', 'blog-g'] },
    { on: ['graph', 'you'], edges: ['g-y'] },
    { on: ['room', 'listener'], edges: ['room-l'] },
  ];
  const ACCENT = { 0: [], 1: [], 2: [], 3: ['graph'], 4: ['moment'], 5: ['you'], 6: ['room'] };

  function paintSvg() {
    const src = D.post.platform in N ? D.post.platform : 'linkedin';
    const A = ACTIVE(src);
    const visitedNodes = new Set(A.slice(0, step).flatMap((s) => s.on));
    const visitedEdges = new Set(A.slice(0, step).flatMap((s) => s.edges));
    document.querySelectorAll('.node').forEach((g) => {
      const k = g.id.slice(2);
      const on = A[step].on.includes(k);
      g.classList.toggle('on', on);
      g.classList.toggle('done', !on && visitedNodes.has(k));
      g.classList.toggle('ac', on && (ACCENT[step].includes(k) || (k === src && step === 0)));
      if (g.classList.contains('cand')) g.style.opacity = step >= 3 && g.classList.contains('lose') ? '.4' : '1';
    });
    document.querySelectorAll('.edge').forEach((p) => {
      const k = p.id.slice(2);
      const on = A[step].edges.includes(k);
      p.classList.toggle('on', on);
      p.classList.toggle('done', !on && visitedEdges.has(k));
      p.setAttribute('marker-end', on ? 'url(#arw)' : '');
    });
    // winner keeps its accent once chosen
    document.querySelectorAll('.cand.win').forEach((g) => g.querySelector('rect').style.stroke = step >= 2 ? 'var(--accent)' : '');
    document.querySelectorAll('.bar-fg').forEach((b) => b.setAttribute('width', step >= 2 ? b.dataset.w : 0));
    ['co-1', 'co-2', 'co-3'].forEach((id) => { $('#' + id).style.opacity = A[step].co === id ? 1 : 0; });
    const showPost = step >= 3;
    $('#mini-post').style.opacity = showPost ? 1 : 0;
    $('#mini-link').style.opacity = showPost ? 1 : 0;
    $('#mini').style.opacity = step >= 3 ? 1 : 0.45;
    const p = pkt(step);
    const pk = $('#pkt');
    pk.style.transform = `translate(${p.x}px, ${p.y}px)`;
    pk.style.opacity = step === 6 ? 0.25 : 1;
    pk.classList.toggle('hl', p.hl);
    $('#pkt-t').textContent = p.label;
    const note = $('#note');
    if (step === 6) {
      note.style.transition = 'none';
      note.style.transform = `translate(${N.room.x - 52}px, ${N.room.y - 42}px)`;
      note.getBoundingClientRect();
      note.style.transition = '';
      requestAnimationFrame(() => { note.style.opacity = 1; note.style.transform = `translate(${N.listener.x - 52}px, ${N.listener.y + 40}px)`; });
    } else { note.style.opacity = 0; }
  }

  // ---------- captions and artifacts -------------------------------------------------------
  function caption(i) {
    const P = D.post, r = D.resolve, win = r.candidates.find((c) => c.winner) || r.candidates[0], runner = r.candidates.find((c) => !c.winner);
    const plat = { linkedin: 'LinkedIn', bsky: 'Bluesky', x: 'X' }[P.platform] || P.platform;
    return [
      `${P.local.slice(0, 5)} PT on day one. ${P.author}, a speaker at the ${D.session.title.toLowerCase()}, posts on ${plat}. Discovery finds it; the payload carries a date but no time of day.`,
      D.decode.kind === 'linkedin'
        ? `The Listener reads the time out of the post ID. LinkedIn activity IDs carry the millisecond in their top 41 bits, so this post is pinned to ${P.local}.${String(D.decode.ms % 1000).padStart(3, '0')}.`
        : `The Listener reads the time out of the post key. Bluesky record keys are timestamps in base32, so this post is pinned to the microsecond.`,
      `The Resolver scores the post against every session. ${win.title} wins at ${win.score.toFixed(2)}${runner ? `, against ${runner.score.toFixed(2)} for the runner-up` : ''}. Every signal it used goes on the edge.`,
      `The edge is written into FalkorDB with its confidence, reason and signals. Any screen can ask why this post is here and get a path back, not a guess.`,
      D.moment ? `${D.moment.evidence.length - 1} other people were posting about the same announcement. The Curator turns that into a Moment: walk over now, or jump to these minutes later.` : 'The Curator watches for bursts and turns them into Moments.',
      `Paste a repository and its manifests become facets. Two hops later you know which announcements and sessions from that morning touch your stack.`,
      `And the line back. Anyone inside a room can send one sentence without an account. It is resolved like this post and reaches every open console the moment it lands.`,
    ][i];
  }

  function artifact(i) {
    const P = D.post, r = D.resolve, S = D.session;
    const plat = { linkedin: 'LinkedIn', bsky: 'Bluesky', x: 'X' }[P.platform] || P.platform;
    if (i === 0) return `<div class="art"><div class="art-b">
        <div class="who"><b>${esc(P.author)}</b><span>${esc(plat)} · @${esc(P.handle || '')}</span></div>
        <div class="quoteblock">${esc(cut(P.text, 420))}</div>
        <p class="muted" style="margin-top:10px">${esc(P.date)}. <a href="${esc(P.url)}" target="_blank" rel="noopener">Open the original</a></p></div>
      <div class="art-b"><span class="label">How it was found</span>
        <p>Exa searches Bluesky and LinkedIn inside the conference dates. Bright Data covers what a plain fetch cannot reach: X through its SERP API, and Bluesky's own API, which blocks ordinary networks.</p></div></div>`;
    if (i === 1) {
      const d = D.decode;
      const body = d.kind === 'linkedin'
        ? `<dl class="kv"><dt>activity id</dt><dd>${esc(d.id)}</dd><dt>bit length</dt><dd>${d.bits}</dd><dt>shift right</dt><dd>${d.shift} bits, keep the top 41</dd><dt>milliseconds</dt><dd>${d.ms}</dd><dt>UTC</dt><dd>${esc(d.iso)}</dd><dt>local</dt><dd class="hl">${esc(P.local)} PT</dd></dl>`
        : `<dl class="kv"><dt>record key</dt><dd>${esc(d.id)}</dd><dt>base32 sortable</dt><dd>${d.int}</dd><dt>microseconds</dt><dd>${d.us}</dd><dt>UTC</dt><dd>${esc(d.iso)}</dd><dt>local</dt><dd class="hl">${esc(P.local)} PT</dd></dl>`;
      return `<div class="art"><div class="art-b"><span class="label">Timestamp from the ID</span>${body}</div>
        <div class="art-b"><p class="muted">No API returned this time. Checked against a live Bluesky record, the decode matches to the millisecond. That is what makes the replay the real minute-by-minute day.</p></div></div>`;
    }
    if (i === 2) {
      const rows = r.candidates.slice(0, 3).map((c) => `<tr class="${c.winner ? 'win' : ''}"><td class="ttl">${esc(cut(c.title, 44))}<small>${esc(c.room)} · ${esc(c.start_local)}</small></td>
        <td><span class="sig">${SIG.map(([k, lab]) => `<i class="${hasSig(c.signals, k) ? 'on' : ''}" title="${esc(lab)}"></i>`).join('')}</span></td><td class="score">${c.score.toFixed(2)}</td></tr>`).join('');
      return `<div class="art accent"><div class="art-b"><div class="art-row"><span class="label">Candidate sessions</span>${tag(r.in_progress + ' in progress')}</div>
        <table class="cand"><thead><tr><th>Session</th><th>Signals</th><th style="text-align:right">Score</th></tr></thead><tbody>${rows}</tbody></table>
        <div class="legendrow">${SIG.map(([k], j) => `<span>${j + 1} ${k}</span>`).join('')}</div></div>
        <div class="art-b"><span class="label">Reason written on the edge</span><p>${esc(r.edge.reason)}</p>
        <p class="muted">Threshold ${r.threshold.toFixed(2)}. An LLM only steps in when two sessions are within 0.12 of each other.</p></div></div>`;
    }
    if (i === 3) {
      const w = D.write;
      const why = (D.why.sessions || [])[0] || {};
      return `<div class="art"><div class="art-b"><span class="label">The write</span><pre class="code">${esc(w.cypher)}</pre>
        <dl class="kv" style="margin-top:10px"><dt>$c</dt><dd>${w.params.c}</dd><dt>$signals</dt><dd>${esc(w.params.signals.join(', '))}</dd></dl></div>
        <div class="art-b"><span class="label">Read back from FalkorDB</span>
        <div class="hop"><span class="n post">Post</span><span class="e">—BY→</span><span class="n">Author</span><span class="e">—IS→</span><span class="n speaker">${esc((why.author_is_speaker || [P.author])[0])}</span></div>
        <div class="hop"><span class="e">←PRESENTED_BY—</span><span class="n session">${esc(S.title)}</span><span class="e">—IN_ROOM→</span><span class="n">${esc(S.room)}</span></div>
        <p class="muted" style="margin-top:8px">${esc(S.start)} to ${esc(S.end)} · ${S.capacity} seats · ${S.replayable ? 'on demand later' : 'live only'} · speakers ${esc(S.speakers.join(', '))}</p></div></div>`;
    }
    if (i === 4) {
      const M = D.moment;
      if (!M) return '<div class="art"><div class="art-b"><p>No Moment for this post.</p></div></div>';
      return `<div class="art accent"><div class="art-b"><div class="art-row"><span class="label">Moment · ${esc(M.kind)}</span>${tag(M.time + ' PT', 'accent')}</div>
        <p><b>${esc(M.headline)}</b></p>
        ${M.announcement ? `<p class="muted">Shipped ${esc(M.announcement.time)} PT · ${esc(M.announcement.status)} · <a href="${esc(M.announcement.url)}" target="_blank" rel="noopener">changelog</a></p>` : ''}</div>
        <div class="art-b"><span class="label">Evidence</span><ul class="ev-list">${M.evidence.map((e) => `<li class="${e.self ? 'self' : ''}"><span class="t">${esc(e.time)}</span><div><b>${esc(e.author)}</b><span>${esc(cut(e.text, 96))}</span></div></li>`).join('')}</ul></div></div>`;
    }
    if (i === 5) {
      const R = D.repo;
      const shared = new Set(R.shared_with_post || []);
      return `<div class="art"><div class="art-b"><div class="art-row"><span class="label">${esc(R.name)}</span>${tag(R.facets.length + ' facets')}</div>
        <div class="item tags" style="border:0;padding:0;margin:0;cursor:default">${R.facets.map((f) => tag(f.facet, shared.has(f.facet) ? 'accent' : '')).join('')}</div>
        <p class="muted" style="margin-top:8px">Read from the repo's manifests: workflows, devcontainer, Cargo, AGENTS files. Highlighted facets are the ones this post was tagged with.</p></div>
        <div class="art-b"><span class="label">Repo → facet → announcement</span>${R.announcements.slice(0, 3).map((a) => `<div class="hop"><span class="n ann">${esc(cut(a.title, 40))}</span><span class="e">via</span>${a.via.slice(0, 2).map((v) => `<span class="n facet">${esc(v)}</span>`).join('')}</div>`).join('')}</div>
        <div class="art-b"><span class="label">Repo → facet → session</span>${R.sessions.slice(0, 2).map((s) => `<div class="hop"><span class="n session">${esc(cut(s.title, 38))}</span><span class="e">${esc(s.room)} · ${esc(s.start)}</span></div>`).join('')}</div></div>`;
    }
    return `<div class="art accent"><div class="art-b"><span class="label">Report from the room</span>
        <div class="quoteblock">Speaker just demoed the coding agent opening a pull request from Slack. Room is full, overflow at the back.</div>
        <dl class="kv" style="margin-top:10px"><dt>placed under</dt><dd>${esc(S.title)}</dd><dt>signal</dt><dd>reported_from_room</dd><dt>confidence</dt><dd>0.95</dd><dt>delivered to</dt><dd class="hl">every open console</dd></dl></div>
      <div class="art-b"><p class="muted">No account, five reports a minute per person, any language. It becomes a Post node like every other, so the next person who opens the console inherits it.</p></div></div>`;
  }

  // ---------- rail, controls, autoplay --------------------------------------------------
  function renderRail() {
    $('#rail').innerHTML = STEPS.map((s, i) => `<li><button data-i="${i}" class="${i === step ? 'on' : i < step ? 'done' : ''}" aria-current="${i === step ? 'step' : 'false'}" style="--ms:${s.ms}ms">
      <span class="n">${s.n}</span><span class="t">${esc(s.t)}</span><span class="bar"><i class="${i === step && playing && step < LAST ? 'run' : ''}"></i></span></button></li>`).join('');
    $('#rail').querySelectorAll('button').forEach((b) => (b.onclick = () => go(+b.dataset.i)));
  }

  function render() {
    renderRail();
    paintSvg();
    const s = STEPS[step];
    $('#stepno').textContent = `step ${s.n} of ${String(STEPS.length).padStart(2, '0')}`;
    $('#caption').innerHTML = `<span class="lead">${esc(s.t)}</span>${esc(caption(step))}`;
    $('#art-k').textContent = `${s.n} · ${s.t.toUpperCase()}`;
    const art = $('#artifact');
    art.classList.remove('anim-rise'); void art.offsetWidth; art.classList.add('anim-rise');
    art.innerHTML = artifact(step);
    $('#prev').disabled = step === 0;
    $('#next').disabled = step === LAST;
    $('#pause').textContent = step === LAST ? 'Replay' : playing ? 'Pause' : 'Play';
    const end = $('#end');
    end.hidden = step !== LAST;
    if (step === LAST) end.innerHTML = `<div class="endcard"><p>Watch the whole day build itself.</p><p class="muted">The console replays Universe 2025 minute by minute into the same graph, and goes live for Universe 2026 on Wednesday.</p>
      <div class="cta"><a class="btn accent" href="/console?play=keynote&amp;speed=120">Replay the keynote</a><a class="btn" href="/">Back to overview</a></div></div>`;
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (playing && step < LAST) timer = setTimeout(() => go(step + 1), STEPS[step].ms);
  }
  function go(i) { step = Math.max(0, Math.min(LAST, i)); render(); }

  $('#prev').onclick = () => go(step - 1);
  $('#next').onclick = () => go(step + 1);
  $('#pause').onclick = () => { if (step === LAST) { playing = true; go(0); } else { playing = !playing; render(); } };
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select')) return;
    if (e.key === 'ArrowRight') go(step + 1);
    if (e.key === 'ArrowLeft') go(step - 1);
    if (e.key === ' ') { e.preventDefault(); $('#pause').click(); }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(timer); else schedule(); });

  fetch('/api/conf/universe25/story').then((r) => r.json()).then((d) => {
    D = d;
    const P = d.post;
    $('#tour-sub').innerHTML = `A real ${esc({ linkedin: 'LinkedIn', bsky: 'Bluesky', x: 'X' }[P.platform] || '')} post from <b>${esc(P.author)}</b> at <b>${esc(P.local.slice(0, 5))} PT</b> on day one of <b>${esc(d.conf.name)}</b>, followed through every stage of Backstage. Every value on this page comes from the graph.`;
    $('#canvas').innerHTML = buildSvg();
    const start = +(new URL(location.href).searchParams.get('step') || 1) - 1;
    step = Math.max(0, Math.min(LAST, start));
    render();
  }).catch(() => { $('#canvas').innerHTML = '<div class="empty">The tour data did not load. Refresh, or open the console.</div>'; });
})();
