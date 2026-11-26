// Procyon JS8 chat - the simulator screen. Wires your station (station.js) and
// the pretend stations (bots.js) to the fake radio link (link.js) and draws it
// all. When this moves into Procyon, the link is swapped for the real JS8
// modem; the station keeps the same send(text) / hear(text, fromCall, snr, freq).
'use strict';

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const snrColor = s => s >= -5 ? 'var(--snr1)' : s >= -12 ? 'var(--snr2)' : s >= -18 ? 'var(--snr3)' : 'var(--snr4)';
const hhmmss = t => new Date(t).toISOString().slice(11, 19);

// Inside Procyon (JS8 picked in the mode menu) this page runs in a frame with
// ?embed=1&call=..&grid=..&palette=..: Procyon's own header does the band,
// mode and clock, the callsign comes from Procyon's settings, and it stays on
// the PRETEND BAND - Procyon's /ws also says "hello", so the live-radio check
// below must not run here.
const EMBED = new URLSearchParams(location.search);
const EMBEDDED = EMBED.has('embed');
if (EMBEDDED) {
  document.body.classList.add('embedded');
  if (EMBED.get('call')) $('myCall').value = EMBED.get('call');
  if (EMBED.get('grid')) $('myGrid').value = EMBED.get('grid').slice(0, 4);
  if (EMBED.get('palette')) document.documentElement.dataset.palette = EMBED.get('palette');
  // the top bar is hidden here, so its speed buttons move into the waterfall's title too
  $('wf').closest('.wf-tile').querySelector('.card-head').append($('speedSeg'), $('layoutReset'));
}

// Procyon's look (Settings → Themes, saved per device): palette, background
// texture and waterfall colours. The main page and this screen share the
// browser's storage, so read it here, and again whenever it changes there.
let wfLut = makeLut(WF_SCHEMES.lightning);
function applyTheme() {
  const get = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const root = document.documentElement.dataset;
  const pal = get('procyon.palette');
  if (pal === 'night') delete root.palette;
  else if (pal) root.palette = pal;
  if (get('procyon.textures') === 'off') root.plain = ''; else delete root.plain;
  const wf = get('procyon.wfScheme');
  wfLut = makeLut(WF_SCHEMES[wf] || WF_SCHEMES.lightning);
  root.wf = WF_SCHEMES[wf] ? wf : 'lightning';
}
applyTheme();
addEventListener('storage', e => { if (!e.key || e.key.startsWith('procyon.')) applyTheme(); });
const wfColor = v => { const i = Math.max(0, Math.min(255, Math.round(v * 255))) * 3; return [wfLut[i], wfLut[i + 1], wfLut[i + 2], 255]; };

// Messages replayed after a page reload are dimmed, with a line under them:
// the helper only remembers what it heard since it started (liveSince).
let liveSince = null;
function oldSep() {
  const since = liveSince ? ` since ${new Date(liveSince).toISOString().slice(11, 16)} UTC` : '';
  return `<div class="old-sep" title="Messages above were heard before this page opened. Anything sent while Procyon wasn't listening is missing.">
    <span>earlier messages above, heard${since} · anything from while Procyon wasn't listening was missed</span></div>`;
}
// After each run of old items, put the separator (rows = [{old}, html]).
function withSep(items) {
  const out = [];
  items.forEach(([old, html], i) => { out.push(html); if (old && !(items[i + 1] || [])[0]) out.push(oldSep()); });
  return out;
}

// "Clear" empties band activity or one chat; remembered per device so a
// reload doesn't bring the cleared messages back.
const CLEAR_KEY = 'procyon.js8.cleared';
function clearedAt(key) { try { return (JSON.parse(localStorage.getItem(CLEAR_KEY)) || {})[key] || 0; } catch { return 0; } }
function markCleared(key) {
  try { const m = JSON.parse(localStorage.getItem(CLEAR_KEY)) || {}; m[key] = Date.now(); localStorage.setItem(CLEAR_KEY, JSON.stringify(m)); } catch { /* private window */ }
}
const ago = t => { const s = Math.max(0, Math.round((Date.now() - t) / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const KM_PER_MI = 1.609344;
const LOW = 400, HIGH = 2800;   // waterfall range, Hz (= LOW/HIGH in procyon/js8/rx.py)
const TX_MAX = HIGH - 100;     // the TX mark stays where you can see your whole signal

// Lists re-render only when their HTML changes, so clicks don't land on replaced buttons.
function setHTML(el, html) { if (el._html !== html) { el._html = html; el.innerHTML = html; return true; } return false; }

const link = new FakeLink();
let me, bots = [];
let current = 'all';          // open tab: 'all', a call, or a @group
let tabs = [];                // open conversation tabs (besides "All activity")
let sortBy = 'last';
// where you transmit: remembered on this device between sessions
const TXF_KEY = 'procyon.js8TxFreq';
let myFreq = (() => {
  let f = NaN;
  try { f = Number(localStorage.getItem(TXF_KEY)); } catch { /* private window */ }
  return f >= LOW && f <= TX_MAX ? f : 1500;
})();
let autoHB = false, hbTimer = null;
let lastSuggest = null;
let toVal = '';               // who the typing box sends to (a call, a @group, or '' = everyone)
let live = null;              // the real radio (modem/js8live.py), when it serves this page
let liveTx = null;            // what Send goes out through ({to, ptt, test}, or a device name from the old helper), null = listen only
const txMsgs = new Map();     // id -> our sent message, while the helper reports on it
const wfLive = [];            // waterfall rows from the real radio, waiting to be drawn

// ---------------------------------------------------------------- setup
function start() {
  if (live) { goLive(live); return; }   // the real radio: never bring the pretend stations back
  link.reset();
  bots.forEach(b => b.stop());
  me = new ChatStation({
    call: JS8.clean($('myCall').value) || 'N0CALL', grid: JS8.clean($('myGrid').value) || 'EN80',
    name: JS8.clean($('myName').value), info: 'KX3 10W EFHW',
    send: text => link.send('me', text),
    onChange: () => render(),
  });
  link.attach('me', { call: me.call, freq: myFreq, snr: null,
    hear: (...a) => me.hear(...a), part: (...a) => me.hearPart(...a) });
  startBots();
  current = 'all'; tabs = []; toVal = '';
  render();
}
function startBots() {
  bots.forEach(b => b.stop());
  if (live) { bots = []; return; }      // pretend stations only on the pretend band
  bots = BOT_STATIONS.filter(b => b.call !== me.call).map(info => new Bot(link, info));
  bots.forEach(b => b.start());
}

function openTab(key, focus = true) {
  if (!tabs.includes(key)) tabs.push(key);
  if (focus) { current = key; me.read(key); toVal = key; }
}

// ---------------------------------------------------------------- render
function render() {
  if (!me) return;
  // a new message to us opens its tab (without jumping to it)
  for (const c of me.convos.values()) if (c.unread && !c.key.startsWith('@') && !tabs.includes(c.key)) openTab(c.key, false);
  // ...and so does one still arriving: the first frame already says it's for us
  for (const from of replying()) if (!tabs.includes(from)) openTab(from, false);
  // band activity popped out as its own panel: no "All activity" tab, show a chat instead
  if (popped() && current === 'all') current = tabs[tabs.length - 1] || '';
  if (!popped() && current === '') current = 'all';
  if (current && current !== 'all') me.read(current);
  renderHeard(); renderTabs(); renderChat(); renderBand(); renderTo(); renderSuggest(); updateMeter(); renderLogBtn();
}

const popped = () => dock.has('band');

function renderBand() {
  if (!popped()) return;
  const el = $('bandList'), stick = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  if (setHTML(el, bandRows()) && stick) el.scrollTop = el.scrollHeight;
}

// Stations whose message to us is still coming in, frame by frame.
function replying() {
  return [...me.arriving].filter(([from]) => !from.startsWith('?')).filter(([, a]) => JS8.parse(a.text).to === me.call || a.text === me.call).map(([from]) => from);
}

let useMiles = true;            // inside Procyon: follows Settings → Display → Distances
function dist(grid) {
  const km = JS8.distanceKm(me.grid, grid);
  if (km == null) return '';
  return useMiles ? `${Math.round(km / KM_PER_MI).toLocaleString()} mi` : `${Math.round(km).toLocaleString()} km`;
}

function renderHeard() {
  const list = [...me.heard.values()].sort(sortBy === 'snr' ? (a, b) => b.snr - a.snr : (a, b) => b.last - a.last);
  $('heardCount').textContent = list.length;
  setHTML($('heardList'), list.length ? list.map(h => {
    const unread = me.convos.get(h.call)?.unread, typing = replying().includes(h.call);
    return `<div class="hrow ${unread || typing ? 'calling' : ''} ${h.call === current ? 'open' : ''} ${h.hbOnly ? 'hb-only' : ''}" data-call="${h.call}"
         title="${h.hbOnly ? 'Only heard by heartbeat so far. ' : ''}Click to chat">
      <span class="call">${h.call}${h.hbOnly ? '<span class="hb">♥</span>' : ''}${typing ? '<span class="more" title="Sending you a message"><i></i><i></i><i></i></span>' : ''}</span>
      <span class="snr" style="--c:${snrColor(h.snr)}">${JS8.fmtSnr(h.snr)}</span>
      <span class="ago" title="minutes:seconds since last heard">${ago(h.last)}</span>
      <span class="meta">${h.grid ? `<span class="grid">${h.grid}</span> · ${dist(h.grid)} · ` : ''}${h.freq} Hz</span>
    </div>`;
  }).join('') : `<div class="empty">Listening… stations show up here as they're heard.${live ? '' : '<br>Tick <b>Fast test</b> above to speed things up.'}</div>`);
  const groups = [...me.groups, ...(me.heardGroups || [])];
  setHTML($('groupList'), groups.map(g => {
    const n = me.convos.get(g)?.unread;
    return `<div class="grow ${g === current ? 'open' : ''}" data-call="${g}">${g}${n ? `<span class="n">${n}</span>` : ''}
      <small>${me.groups.has(g) ? 'listening' : 'heard'}</small></div>`;
  }).join(''));
}

function renderTabs() {
  const all = popped() ? '' : `<button class="tab all ${current === 'all' ? 'on' : ''}" data-tab="all" title="Drag out, or click ⧉, to show it as its own panel">All activity<span class="pop" data-pop title="Show band activity as its own panel">⧉</span></button>`;
  setHTML($('tabs'), all + tabs.map(k => {
    const n = me.convos.get(k)?.unread, typing = replying().includes(k) && k !== current;
    return `<button class="tab ${k.startsWith('@') ? 'group' : ''} ${k === current ? 'on' : ''}" data-tab="${k}">
      ${k}${typing ? '<span class="more" title="Sending you a message"><i></i><i></i><i></i></span>' : ''}${n && k !== current ? `<span class="n">${n}</span>` : ''}<span class="x" data-close="${k}" title="Close">×</span></button>`;
  }).join(''));
}

function fmtText(from, to, body) {
  const who = c => c === me.call ? `<span class="c-me">${c}</span>` : c.startsWith('@') ? `<span class="c-group">${c}</span>` : `<span class="c-to">${c}</span>`;
  let b = esc(body);
  const w = body.split(' ')[0];
  if (JS8.COMMANDS.includes(w)) b = `<span class="c-cmd">${esc(w)}</span>` + esc(body.slice(w.length));
  b = b.replace(/\bCQ\b/g, '<span class="c-cq">CQ</span>');
  return `<span class="${from === me.call ? 'c-me' : 'c-from'}">${from}</span>: ${to ? who(to) + ' ' : ''}${b}`;
}

// Sending into a test sound device (no radio) rather than the radio?
function txTest() { return !!liveTx && (typeof liveTx === 'string' || liveTx.test); }

function txState(m) {
  const now = Date.now();
  if (m.error) return `<div class="state bad">not sent: ${esc(m.error)}</div>`;
  if (m.stopped) return '<div class="state bad">stopped</div>';
  if (m.heardBack) return `<div class="ack">✓ sent · decoded back by our own receiver${txTest() ? ' (test)' : ''}</div>`;
  if (m.heardBy) return `<div class="ack">✓ heard by ${m.heardBy}</div>`;
  if (!m.start) return '';
  if (now < m.start) return `<div class="state">waiting for the next slot · ${m.n} frame${m.n > 1 ? 's' : ''}</div>`;
  if (now < m.end) {
    const P = (m.end - m.start) / m.n, k = Math.min(m.n, Math.floor((now - m.start) / P) + 1);
    return `<div class="state on">${live && txTest() ? 'sending (test sound device, not the radio)' : 'on the air'} · frame ${k} of ${m.n} · ${Math.ceil((m.end - now) / 1000)} s</div>`;
  }
  return '<div class="state">sent</div>';
}

function bandRows() {
  const rows = withSep(me.band.slice(-200).map(b => [b.old, `
    <div class="brow ${b.hb ? 'hb' : ''} ${b.mine ? 'mine' : ''} ${b.toMe ? 'to-me' : ''} ${b.old ? 'old' : ''}" ${b.mine || b.from === '?' ? '' : `data-call="${b.from}"`}>
      <span class="t">${hhmmss(b.t)}</span><span class="snr" style="--c:${b.mine ? 'var(--tx)' : snrColor(b.snr)}">${b.mine ? 'TX' : JS8.fmtSnr(b.snr)}</span>
      <span class="hz">${b.mine ? (b.msg?.freq ?? myFreq) : b.freq}</span><span class="text">${fmtText(b.from, b.to, b.body)}</span>
    </div>`]));
  for (const [from, a] of me.arriving) {
    const p = JS8.parse(a.text);
    const who = from.startsWith('?') ? '?' : from;
    rows.push(`<div class="brow arriving" ${who === '?' ? '' : `data-call="${from}"`}><span class="t">…</span><span class="snr" style="--c:${snrColor(a.snr)}">${JS8.fmtSnr(a.snr)}</span>
      <span class="hz">${a.freq}</span><span class="text">${fmtText(who, p.to, p.body)}<span class="more"><i></i><i></i><i></i></span></span></div>`);
  }
  return rows.join('') || '<div class="empty">Nothing heard yet.</div>';
}

function renderChat() {
  const el = $('chat'), stick = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  let html;
  if (current === 'all') {
    el.classList.add('band-list');
    html = bandRows();
  } else if (!current) {
    el.classList.remove('band-list');
    html = '<div class="empty">Click a station in <b>Heard</b> or <b>Band activity</b> to chat with it.</div>';
  } else {
    el.classList.remove('band-list');
    const h = me.heard.get(current), c = me.convos.get(current);
    const head = current.startsWith('@')
      ? `Group <b>${current}</b> · messages to everyone listening for it`
      : `Chatting with <b>${current}</b>${h?.grid ? ` · ${h.grid} · ${dist(h.grid)}` : ''}${h ? ` · last heard ${ago(h.last)} ago` : ''}`;
    const bubbles = withSep((c?.msgs || []).map(m => [m.old, m.dir === 'tx' ? `
      <div class="bubble tx"><span class="who">You <span class="m">${hhmmss(m.t)}</span></span>${esc(m.body)}${txState(m)}</div>` : `
      <div class="bubble rx ${m.old ? 'old' : ''}"><span class="who">${m.from} <span class="m">${hhmmss(m.t)} · ${JS8.fmtSnr(m.snr)} dB</span></span>${esc(m.body)}</div>`]));
    // frames still coming in: from this station to us, or to this group
    for (const [from, a] of me.arriving) {
      const p = JS8.parse(a.text);
      if ((from === current && p.to === me.call) || (current.startsWith('@') && p.to === current))
        bubbles.push(`<div class="bubble rx arriving"><span class="who">${from} <span class="m">receiving…</span></span>${esc(p.body)}<span class="more" title="Still arriving: more frames to come"><i></i><i></i><i></i></span></div>`);
    }
    html = `<div class="chat-head">${head}</div>` + (bubbles.join('') || '<div class="empty">Nothing yet. Type below to start.</div>');
  }
  if (setHTML(el, html) && stick) el.scrollTop = el.scrollHeight;
}

function renderTo() {
  const sel = $('toSel'), v = toVal;
  const calls = [...new Set([...tabs.filter(k => !k.startsWith('@')), ...[...me.heard.keys()]])];
  const opts = [['', '(everyone - no call)'], ...calls.map(c => [c, c]), ...[...me.groups, ...(me.heardGroups || [])].map(g => [g, g])];
  if (v && !opts.some(o => o[0] === v)) opts.push([v, v]);
  setHTML(sel, opts.map(([k, l]) => `<option value="${k}">${l}</option>`).join(''));
  sel.value = v;
  $('toHint').textContent = !sel.value ? 'everyone hears it; nobody is asked to reply'
    : sel.value.startsWith('@') ? 'a group: everyone listening for it' : 'directed: only that station is asked to reply';
}

function renderSuggest() {
  const s = me.suggest;
  if (s && s !== lastSuggest) {
    lastSuggest = s;
    if (!$('txt').value.trim()) { toVal = s.to; $('txt').value = s.text; renderTo(); }
  }
  const box = $('suggest');
  box.hidden = !s;
  if (s) setHTML(box, `<span>${esc(s.why)}. ${$('txt').value === s.text ? 'The answer is in the box: press <b>Send</b>.' : ''}</span>
    ${$('txt').value === s.text ? '' : `<button class="btn small" id="useSuggest">Use "${esc(s.text)}"</button>`}
    <button class="link-x" id="dropSuggest" title="Don't answer">×</button>`);
}

// ---------------------------------------------------------------- typing box
// JS8 puts your call in front of every message, and the To box puts the
// other station's call after it - typing them again (or "DE KR1TTR") only
// makes the message longer. tidy() finds those words and leaves the rest.
function tidy(to, text) {
  const mine = ((me && me.call) || '').toUpperCase(), them = (to || '').toUpperCase();
  const core = w => w.toUpperCase().replace(/[.,:;!]+$/, '');
  const words = text.split(/\s+/).filter(Boolean), keep = [], found = new Set();
  for (let i = 0; i < words.length; i++) {
    const w = core(words[i]), next = core(words[i + 1] || '');
    if (w === 'DE' && mine && next === mine) { found.add('de'); i++; continue; }
    if (mine && w === mine) { found.add('mine'); continue; }
    if (them && !them.startsWith('@') && w === them && !keep.length) { found.add('them'); continue; }
    keep.push(words[i]);
  }
  return { text: keep.join(' '), found };
}

// How many frames a text takes. Inside Procyon the exact number comes from
// the same packer Send uses (asked in the background, nothing is sent); until
// it answers - or in the simulator - the page's own estimate stands in.
const counts = new Map();                // text -> exact frames (from Procyon)
let countAsked = new Set(), countTimer = null;
function frameCount(text) {
  if (counts.has(text)) return { n: counts.get(text), exact: true };
  if (EMBEDDED && live && live.readyState === 1 && !countAsked.has(text)) {
    countAsked.add(text);
    clearTimeout(countTimer);
    const ask = [...countAsked].filter(t => !counts.has(t)).slice(-4);
    countTimer = setTimeout(() => ask.forEach(t => live.send(JSON.stringify({ type: 'count', text: t }))), 150);
  }
  return { n: JS8.frames(text), exact: false };
}

function tidyNote(to, body) {
  const t = tidy(to, body);
  if (!t.found.size || !JS8.clean(t.text)) return '';
  const what = [];
  if (t.found.has('mine') || t.found.has('de')) what.push(t.found.has('de') ? `"DE ${me.call}"` : `your call`);
  if (t.found.has('them')) what.push(`${to} (from the To box)`);
  const full = to ? `${to} ${body}` : body, short = to ? `${to} ${t.text}` : t.text;
  const saved = frameCount(full).n - frameCount(short).n;
  return `<span class="tidy-note">JS8 adds ${what.join(' and ')} for you${saved > 0 ? ` - leaving it out saves ${saved} frame${saved > 1 ? 's' : ''}` : ''}.
    <button type="button" class="btn ghost small" id="tidyBtn" title="Take those words out of the message">Take it out</button></span>`;
}

// Heartbeats go out in 500-1000 Hz, like JS8Call's, so they stay out of the
// chat area. If your TX mark sits elsewhere, a heartbeat gets its own spot
// there (picked at random, so many stations don't pile up on one frequency);
// your chat frequency stays where it is.
const HB_LO = 500, HB_HI = 1000;
let hbSpot = null;
function txFreqFor(to) {
  if (to !== '@HB' || myFreq <= HB_HI) return myFreq;      // JS8Call keeps your own spot when it's at 1000 Hz or below
  if (hbSpot === null || !spotIsClear(hbSpot)) hbSpot = clearSpot(HB_LO, HB_HI, { random: true }).f;
  return hbSpot;
}

// ---------------------------------------------------------------- finding a clear spot
// Like JS8Call: a spot is busy if a station was heard within about its width
// in the last minute. Procyon also looks at the waterfall itself, so a signal
// too weak (or too new) to decode still counts.
const BUSY_SEC = 60;
const recentRows = [];          // the last ~15 s of waterfall rows from the radio
function noteRow(v) { recentRows.push(v); if (recentRows.length > 120) recentRows.shift(); }
function busyRanges() {
  const now = Date.now(), w = Math.max(link.width, 50), out = [];
  for (const b of me.band) if (!b.mine && b.freq != null && now - b.t < BUSY_SEC * 1000) out.push([b.freq, b.freq + w]);
  for (const a of me.arriving.values()) if (a.freq != null && now - a.t < BUSY_SEC * 1000) out.push([a.freq, a.freq + w]);
  if (recentRows.length >= 24) {          // a few seconds of waterfall: bins that keep lighting up
    const n = recentRows[0].length, hz = i => LOW + i / n * (HIGH - LOW);
    for (let i = 0; i < n; i++) {
      let hot = 0;
      for (const r of recentRows) if (r[i] > 0.5) hot++;
      if (hot >= recentRows.length * 0.15) out.push([hz(i), hz(i + 1)]);
    }
  }
  return out;
}
const GUARD = 10;               // Hz kept clear on each side of our signal
function spotIsClear(f, busy = busyRanges()) {
  const w = link.width;
  return busy.every(([a, b]) => b <= f - GUARD || a >= f + w + GUARD);
}
// A clear spot in lo..hi for our signal. random: any clear one (heartbeats, so
// many stations don't pick the same one); else the clear one nearest your mark.
// Returns {f, clear} - with nothing clear, the spot farthest from any signal.
function clearSpot(lo, hi, { random = false } = {}) {
  const busy = busyRanges(), w = link.width, spots = [];
  for (let f = lo; f + w <= hi; f += 10) spots.push(f);
  const free = spots.filter(f => spotIsClear(f, busy));
  if (free.length) {
    if (random) return { f: free[Math.floor(Math.random() * free.length)], clear: true };
    return { f: free.reduce((a, b) => Math.abs(b - myFreq) < Math.abs(a - myFreq) ? b : a), clear: true };
  }
  const room = f => Math.min(...busy.map(([a, b]) => Math.max(a - (f + w), f - b, 0)), 1e9);
  return { f: spots.reduce((a, b) => room(b) > room(a) ? b : a, lo), clear: false };
}
function setTxFreq(f) {
  myFreq = Math.max(LOW, Math.min(TX_MAX, f));
  const st = link.stations.get('me'); if (st) st.freq = myFreq;
  try { localStorage.setItem(TXF_KEY, String(myFreq)); } catch { /* private window */ }
  placeTx(); updateMeter();
}
let spotNoteTimer = null;
function spotNote(text, warn) {
  const el = $('spotNote');
  el.textContent = text; el.classList.toggle('warn', !!warn);
  clearTimeout(spotNoteTimer); spotNoteTimer = setTimeout(() => { el.textContent = ''; }, 8000);
}
$('spotBtn').onclick = () => {
  if (live && liveSince && Date.now() - liveSince < 45000) return spotNote('Listen for a minute first, so Procyon knows who is where', true);
  const inChat = myFreq >= 1000 && myFreq + link.width <= 2500;
  if (inChat && spotIsClear(myFreq)) return spotNote(`${myFreq} Hz is already clear - staying there`);
  const s = clearSpot(1000, 2500);
  setTxFreq(s.f);
  spotNote(s.clear ? `Moved to ${s.f} Hz - nobody heard there in the last minute` : `No fully clear spot - moved to ${s.f} Hz, the most room`, !s.clear);
};

function updateMeter() {
  const to = toVal, body = JS8.clean($('txt').value);
  const fc = body ? frameCount(to ? `${to} ${body}` : body) : { n: 0 }, n = fc.n;
  const speed = link.speed, sec = n * JS8.SPEEDS[speed];
  if (!n) return setHTML($('meter'), '<span>Type to see how long it takes on the air</span>');
  const dur = sec >= 60 ? `${Math.floor(sec / 60)} min${sec % 60 ? ' ' + (sec % 60) + ' s' : ''}` : `${sec} s`;
  const bars = Array.from({ length: Math.min(n, 16) }, (_, i) => `<i class="${i === 0 && to ? 'hdr' : ''}"></i>`).join('');
  $('meter').className = 'meter' + (sec > 90 ? ' long' : '');
  setHTML($('meter'), `<span class="frames">${bars}${n > 16 ? '…' : ''}</span>
    <span>${fc.exact ? '' : 'about '}<b>${n} frame${n > 1 ? 's' : ''}</b> · <b>${dur}</b> at ${speed}</span>
    ${sec > 90 ? '<span>long - maybe split it</span>' : ''}${txFreqFor(to) !== myFreq ? `<span class="hb-note">goes out at <b>${txFreqFor(to)} Hz</b> - heartbeats stay in ${HB_LO}-${HB_HI} Hz</span>` : ''}${tidyNote(to, body)}`);
}
$('meter').addEventListener('click', e => {
  if (!e.target.closest('#tidyBtn')) return;
  $('txt').value = tidy(toVal, JS8.clean($('txt').value)).text;
  $('txt').focus(); renderSuggest(); updateMeter();
});

function send() {
  const to = toVal, body = $('txt').value;
  if (!JS8.clean(body)) return;
  if (live) {
    // Procyon sends it (the radio, or a test sound device); it reports back the timing.
    if (!liveTx || live.readyState !== 1) return;
    if (to) openTab(to);
    const clean = JS8.clean(body), text = to ? `${to} ${clean}` : clean;
    const m = me.send(to, body);
    if (!m) return;
    const freq = txFreqFor(to);
    hbSpot = null;                       // the next heartbeat picks a fresh spot
    m.freq = freq;
    txMsgs.set(m.id, m);
    live.send(JSON.stringify({ type: 'tx', id: m.id, text, call: me.call, grid: me.grid, freq }));
    $('txt').value = '';
    render();
    return;
  }
  if (to) openTab(to);
  me.send(to, body);
  $('txt').value = '';
  render();
}

const MACROS = [
  ['CQ', '', 'CQ CQ {GRID}', 'cq'],
  ['Heartbeat', '@HB', 'HEARTBEAT {GRID}', 'hb'],
  ['SNR?', null, 'SNR?'],
  ['GRID?', null, 'GRID?'],
  ['INFO?', null, 'INFO?'],
  ['HEARING?', null, 'HEARING?'],
  ['Name', null, 'GE, NAME HERE IS {NAME}'],
  ['Station', null, 'RIG {INFO}'],
  ['Weather', null, 'WX HERE CLOUDY 12C'],
  ['73', null, 'TNX FER THE CHAT, 73'],
];
$('macros').innerHTML = MACROS.map(([label, , , cls], i) => `<button class="macro ${cls || ''}" data-i="${i}">${label}</button>`).join('');
$('macros').addEventListener('click', e => {
  const b = e.target.closest('.macro[data-i]'); if (!b) return;
  const [, to, text] = MACROS[b.dataset.i];
  if (to !== null) toVal = to;
  $('txt').value = text.replace('{GRID}', me.grid).replace('{NAME}', me.name || '').replace('{INFO}', me.info);
  renderTo(); updateMeter(); $('txt').focus();
});

// ---------------------------------------------------------------- clicks
document.addEventListener('click', e => {
  if (e.target.closest('[data-pop]')) { dock.add('band', 'convo', 'right'); return; }
  if (e.target.closest('[data-unpop]')) { dock.drop('band'); current = 'all'; render(); return; }
  const x = e.target.closest('[data-close]');
  if (x) { tabs = tabs.filter(k => k !== x.dataset.close); if (current === x.dataset.close) current = 'all'; render(); return; }
  const tab = e.target.closest('[data-tab]');
  if (tab) { current = tab.dataset.tab; if (current !== 'all') { me.read(current); toVal = current; } render(); return; }
  const row = e.target.closest('[data-call]');
  if (row) { openTab(row.dataset.call); render(); return; }
  if (e.target.id === 'useSuggest') { toVal = me.suggest.to; $('txt').value = me.suggest.text; render(); return; }
  if (e.target.id === 'dropSuggest') { me.suggest = null; render(); return; }
  const clr = e.target.closest('[data-clear]');
  if (clr) {
    const key = clr.dataset.clear === 'view' ? current : clr.dataset.clear;
    if (!key) return;
    if (!clr.classList.contains('armed')) {           // first click: ask
      clr.classList.add('armed'); clr.textContent = 'Clear? Click again';
      clearTimeout(clr._t); clr._t = setTimeout(() => { clr.classList.remove('armed'); clr.textContent = 'Clear'; }, 4000);
      return;
    }
    clearTimeout(clr._t); clr.classList.remove('armed'); clr.textContent = 'Clear';
    markCleared(key); me.clear(key);
  }
});
$('sendBtn').onclick = send;
// Stop sending (Esc too): drops queued messages and cuts the frame that's playing.
function stopTx() { if (live && liveTx) live.send(JSON.stringify({ type: 'txstop' })); }
$('stopTx').onclick = stopTx;
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('stopTx').hidden && !$('logDlg').open) stopTx(); });
// Inside Procyon the button is always there as "Halt" while you can transmit
// (like the FT8 screen's Halt); it lights up while something is going out.
function updateStop() {
  const busy = [...txMsgs.values()].some(x => !x.done), b = $('stopTx');
  if (EMBEDDED && liveTx) {
    b.hidden = false; b.textContent = 'Halt'; b.classList.toggle('idle', !busy);
    b.title = busy ? 'Stop sending now (Esc) - drops anything still queued' : 'Nothing is going out. Halt (or Esc) stops a message at once.';
  } else b.hidden = !busy;
}

// ---------------------------------------------------------------- Log QSO
// JS8 has no fixed QSO sequence, so a contact is logged when you say so: the
// button on a chat's tab fills in what the chat already knows.
const logged = new Set();       // calls logged since this page opened
const isStation = k => !!k && k !== 'all' && !k.startsWith('@') && !k.startsWith('?');
function renderLogBtn() {
  const b = $('logBtn'), on = EMBEDDED && !!live && !!liveTx && isStation(current);
  b.hidden = !on;
  if (!on) return;
  b.classList.toggle('done', logged.has(current));
  b.textContent = logged.has(current) ? 'Logged ✓' : 'Log QSO';
}
// A report in a message: "SNR -09" (JS8Call's way), or a signed number typed
// by hand - "GOT YOU -09.", "YOUR -08 HERE", "HERE-10". The last one counts.
const SNR_IN = /\bSNR ([+-]?\d{1,2})\b/g;
const REPORT_IN = /(?<![\d+-])([+-]\d{1,2})(?!\d)/g;
function reportIn(body) {
  for (const re of [SNR_IN, REPORT_IN]) {
    const all = [...body.matchAll(re)].map(r => +r[1]).filter(n => n >= -30 && n <= 30);
    if (all.length) return all[all.length - 1];
  }
  return null;
}
function lastSnr(msgs, dir) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const x = msgs[i];
    if (x.dir !== dir) continue;
    const n = reportIn(x.body);
    if (n !== null) return JS8.fmtSnr(n);
  }
  return '';
}
function openLog() {
  const call = current, c = me.convos.get(call), h = me.heard.get(call) || {};
  const msgs = (c?.msgs || []).filter(x => !x.queued);
  const name = msgs.filter(x => x.dir === 'rx').map(x => (x.body.match(/\b(?:NAME(?: HERE)?(?: IS)?|OP(?: HERE)?(?: IS)?)\s+([A-Z]{2,15})\b/) || [])[1]).filter(Boolean).pop();
  const f = $('logForm');
  f.call.value = call;
  f.grid.value = h.grid || '';
  f.rst_sent.value = lastSnr(msgs, 'tx') || (h.snr != null ? JS8.fmtSnr(h.snr) : '');
  f.rst_rcvd.value = lastSnr(msgs, 'rx');
  f.name.value = name || '';
  f.comment.value = '';
  const t0 = msgs.length ? msgs[0].t : Date.now();
  f.dataset.timeOn = t0;
  $('logInfo').textContent = `Started ${new Date(t0).toISOString().slice(11, 16)} UTC · ${link.speed} · your TX at ${myFreq} Hz. Check the reports - "Sent" is the last report you sent them (or how you hear them).`;
  $('logErr').hidden = true;
  $('logDlg').showModal();
  f.rst_rcvd.value ? f.name.focus() : f.rst_rcvd.focus();
}
$('logBtn').onclick = () => { if (isStation(current)) openLog(); };
$('logForm').addEventListener('submit', e => {
  if (e.submitter?.value !== 'log') return;
  const f = $('logForm');
  e.preventDefault();
  const rep = v => { v = v.trim(); return /^[+-]?\d{1,2}$/.test(v) ? v : ''; };
  for (const k of ['rst_sent', 'rst_rcvd']) {
    if (f[k].value.trim() && !rep(f[k].value)) { $('logErr').textContent = `${k === 'rst_sent' ? 'Sent' : 'Rcvd'} should be a number like -05 or +03 (or empty).`; $('logErr').hidden = false; return; }
  }
  live.send(JSON.stringify({ type: 'log', call: JS8.clean(f.call.value), grid: f.grid.value.trim().toUpperCase(),
    rst_sent: rep(f.rst_sent.value), rst_rcvd: rep(f.rst_rcvd.value), name: f.name.value.trim(),
    comment: f.comment.value.trim(), time_on: +f.dataset.timeOn, freq: myFreq }));
  $('logOk').disabled = true;
});

// ---------------------------------------------------------------- TX drive (the FT8 screen's slider, here too)
let driveTimer = null;
$('driveIn').addEventListener('input', () => {
  $('driveOut').textContent = `${$('driveIn').value}%`;
  clearTimeout(driveTimer);
  driveTimer = setTimeout(() => live?.send(JSON.stringify({ type: 'drive', v: +$('driveIn').value })), 120);
});
function pageInfo(p) {          // from the Procyon page around this frame
  if (typeof p.miles === 'boolean' && p.miles !== useMiles) { useMiles = p.miles; if (me) renderHeard(); }
  if (Number.isFinite(p.drive)) {
    $('driveBox').hidden = false;
    if (document.activeElement !== $('driveIn')) $('driveIn').value = p.drive;
    $('driveOut').textContent = `${p.drive}%`;
  }
}

// ---------------------------------------------------------------- look up on QRZ
// Right-click a call (or press and hold on a touchscreen), like the FT8 screen.
const STD_CALL = /^[A-Z0-9]{1,2}\d[A-Z]{1,3}$/;
function baseCall(c) {
  if (!c.includes('/')) return c;
  const parts = c.split('/').filter(Boolean), std = parts.filter(x => STD_CALL.test(x));
  return (std.length ? std : parts).reduce((a, b) => (b.length > a.length ? b : a), '');
}
function callAt(el) {
  const t = el.closest('[data-call],[data-tab]');
  const c = t && (t.dataset.call || t.dataset.tab);
  return isStation(c) && c !== me?.call ? c : null;
}
function openCallMenu(call, x, y) {
  const m = $('callMenu'), base = baseCall(call);
  m.innerHTML = `<a href="https://www.qrz.com/db/${encodeURIComponent(base)}" target="_blank" rel="noopener noreferrer">Look up ${esc(base)} on QRZ ↗</a>`;
  m.hidden = false;
  m.style.left = `${Math.min(x, innerWidth - m.offsetWidth - 8)}px`;
  m.style.top = `${Math.min(y, innerHeight - m.offsetHeight - 8)}px`;
}
const closeCallMenu = () => { $('callMenu').hidden = true; };
document.addEventListener('contextmenu', e => {
  if (e.target.closest('input, textarea, select')) return;
  const c = callAt(e.target);
  if (!c) return;
  e.preventDefault();
  openCallMenu(c, e.clientX, e.clientY);
});
let holdTimer = null, held = false;
document.addEventListener('touchstart', e => {
  const c = callAt(e.target), t = e.touches[0];
  held = false;
  if (c) holdTimer = setTimeout(() => { held = true; openCallMenu(c, t.clientX, t.clientY); }, 550);
}, { passive: true });
['touchend', 'touchmove', 'touchcancel'].forEach(ev => document.addEventListener(ev, () => clearTimeout(holdTimer), { passive: true }));
document.addEventListener('click', e => {
  if (held) { held = false; e.stopPropagation(); e.preventDefault(); return; }   // the hold opened the menu: don't open the chat too
  if (!e.target.closest('#callMenu')) closeCallMenu();
  else setTimeout(closeCallMenu, 0);
}, true);
addEventListener('keydown', e => { if (e.key === 'Escape') closeCallMenu(); }, true);
$('clearBtn').onclick = () => { $('txt').value = ''; render(); };
$('txt').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
$('txt').addEventListener('input', () => { renderSuggest(); updateMeter(); });
$('toSel').addEventListener('change', e => { toVal = e.target.value; renderTo(); updateMeter(); });
$('sortSeg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  [...b.parentNode.children].forEach(x => x.classList.toggle('on', x === b));
  sortBy = b.dataset.sort; render();
});
$('speedSeg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  [...b.parentNode.children].forEach(x => x.classList.toggle('on', x === b));
  link.speed = b.textContent;
  if (live) { live.send(JSON.stringify({ type: 'speed', speed: link.speed })); render(); return; }
  startBots(); setHB(autoHB); render();
});
$('fast').onchange = e => { link.fast = e.target.checked; startBots(); setHB(autoHB); };
for (const k of ['loss', 'garble']) $(k).oninput = e => { link[k] = e.target.value / 100; $(k + 'V').textContent = e.target.value + '%'; };
$('resetBtn').onclick = start;
for (const k of ['myCall', 'myGrid']) $(k).onchange = start;
// your name only fills in the Name button's text: changing it starts nothing over
$('myName').onchange = () => { if (live) { me.name = JS8.clean($('myName').value); render(); } else start(); };
if (EMBEDDED) for (const k of ['myCall', 'myGrid']) {   // inside Procyon: they come from its Settings
  $(k).readOnly = true; $(k).title = "Set in Procyon's Settings (Station & radio)";
}
$('palette').onchange = e => {
  if (e.target.value) document.documentElement.dataset.palette = e.target.value;
  else delete document.documentElement.dataset.palette;
};

// Automatic heartbeat every 10 minutes (faster in Fast test mode).
function setHB(on) {
  if (live) on = false;         // listen only: no heartbeats on the real radio
  autoHB = on;
  clearInterval(hbTimer);
  if (on) hbTimer = setInterval(() => me.send('@HB', `HEARTBEAT ${me.grid}`), 600 * 1000 * link.frameSec / 15);
  $('hbLabel').textContent = on ? 'Heartbeat: every 10 min' : 'Heartbeat: off';
  $('hbChip').classList.toggle('ok', on);
}
$('hbChip').onclick = () => setHB(!autoHB);

// ---------------------------------------------------------------- clock, cycle, waterfall
$('wfStrip').addEventListener('click', e => {
  const r = $('wf').getBoundingClientRect();
  myFreq = Math.round((LOW + (e.clientX - r.left) / r.width * (HIGH - LOW)) / 10) * 10;
  setTxFreq(myFreq);
});
// band plan brackets above the waterfall, placed from the same range
for (const [sel, lo, hi] of [['.wf-plan .hb', 500, 1000], ['.wf-plan .chat', 1000, 2500]]) {
  const el = document.querySelector(sel);
  if (el) { el.style.left = `${(lo - LOW) / (HIGH - LOW) * 100}%`; el.style.width = `${(hi - lo) / (HIGH - LOW) * 100}%`; }
}
function placeTx() {
  $('wfTx').style.left = ((myFreq - LOW) / (HIGH - LOW) * 100) + '%';
  $('wfTx').style.width = (link.width / (HIGH - LOW) * 100) + '%';
  $('wfTxLabel').textContent = `TX ${myFreq} Hz`;
}

setInterval(() => {
  const now = Date.now(), P = link.frameSec, into = (now / 1000) % P;
  $('utc').innerHTML = hhmmss(now) + ' <small>UTC</small>';
  const mine = link.nowAiring(now).find(a => a.id === 'me');
  if (mine) {
    const k = Math.floor((now - mine.start) / (P * 1000)) + 1;
    $('cycleLabel').textContent = `TX ${k}/${mine.n} · ${Math.ceil(P - into)} s`;
  } else $('cycleLabel').textContent = `RX · ${Math.ceil(P - into)} s`;
  $('cycleFill').style.width = (into / P * 100) + '%';
  $('cycleFill').classList.toggle('tx', !!mine);
  placeTx();
}, 100);
// times ("last heard", "on the air") tick along
setInterval(render, 1000);

(function waterfall() {
  const c = $('wf'), g = c.getContext('2d');
  // the canvas follows its tile's size (the tile can be resized); start it full of band noise
  function size() {
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h || (c.width === w && c.height === h)) return;
    if (live && c.width && c.height) {   // real radio: keep what's been drawn, stretched to the new size
      const old = document.createElement('canvas');
      old.width = c.width; old.height = c.height;
      old.getContext('2d').drawImage(c, 0, 0);
      c.width = w; c.height = h;
      g.drawImage(old, 0, 0, old.width, old.height, 0, 0, w, h);
      return;
    }
    c.width = w; c.height = h;
    for (let y = 0; y < h; y++) row([]);
  }
  // one new line at the top, the rest moves down
  function row(on) {
    const w = c.width, img = g.createImageData(w, 1);
    for (let x = 0; x < w; x++) {
      const hz = LOW + x / w * (HIGH - LOW);
      let v = Math.random() * 0.22;
      for (const a of on) {
        if (a.id === 'me' || hz < a.freq || hz > a.freq + link.width) continue;
        const st = link.stations.get(a.id);
        v += (0.35 + ((st?.snr ?? -10) + 24) / 50) * (0.75 + Math.random() * 0.25);
      }
      v = Math.min(1, v);
      img.data.set(wfColor(v), x * 4);   // Procyon's waterfall colours (Settings → Themes)
    }
    g.drawImage(c, 0, 1);
    g.putImageData(img, 0, 0);
  }
  size(); new ResizeObserver(size).observe(c);
  // the real radio: rows of 0..1 values (noise floor .. strong) from js8live.py
  function liveRow(v) {
    const w = c.width, img = g.createImageData(w, 1);
    for (let x = 0; x < w; x++) {
      const a = Math.sqrt(v[Math.min(v.length - 1, Math.floor(x / w * v.length))]) * 0.85 + Math.random() * 0.06;
      const t = Math.min(1, a);
      img.data.set(wfColor(t), x * 4);
    }
    g.drawImage(c, 0, 1);
    g.putImageData(img, 0, 0);
  }
  setInterval(() => {
    size();
    if (!live) return row(link.nowAiring());
    while (wfLive.length > 16) wfLive.shift();   // fell behind (hidden tab): skip ahead
    if (wfLive.length) liveRow(wfLive.shift());
    if (wfLive.length > 2) liveRow(wfLive.shift());   // a little behind: catch up gently
  }, 125);   // 8 rows a second, like the FT8 waterfall
})();

// Panels: waterfall on top, Heard beside the chat below it (until you move them).
const dock = new Dock($('main'), {
  key: 'procyon.js8.layout',
  defaults: { dir: 'col', kids: ['wf', { dir: 'row', kids: ['heard', 'convo'], sizes: [1, 3.2] }], sizes: [1.4, 6] },
  optional: ['band'],   // band activity: a tab in the chat, or popped out as its own panel
  tabHome: { band: { into: 'convo', row: '.tabs-row' } },
  keep: ['heard'],   // the Heard list keeps its width when panels come and go (drag its edge to change it)
});
dock.onLayout = () => render();
dock.onTabbed = () => { current = 'all'; render(); };
$('layoutReset').onclick = () => dock.reset();

// Drag the "All activity" tab out of the chat to make it a panel (a click still just opens it).
$('tabs').addEventListener('pointerdown', e => {
  const tab = e.target.closest('.tab.all');
  if (!tab || e.button !== 0 || dock.phone || e.target.closest('[data-pop]')) return;
  const x0 = e.clientX, y0 = e.clientY;
  const move = ev => {
    if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) return;
    stop();
    dock.dragTile(ev, dock.tiles.get('band'));   // (not on the page while it's a tab)
  };
  const stop = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', stop); };
  addEventListener('pointermove', move);
  addEventListener('pointerup', stop);
});

start();

// ---------------------------------------------------------------- the real radio (listen only)
// When modem/js8live.py serves this page it answers on /ws: the pretend band
// stops and real decodes come in through the same hear()/hearPart() as the bots'.
// Inside Procyon the decodes come from Procyon's own engine, passed on by the
// page around this frame (postMessage, same origin only); the chat screen's
// commands (the speed buttons) go back the same way.
function handleLive(m, ws) {
    if (m.type === 'logged') {             // Procyon's answer to Log QSO
      $('logOk').disabled = false;
      if (m.error) { $('logErr').textContent = m.error; $('logErr').hidden = false; return; }
      logged.add(m.call);
      $('logDlg').close();
      renderLogBtn();
      return;
    }
    if (m.type === 'count') {              // Procyon's exact frame count for a text
      if (!Number.isInteger(m.n) || m.n < 1) return;   // couldn't count it: keep the estimate (and don't ask again)
      countAsked.delete(m.text);
      counts.set(m.text, m.n);
      if (counts.size > 200) counts.delete(counts.keys().next().value);
      updateMeter();
      return;
    }
    if (m.type === 'hello') {
      counts.clear(); countAsked.clear();   // your call (in the first frame) may have changed
      if (!live) goLive(ws);
      liveSince = m.since || null;
      const sp = [...$('speedSeg').children].find(b => b.textContent === m.speed);
      if (sp) { [...$('speedSeg').children].forEach(x => x.classList.toggle('on', x === sp)); link.speed = m.speed; }
      $('liveNote').textContent = m.error ? `audio problem: ${m.error}`
        : m.quiet ? `no audio coming in (${m.level} dB) - radio off or volume down?`
        : !m.tx ? `listening only on ${m.device} - set your callsign in Settings to send`
        : typeof m.tx === 'string' ? `listening on ${m.device} · TEST sending into ${m.tx} (a test sound device - no radio)`
        : m.tx.test ? `listening on ${m.device} · TEST sending into ${m.tx.to} (a test sound device - no radio)`
        : `listening on ${m.device} · Send transmits on your radio (PTT by ${m.tx.ptt})`;
      $('liveNote').classList.toggle('bad', !!(m.error || m.quiet));
      // inside Procyon the note only shows when something needs attention
      document.body.classList.toggle('note-warn', !!(m.error || m.quiet || !m.tx || m.tx.test || typeof m.tx === 'string'));
      if (m.me && m.me.call) {                  // Settings → Station: call, grid, name
        $('myCall').value = m.me.call; $('myGrid').value = (m.me.grid || '').slice(0, 4); $('myName').value = m.me.name || '';
        if (me) Object.assign(me, { call: JS8.clean(m.me.call), grid: JS8.clean(m.me.grid || '') || me.grid, name: JS8.clean(m.me.name || '') });
      }
      liveTx = m.tx || null;
      updateStop();
      $('sendBtn').disabled = !liveTx;
      $('sendBtn').textContent = !liveTx ? 'Listen only' : txTest() ? 'Send (test)' : 'Send';
      $('sendBtn').title = !liveTx ? 'Set your callsign in Settings to send'
        : txTest() ? `Plays the message into ${liveTx.to || liveTx}, a test sound device - the radio isn't connected to it`
        : 'Transmits the message on your radio, one frame per slot. Stop (or Esc, or Halt) stops it at once.';
    } else if (m.type === 'part') { if (m.from !== me.call) me.hearPart(m.text, m.from, m.snr, m.freq); }
    else if (m.type === 'msg') {
      if (m.from && m.from === me.call) {     // our own signal, heard back (test sound device loops it round)
        // which of ours: the one that finished just before, on that audio frequency
        const sent = [...txMsgs.values()].find(x => !x.heardBack && x.end && Math.abs(x.freq - m.freq) <= 10 && m.t >= x.end - 1000 && m.t <= x.end + 4000);
        if (sent) sent.heardBack = true;
        render();
      } else me.hear(m.text, m.from, m.snr, m.freq, m.t);
    }
    else if (m.type === 'txplan' || m.type === 'txdone') {
      const sent = txMsgs.get(m.id);
      if (sent) {
        if (m.type === 'txplan') Object.assign(sent, { start: m.start, end: m.end, n: m.n });
        else { if (m.error) sent.error = m.error; if (m.stopped) sent.stopped = true; sent.done = true; }
        render();
      }
      updateStop();
    }
    else if (m.type === 'history') {          // what the helper heard before this page opened
      for (const h of m.msgs) if (h.from !== me.call) me.hear(h.text, h.from, h.snr, h.freq, h.t, true);
      // leave out what was cleared before the reload
      me.band = me.band.filter(b => b.t > clearedAt('all'));
      for (const c of me.convos.values()) { c.msgs = c.msgs.filter(x => x.t > clearedAt(c.key)); c.unread = 0; }
      render();
    }
    else if (m.type === 'wf') { wfLive.push(m.v); noteRow(m.v); }
}

(function connectLive() {
  if (EMBEDDED) {
    const bridge = { readyState: 1, send: d => parent.postMessage({ procyonJs8Cmd: JSON.parse(d) }, location.origin) };
    window.addEventListener('message', e => {
      if (e.origin !== location.origin || e.source !== parent) return;
      const d = e.data || {};
      if (d.procyonJs8Page) { pageInfo(d.procyonJs8Page); return; }
      if (!d.procyonJs8) return;
      handleLive(d.procyonJs8, bridge);
      if (d.history) handleLive({ type: 'history', msgs: d.history }, bridge);
    });
    parent.postMessage({ procyonJs8Cmd: { type: 'ready' } }, location.origin);   // "catch me up"
    return;
  }
  if (!/^https?:$/.test(location.protocol)) return;
  let ws;
  try { ws = new WebSocket(`ws://${location.host}/ws`); } catch { return; }
  ws.onmessage = e => handleLive(JSON.parse(e.data), ws);
  ws.onclose = () => {
    if (!live) return;          // never was live (a plain web server): stay on the pretend band
    $('liveNote').textContent = 'lost the radio helper (js8live.py stopped?) - reload when it runs again';
    $('liveNote').classList.add('bad');
  };
})();

function goLive(ws) {
  live = ws;
  bots.forEach(b => b.stop()); bots = [];
  link.reset();
  link.fast = false; $('fast').checked = false;
  setHB(false);
  const myCall = JS8.clean($('myCall').value) || me.call, grid = JS8.clean($('myGrid').value) || me.grid;
  const name = JS8.clean($('myName').value);
  me = new ChatStation({ call: myCall, grid, name, info: 'KX3 10W EFHW', send: () => null, onChange: () => render() });
  document.body.classList.add('live');
  $('simLabel').textContent = 'LIVE RADIO';
  current = 'all'; tabs = []; toVal = '';
  render();
}
