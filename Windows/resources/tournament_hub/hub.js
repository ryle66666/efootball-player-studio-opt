(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('schedule-data').textContent);
  const $ = id => document.getElementById(id);
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const PLAYER = data.player_team_id;
  const teams = new Map(data.teams.map(t => [t.id, t]));
  const byId = new Map(data.fixtures.map(f => [String(f.id), f]));
  const team = id => teams.get(id) || {id:null, name:'待定', own:false};
  const tableStage = s => !!s && (s.type === 'groups' || s.type === 'league');
  const cancelled = f => f.cancelled_by_forfeit === true;
  const hasScore = f => f.played && !f.bye && !cancelled(f) && Array.isArray(f.score) && f.score.length === 2;
  const involves = (f, id) => f.home === id || f.away === id;
  const PER_PAGE = 12, GROUPS_PER_PAGE = 6;
  const STATUS = {champion:'本届冠军', complete:'赛事已结束', waiting:'等待下一阶段', eliminated:'已出局', playing:'征战中'};
  const STAGE_EN = {groups:'GROUP STAGE', league:'LEAGUE PHASE', knockout:'KNOCKOUT', playoff:'PLAY-OFFS'};
  const BALL = '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6"/><path d="M8 4.6l3.1 2.2-1.2 3.6H6.1L4.9 6.8z"/></svg>';
  const TROPHY = '<svg class="trophy" viewBox="0 0 120 150"><path d="M34 14h52v34c0 22-12 36-26 36S34 70 34 48V14Z"/><path d="M34 24H16v10c0 16 10 24 22 26M86 24h18v10c0 16-10 24-22 26"/><path d="M60 84v20M44 104h32l6 14H38l6-14ZM32 118h56v14H32z"/><path d="M60 30l4 9 10 1-7 7 2 10-9-5-9 5 2-10-7-7 10-1Z"/></svg>';

  // ---------- naming ----------
  function roundName(s, r) { return !tableStage(s) && s.bracket[r] ? s.bracket[r].name : '第 ' + (r + 1) + ' 轮'; }
  function roundEn(s, r) {
    if (tableStage(s)) return 'MATCHDAY ' + (r + 1);
    const size = s.bracket[r] && s.bracket[r].size;
    return size === 2 ? 'FINAL' : size === 4 ? 'SEMI-FINALS' : size === 8 ? 'QUARTER-FINALS' : size ? 'ROUND OF ' + size : 'ROUND ' + (r + 1);
  }
  function roundShort(s, r) {
    if (tableStage(s)) return 'MD' + (r + 1);
    const size = s.bracket[r] && s.bracket[r].size;
    return size === 2 ? 'FINAL' : size === 4 ? 'SF' : size === 8 ? 'QF' : size ? 'R' + size : 'R' + (r + 1);
  }
  function groupName(s, g) {
    const group = s.groups.find(x => x.index === g);
    return group ? group.name : g != null ? (g < 26 ? String.fromCharCode(65 + g) : g + 1) + ' 组' : '';
  }
  const stageFixtures = s => data.fixtures.filter(f => f.stage === s.index);
  function roundCount(s) {
    if (tableStage(s)) return s.rules.rounds || 0;
    if (s.bracket.length) return s.bracket.length;
    return stageFixtures(s).reduce((m, f) => Math.max(m, f.round + 1), 0);
  }
  function winnerOf(f) {
    if (!hasScore(f)) return null;
    const [h, a] = f.score;
    if (h !== a) return h > a ? f.home : f.away;
    if (Array.isArray(f.penalties) && f.penalties[0] !== f.penalties[1]) return f.penalties[0] > f.penalties[1] ? f.home : f.away;
    return null;
  }
  function aggregate(list) {
    const out = new Map();
    (list || []).forEach(e => { const k = String(e.id); const row = out.get(k) || {name:e.name, n:0}; row.n++; out.set(k, row); });
    return [...out.values()];
  }
  function toast(message) {
    $('toast').textContent = message; $('toast').hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { $('toast').hidden = true; }, 4200);
  }

  // ---------- team colours from the crests themselves ----------
  const colors = new Map(), loading = new Set();
  const NEUTRAL = 'hsl(228 12% 34%)';
  function fallbackColor(id) {
    const index = data.teams.findIndex(t => t.id === id);
    return `hsl(${((Math.max(0, index) * 137) + 210) % 360} 52% 40%)`;
  }
  function extract(img) {
    const size = 32, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
    const g = canvas.getContext('2d', {willReadFrequently:true}); g.drawImage(img, 0, 0, size, size);
    const px = g.getImageData(0, 0, size, size).data, buckets = Array.from({length:12}, () => ({n:0, r:0, g:0, b:0}));
    let found = 0;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i + 3] < 170) continue;
      const r = px[i], gg = px[i + 1], b = px[i + 2], max = Math.max(r, gg, b), min = Math.min(r, gg, b), l = (max + min) / 510;
      const s = max === min ? 0 : (max - min) / (255 - Math.abs(max + min - 255));
      if (s < .38 || l < .14 || l > .88) continue;
      let h = max === r ? ((gg - b) / (max - min)) % 6 : max === gg ? (b - r) / (max - min) + 2 : (r - gg) / (max - min) + 4;
      h = (h * 60 + 360) % 360;
      const k = Math.floor(h / 30) % 12; buckets[k].n++; buckets[k].r += r; buckets[k].g += gg; buckets[k].b += b; found++;
    }
    if (found < 14) return null;
    const best = buckets.reduce((a, c) => c.n > a.n ? c : a);
    const r = best.r / best.n / 255, gg = best.g / best.n / 255, b = best.b / best.n / 255;
    const max = Math.max(r, gg, b), min = Math.min(r, gg, b), d = max - min;
    let h = d === 0 ? 0 : max === r ? ((gg - b) / d) % 6 : max === gg ? (b - r) / d + 2 : (r - gg) / d + 4;
    h = Math.round((h * 60 + 360) % 360);
    const l = (max + min) / 2, s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
    return `hsl(${h} ${Math.round(Math.max(.55, Math.min(.9, s)) * 100)}% ${Math.round(Math.max(.34, Math.min(.5, l)) * 100)}%)`;
  }
  function colorOf(id) {
    if (id == null) return NEUTRAL;
    if (colors.has(id)) return colors.get(id);
    const t = team(id);
    if (!t.emblem || !t.emblem.uri) { colors.set(id, fallbackColor(id)); return colors.get(id); }
    if (!loading.has(id)) {
      loading.add(id);
      const img = new Image();
      img.onload = () => { let c = null; try { c = extract(img); } catch (_) {} colors.set(id, c || NEUTRAL); paint(id); };
      img.onerror = () => { colors.set(id, fallbackColor(id)); paint(id); };
      img.src = t.emblem.uri;
    }
    return NEUTRAL;
  }
  function paint(id) {
    const value = colors.get(id), key = CSS.escape(String(id));
    ['hc', 'ac', 'tc'].forEach(k => document.querySelectorAll(`[data-${k}="${key}"]`).forEach(el => el.style.setProperty('--' + k, value)));
  }
  function cAttr(map, extra) {
    const attrs = [], style = [];
    Object.entries(map).forEach(([k, id]) => { if (id != null) attrs.push(`data-${k}="${esc(id)}"`); style.push(`--${k}:${colorOf(id)}`); });
    if (extra) style.push(extra);
    return attrs.join(' ') + ` style="${style.join(';')}"`;
  }
  function crest(id, size) {
    const cls = size ? ' ' + size : '';
    if (id == null) return `<span class="crest crest-empty${cls}">?</span>`;
    const t = team(id);
    if (t.emblem && t.emblem.uri) return `<img class="crest${cls}" src="${esc(t.emblem.uri)}" alt="" decoding="async">`;
    return `<span class="crest crest-text${cls}">${esc(Array.from(t.name).slice(0, 2).join(''))}</span>`;
  }

  // ---------- state ----------
  const HUB = data.hub || {team:{enabled:false, allowed:[]}};
  // Until the team is confirmed for this edition the draw stays sealed: only
  // the team page is shown, without groups. Confirming plays the draw once.
  const SEALED = !!HUB.team.needs;
  const HAS_SCHEDULE = data.stages.length > 0 && !SEALED;
  const KEY = 'studio-hub-v1:' + data.uuid;
  const DRAW_KEY = KEY + ':draw:' + data.run_id;
  let saved = {}, drawPending = false;
  try { saved = JSON.parse(sessionStorage.getItem(KEY) || '{}'); drawPending = sessionStorage.getItem(DRAW_KEY) === '1'; } catch (_) { /* storage may be disabled in WebViews */ }
  // Each stage's line-up (groups or ties) is only fixed when that stage starts:
  // later stages seed the qualifiers by previous results. The first time the
  // hub opens after a stage begins, its draw is revealed once (stored in the
  // WebView profile; without storage only the team-confirmation draw plays).
  const REVEAL_KEY = 'studio-hub-v1:revealed:' + data.uuid + ':' + data.run_id;
  function revealedStage() {
    try { const v = localStorage.getItem(REVEAL_KEY); return v == null ? -1 : Number(v); } catch (_) { return null; }
  }
  function markRevealed(k) {
    const seen = revealedStage();
    if (seen === null) return;
    try { localStorage.setItem(REVEAL_KEY, String(Math.max(k, seen))); } catch (_) {}
  }
  function pendingDrawStage() {
    if (!HAS_SCHEDULE || data.completed) return null;
    const k = data.stage_index, s = data.stages[k], seen = revealedStage();
    if (!s || seen === null || seen >= k) return null;
    // Already playing this stage: nothing left to reveal.
    if (stageFixtures(s).some(f => f.played && !f.bye && involves(f, PLAYER))) { markRevealed(k); return null; }
    return k;
  }
  const autoDraw = SEALED ? null : (drawPending || HUB.first_run) && HAS_SCHEDULE ? data.stage_index : pendingDrawStage();
  const FIRST_RUN = !SEALED && HAS_SCHEDULE && (HUB.first_run || drawPending);
  const VIEWS = ['summary', 'team', 'fixtures', 'tables', 'bracket', 'leaders'];
  // The edition is over for the player: finished, champion, or eliminated.
  const ENDED = !SEALED && data.stages.length > 0 && (data.completed || ['eliminated', 'champion', 'complete'].includes(data.player_status));
  const state = {
    view: SEALED ? 'team' : autoDraw != null ? 'draw' : VIEWS.includes(saved.view) && (saved.view !== 'summary' || ENDED) ? saved.view : ENDED ? 'summary' : 'fixtures',
    drawStage: autoDraw != null ? autoDraw : data.stage_index,
    pick: HUB.team.current,
    stage: Number.isInteger(saved.stage) && data.stages[saved.stage] ? saved.stage : Math.min(data.stage_index, Math.max(0, data.stages.length - 1)),
    round: Number.isInteger(saved.round) ? saved.round : null,
    group: 'all', own: false, page: 0, tablePage: null, bracketStart: null,
  };
  const stage = () => data.stages[state.stage];
  const persist = () => { try { sessionStorage.setItem(KEY, JSON.stringify({view:state.view, stage:state.stage, round:state.round})); } catch (_) {} };
  function defaultRound(s) {
    const next = byId.get(String(data.next_fixture_id));
    if (next && next.stage === s.index) return next.round;
    const rows = stageFixtures(s), open = rows.filter(f => !f.played).sort((a, b) => a.round - b.round)[0];
    return open ? open.round : rows.reduce((m, f) => Math.max(m, f.round), 0);
  }
  function roundRows() {
    return stageFixtures(stage()).filter(f => f.round === state.round
      && (state.group === 'all' || String(f.group) === state.group) && (!state.own || involves(f, PLAYER)))
      .sort((a, b) => (a.group ?? 0) - (b.group ?? 0) || String(a.tie || '').localeCompare(String(b.tie || '')) || a.leg - b.leg || a.id - b.id);
  }
  function heroFixture(rows) {
    const mine = rows.filter(f => involves(f, PLAYER));
    return mine.find(f => !f.played) || mine[mine.length - 1] || null;
  }
  function normalize() {
    if (SEALED) { state.view = 'team'; return; }
    if (!HAS_SCHEDULE) { if (state.view !== 'team') state.view = 'team'; return; }
    if (state.view === 'summary' && !ENDED) state.view = 'fixtures';
    if (state.view === 'team' || state.view === 'draw' || state.view === 'summary') return;
    const s = stage();
    if (state.view === 'tables' && !tableStage(s)) state.view = 'bracket';
    if (state.view === 'bracket' && tableStage(s)) state.view = 'tables';
    if (state.round == null || state.round < 0 || state.round >= Math.max(1, roundCount(s))) { state.round = defaultRound(s); state.page = 0; }
  }

  // ---------- masthead ----------
  function record() {
    const r = {w:0, d:0, l:0, gf:0, ga:0};
    data.fixtures.forEach(f => {
      if (!hasScore(f) || !involves(f, PLAYER)) return;
      const a = f.home === PLAYER ? f.score[0] : f.score[1], b = f.home === PLAYER ? f.score[1] : f.score[0];
      r.gf += a; r.ga += b; if (a > b) r.w++; else if (a < b) r.l++; else r.d++;
    });
    return r;
  }
  function rankText() {
    if (SEALED) return HUB.opening_draw === false ? '确认球队后公布联赛阶段赛程' : '抽签将在确认球队后揭晓';
    const s = data.stages[data.stage_index];
    if (!tableStage(s)) return '';
    for (const g of s.groups) { const at = g.table.findIndex(row => row.team === PLAYER); if (at >= 0) return `${g.name} 第 ${at + 1} 名 · ${g.table[at].points} 分`; }
    return '';
  }
  function masthead() {
    const s = stage(), cur = data.stages[data.stage_index];
    $('mark').textContent = Array.from(data.name)[0] || '赛';
    $('title').textContent = data.name;
    document.title = (marker || '') + data.name + ' · 锦标赛中心';
    $('kicker').textContent = SEALED ? 'TOURNAMENT HUB · 等待确认球队' : !cur ? 'TOURNAMENT HUB · 新一届即将开始' : data.completed ? 'FINAL WHISTLE · 冠军 ' + team(data.champion).name : 'TOURNAMENT HUB · ' + (STAGE_EN[cur.type] || '') + ' · ' + roundEn(cur, data.current_round);
    $('watermark').textContent = state.view === 'fixtures' && s ? roundEn(s, state.round) : {summary:'FINAL WHISTLE', team:'CHOOSE YOUR TEAM', draw:'THE DRAW', tables:'STANDINGS', bracket:'ROAD TO THE FINAL', leaders:'TOP PLAYERS'}[state.view] || '';
    $('stages').innerHTML = data.stages.map(x => `<button type="button" class="stg ${SEALED ? 'pending' : x.status}" data-act="stage" data-arg="${x.index}" data-k="stg:${x.index}" aria-current="${!SEALED && x.index === state.stage}"${SEALED ? ' disabled' : ''}><em>${esc(STAGE_EN[x.type] || '')}</em><b>${esc(x.name)}</b><i>${SEALED ? '未开始' : x.status === 'complete' ? '已结束' : x.status === 'current' ? '进行中' : '未开始'}</i></button>`).join('');
    const r = record(), out = data.player_status === 'eliminated';
    $('club').innerHTML = `<div class="club" ${cAttr({tc:PLAYER})}><span class="club-bg"></span>${crest(PLAYER, 'lg')}
      <div class="club-info"><em>${SEALED ? 'TEAM PREVIEW' : 'MY CLUB'}</em><b>${esc(team(PLAYER).name)}<span class="st${out ? ' out' : ''}">${SEALED ? '待确认' : esc(STATUS[data.player_status] || '征战中')}</span></b><span>${esc(rankText())}</span></div>
      ${SEALED ? '' : `<div class="club-rec"><span><b>${r.w}</b>胜</span><span><b>${r.d}</b>平</span><span><b>${r.l}</b>负</span><span><b>${r.gf}:${r.ga}</b>进失</span></div>`}</div>`;
  }

  // ---------- fixtures ----------
  function evLine(f, side) {
    if (!f.events || !hasScore(f)) return '';
    const goals = aggregate(f.events.goals && f.events.goals[side]), assists = aggregate(f.events.assists && f.events.assists[side]);
    if (!goals.length && !assists.length) return '';
    return `<span class="ev-line">${goals.map(e => `<span class="ev">${BALL}${esc(e.name)}${e.n > 1 ? `<span class="x">×${e.n}</span>` : ''}</span>`).join('')}${assists.map(e => `<span class="ev as"><i>A</i>${esc(e.name)}${e.n > 1 ? ` ×${e.n}` : ''}</span>`).join('')}</span>`;
  }
  function note(f) {
    const parts = [];
    if (Array.isArray(f.extra_time_score) && f.extra_time_score.some(Boolean)) parts.push('含加时');
    if (Array.isArray(f.penalties)) parts.push('点球 ' + f.penalties.join(':'));
    if (f.forfeiting_team != null) parts.push(team(f.forfeiting_team).name + ' 弃权');
    return parts.join(' · ');
  }
  function scoreBlock(f) {
    const n = note(f);
    const box = hasScore(f) ? `<span class="sc-box"><b><span>${f.score[0]}</span></b><b><span>${f.score[1]}</span></b></span>`
      : `<span class="sc-vs"><span>${f.bye ? 'BYE' : cancelled(f) ? 'VOID' : f.played ? '— —' : 'VS'}</span></span>`;
    return `<span class="sc">${box}${n ? `<span class="sc-note">${esc(n)}</span>` : ''}</span>`;
  }
  function tag(f) {
    if (f.bye) return '<span class="fx-tag">BYE</span>';
    if (cancelled(f)) return '<span class="fx-tag t-void">VOID</span>';
    if (f.forfeiting_team != null) return '<span class="fx-tag t-ft">W/O</span>';
    if (f.played) return '<span class="fx-tag t-ft">FULL TIME</span>';
    if (String(f.id) === String(data.next_fixture_id)) return '<span class="fx-tag t-next">NEXT MATCH</span>';
    return '<span class="fx-tag">KICK-OFF</span>';
  }
  function side(f, id, cls) {
    const w = winnerOf(f), name = `<b>${esc(id == null ? '待定' : team(id).name)}</b>`;
    return `<span class="fx-side ${cls}${w != null && w !== id ? ' lost' : ''}${id === PLAYER ? ' mine' : ''}"><span class="fx-name">${cls === 'home' ? name + crest(id) : crest(id) + name}</span>${evLine(f, cls)}</span>`;
  }
  function fxRow(f, i, sectioned) {
    const s = data.stages[f.stage];
    const meta = [tableStage(s) && !sectioned ? groupName(s, f.group) : '', f.legs === 2 ? '第 ' + f.leg + ' 回合' : '', f.neutral ? '中立场' : ''].filter(Boolean).join(' · ');
    return `<button type="button" class="fx rise${involves(f, PLAYER) ? ' own' : ''}" data-act="fx" data-arg="${esc(f.id)}" data-k="fx:${esc(f.id)}" ${cAttr({hc:f.home, ac:f.away}, '--i:' + i)}>
      ${meta ? `<span class="fx-meta">${esc(meta)}</span>` : ''}${tag(f)}${side(f, f.home, 'home')}${scoreBlock(f)}${f.bye ? '<span></span>' : side(f, f.away, 'away')}</button>`;
  }
  function hero(f) {
    const s = data.stages[f.stage], w = winnerOf(f);
    const label = String(f.id) === String(data.next_fixture_id) ? 'NEXT MATCH' : f.played ? 'FULL TIME' : 'MY MATCH';
    const where = [s.name, tableStage(s) ? groupName(s, f.group) : '', roundName(s, f.round), f.legs === 2 ? '第 ' + f.leg + ' 回合' : '', f.neutral ? '中立场' : ''].filter(Boolean).join(' · ');
    const t = (id, cls) => `<span class="hero-team ${cls}${w != null && w !== id ? ' lost' : ''}">${crest(id, 'xl')}<b>${esc(team(id).name)}</b>${evLine(f, cls)}</span>`;
    const mid = hasScore(f) ? `<span class="hero-score"><b><span>${f.score[0]}</span></b><b><span>${f.score[1]}</span></b></span>` : `<span class="hero-vs">${f.bye ? 'BYE' : 'VS'}</span>`;
    return `<button type="button" class="hero rise" data-act="fx" data-arg="${esc(f.id)}" data-k="hero:${esc(f.id)}" ${cAttr({hc:f.home, ac:f.away})}><span class="hero-bg"></span>
      <span class="hero-meta"><em>${label}</em>${esc(where)}</span>${t(f.home, 'home')}<span class="hero-mid">${mid}${note(f) ? `<span class="hero-sub">${esc(note(f))}</span>` : ''}</span>${f.bye ? '<span></span>' : t(f.away, 'away')}</button>`;
  }
  // A long league phase has dozens of matchdays: show a window of rounds
  // around the selected one (as many as fit the toolbar) instead of wrapping
  // every matchday over the page; the arrows step one round at a time.
  function timelineSpan(n) {
    const width = ($('toolbar') && $('toolbar').clientWidth) || window.innerWidth || 1280;
    if (window.innerWidth <= 480) return Math.min(n, Math.max(1, Math.floor((width - 160) / 80)));
    return Math.min(n, Math.max(3, Math.floor((width - 420) / 150)));
  }
  function timeline(s) {
    const n = roundCount(s), all = stageFixtures(s), span = timelineSpan(n);
    const at = Math.min(Math.max(0, state.round || 0), n - 1), from = Math.max(0, Math.min(n - span, at - Math.floor(span / 2)));
    const rounds = Array.from({length:span}, (_, k) => {
      const r = from + k, rows = all.filter(f => f.round === r), done = rows.filter(f => f.played).length;
      const pct = rows.length ? Math.round(done / rows.length * 100) : 0, live = done > 0 && done < rows.length || (rows.length && !done && r === defaultRound(s));
      return `<button type="button" class="tl-r${live ? ' live' : ''}" data-act="round" data-arg="${r}" data-k="rd:${r}" aria-current="${r === state.round}" title="${esc(roundName(s, r))}" style="--p:${pct}%"><em>${esc(roundShort(s, r))}</em><b>${esc(tableStage(s) ? '第' + (r + 1) + '轮' : roundName(s, r))}</b><i></i></button>`;
    }).join('');
    const where = span < n ? `<span class="tl-pos"><b>${at + 1}</b>/ ${n} 轮</span>` : '';
    return `<button type="button" class="tl-arrow" data-act="rprev" data-k="rprev" aria-label="上一轮"${state.round <= 0 ? ' disabled' : ''}>‹</button><div class="tl">${rounds}</div><button type="button" class="tl-arrow" data-act="rnext" data-k="rnext" aria-label="下一轮"${state.round >= n - 1 ? ' disabled' : ''}>›</button>${where}`;
  }
  const ownChip = () => `<button type="button" class="chip" data-act="own" data-k="own" aria-pressed="${state.own}"><span class="key key-sm">X</span>只看我的球队</button>`;
  function renderFixtures() {
    const s = stage(), all = stageFixtures(s);
    const groupSelect = tableStage(s) && s.groups.length > 1 ? `<select class="select" id="group-select" data-k="group" aria-label="选择小组"><option value="all">全部小组</option>${s.groups.map(g => `<option value="${g.index}"${String(g.index) === state.group ? ' selected' : ''}>${esc(g.name)}</option>`).join('')}</select>` : '';
    $('toolbar').innerHTML = (roundCount(s) ? timeline(s) : '') + `<span class="grow"></span>${groupSelect}${ownChip()}`;
    if (!all.length) { $('view').innerHTML = '<div class="empty"><b>本阶段对阵尚未产生</b>上一阶段结束后显示完整赛程</div>'; $('pager').innerHTML = ''; return; }
    const rows = roundRows(), top = heroFixture(rows), list = rows.filter(f => f !== top);
    const pages = Math.max(1, Math.ceil(list.length / PER_PAGE));
    state.page = Math.min(Math.max(0, state.page), pages - 1);
    const visible = list.slice(state.page * PER_PAGE, (state.page + 1) * PER_PAGE);
    const sectioned = tableStage(s) && state.group === 'all' && s.groups.length > 1;
    let html = '', last = null;
    visible.forEach((f, i) => {
      if (sectioned && f.group !== last) { html += `<div class="sec">${esc(groupName(s, f.group))}<small>${esc(roundEn(s, f.round))}</small></div>`; last = f.group; }
      html += fxRow(f, i + 1, sectioned);
    });
    if (state.page === pages - 1 && tableStage(s)) {
      const byes = (s.byes || []).filter(b => b.round === state.round && (state.group === 'all' || String(b.group) === state.group) && (!state.own || b.team === PLAYER));
      if (byes.length) html += `<div class="bye-strip">本轮轮空：${byes.map(b => esc(team(b.team).name)).join('、')}</div>`;
    }
    const heroHtml = top && state.page === 0 ? hero(top) : '';
    $('view').innerHTML = rows.length ? heroHtml + (visible.length ? `<div class="fx-grid">${html}</div>` : '') : `<div class="empty"><b>${state.own ? '我的球队本轮没有比赛' : '本轮没有比赛'}</b>${state.own ? '按 X 显示全部比赛' : ''}</div>`;
    pager(pages, list.length + (top ? 1 : 0) + ' 场 · ' + roundEn(s, state.round));
  }
  function pager(pages, label) {
    $('pager').innerHTML = pages > 1 ? `<button type="button" data-act="pprev" data-k="pprev"${state.page === 0 ? ' disabled' : ''}>‹ 上一页</button><span>${state.page + 1} / ${pages} · ${esc(label)}</span><button type="button" data-act="pnext" data-k="pnext"${state.page >= pages - 1 ? ' disabled' : ''}>下一页 ›</button>` : '';
  }

  // ---------- standings ----------
  function formOf(s, id) {
    return stageFixtures(s).filter(f => hasScore(f) && involves(f, id)).sort((a, b) => a.round - b.round || a.leg - b.leg).slice(-3)
      .map(f => { const a = f.home === id ? f.score[0] : f.score[1], b = f.home === id ? f.score[1] : f.score[0]; return a > b ? 'W' : a < b ? 'L' : 'D'; });
  }
  const LEAGUE_SLICE = 16;
  function leagueSlices(s, groups) {
    if (s.type !== 'league') return groups;
    return groups.flatMap(g => {
      if (g.table.length <= LEAGUE_SLICE) return [g];
      const out = [];
      for (let a = 0; a < g.table.length; a += LEAGUE_SLICE) {
        const table = g.table.slice(a, a + LEAGUE_SLICE);
        out.push(Object.assign({}, g, {table, teams:table.map(x => x.team), offset:a, name:`${g.name} · 第 ${a + 1}–${a + table.length} 名`}));
      }
      return out;
    });
  }
  function renderTables() {
    const s = stage(), r = s.rules;
    let groups = leagueSlices(s, s.groups.slice());
    if (state.own) groups = groups.filter(g => g.teams.includes(PLAYER));
    const adv = r.advance_per_group || 0, po = r.playoff_per_group || 0;
    $('toolbar').innerHTML = `<div class="legend"><span><i style="background:var(--win);border-radius:50%"></i>直接晋级 · 前 ${adv} 名</span>${po ? `<span><i style="background:#b98cff;border-radius:50%"></i>附加赛 · 随后 ${po} 名</span>` : ''}<span>胜 ${r.win_points} · 平 ${r.draw_points} · 负 ${r.loss_points}</span></div><span class="grow"></span><span class="count">${s.groups.length} 个${s.type === 'league' ? '积分榜' : '小组'}</span>${ownChip()}`;
    if (!s.groups.length) { $('view').innerHTML = `<div class="empty"><b>积分榜尚未产生</b>${s.type === 'league' ? '本阶段开始后显示' : '本阶段抽签完成后显示'}</div>`; $('pager').innerHTML = ''; return; }
    const pages = Math.max(1, Math.ceil(groups.length / GROUPS_PER_PAGE));
    if (state.tablePage == null) { const at = groups.findIndex(g => g.teams.includes(PLAYER)); state.tablePage = at < 0 ? 0 : Math.floor(at / GROUPS_PER_PAGE); }
    state.tablePage = Math.min(Math.max(0, state.tablePage), pages - 1);
    const row = (x, i) => {
      const form = formOf(s, x.team), dots = Array.from({length:3}, (_, k) => form[k] ? `<i class="${form[k]}">${form[k] === 'W' ? '胜' : form[k] === 'D' ? '平' : '负'}</i>` : '<i class="n"></i>').join('');
      return `<div class="tr${i < adv ? ' adv' : i < adv + po ? ' po' : ''}${x.team === PLAYER ? ' mine' : ''}" ${cAttr({tc:x.team})}><span class="pos">${i + 1}</span><span class="tm">${crest(x.team, 'sm')}<b>${esc(team(x.team).name)}</b></span><span>${x.played}</span><span>${x.wins}</span><span>${x.draws}</span><span>${x.losses}</span><span>${x.goal_difference > 0 ? '+' : ''}${x.goal_difference}</span><span class="pts">${x.points}</span><span class="form">${dots}</span></div>`;
    };
    const card = (g, i) => `<div class="tbl rise${g.teams.includes(PLAYER) ? ' own' : ''}" role="button" tabindex="0" data-act="group" data-arg="${g.index}" data-k="grp:${g.index}:${g.offset || 0}" style="--i:${i}">
      <header><b>${esc(g.name)}</b><span>${s.type === 'league' ? 'LEAGUE TABLE' : 'GROUP TABLE'} · A 查看赛程</span></header>
      <div class="tr h"><span>#</span><span style="text-align:left">球队</span><span>赛</span><span>胜</span><span>平</span><span>负</span><span>净</span><span>积分</span><span>近况</span></div>${g.table.map((x, k) => row(x, (g.offset || 0) + k)).join('')}</div>`;
    $('view').innerHTML = `<div class="tbl-grid">${groups.slice(state.tablePage * GROUPS_PER_PAGE, (state.tablePage + 1) * GROUPS_PER_PAGE).map(card).join('')}</div>`;
    state.page = state.tablePage; pager(pages, s.type === 'league' ? s.groups.reduce((m, g) => m + g.table.length, 0) + ' 队' : groups.length + ' 组');
  }

  // ---------- bracket ----------
  function tieScore(node) {
    const legs = node.fixtures.map(id => byId.get(String(id))).filter(Boolean), total = {}; let any = false, pens = null;
    node.teams.forEach(id => { total[id] = 0; });
    legs.forEach(f => { if (!hasScore(f)) return; any = true; total[f.home] += f.score[0]; total[f.away] += f.score[1]; if (Array.isArray(f.penalties)) pens = {[f.home]:f.penalties[0], [f.away]:f.penalties[1]}; });
    return {legs, any, total, pens};
  }
  function renderBracket() {
    const s = stage(), rounds = s.bracket;
    if (!rounds.length) { $('toolbar').innerHTML = ownChip(); $('view').innerHTML = '<div class="empty"><b>对阵图尚未产生</b>进入本阶段后显示晋级路线</div>'; $('pager').innerHTML = ''; return; }
    const cols = Math.max(1, Math.floor((($('view').clientWidth || 1400) + 34) / 324));
    if (state.bracketStart == null) state.bracketStart = Math.max(0, Math.min(defaultRound(s) - 1, rounds.length - cols));
    state.bracketStart = Math.min(Math.max(0, state.bracketStart), Math.max(0, rounds.length - cols));
    const shown = rounds.slice(state.bracketStart, state.bracketStart + cols);
    $('toolbar').innerHTML = `<button type="button" class="tl-arrow" data-act="bprev" data-k="bprev" aria-label="向前"${state.bracketStart <= 0 ? ' disabled' : ''}>‹</button><div class="tl">${rounds.map((x, i) => `<button type="button" class="tl-r" data-act="bjump" data-arg="${i}" data-k="bj:${i}" aria-current="${i >= state.bracketStart && i < state.bracketStart + cols}" style="--p:${x.generated ? 100 : 0}%"><em>${esc(roundShort(s, i))}</em><b>${esc(x.name)}</b><i></i></button>`).join('')}</div><button type="button" class="tl-arrow" data-act="bnext" data-k="bnext" aria-label="向后"${state.bracketStart + cols >= rounds.length ? ' disabled' : ''}>›</button><span class="grow"></span>${ownChip()}`;
    let i = 0;
    const tie = node => {
      i++;
      if (!node.generated) return `<div class="tie ghost rise" style="--i:${i}"><div class="tie-row">${crest(null, 'sm')}<span class="n" style="color:var(--dim)">待定</span><b></b></div><div class="tie-row">${crest(null, 'sm')}<span class="n" style="color:var(--dim)">待定</span><b></b></div></div>`;
      const sc = tieScore(node), own = node.teams.includes(PLAYER);
      if (state.own && !own) return `<div class="tie ghost rise" style="--i:${i};opacity:.35"><div class="tie-row"><span class="n">${esc(team(node.teams[0]).name)}</span><b></b></div><div class="tie-row"><span class="n">${esc(node.teams[1] == null ? '轮空' : team(node.teams[1]).name)}</span><b></b></div></div>`;
      const row = id => `<div class="tie-row${node.winner != null && node.winner !== id ? ' out' : ''}${node.winner === id ? ' win' : ''}${id === PLAYER ? ' mine' : ''}" ${cAttr({tc:id})}>${crest(id, 'sm')}<span class="n">${esc(id == null ? (node.bye ? '轮空' : '待定') : team(id).name)}${sc.pens && id != null ? `<small>(${sc.pens[id]})</small>` : ''}</span><b>${sc.any && id != null ? sc.total[id] : ''}</b></div>`;
      const legsNote = node.bye ? '轮空晋级' : sc.legs.length === 2 ? 'AGG · 两回合' : sc.legs.some(f => !f.played) ? 'KICK-OFF' : 'FULL TIME';
      return `<button type="button" class="tie rise${own ? ' own' : ''}" data-act="fx" data-arg="${esc(node.fixtures[0])}" data-k="tie:${esc(node.id)}" style="--i:${i}">${row(node.teams[0])}${row(node.teams[1])}<div class="tie-note">${legsNote}</div></button>`;
    };
    const height = shown[0].nodes.length * 104;
    $('view').innerHTML = `<div class="bracket">${shown.map(round => `<div class="b-col${round.size === 2 ? ' final' : ''}" style="min-height:${height}px"><h3>${esc(round.name)}<span>${esc(roundShort(s, round.index))} · ${round.generated ? round.nodes.length + ' 组' : '待产生'}</span></h3><div class="b-ties">${round.size === 2 ? `<div class="final-wrap">${TROPHY}${round.nodes.map(tie).join('')}</div>` : round.nodes.map(tie).join('')}</div></div>`).join('')}</div>`;
    $('pager').innerHTML = '';
  }

  // ---------- leaders ----------
  function renderLeaders() {
    const lb = data.leaderboards || {scorers:[], assists:[], matches_counted:0, matches_played:0};
    const filter = rows => state.own ? rows.filter(r => r.team === PLAYER) : rows;
    $('toolbar').innerHTML = `<span class="count">统计 ${lb.matches_counted} / ${lb.matches_played} 场已完赛比赛</span><span class="grow"></span>${ownChip()}`;
    const block = (title, en, rows, key, unit, other, otherUnit) => {
      const top = rows.slice(0, 3), order = [top[1], top[0], top[2]];
      const pod = order.map((r, j) => r ? `<div class="pod p${[2, 1, 3][j]} rise" tabindex="0" data-k="${key}:p${j}" ${cAttr({tc:r.team}, '--i:' + j)}><em>NO.${[2, 1, 3][j]}</em>${crest(r.team, 'lg')}<b>${esc(r.name)}</b><span>${esc(team(r.team).name)}</span><strong>${r[key]}</strong><small>${unit} · ${r[other]} ${otherUnit}</small></div>` : '<div></div>').join('');
      const list = rows.slice(3, 15).map((r, j) => `<div class="lrow rise${r.team === PLAYER ? ' mine' : ''}" tabindex="0" data-k="${key}:${j}" ${cAttr({tc:r.team}, '--i:' + (j + 3))}><span class="rk">${j + 4}</span>${crest(r.team, 'sm')}<span class="who"><b>${esc(r.name)}</b><span>${esc(team(r.team).name)}</span></span><span class="v">${r[key]}<small>${unit} · ${r[other]} ${otherUnit}</small></span></div>`).join('');
      return `<section><h2>${title}<em>${en}</em></h2>${rows.length ? `<div class="podium">${pod}</div>${list}` : '<div class="empty"><b>暂无数据</b>完赛后统计</div>'}</section>`;
    };
    $('view').innerHTML = `<div class="lead">${block('射手榜', 'TOP SCORERS', filter(lb.scorers), 'goals', '球', 'assists', '助攻')}${block('助攻榜', 'TOP ASSISTS', filter(lb.assists), 'assists', '次', 'goals', '进球')}</div>`;
    $('pager').innerHTML = '';
  }

  // ---------- full-time screen ----------
  function ruleText(s, f) {
    const r = s.rules;
    if (tableStage(s)) return [r.cycles === 2 ? '双循环' : '单循环', `胜 ${r.win_points} / 平 ${r.draw_points} / 负 ${r.loss_points}`, `前 ${r.advance_per_group} 名晋级${r.playoff_per_group ? '，随后 ' + r.playoff_per_group + ' 名附加赛' : ''}`];
    return [f.legs === 2 ? '主客场两回合' : '单场决胜', r.away_goals ? '客场进球规则' : '', r.extra_time ? '平局加时' : '', f.penalties_allowed ? '点球决胜' : '', f.neutral ? '中立场地' : ''].filter(Boolean);
  }
  function evList(f, sideKey) {
    if (!f.events) return '';
    const goals = aggregate(f.events.goals && f.events.goals[sideKey]), assists = aggregate(f.events.assists && f.events.assists[sideKey]);
    return goals.map(e => `<span class="ev">${BALL}${esc(e.name)}${e.n > 1 ? `<span class="x">×${e.n}</span>` : ''}</span>`).join('') + assists.map(e => `<span class="ev as"><i>A</i>${esc(e.name)}${e.n > 1 ? ' ×' + e.n : ''}</span>`).join('');
  }
  let lastFocus = null;
  function openDetail(f) {
    const s = data.stages[f.stage];
    const legs = f.tie ? data.fixtures.filter(x => x.stage === f.stage && x.tie === f.tie).sort((a, b) => a.leg - b.leg) : [f];
    const legHtml = x => {
      const w = winnerOf(x);
      const t = id => `<div class="ft-team${id === PLAYER ? ' mine' : ''}" style="${w != null && w !== id ? 'opacity:.65' : ''}">${crest(id, 'xl')}<b>${esc(id == null ? '—' : team(id).name)}</b></div>`;
      const mid = hasScore(x) ? `<span class="hero-score"><b><span>${x.score[0]}</span></b><b><span>${x.score[1]}</span></b></span>` : `<span class="hero-vs">${x.bye ? 'BYE' : cancelled(x) ? 'VOID' : 'VS'}</span>`;
      const total = Array.isArray(x.xg) && hasScore(x) ? x.xg[0] + x.xg[1] : 0;
      const xg = total > 0 ? `<div class="xg"><span>${x.xg[0].toFixed(2)}</span><div class="bar"><i style="width:${(x.xg[0] / total * 100).toFixed(1)}%"></i></div><span>${x.xg[1].toFixed(2)}</span></div><div class="xg-cap">EXPECTED GOALS · 预期进球</div>` : '';
      const home = evList(x, 'home'), away = evList(x, 'away');
      return `<section class="ft" ${cAttr({hc:x.home, ac:x.away})}><span class="ft-bg"></span>
        <div class="ft-head"><em>${x.played ? 'FULL TIME' : 'KICK-OFF'}</em>${esc([s.name, tableStage(s) ? groupName(s, x.group) : '', roundName(s, x.round), x.legs === 2 ? '第 ' + x.leg + ' 回合' : ''].filter(Boolean).join(' · '))}</div>
        <div class="ft-row">${t(x.home)}${mid}${x.bye ? '<div></div>' : t(x.away)}</div>${note(x) ? `<div class="ft-sub">${esc(note(x))}</div>` : ''}
        ${home || away ? `<div class="ft-ev"><div>${home}</div><div>${away}</div></div>` : ''}${xg}</section>`;
    };
    let agg = '';
    if (legs.length === 2 && legs.every(hasScore)) {
      const a = legs[0].home, b = legs[0].away, t = {[a]:0, [b]:0};
      legs.forEach(x => { t[x.home] += x.score[0]; t[x.away] += x.score[1]; });
      agg = `<div class="agg">两回合总比分 · ${esc(team(a).name)} ${t[a]} : ${t[b]} ${esc(team(b).name)}</div>`;
    }
    $('detail-kicker').textContent = (legs.every(x => x.played) ? 'FULL TIME' : 'MATCH PREVIEW') + ' · ' + roundEn(s, f.round);
    $('detail-body').innerHTML = legs.map(legHtml).join('') + agg + `<div class="rules">${ruleText(s, f).map(x => `<span>${esc(x)}</span>`).join('')}</div>`;
    lastFocus = document.activeElement;
    const dlg = $('detail');
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    $('detail-close').focus();
  }
  const dialogOpen = () => $('detail').open;
  function closeDetail() {
    const dlg = $('detail');
    if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open');
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  // ---------- hub writes ----------
  async function post(action, body, text) {
    $('busy-text').textContent = text || '处理中…'; $('busy').hidden = false;
    try {
      const r = await fetch(HUB.endpoint + '/' + action, {method:'POST',
        headers:{'Content-Type':'application/json', 'X-Studio-Hub-Token':HUB.token}, body:JSON.stringify(body)});
      let j = {};
      try { j = await r.json(); } catch (_) {}
      if (!r.ok) throw new Error(j.error || ('请求失败（' + r.status + '）'));
      return j;
    } finally { $('busy').hidden = true; }
  }
  function reloadTo(view) { state.view = view; persist(); location.reload(); }
  const busy = () => !$('busy').hidden;

  // ---------- team choice ----------
  function groupOf(id) {
    if (SEALED) return '';
    const s0 = data.stages[0];
    if (!s0 || !tableStage(s0)) return '';
    const g = s0.groups.find(x => x.teams.includes(id));
    return g ? g.name : '';
  }
  function renderTeam() {
    const T = HUB.team, sel = state.pick, can = T.enabled && T.changeable;
    const pending = T.needs || T.next || sel !== T.current;
    $('toolbar').innerHTML = `<span class="count">${T.enabled ? `本届可选 ${T.allowed.length} 支球队 · 首场比赛开球后锁定` : '本赛事在工坊里设为固定球队'}</span>`;
    const note = !T.enabled ? '固定球队' : sel !== T.current ? '已选中，确认后生效' : T.needs ? (HUB.opening_draw === false ? '确认后进入游戏赛事菜单' : '确认后播放抽签并进入游戏赛事菜单') : '阵容与进入比赛在游戏赛事菜单中';
    const head = `<div class="pick-head" ${cAttr({tc:sel})}>${crest(sel, 'xl')}<div><em>${T.needs ? 'CHOOSE YOUR TEAM · 请选择本届球队' : 'YOUR TEAM · 本届控制球队'}</em><b>${esc(team(sel).name)}</b><p>${esc(note)}</p>${T.enabled && !T.changeable ? '<span class="lock">首场比赛已开球，本届控制球队已锁定</span>' : ''}</div>
      <button type="button" class="btn" data-act="confirm-team" data-k="confirm-team"${can && pending ? '' : ' disabled'}>${T.next ? '确认新一届' : pending ? '确认选择' : '已确认'}</button></div>`;
    const ids = T.enabled ? T.allowed : [T.current];
    const cards = ids.map((id, i) => `<button type="button" class="team-card rise${id === sel ? ' sel' : ''}${id === T.current ? ' cur' : ''}" data-act="pick" data-arg="${id}" data-k="pick:${id}" ${cAttr({tc:id}, '--i:' + Math.min(i, 14))}${can ? '' : ' disabled'}>${id === T.current ? '<span class="tag">CURRENT</span>' : ''}${crest(id, 'lg')}<b>${esc(team(id).name)}</b><span>${esc(groupOf(id))}</span></button>`).join('');
    $('view').innerHTML = head + `<div class="team-grid">${cards}</div>`;
    $('pager').innerHTML = '';
  }

  async function quietPost(action, body) {
    const r = await fetch(HUB.endpoint + '/' + action, {method:'POST',
      headers:{'Content-Type':'application/json', 'X-Studio-Hub-Token':HUB.token}, body:JSON.stringify(body)});
    let j = {}; try { j = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(j.error || ('请求失败（' + r.status + '）'));
    return j;
  }

  // ---------- edition summary ----------
  function renderSummary() {
    const r = record(), champ = data.champion, P = HUB.progress || {history:[]};
    const fin = data.completed ? (champ === PLAYER ? '本届冠军' : '本届已结束') : data.player_status === 'eliminated' ? '本届已被淘汰' : '本届已结束';
    const reached = P.history.length ? P.history[P.history.length - 1] : null;
    const lb = data.leaderboards || {scorers:[], assists:[]};
    const mine = rows => rows.filter(x => x.team === PLAYER).slice(0, 3);
    const T = HUB.team || {};
    const next = data.completed
      ? (T.enabled && T.changeable ? '下一届可以换队：在「选队」里确认球队后，回到游戏点「进入」开始新一届（本届赛果会保留）' : '回到游戏点「进入」即开始新一届（本届赛果会保留）')
      : '其余电脑赛程会继续结算；全部结束后可开始下一届';
    $('toolbar').innerHTML = ''; $('pager').innerHTML = '';
    $('view').innerHTML = `<div class="sum">
      <div class="sum-hero${champ === PLAYER ? ' gold' : ''}" ${cAttr({tc:PLAYER})}>${champ === PLAYER ? TROPHY : crest(PLAYER, 'xl')}
        <div><em>${esc(data.name)}</em><b>${esc(team(PLAYER).name)}</b><span>${esc(fin)}${reached ? ' · ' + esc(reached.stage_name) + ' ' + esc(reached.title) : ''}</span></div></div>
      <div class="sum-grid">
        <div class="sum-box"><h3>战绩</h3><div class="sum-rec"><span><b>${r.w}</b>胜</span><span><b>${r.d}</b>平</span><span><b>${r.l}</b>负</span><span><b>${r.gf}:${r.ga}</b>进失</span></div></div>
        <div class="sum-box"><h3>冠军</h3>${champ != null ? `<div class="sum-champ">${crest(champ, 'lg')}<b>${esc(team(champ).name)}</b></div>` : '<p class="muted">冠军尚未产生</p>'}</div>
        <div class="sum-box"><h3>晋级之路</h3>${P.history.length ? P.history.map(e => `<div class="sum-step${e.good ? ' good' : ' bad'}"><span>${esc(e.stage_name)}</span><b>${esc(e.title)}</b></div>`).join('') : '<p class="muted">暂无阶段结果</p>'}</div>
        <div class="sum-box"><h3>本队射手 / 助攻</h3>${[...mine(lb.scorers).map(x => `<div class="sum-step"><span>${esc(x.name)}</span><b>${x.goals} 球</b></div>`), ...mine(lb.assists).map(x => `<div class="sum-step"><span>${esc(x.name)}</span><b>${x.assists} 助攻</b></div>`)].join('') || '<p class="muted">暂无数据</p>'}</div>
      </div>
      <div class="sum-next">${esc(next)}${data.completed && T.enabled && T.changeable ? ' <button type="button" class="btn" data-act="goto" data-arg="team" data-k="sum-team">去选队</button>' : ''}</div></div>`;
  }

  // ---------- stage-result ceremony (the game's progression journal) ----------
  function showCeremony() {
    const ev = HUB.progress && HUB.progress.pending;
    if (!ev || SEALED || $('ceremony')) return;
    const el = document.createElement('div');
    el.id = 'ceremony'; el.className = 'cer' + (ev.good ? ' good' : ' bad') + (ev.champion ? ' champ' : '');
    el.innerHTML = `<div class="cer-box" ${cAttr({tc:PLAYER})}>${ev.champion ? TROPHY : crest(PLAYER, 'xl')}
      <em>${esc(data.name)} · ${esc(ev.stage_name)}</em><b>${esc(ev.title)}</b><span>${esc(team(PLAYER).name)}</span>

      <button type="button" class="btn" id="cer-ok">确认</button></div>`;
    document.body.appendChild(el);
    const ok = el.querySelector('#cer-ok'); ok.focus();
    ok.onclick = async () => {
      ok.disabled = true;
      try { const r = await quietPost('progress-seen', {event_id:ev.id}); HUB.progress = r.progress; } catch (e) { toast(e.message); }
      el.remove(); if (HUB.progress && HUB.progress.pending) showCeremony(); else render(true);
    };
  }

  // ---------- live round: other matches are still being played ----------
  let liveTimer = 0;
  function liveLine() {
    const L = HUB.live || {};
    const el = $('live-line') || (() => { const x = document.createElement('div'); x.id = 'live-line'; x.className = 'live-line'; $('club').after(x); return x; })();
    el.hidden = SEALED || FIRST_RUN || !L.active;
    if (!el.hidden) el.innerHTML = L.blocked ? `<i></i>本轮已完成 ${L.settled}/${L.total} 场 · ${esc((L.errors[0] || {}).message || '等待球队资料修复')}` : `<i></i>本轮已完成 ${L.settled}/${L.total} 场，其他比赛处理中，比分会自动更新`;
    if (!SEALED && !FIRST_RUN && !liveTimer && HUB.token && (L.active || L.started)) liveTimer = setInterval(async () => {
      try {
        const n = await quietPost('live', {});
        if (n.revision !== L.revision) { persist(); location.reload(); return; }
        if (n.active !== L.active || n.settled !== L.settled) { HUB.live = n; liveLine(); }
        if (!n.active && !n.started) { clearInterval(liveTimer); liveTimer = 0; }
      } catch (_) {}
    }, 5000);
  }

  // ---------- draw ceremony (played once after the first confirmation) ----------
  let drawTimer = 0, drawItems = [], drawAt = 0, drawKind = 'groups';
  function stopDraw() { clearTimeout(drawTimer); drawTimer = 0; }
  // The gateway builds the current stage's draw (pc_tournament_hub.draw_plan,
  // checked against every workshop structure): groups by pot, single-group
  // pairings with byes (a league phase has no draw), knockout / play-off ties with seeded
  // byes, and the direct qualifiers who skip a play-off.
  // Boxes: {name, kind:'group'|'pair'|'bye'|'direct', labels:[slot captions]}.
  function drawPlan() {
    const d = HUB.draw;
    const drawn = data.stages[state.drawStage];
    if (!d || d.stage !== state.drawStage || (drawn && drawn.type === 'league')) return {kind:'none', boxes:[], items:[]};
    return d;
  }
  function drawPlanBox(box) {
    const el = $('view').querySelector(`[data-box="${box}"]`);
    return {name:boxName(box), kind:el ? (el.className.match(/dg-(group|pair|bye|direct)/) || [])[1] : 'pair'};
  }
  function boxName(box) { const h = $('view').querySelector(`[data-box="${box}"] header b`); return h ? h.textContent : ''; }
  function revealSlot(it, animate) {
    const el = $('view').querySelector(`[data-slot="${it.box}:${it.slot}"]`);
    if (!el || el.classList.contains('on')) return;
    const mine = it.team === PLAYER;
    el.className = 'dg-slot on' + (mine ? ' mine' : '') + (animate ? ' pop' : '');
    el.dataset.tc = it.team; el.style.setProperty('--tc', colorOf(it.team));
    el.innerHTML = `${crest(it.team, 'sm')}<b>${esc(team(it.team).name)}</b>`;
    const call = $('draw-call');
    if (call && animate) {
      call.innerHTML = `<em>${mine ? 'YOUR TEAM' : 'THE DRAW'}</em><b>${esc(team(it.team).name)}<i>→</i>${esc(boxName(it.box))}</b>`;
      call.classList.toggle('mine', mine);
      if (mine) el.scrollIntoView({block:'center', behavior:'smooth'});
    }
  }
  function renderDraw() {
    const plan = drawPlan(); drawItems = plan.items; drawAt = 0; drawKind = plan.kind;
    try { sessionStorage.removeItem(DRAW_KEY); } catch (_) {}
    markRevealed(state.drawStage);
    if (!plan.items.length) { state.view = 'fixtures'; render(true); return; }  // nothing to reveal
    const groupsCount = plan.boxes.filter(b => b.kind === 'group').length;
    const what = (plan.stage_name || '') + ' · ' + {groups:groupsCount + ' 个小组', pairs:'第 1 轮', ties:'首轮'}[plan.kind]
      + (plan.seeded ? ' · 按上一阶段成绩分档' : '') + (plan.bypass && plan.bypass.length ? ' · ' + plan.bypass.length + ' 队直接晋级' : '');
    $('toolbar').innerHTML = `<span class="count">${esc(what)} · ${plan.items.length} 支球队</span><span class="grow"></span><button type="button" class="btn ghost" data-act="draw-skip" data-k="draw-skip">跳过抽签</button>`;
    const boxes = plan.boxes.map((b, bi) => `<div class="dg dg-${b.kind}" data-box="${bi}"><header><b>${esc(b.name)}</b></header>${b.labels.map((label, k) => `<div class="dg-slot${b.kind === 'bye' && k > 0 ? ' fixed' : ''}" data-slot="${bi}:${k}"><span class="pot">${esc(label)}</span></div>`).join('')}</div>`).join('');
    $('view').innerHTML = `<div class="draw-stage"><div class="draw-call" id="draw-call"><em>THE DRAW</em><b>抽签即将开始</b></div><div class="draw-grid${plan.kind === 'groups' ? '' : ' ties'}">${boxes}</div><div class="draw-end" id="draw-end" hidden><button type="button" class="btn" data-act="draw-done" data-k="draw-done">${FIRST_RUN ? '进入赛事' : '查看我的赛程'}</button></div></div>`;
    $('pager').innerHTML = '';
    const step = Math.max(45, Math.min(260, 9000 / Math.max(1, plan.items.length)));
    const tick = () => {
      if (drawAt >= drawItems.length) return finishDraw();
      const it = drawItems[drawAt++];
      revealSlot(it, true);
      drawTimer = setTimeout(tick, it.team === PLAYER ? 1800 : step);
    };
    drawTimer = setTimeout(tick, 900);
    requestAnimationFrame(() => { const b = $('toolbar').querySelector('[data-act=draw-skip]'); if (b) b.focus(); });
  }
  function finishDraw() {
    stopDraw();
    while (drawAt < drawItems.length) revealSlot(drawItems[drawAt++], false);
    const end = $('draw-end'); if (end) end.hidden = false;
    const mine = drawItems.find(i => i.team === PLAYER), call = $('draw-call');
    if (call) {
      const box = mine ? drawPlanBox(mine.box) : null;
      let text;
      if (!mine) text = esc(team(PLAYER).name) + ' 未进入本阶段';
      else if (box.kind === 'group') text = esc(team(PLAYER).name) + ' 分在 ' + esc(box.name);
      else if (box.kind === 'bye') text = drawKind === 'pairs' ? '第 1 轮轮空，第 2 轮登场' : '首轮轮空，直接晋级下一轮';
      else if (box.kind === 'direct') text = '直接晋级，跳过本阶段，等待本阶段胜者';
      else { const rival = drawItems.find(i => i.box === mine.box && i.team !== PLAYER); text = '首轮对手：' + esc(rival ? team(rival.team).name : '待定'); }
      call.innerHTML = `<em>DRAW COMPLETE</em><b>${text}</b>`; call.classList.toggle('mine', !!mine);
      const el = mine && $('view').querySelector(`[data-slot="${mine.box}:${mine.slot}"]`); if (el) el.scrollIntoView({block:'center'});
    }
    const skip = $('toolbar').querySelector('[data-act=draw-skip]'); if (skip) skip.disabled = true;
    const done = $('view').querySelector('[data-act=draw-done]'); if (done) done.focus({preventScroll:true});
  }

  // ---------- render ----------
  function render(focusDefault) {
    normalize();
    const s = stage(), before = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.k : null;
    $('tab-tables').hidden = !HAS_SCHEDULE || !tableStage(s);
    $('tab-bracket').hidden = !HAS_SCHEDULE || tableStage(s);
    $('tab-fixtures').hidden = $('tab-leaders').hidden = !HAS_SCHEDULE;
    $('tab-summary').hidden = !ENDED;

    document.querySelectorAll('[role=tab]').forEach(b => { b.setAttribute('aria-selected', String(b.dataset.view === state.view)); b.dataset.k = 'tab:' + b.dataset.view; });
    if (state.view !== 'draw') stopDraw();
    masthead();
    ({summary:renderSummary, team:renderTeam, draw:renderDraw, fixtures:renderFixtures, tables:renderTables, bracket:renderBracket, leaders:renderLeaders})[state.view]();
    $('view').scrollTop = 0;
    persist();
    const again = before && document.querySelector(`[data-k="${CSS.escape(before)}"]`);
    if (again && !again.disabled) again.focus({preventScroll:true});
    else if (focusDefault || !document.activeElement || document.activeElement === document.body) focusContent();
  }
  function focusContent() {
    const view = $('view');
    const target = view.querySelector('.pick-head .btn:not(:disabled), .slot, .hero, .fx.own, .tie.own, .tbl.own') || view.querySelector('button:not(:disabled), [tabindex="0"]') || document.querySelector('[role=tab][aria-selected="true"]');
    if (target) target.focus({preventScroll:true});
  }

  // ---------- actions ----------
  function changeRound(delta) {
    const next = state.round + delta;
    if (next < 0 || next >= roundCount(stage())) return false;
    state.round = next; state.page = 0; render(); return true;
  }
  function turnPage(delta) {
    if (state.view === 'fixtures') {
      const top = heroFixture(roundRows()), pages = Math.max(1, Math.ceil(roundRows().filter(f => f !== top).length / PER_PAGE));
      if (state.page + delta >= 0 && state.page + delta < pages) { state.page += delta; render(); focusContent(); return; }
      if (changeRound(delta)) focusContent();
    } else if (state.view === 'tables') { state.tablePage = (state.tablePage || 0) + delta; render(); focusContent(); }
    else if (state.view === 'bracket') { state.bracketStart = (state.bracketStart || 0) + delta; render(); focusContent(); }
  }
  function switchTab(delta) {
    const tabs = [...document.querySelectorAll('[role=tab]')].filter(b => !b.hidden);
    const at = tabs.findIndex(b => b.dataset.view === state.view);
    state.view = tabs[(at + delta + tabs.length) % tabs.length].dataset.view; state.page = 0; render(true);
  }
  function toggleOwn() { state.own = !state.own; state.page = 0; state.tablePage = null; render(true); }
  function act(name, arg) {
    switch (name) {
      case 'stage': state.stage = Number(arg); state.round = null; state.group = 'all'; state.page = 0; state.tablePage = null; state.bracketStart = null;
        if (state.view === 'tables' || state.view === 'bracket') state.view = tableStage(stage()) ? 'tables' : 'bracket'; render(); break;
      case 'round': state.round = Number(arg); state.page = 0; render(); break;
      case 'rprev': changeRound(-1); break;
      case 'rnext': changeRound(1); break;
      case 'pprev': turnPage(-1); break;
      case 'pnext': turnPage(1); break;
      case 'bprev': state.bracketStart--; render(); break;
      case 'bnext': state.bracketStart++; render(); break;
      case 'bjump': state.bracketStart = Number(arg); render(); break;
      case 'group': state.view = 'fixtures'; state.group = String(arg); state.page = 0; render(true); break;
      case 'own': toggleOwn(); break;
      case 'fx': { const f = byId.get(String(arg)); if (f) openDetail(f); break; }
      case 'pick': state.pick = Number(arg); render(); { const b = $('view').querySelector('[data-act=confirm-team]'); if (b && !b.disabled) b.focus(); } $('view').scrollTop = 0; break;
      case 'confirm-team':
        post('team', {team:state.pick}, '正在确认球队…').then(() => {
          // First confirmation of this edition: unseal and play the draw once.
          try { sessionStorage.setItem(DRAW_KEY, '1'); } catch (_) {}
          reloadTo('draw');
        }).catch(e => toast(e.message)); break;
      case 'draw-skip': finishDraw(); break;
      case 'draw-done':
        if (FIRST_RUN) { openNative(); break; }
        state.view = 'fixtures'; state.stage = state.drawStage; state.round = null; render(true); showCeremony(); break;
      case 'goto': state.view = String(arg); render(true); break;

    }
  }
  document.addEventListener('click', e => {
    if (busy()) return;
    const tab = e.target.closest('[role=tab]');
    if (tab) { state.view = tab.dataset.view; state.page = 0; render(true); return; }
    const el = e.target.closest('[data-act]');
    if (el && !el.disabled) act(el.dataset.act, el.dataset.arg);
  });
  document.addEventListener('change', e => {
    if (e.target.id === 'group-select') { state.group = e.target.value; state.page = 0; render(); }
  });
  $('detail-close').onclick = closeDetail;
  $('detail').addEventListener('click', e => { if (e.target === $('detail')) { const r = $('detail').getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeDetail(); } });
  $('detail').addEventListener('cancel', e => { e.preventDefault(); closeDetail(); });
  $('refresh').onclick = () => { persist(); location.reload(); };

  // ---------- host: back to game ----------
  const query = new URLSearchParams(location.search), host = query.get('host'), token = query.get('window');
  const windowsHosted = host === 'windows' && /^[a-f0-9]{32}$/.test(token || '');
  // Windows keeps the original game entry; this view is its owned app-mode
  // dialog. A unique title prevents another browser window being selected.
  const marker = windowsHosted ? 'PC赛事-' + token + ' ' : '';
  const closeHost = () => {
    // The native host receives WebView's title callback without exposing a
    // broad JavascriptInterface. Outside that host show an honest message.
    if (windowsHosted) document.title = marker + 'player-studio-schedule:close';
    else if (host === 'android' || host === 'windows') document.title = 'player-studio-schedule:close';
    else toast('这是独立界面预览；在游戏中此按钮会关闭赛程并返回游戏。');
  };
  function openNative() {
    if (busy()) return;
    post('open', {}, '正在进入赛事…').then(() => {
      if (host === 'android') document.title = 'player-studio-schedule:handoff';
      else closeHost();
    }).catch(e => toast(e.message));
  }
  const back = () => {
    if (busy()) return;
    if (dialogOpen()) return closeDetail();
    if (state.view === 'draw' && drawTimer) return finishDraw();
    if (FIRST_RUN) return openNative();
    closeHost();
  };
  $('back').onclick = back;
  const xButton = () => { if (HAS_SCHEDULE && state.view !== 'team') toggleOwn(); };

  // ---------- spatial navigation ----------
  function selectOption(delta) {
    const el = document.activeElement;
    if (!el || el.tagName !== 'SELECT' || el.disabled) return false;
    const options = [...el.options].filter(o => !o.disabled && !o.hidden && !(o.parentElement.tagName === 'OPTGROUP' && o.parentElement.disabled));
    if (!options.length) return true;
    const at = options.findIndex(o => o.selected), next = options[(Math.max(0, at) + delta + options.length) % options.length];
    if (next.value !== el.value) {
      el.value = next.value;
      el.dispatchEvent(new Event('change', {bubbles:true}));
      const current = el.id ? $(el.id) : el.dataset.duty ? document.querySelector(`select[data-duty="${CSS.escape(el.dataset.duty)}"]`) : el;
      if (current && current.isConnected) current.focus({preventScroll:true});
    }
    return true;
  }
  function focusables() {
    const scope = dialogOpen() ? $('detail') : document;
    return [...scope.querySelectorAll('button, select, [tabindex="0"]')].filter(n => !n.disabled && n.getClientRects().length && !n.closest('[hidden]') && (dialogOpen() || !n.closest('dialog')));
  }
  function move(dir) {
    if ((dir === 'left' || dir === 'right') && selectOption(dir === 'left' ? -1 : 1)) return;
    const list = focusables(), cur = document.activeElement;
    if (!list.includes(cur)) { (list.find(n => n.closest('#view')) || list[0])?.focus(); return; }
    const a = cur.getBoundingClientRect(), ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    let best = null, bestScore = Infinity;
    list.forEach(n => {
      if (n === cur) return;
      const b = n.getBoundingClientRect(), dx = b.left + b.width / 2 - ax, dy = b.top + b.height / 2 - ay;
      let main, cross;
      if (dir === 'left') { if (b.right > a.left + 4 || dx > -4) return; main = -dx; cross = Math.abs(dy); }
      else if (dir === 'right') { if (b.left < a.right - 4 || dx < 4) return; main = dx; cross = Math.abs(dy); }
      else if (dir === 'up') { if (b.bottom > a.top + 4 || dy > -4) return; main = -dy; cross = Math.abs(dx); }
      else { if (b.top < a.bottom - 4 || dy < 4) return; main = dy; cross = Math.abs(dx); }
      const score = main + cross * 2.2;
      if (score < bestScore) { bestScore = score; best = n; }
    });
    if (best) { best.focus({preventScroll:true}); best.scrollIntoView({block:'nearest', inline:'nearest'}); }
  }
  document.addEventListener('keydown', e => {
    const typing = e.target && e.target.tagName === 'SELECT';
    const dirs = {ArrowLeft:'left', ArrowRight:'right', ArrowUp:'up', ArrowDown:'down'};
    if (e.key === 'Escape') { e.preventDefault(); back(); }
    else if ((e.key === 'Enter' || e.key === ' ') && e.target.getAttribute && e.target.getAttribute('role') === 'button') { e.preventDefault(); e.target.click(); }
    else if (dirs[e.key] && !typing) { e.preventDefault(); move(dirs[e.key]); }
    else if (dialogOpen()) return;
    else if (e.key === 'q' || e.key === 'Q') switchTab(-1);
    else if (e.key === 'e' || e.key === 'E') switchTab(1);
    else if (e.key === 'PageUp') { e.preventDefault(); turnPage(-1); }
    else if (e.key === 'PageDown') { e.preventDefault(); turnPage(1); }
    else if ((e.key === 'x' || e.key === 'X') && !typing) xButton();
  }, true);
  let resizeTimer = 0;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.view === 'bracket') render(); }, 150); });

  // Controller: A confirm, B back, X my team, Y refresh, LB/RB pages,
  // LT/RT rounds or pages, D-pad / left stick move focus with repeat.
  let held = [], repeatAt = {}, stickDir = null;
  const DIRS = {12:'up', 13:'down', 14:'left', 15:'right'};
  function press(n) {
    if (busy()) return;
    if (n === 1) return back();
    if (n === 0) { if (selectOption(1)) return; const el = document.activeElement; if (el && el !== document.body) el.click(); return; }
    if (dialogOpen()) return;
    if (n === 2) xButton(); else if (n === 3) $('refresh').click();
    else if (n === 4) switchTab(-1); else if (n === 5) switchTab(1);
    else if (n === 6) turnPage(-1); else if (n === 7) turnPage(1);
  }
  function tick(now) {
    const pad = [...(navigator.getGamepads ? navigator.getGamepads() : [])].find(Boolean);
    const buttons = pad ? pad.buttons.map(b => b.pressed) : [];
    if (pad && document.hasFocus()) {
      buttons.forEach((down, n) => {
        if (DIRS[n]) {
          if (down && !held[n]) { move(DIRS[n]); repeatAt[n] = now + 380; }
          else if (down && now >= repeatAt[n]) { move(DIRS[n]); repeatAt[n] = now + 110; }
          return;
        }
        if (down && !held[n]) press(n);
      });
      const x = pad.axes[0] || 0, y = pad.axes[1] || 0;
      const dir = Math.abs(x) < .55 && Math.abs(y) < .55 ? null : Math.abs(x) > Math.abs(y) ? (x > 0 ? 'right' : 'left') : (y > 0 ? 'down' : 'up');
      if (dir && dir !== stickDir) { move(dir); repeatAt.stick = now + 380; }
      else if (dir && now >= repeatAt.stick) { move(dir); repeatAt.stick = now + 120; }
      stickDir = dir;
    }
    held = buttons; requestAnimationFrame(tick);
  }

  render(true);
  liveLine();
  // A stage result waits behind the draw ceremony when both are due.
  if (state.view !== 'draw') showCeremony();
  requestAnimationFrame(tick);
  window.tournamentScheduleReady = true;
})();
