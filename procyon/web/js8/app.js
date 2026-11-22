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
}

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
const LOW = 300, HIGH = 3300;   // waterfall range, Hz

// Lists re-render only when their HTML changes, so clicks don't land on replaced buttons.
function setHTML(el, html) { if (el._html !== html) { el._html = html; el.innerHTML = html; return true; } return false; }

const link = new FakeLink();
let me, bots = [];
let current = 'all';          // open tab: 'all', a call, or a @group
let tabs = [];                // open conversation tabs (besides "All activity")
let sortBy = 'last';
let myFreq = 1500;
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
  renderHeard(); renderTabs(); renderChat(); renderBand(); renderTo(); renderSuggest(); updateMeter();
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

function dist(grid) {
  const km = JS8.distanceKm(me.grid, grid);
  return km == null ? '' : `${Math.round(km / KM_PER_MI).toLocaleString()} mi`;
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
      <span class="hz">${b.mine ? myFreq : b.freq}</span><span class="text">${fmtText(b.from, b.to, b.body)}</span>
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
function updateMeter() {
  const to = toVal, body = JS8.clean($('txt').value);
  const n = body ? JS8.frames(to ? `${to} ${body}` : body) : 0;
  const speed = link.speed, sec = n * JS8.SPEEDS[speed];
  if (!n) return setHTML($('meter'), '<span>Type to see how long it takes on the air</span>');
  const dur = sec >= 60 ? `${Math.floor(sec / 60)} min${sec % 60 ? ' ' + (sec % 60) + ' s' : ''}` : `${sec} s`;
  const bars = Array.from({ length: Math.min(n, 16) }, (_, i) => `<i class="${i === 0 && to ? 'hdr' : ''}"></i>`).join('');
  $('meter').className = 'meter' + (sec > 90 ? ' long' : '');
  setHTML($('meter'), `<span class="frames">${bars}${n > 16 ? '…' : ''}</span>
    <span>about <b>${n} frame${n > 1 ? 's' : ''}</b> · <b>${dur}</b> at ${speed}</span>
    ${sec > 90 ? '<span>long - maybe split it</span>' : ''}`);
}

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
    m.freq = myFreq;
    txMsgs.set(m.id, m);
    live.send(JSON.stringify({ type: 'tx', id: m.id, text, call: me.call, grid: me.grid, freq: myFreq }));
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
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('stopTx').hidden) stopTx(); });
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
  myFreq = Math.max(LOW, Math.min(2900, myFreq));
  const st = link.stations.get('me'); if (st) st.freq = myFreq;
  placeTx();
});
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
      img.data.set([20 + 235 * v * v, 30 + 220 * v, 80 + 175 * v, 255], x * 4);   // "lightning": blue -> cyan -> white
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
      img.data.set([20 + 235 * t * t, 30 + 220 * t, 80 + 175 * t, 255], x * 4);
    }
    g.drawImage(c, 0, 1);
    g.putImageData(img, 0, 0);
  }
  setInterval(() => {
    size();
    if (!live) return row(link.nowAiring());
    while (wfLive.length > 8) wfLive.shift();   // fell behind (hidden tab): skip ahead
    if (wfLive.length) liveRow(wfLive.shift());
  }, 250);
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
    if (m.type === 'hello') {
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
      liveTx = m.tx || null;
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
      $('stopTx').hidden = ![...txMsgs.values()].some(x => !x.done);
    }
    else if (m.type === 'history') {          // what the helper heard before this page opened
      for (const h of m.msgs) if (h.from !== me.call) me.hear(h.text, h.from, h.snr, h.freq, h.t, true);
      // leave out what was cleared before the reload
      me.band = me.band.filter(b => b.t > clearedAt('all'));
      for (const c of me.convos.values()) { c.msgs = c.msgs.filter(x => x.t > clearedAt(c.key)); c.unread = 0; }
      render();
    }
    else if (m.type === 'wf') wfLive.push(m.v);
}

(function connectLive() {
  if (EMBEDDED) {
    const bridge = { readyState: 1, send: d => parent.postMessage({ procyonJs8Cmd: JSON.parse(d) }, location.origin) };
    window.addEventListener('message', e => {
      if (e.origin !== location.origin || e.source !== parent) return;
      const d = e.data || {};
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
