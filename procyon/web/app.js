"use strict";

const $ = (s) => document.querySelector(s);
// Cycle timing of the mode in use (FT8: 15 s, FT4: 7.5 s), from the engine.
const T = () => (S && S.timing) || { mode: "FT8", period: 15, tx_start: 0.5, signal: 12.64, decode_at: 13.4, width: 50 };
const period = () => T().period;
// Which slots a cycle is in: FT8 :00/:30 and :15/:45, FT4 :00/:15 and :07/:22 (7.5 s apart).
const slotName = (par) => (T().mode === "FT4" ? (par ? ":07/:22" : ":00/:15") : (par ? ":15/:45" : ":00/:30"));
const BIN_HZ = 12000 / 4096;    // width of one waterfall bin from the engine
// The visible waterfall range comes from Settings (to match the radio's filter).
const wfLo = () => (S ? +S.settings.wf_start : 0);
const wfHi = () => (S ? +S.settings.wf_end : 3000);
const xFrac = (hz) => (hz - wfLo()) / (wfHi() - wfLo());
const MAX_PERIODS = 40;

let S = null;                // latest engine state
let ws = null;
let batches = [];            // decode batches, oldest first
let filter = "all";
let isLocal = true;          // false when this browser is a remote (network) device

const send = (o) => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ------------------------------------------------------------------ socket

let stopped = false;   // Procyon was stopped with Quit (here or on the station computer)

function showStopped() {
  stopped = true;
  $("#offline").firstElementChild.outerHTML = `<div class="stopped">
    <img src="/static/icon.svg" alt=""><b>Procyon has stopped</b>
    ${isLocal ? "You can close this tab. To start again, open Procyon from your menu."
    : "It was stopped on the station computer."}<br>This page reconnects by itself when Procyon is running again.</div>`;
  $("#offline").hidden = false;
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    $("#offline").hidden = true;
    if (stopped) { stopped = false; $("#offline").firstElementChild.outerHTML = "<div>Reconnecting to Procyon…</div>"; }
  };
  ws.onclose = async () => {
    $("#offline").hidden = false;
    if (stopped) {   // Quit was pressed: say so, and pick up again if Procyon is restarted
      setTimeout(connect, 3000);
      return;
    }
    if (!isLocal) {
      // Signed out (PIN changed) or remote access switched off?
      try {
        const r = await fetch("/", { redirect: "manual" });
        if (r.type === "opaqueredirect" || r.status === 401) { location.href = "/login"; return; }
        $("#offline").firstElementChild.textContent = r.status === 403
          ? "Remote access was switched off on the station computer." : "Reconnecting to Procyon…";
      } catch { /* Procyon not running: keep retrying */ }
    }
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") return wfRow(new Uint8Array(ev.data));
    const m = JSON.parse(ev.data);
    if (m.type === "hello") { isLocal = m.local; $("#quitBtn").hidden = !isLocal; }
    else if (m.type === "stopped") showStopped();
    else if (m.type === "state") onState(m);
    else if (m.type === "decodes") onDecodes(m, true);
    else if (m.type === "history") {
      batches = [];
      callers.clear(); dismissed.clear(); renderCallers();   // e.g. after an FT8/FT4 switch
      m.batches.forEach((b) => onDecodes(b, false));
      renderDecodes();
    }
    else if (m.type === "level") onLevel(m.db);
    else if (m.type === "toast") toast(m.text, m.kind);
    else if (m.type === "js8") toJs8Frame(m);
    else if (m.type === "devices") { fillDevices(m); fillRadioLists(m); }
    else if (m.type === "wsjtx_import") applyImport(m);
    else if (m.type === "detect_radio") onDetected(m);
    else if (m.type === "rig_test") onRigTest(m);
    else if (m.type === "qrz_check") onQrzCheck(m);
    else if (m.type === "log_import") importMsg(m);
    else if (m.type === "cty_update") ctyMsg(m);
    else if (m.type === "export_marked") { $("#exportMsg").textContent = m.text; $("#exportMsg").classList.add("ok"); }
    else if (m.type === "pota") onPota(m);
    else if (m.type === "install") onInstall(m);
  };
}

// ---------------------------------------------------------------- waterfall

const wf = $("#wf");
const wctx = wf.getContext("2d");
const rowCanvas = document.createElement("canvas");
const rowCtx = rowCanvas.getContext("2d");
let rowImg = null;
let lastPeriodMark = -1;
let rowsSinceMark = -1;      // waterfall rows since the current cycle started
const LABEL_ROWS = 16;       // 2 s at 8 rows/s: room to write the label above the line

// Waterfall colour schemes (Settings → Themes; chosen per device). Each is
// a ramp of [position 0-1, R, G, B] stops from the noise floor up to the
// strongest signals.
const WF_SCHEMES = {
  lightning: [[0, 5, 8, 22], [.22, 14, 30, 82], [.42, 26, 88, 170], [.6, 40, 175, 205],
              [.76, 150, 232, 170], [.9, 255, 226, 122], [1, 255, 255, 255]],
  classic:   [[0, 3, 6, 24], [.25, 6, 14, 48], [.45, 14, 32, 92], [.62, 60, 80, 110],
              [.75, 220, 195, 40], [.9, 255, 236, 90], [1, 255, 252, 215]],
  tubeglow:  [[0, 9, 7, 4], [.25, 26, 18, 7], [.42, 62, 42, 12], [.58, 132, 90, 22],
              [.72, 206, 148, 46], [.86, 250, 204, 104], [1, 255, 242, 204]],
  ember:     [[0, 8, 4, 3], [.25, 26, 9, 4], [.42, 62, 18, 5], [.58, 128, 40, 6],
              [.72, 205, 82, 14], [.86, 255, 150, 48], [1, 255, 226, 170]],
  surf:      [[0, 2, 14, 26], [.25, 4, 30, 52], [.42, 6, 70, 100], [.56, 0, 130, 170],
              [.7, 30, 190, 210], [.84, 120, 235, 230], [.95, 230, 255, 250], [1, 255, 255, 255]],
};
let wfScheme = "lightning";
try { wfScheme = localStorage.getItem("procyon.wfScheme") || wfScheme; } catch { /* default */ }
if (!WF_SCHEMES[wfScheme]) wfScheme = "lightning";

function makeLut(stops) {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    let k = 0;
    while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
    const [a, b] = [stops[k], stops[k + 1]];
    const t = (v - a[0]) / (b[0] - a[0]);
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * t;
  }
  return lut;
}
let LUT = makeLut(WF_SCHEMES[wfScheme]);
const wfBg = () => `rgb(${LUT[0]}, ${LUT[1]}, ${LUT[2]})`;   // the scheme's noise-floor colour

function setWfScheme(name) {
  if (!WF_SCHEMES[name]) return;
  wfScheme = name;
  LUT = makeLut(WF_SCHEMES[name]);
  try { localStorage.setItem("procyon.wfScheme", name); } catch { /* not persisted */ }
  document.documentElement.dataset.wf = name;
  // Start the picture afresh, so old and new colours don't mix
  wctx.fillStyle = wfBg();
  wctx.fillRect(0, 0, wf.width, wf.height);
}
document.documentElement.dataset.wf = wfScheme;

// Colour palettes (Settings → Themes; per device): <html data-palette>
// switches the CSS variables in style.css. "night" is the standard look.
const PALETTES = ["night", "classic", "radio", "campfire", "beach", "sunset"];
let palette = "night";
try { palette = localStorage.getItem("procyon.palette") || palette; } catch { /* default */ }
if (!PALETTES.includes(palette)) palette = "night";
function setPalette(name) {
  if (!PALETTES.includes(name)) return;
  palette = name;
  try { localStorage.setItem("procyon.palette", name); } catch { /* not persisted */ }
  if (name === "night") delete document.documentElement.dataset.palette;
  else document.documentElement.dataset.palette = name;
}
setPalette(palette);
// Background textures, per device too: on unless switched off here.
function setTextures(on) {
  try { localStorage.setItem("procyon.textures", on ? "on" : "off"); } catch { /* not persisted */ }
  if (on) delete document.documentElement.dataset.plain;
  else document.documentElement.dataset.plain = "";
}
try { setTextures(localStorage.getItem("procyon.textures") !== "off"); } catch { setTextures(true); }

function sizeWaterfall() {
  const r = wf.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (wf.width === w && wf.height === h) return;
  const old = document.createElement("canvas");
  old.width = wf.width; old.height = wf.height;
  if (wf.width) old.getContext("2d").drawImage(wf, 0, 0);
  wf.width = w; wf.height = h;
  wctx.fillStyle = wfBg();
  wctx.fillRect(0, 0, w, h);
  if (old.width) wctx.drawImage(old, 0, 0, old.width, old.height, 0, 0, w, old.height * (w / old.width));
  renderScale();
  placeMarkers();
}

function wfRow(bins) {
  if (wf.width < 1 || wf.height < 2) return;   // not laid out yet (hidden tab)
  if (!rowImg || rowImg.width !== bins.length) {
    rowCanvas.width = bins.length; rowCanvas.height = 1;
    rowImg = rowCtx.createImageData(bins.length, 1);
  }
  for (let i = 0; i < bins.length; i++) {
    const v = bins[i] * 3;
    rowImg.data[i * 4] = LUT[v]; rowImg.data[i * 4 + 1] = LUT[v + 1];
    rowImg.data[i * 4 + 2] = LUT[v + 2]; rowImg.data[i * 4 + 3] = 255;
  }
  rowCtx.putImageData(rowImg, 0, 0);
  const dpr = window.devicePixelRatio || 1;
  const rh = Math.max(1, Math.round(dpr));
  const { width: W, height: H } = wf;
  wctx.drawImage(wf, 0, 0, W, H - rh, 0, rh, W, H - rh);
  wctx.imageSmoothingEnabled = true;
  wctx.drawImage(rowCanvas, wfLo() / BIN_HZ, 0, (wfHi() - wfLo()) / BIN_HZ, 1, 0, 0, W, rh);

  // Mark each cycle with a faint line where it starts. Its timestamp goes ABOVE
  // the line, inside the cycle it names - drawn ~2 s in, once there's room.
  const p = Math.floor(bandNow() / period());
  if (p !== lastPeriodMark) {
    if (lastPeriodMark !== -1) {
      wctx.fillStyle = "#ffffff30";
      wctx.fillRect(0, 0, W, Math.max(1, dpr | 0));
      rowsSinceMark = 0;
    }
    lastPeriodMark = p;
  } else if (rowsSinceMark >= 0 && ++rowsSinceMark === LABEL_ROWS) {
    const tx = S && S.tx && !S.tx.tune ? "  TX" : "";
    // Bold and bright with a dark outline, so it stays readable over strong signals.
    const text = utcStr(new Date(p * period() * 1000)) + tx, x = 6 * dpr, y = LABEL_ROWS * rh - 4 * dpr;
    wctx.font = `700 ${11 * dpr}px ui-monospace, monospace`;
    wctx.lineJoin = "round";
    wctx.lineWidth = 3 * dpr;
    wctx.strokeStyle = "#050812e0";
    wctx.strokeText(text, x, y);
    wctx.fillStyle = tx ? "#ffa3ae" : "#f2f5fb";
    wctx.fillText(text, x, y);
    rowsSinceMark = -1;
  }
}

function wfLabels(items) {
  // Paint call signs onto the waterfall next to their traces; they scroll with it.
  // On a busy band they'd cover each other, so they go in lanes, tier by tier:
  // CQs on top, stations calling you below them, everything else at the
  // bottom. A tier uses as many lanes as its labels need, up to what fits in
  // one cycle (7 in FT8, 3 in FT4); after that the least crowded lane is used.
  // Calls that only came through hashed ("<...>") get no label.
  const dpr = window.devicePixelRatio || 1;
  const rowsPerSec = 8, rh = Math.max(1, Math.round(dpr));
  const laneH = 16 * dpr, gap = 4 * dpr;
  const maxLanes = Math.max(1, Math.floor((period() * rowsPerSec * rh - 8 * dpr) / laneH));
  wctx.font = `600 ${10.5 * dpr}px ui-monospace, monospace`;
  const tier = (d) => d.p.kind === "cq" ? 0 : d.to_me && !d.uncertain ? 1 : 2;
  const placed = [];   // {d, label, l, r, w, lane}
  let next = 0;        // first lane free for the next tier
  for (let t = 0; t < 3; t++) {
    const first = Math.min(next, maxLanes - 1);
    for (const d of items.filter((x) => tier(x) === t).sort((a, b) => a.freq - b.freq)) {
      const label = d.p.from || d.msg.split(" ")[0];
      if (label === "<...>") continue;   // a call we couldn't resolve: says nothing, takes room
      const x = xFrac(d.freq + T().width / 2) * wf.width;
      const w = wctx.measureText(label).width + 8 * dpr;
      const l = x - w / 2, r = x + w / 2;
      const hits = (lane) => placed.filter((p) => p.lane === lane && l < p.r + gap && r > p.l - gap).length;
      let lane = first;
      while (lane < maxLanes - 1 && hits(lane)) lane++;
      if (hits(lane)) {   // all full: the tier's least crowded lane
        for (let k = first; k < maxLanes; k++) if (hits(k) < hits(lane)) lane = k;
      }
      placed.push({ d, label, l, r, w, lane });
      next = Math.max(next, lane + 1);
    }
  }
  // Centre the lanes on the middle of the signals, which scrolled down since they were sent.
  const mid = (T().decode_at - T().tx_start - T().signal / 2 + 0.4) * rowsPerSec * rh;
  const top = Math.max(10 * dpr, mid - next * laneH / 2);
  for (const { d, label, l, w, lane } of placed) {
    const y = top + lane * laneH + laneH / 2;
    const me = tier(d) === 1;
    wctx.fillStyle = me ? "#ffb547e6" : d.p.kind === "cq" ? "#0b2a1ad9" : "#060a16c8";
    roundRect(l, y - 8 * dpr, w, 15 * dpr, 4 * dpr);
    wctx.fillStyle = me ? "#1a1200" : d.p.kind === "cq" ? "#5fe08f" : "#e7ecf6";
    wctx.fillText(label, l + 4 * dpr, y + 3.5 * dpr);
  }
}

function roundRect(x, y, w, h, r) {
  wctx.beginPath();
  wctx.moveTo(x + r, y); wctx.arcTo(x + w, y, x + w, y + h, r); wctx.arcTo(x + w, y + h, x, y + h, r);
  wctx.arcTo(x, y + h, x, y, r); wctx.arcTo(x, y, x + w, y, r); wctx.fill();
}

function renderScale() {
  const el = $("#wfScale");
  el.innerHTML = "";
  for (let f = Math.ceil((wfLo() + 1) / 250) * 250; f < wfHi(); f += 250) {
    const s = document.createElement("span");
    s.style.left = `${xFrac(f) * 100}%`;
    s.textContent = f % 500 === 0 ? f : "";
    el.appendChild(s);
  }
}

function placeMarkers() {
  if (!S) return;
  const pct = (hz) => `${xFrac(hz) * 100}%`;
  const width = `${T().width / (wfHi() - wfLo()) * 100}%`;
  for (const [id, hz] of [["#rxMarker", S.settings.rx_freq], ["#txMarker", S.settings.tx_freq]]) {
    $(id).style.left = pct(hz);
    $(id).style.width = width;
  }
}

const wrap = $("#wfWrap");
const hzAt = (ev) => {
  const r = wrap.getBoundingClientRect();
  return Math.round(wfLo() + (ev.clientX - r.left) / r.width * (wfHi() - wfLo()));
};
wrap.addEventListener("mousemove", (ev) => {
  const hl = $("#hoverLine");
  hl.style.display = "block";
  hl.style.left = `${ev.clientX - wrap.getBoundingClientRect().left}px`;
  $("#hoverFreq").textContent = `${hzAt(ev)} Hz`;
});
wrap.addEventListener("mouseleave", () => { $("#hoverLine").style.display = "none"; });
// Touchscreens have no Shift key: press and hold to set the transmit frequency.
let holdTimer = null, held = false;
wrap.addEventListener("pointerdown", (ev) => {
  if (ev.pointerType === "mouse") return;
  held = false;
  const hz = Math.round(hzAt(ev) / 5) * 5;
  holdTimer = setTimeout(() => {
    held = true;
    send({ type: "freq", which: "tx", hz });
    toast(`Transmit frequency set to ${hz} Hz`, "good");
    if (navigator.vibrate) navigator.vibrate(30);
  }, 550);
});
for (const t of ["pointerup", "pointercancel", "pointerleave"]) wrap.addEventListener(t, () => clearTimeout(holdTimer));
wrap.addEventListener("contextmenu", (ev) => ev.preventDefault());

wrap.addEventListener("click", (ev) => {
  if (held) { held = false; return; }   // that was a press-and-hold, already handled
  const hz = Math.round(hzAt(ev) / 5) * 5;
  send({ type: "freq", which: ev.shiftKey || ev.ctrlKey ? "tx" : "rx", hz });
});

// ------------------------------------------------------------------- clock

const pad = (n) => String(n).padStart(2, "0");
const utcStr = (d) => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;

function tick() {
  const now = bandNow();   // the computer's clock, or the band's with band sync on
  const P = period(), p = Math.floor(now / P), pos = now - p * P;
  $("#utc").textContent = utcStr(new Date(now * 1000));
  const isTx = S && S.qso.tx_enabled && p % 2 === S.qso.parity;
  const fill = $("#cycleFill");
  fill.style.width = `${pos / P * 100}%`;
  fill.classList.toggle("tx", !!(S && S.tx) || isTx);
  $("#cycleLabel").textContent = `RX ${slotName(p % 2)} ${Math.floor(P - pos)}s`;
  $("#onAirSecs").textContent = `${Math.floor(P - pos)}s`;
  if (S && S.tx) {
    const t = S.tx;
    const frac = Math.min(1, Math.max(0, (Date.now() / 1000 - t.start) / (t.end - t.start)));   // t: computer's clock
    $("#txProgress").style.width = `${frac * 100}%`;
  }
  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------- state

let shownRange = "", shownMode = "";
let modePending = "", modePendingTimer = null;   // clicked, waiting for the engine to switch

let shownBadges = null, shownMiles = null;

function onState(m) {
  const first = !S;
  S = m;
  if (!first && dlg.open) showCty();   // country data line follows an update
  // A new waterfall range: start a fresh picture with the new scale.
  const range = `${wfLo()}-${wfHi()}-${T().mode}`;
  if (range !== shownRange) {
    shownRange = range;
    if (!first) { wctx.fillStyle = wfBg(); wctx.fillRect(0, 0, wf.width, wf.height); }
    renderScale();
    $("#txFreq").min = Math.max(100, wfLo());
    $("#txFreq").max = Math.min(4000, wfHi()) - T().width;
  }
  if (modePending && T().mode === modePending) {
    modePending = "";
    clearTimeout(modePendingTimer);
    $("#modePicker").classList.remove("pending");
  }
  if (T().mode !== shownMode) {
    shownMode = T().mode;
    if (!modePending) showModeButtons(shownMode);
    [...$("#paritySeg").children].forEach((b) => { b.textContent = slotName(+b.dataset.p).replace("/", " / "); });
    document.documentElement.dataset.mode = shownMode;
  }
  const st = m.settings;

  $("#dialFreq").textContent = (m.dial / 1e6).toFixed(3);
  $("#bandName").textContent = m.band || "—";
  $("#bandName").hidden = !m.band;
  renderBandMenu();

  const audio = $("#chipAudio");
  audio.className = `chip ${m.audio.error ? "bad" : m.audio.running ? "ok" : ""}`;
  audio.title = m.audio.error ? `Audio error: ${m.audio.error}` : "Receive audio from your radio - set it with the radio's AF/volume knob, aiming for about -30 dB";
  const rig = $("#chipRig");
  rig.hidden = !m.rig.enabled;
  // Hamlib's rigctld can be running while the radio itself is off: "answering"
  // says whether the radio replies (older engines don't send it).
  const radioUp = m.rig.connected && m.rig.answering !== false;
  rig.className = `chip ${radioUp ? "ok" : "bad"}`;
  noteRig({ ...m.rig, connected: radioUp });
  $("#rigLabel").textContent = !m.rig.connected ? "Rig offline"
    : !radioUp ? "Radio off?"
    : Date.now() < rigShinyUntil ? SHINY : "Rig";
  rig.title = m.rig.error || "Connected to the radio";
  renderClock(m.clock);
  $("#chipDemo").hidden = !st.demo;
  $("#demoBanner").hidden = !st.demo;
  renderPota();
  requestAnimationFrame(() => fitHeader());   // after the other chips are updated too
  $("#chipRemote").hidden = !m.remote.enabled;
  $("#chipRemote").title = isLocal
    ? `Other devices can connect with the PIN:\n${m.remote.urls.join("\n")}`
    : "You're connected from another device";
  // While transmitting, the ON AIR badge takes the cycle timer's place.
  $("#onAir").hidden = !m.tx;
  $("#cycleLabel").hidden = !!m.tx;

  renderQso();
  renderLog();
  renderCallers();
  placeMarkers();

  if (document.activeElement !== $("#txFreq")) $("#txFreq").value = st.tx_freq;
  if (document.activeElement !== $("#txLevel")) $("#txLevel").value = st.tx_level;
  if (document.activeElement !== $("#txWatts")) $("#txWatts").value = st.tx_watts;
  if (document.activeElement !== $("#qsoNote")) $("#qsoNote").value = m.qso.note || "";
  $("#txLevelOut").textContent = `${st.tx_level}%`;
  $("#tuneBtn").classList.toggle("on", m.tuning);
  $("#tuneBtn").textContent = m.tuning ? "Stop tuning" : "Tune";

  // An update was installed while this copy was running: it keeps running the
  // old version until it's started again.
  $("#updateWarn").hidden = !m.update;
  if (m.update) {
    $("#updateTo").textContent = m.update;
    $("#updateHow").textContent = isLocal
      ? `You are still running ${m.about.version}. Quit Procyon and start it again to finish updating.`
      : `The station computer is still running ${m.about.version}. Quit Procyon there and start it again to finish updating.`;
    $("#updateQuit").hidden = !isLocal;
  }

  // A newer release is out (checked once a day). Hidden per version once dismissed,
  // and while the "restart to finish updating" bar is up.
  let skipped = "";
  try { skipped = localStorage.getItem("procyon.skipVersion") || ""; } catch { /* not stored */ }
  const nv = m.newer;
  $("#newVersion").hidden = !nv || !!m.update || nv.version === skipped;
  if (nv) {
    $("#newVersionNo").textContent = nv.version;
    $("#newVersionHow").textContent = `You have ${m.about.version}.`;
    $("#newVersionNotes").href = nv.url || "https://procyon.kr1ttr.radio";
    $("#newVersionNotes").hidden = !nv.url;
  }

  // Only matters when Settings asks for WSJT-X's decoder: Procyon decodes by itself.
  const missing = Object.entries(m.tools || {}).filter(([k, ok]) => !ok && k === "jt9" && st.decoder === "jt9").map(([k]) => k);
  $("#toolWarn").hidden = !missing.length;
  $("#toolInstall").hidden = !(missing.length && m.can_install_wsjtx);
  $("#toolHowto").hidden = !!m.can_install_wsjtx;
  $("#toolMissing").textContent = missing.length ? `(Missing: ${missing.join(", ")})` : "";

  if (first && !st.mycall) openSettings(true);
  if (!first) {
    if (js8Asked !== null && js8Asked === !!m.js8) js8Asked = null;   // the engine did what we asked
    if (js8Demo !== null && js8Demo !== !!st.demo && js8On) {
      // demo band switched on/off while on JS8: the chat screen starts over on
      // the pretend band, or asks the engine to listen to the radio
      const f = $("#js8Frame"); f.src = f.src;
      if (!st.demo) askJs8(true);
    } else if (!st.demo && js8Asked === null && !!m.js8 !== js8On) showJs8(!!m.js8, false);   // changed elsewhere
  }
  js8Demo = !!st.demo;
  if (first) {                   // JS8 screen open last time on this device?
    let on = false;
    try { on = localStorage.getItem("procyon.js8View") === "1"; } catch { /* not stored */ }
    if (on) showJs8(true);
  }
  applySpelling(m.home.us_spelling);
  if (first || st.team_badges !== shownBadges || useMiles() !== shownMiles) renderDecodes();
  shownBadges = st.team_badges;
  shownMiles = useMiles();
}

function renderBandMenu() {
  const menu = $("#bandMenu");
  menu.innerHTML = S.bands.map((b) =>
    `<button data-hz="${b.hz}" class="${b.name === S.band ? "on" : ""}">${b.name}<small>${(b.hz / 1e6).toFixed(3)}</small></button>`).join("");
}
// JS8 (experimental, listen only): picking it shows the chat screen in place of
// the FT8/FT4 screen and asks the engine to listen for JS8 (it pauses FT8/FT4,
// blocks transmitting and moves the radio to the JS8 frequency). On the demo
// band the chat screen keeps its own pretend band. Remembered per device.
let js8On = false, js8Asked = null, js8Demo = null;
function toJs8Frame(m) {        // the engine's JS8 news -> the chat screen in the frame
  const w = $("#js8Frame").contentWindow;
  if (w && js8On) w.postMessage({ procyonJs8: m.m, history: m.history }, location.origin);
}
window.addEventListener("message", (e) => {    // the chat screen -> the engine
  if (e.origin !== location.origin || e.source !== $("#js8Frame").contentWindow) return;
  const c = (e.data || {}).procyonJs8Cmd;
  if (!c || !S) return;
  if (c.type === "ready" && js8On && !S.settings.demo) send({ type: "js8", on: true });   // catch it up
  else if (c.type === "speed") send({ type: "js8_speed", speed: c.speed });
  // nothing else: JS8 doesn't transmit yet
});
function askJs8(on) {
  if (!S || js8Asked === on) return;
  js8Asked = on;
  send({ type: "js8", on });
}
function showJs8(on, tell = true) {
  js8On = on;
  if (tell && S && !S.settings.demo) askJs8(on);
  document.body.classList.toggle("js8", on);
  $("#js8View").hidden = !on;
  const f = $("#js8Frame");
  if (on && !f.src) {
    const st = (S && S.settings) || {};
    const q = new URLSearchParams({ embed: 1, call: st.mycall || "", grid: st.mygrid || "",
      palette: document.documentElement.dataset.palette || "" });
    f.src = `/static/js8/index.html?${q}`;
  }
  try { localStorage.setItem("procyon.js8View", on ? "1" : ""); } catch { /* not stored */ }
  showModeButtons(on ? "JS8" : T().mode);
}
function showModeButtons(mode) {
  if (js8On && !modePending) mode = "JS8";
  $("#modeName").textContent = mode;
  [...$("#modeMenu").children].forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
}
// The mode drop-down: a list rather than a switch, so more modes fit later.
$("#modeBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#bandMenu").hidden = true;
  $("#modeMenu").hidden = !$("#modeMenu").hidden;
});
$("#modeMenu").addEventListener("click", (e) => {
  e.stopPropagation();
  const b = e.target.closest("button");
  $("#modeMenu").hidden = true;
  if (!b || !S || modePending) return;   // one switch at a time
  const want = b.dataset.mode;
  if (want === "JS8" ? js8On : (!js8On && want === T().mode)) return;
  const q = S.qso, busy = q.tx_enabled || S.tx || (q.dxcall && !q.logged);
  if (want !== "JS8" && js8On && want === T().mode) return showJs8(false);   // back to the FT8/FT4 screen
  if (!busy) return pickMode(want);
  // Our own dialog, not window.confirm: the browser's pop-up can open behind a
  // full-screen page, which then looks frozen.
  $("#modeTitle").textContent = `Switch to ${want}?`;
  $("#modeText").textContent = want === "JS8" ? "FT8/FT4 transmitting stops."
    : "Transmitting stops" + (q.dxcall && !q.logged ? ` and the QSO with ${q.dxcall} ends (not logged).` : ".");
  $("#modeConfirm").textContent = `Switch to ${want}`;
  $("#modeDlg").returnValue = "";   // Esc must never count as "Switch"
  $("#modeDlg").dataset.mode = want;
  $("#modeDlg").showModal();
});
$("#modeDlg").addEventListener("close", () => {
  if ($("#modeDlg").returnValue === "switch" && !modePending) pickMode($("#modeDlg").dataset.mode);
});
function pickMode(mode) {
  if (mode === "JS8") {           // stop FT8/FT4 transmitting, then show the chat screen
    if (S.qso.tx_enabled || S.tx) send({ type: "halt" });
    return showJs8(true);
  }
  if (js8On) showJs8(false);
  if (mode !== T().mode) switchMode(mode);
}
function switchMode(mode) {
  // Light the new mode straight away; the engine's answer confirms it.
  modePending = mode;
  showModeButtons(modePending);
  $("#modePicker").classList.add("pending");
  modePendingTimer = setTimeout(() => {   // no answer: show what's really in use
    modePending = "";
    $("#modePicker").classList.remove("pending");
    showModeButtons(T().mode);
  }, 5000);
  send({ type: "mode", mode });
}
$("#bandBtn").addEventListener("click", (e) => { e.stopPropagation(); $("#modeMenu").hidden = true; $("#bandMenu").hidden = !$("#bandMenu").hidden; });
$("#bandMenu").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) send({ type: "band", hz: +b.dataset.hz });
  $("#bandMenu").hidden = true;
});
document.addEventListener("click", () => { $("#bandMenu").hidden = true; $("#modeMenu").hidden = true; });

function onLevel(db) {
  // -60 dBFS .. 0 dBFS; ideal FT8 input sits around -30 dBFS.
  const pct = Math.max(0, Math.min(100, (db + 60) / 60 * 100));
  $("#meterFill").style.width = `${pct}%`;
  $("#chipAudio").title = `Receive audio ${db.toFixed(0)} dB - set it with your radio's AF/volume knob, aiming for about -30`;
}

// --------------------------------------------------------------- decodes

// A cycle's decodes arrive a few at a time while the decoder works (more:
// true), then a closing message (more: false). They're gathered into one
// batch per cycle; only the newly arrived rows fade in.
function onDecodes(b, live) {
  const last = batches[batches.length - 1];
  let batch = b;
  if (last && last.period === b.period && last.time === b.time && last.open) {
    batch = last;
    batch.items.push(...b.items);
  } else {
    batch = { ...b, items: [...b.items] };
    batches.push(batch);
    if (batches.length > MAX_PERIODS) batches.shift();
  }
  batch.open = !!b.more;
  if (!live) return;
  if (b.items.length) {
    noteCallers({ ...b, items: b.items });
    renderDecodes(new Set(b.items));
  } else renderDecodes();
  // Call signs go onto the waterfall once the cycle is complete, so the lanes
  // are worked out for all of them together.
  if (!b.more) wfLabels(batch.items);
}

// ------------------------------------------------- stations calling you
// Anyone calling you (other than the station you're already working) gets a
// prominent alert with a Reply button, a chime and a tab-title change.

const CALLER_TIMEOUT_SEC = 120;  // 2 minutes without hearing them again
const callerTimeout = () => Math.round(CALLER_TIMEOUT_SEC / period());   // in cycles
const callers = new Map();       // call -> {d, first, last, count}
const dismissed = new Map();     // call -> period dismissed
let chimeOn = true;
try { chimeOn = localStorage.getItem("procyon.chime") !== "off"; } catch { /* default on */ }
let audioCtx = null;

function noteCallers(b) {
  const dx = S ? S.qso.dxcall : "";
  let fresh = false;
  for (const d of b.items) {
    const call = d.p.from;
    if (!d.to_me || !call || sameStation(call, dx)) continue;
    // A guessed decode (see "uncertain" in the engine) never raises a new alert:
    // it's often noise. A real caller's next message usually decodes cleanly.
    if (d.uncertain && !callers.has(call)) continue;
    if (dismissed.has(call) && b.period - dismissed.get(call) < callerTimeout()) continue;
    const c = callers.get(call);
    if (c) { c.d = d; c.last = b.period; c.count++; }
    else { callers.set(call, { d, first: b.period, last: b.period, count: 1 }); fresh = true; }
  }
  for (const [call, c] of callers) {
    if (call === dx || b.period - c.last >= callerTimeout()) callers.delete(call);
  }
  if (fresh) chime();
  renderCallers();
}

function renderCallers() {
  const dx = S ? S.qso.dxcall : "";
  for (const call of [...callers.keys()]) if (call === dx) callers.delete(call);
  const box = $("#callers");
  box.hidden = !callers.size;
  const html = [...callers.values()].sort((a, b) => b.last - a.last).map((c) => {
    const d = c.d;
    return `<div class="caller" data-call="${esc(d.p.from)}">
      <span class="caller-dot"></span>
      <span class="caller-text"><b>${esc(d.p.from)}</b>${badge(d.p.from)}${d.country ? ` <span class="muted">(${esc(d.country)})</span>` : ""} is calling you
        <span class="caller-meta">${d.snr > 0 ? "+" : ""}${d.snr} dB${c.count > 1 ? ` · ${c.count}×` : ""} · <span class="mono">${esc(d.msg)}</span></span></span>
      <button class="btn primary small caller-reply" type="button">Reply</button>
      <button class="icon-btn caller-x" type="button" title="Dismiss">✕</button>
    </div>`;
  }).join("");
  // Only redraw when something changed, so a click never lands on a replaced button.
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  document.title = callers.size
    ? `${[...callers.keys()][0]} calling${callers.size > 1 ? ` (+${callers.size - 1})` : ""} – Procyon` : "Procyon";
}

$("#callers").addEventListener("click", (e) => {
  const row = e.target.closest(".caller");
  if (!row) return;
  const call = row.dataset.call, c = callers.get(call);
  callers.delete(call);
  if (e.target.closest(".caller-reply") && c) {
    if (!S.settings.mycall) return openSettings(true);
    const d = c.d;
    send({ type: "reply", decode: { msg: d.msg, snr: d.snr, freq: d.freq, parity: d.parity, period: d.period } });
  } else {
    dismissed.set(call, c ? c.last : 0);
  }
  renderCallers();
});

$("#newVersionX").addEventListener("click", () => {
  try { localStorage.setItem("procyon.skipVersion", S.newer ? S.newer.version : ""); } catch { /* not stored */ }
  $("#newVersion").hidden = true;
});

function chime() {
  if (!chimeOn) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const t = audioCtx.currentTime;
    [[880, 0], [1320, 0.16]].forEach(([f, dt]) => {   // two soft rising notes
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0, t + dt);
      g.gain.linearRampToValueAtTime(0.18, t + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.001, t + dt + 0.35);
      o.connect(g).connect(audioCtx.destination);
      o.start(t + dt); o.stop(t + dt + 0.4);
    });
  } catch { /* no audio available */ }
}
// Browsers only allow sound after the page has been clicked once.
document.addEventListener("pointerdown", () => {
  if (!audioCtx && chimeOn) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch { /* none */ }
  }
}, { once: true });

const snrColor = (s) => `var(--snr${s >= 0 ? 1 : s >= -10 ? 2 : s >= -16 ? 3 : 4})`;   // palette colours

// Band clock check: the median DT of the stations heard lately says how far
// this computer's clock is from the band's (c.band). With band sync
// (Settings → Advanced) Procyon runs its own timing that much earlier or later
// (c.offset); the chip then shows what's left over.
const sec = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(1)} s`;
const clockOffset = () => (S && S.clock ? S.clock.offset || 0 : 0);
const bandNow = () => Date.now() / 1000 - clockOffset();   // seconds, by the band's time
function renderClock(c) {
  const chip = $("#chipClock");
  if (!c) return;
  const manual = c.sync === "manual", auto = c.sync === "auto";
  chip.classList.toggle("clickable", manual);
  const fixed = c.offset ? `Procyon's timing is corrected by ${sec(c.offset)} (your computer's clock itself isn't changed).\n` : "";
  if (c.band == null) {
    chip.className = `chip${manual ? " clickable" : ""}`;
    $("#clockLabel").textContent = auto && c.offset ? `Synced ${sec(c.offset)}` : auto ? "Band sync" : "Clock";
    chip.title = `Band clock check: needs at least 8 stations decoded in the last 2 minutes (${c.n} so far).\n` + fixed;
    return;
  }
  const left = c.band - c.offset, a = Math.abs(left);
  chip.className = `chip ${a < 0.5 ? "ok" : a < 1 ? "warn" : "bad"}${manual ? " clickable" : ""}`;
  $("#clockLabel").textContent = auto ? `Synced ${sec(c.offset)}` : `Clock ${sec(left)}`;
  const how = a < 0.3 ? `on time, ${sec(left)}`
    : left > 0 ? `${a.toFixed(1)} s late - ${c.offset ? "Procyon's timing" : "your clock"} is probably about that much fast`
    : `${a.toFixed(1)} s early - ${c.offset ? "Procyon's timing" : "your clock"} is probably about that much slow`;
  chip.title = `Band clock check: stations here arrive ${how} (middle value of ${c.n} stations over the last 2 minutes).\n`
    + fixed
    + (auto ? "Band sync is on: Procyon keeps itself in time with the stations it hears.\n"
      : a < 0.5 ? "That's fine for FT8 and FT4.\n"
      : a < 1 ? "Still decodes, but weak stations may be missed.\n"
      : "Far enough off to lose decodes.\n")
    + (manual ? "Click to sync Procyon to the band now." : !auto && a >= 0.5
      ? "Set your computer's clock, or turn on band sync (Settings → Advanced → Clock)." : "")
    + "\nA small + (up to 0.2-0.3 s) is normal: sound cards add a little delay.";
}
$("#chipClock").addEventListener("click", () => {
  if (S && S.clock && S.clock.sync === "manual") send({ type: "clock_sync" });
});

// Procyon's team. Every copy knows these calls and puts a small raccoon (the
// developer) or star (testers - added only with their consent) next to them,
// and a coloured pill: green for the developer, purple for testers. Shown only on this screen, nothing
// extra is transmitted.
const TEAM = {
  KR1TTR: { role: "Procyon developer", icon: "icon.svg", pill: "t-dev" },
  KE4BFI: { role: "Procyon tester", icon: "star.svg", pill: "t-tester" },   // Andy Lamb
  KE8EON: { role: "Procyon tester", icon: "star.svg", pill: "t-tester" },   // Jim Curley
  KE8SAU: { role: "Procyon tester", icon: "star.svg", pill: "t-tester" },   // Scott McCrory
};
const teamMember = (call) => (S && S.settings.team_badges === false ? null
  : TEAM[String(call || "").replace(/[<>]/g, "").split("/")[0].toUpperCase()] || null);
const teamOf = (call) => (teamMember(call) || {}).role || "";
const badge = (call) => {
  const t = teamMember(call);
  return t ? `<img class="team-badge${t.icon === "star.svg" ? " star" : ""}" src="/static/${t.icon}" alt="" title="${esc(t.role)}">` : "";
};

function fmtMsg(d, terms = []) {
  const p = d.p, my = S ? S.settings.mycall : "";
  if (p.kind === "free") return `<span class="t-free">${esc(d.msg)}</span>`;
  return d.msg.split(" ").map((t) => {
    const bare = t.replace(/[<>;]/g, "");   // ";" joins the two halves of a DXpedition message
    let c = "";
    if (t === "CQ") c = "t-cq";
    else if (my && bare === my) c = "t-me";
    else if (teamMember(bare)?.pill) c = teamMember(bare).pill;
    else if (bare === p.from) c = "t-from";
    else if (bare === p.to || bare === p.other) c = "t-to";
    else if (/^[A-R]{2}\d{2}$/.test(t) && t !== "RR73") c = "t-grid";
    else if (/^R?[+-]\d\d$/.test(t)) c = "t-rpt";
    else if (["RR73", "RRR", "73"].includes(bare)) c = "t-end";
    if (terms.length && tokenHits(t, terms)) c += " t-hit";
    const call = /\bt-(me|tester|dev|from|to)\b/.test(c) && lookupable(bare) ? ` data-call="${esc(bare)}"` : "";
    return `<span class="${c}"${call}>${esc(t)}</span>${badge(bare)}`;
  }).join(" ");
}

// Custom text filter. Remembered per device; works together with the buttons.
let custom = { text: "", on: false };
try {
  const saved = localStorage.getItem("procyon.filter") ?? localStorage.getItem("skywave.filter");   // earlier name
  custom = { ...custom, ...JSON.parse(saved || "{}") };
} catch { /* default */ }

const customTerms = () => (custom.on ? custom.text.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean) : []);
const tokenHits = (tok, terms) => terms.some((t) => tok.replace(/[<>]/g, "").toUpperCase().startsWith(t));

// A term matches a word in the message that starts with it (calls, grids, POTA,
// contest exchanges), part of the country name, or the continent code.
function matchesCustom(d, terms) {
  if (!terms.length) return true;
  const country = (d.country || "").toUpperCase();
  return terms.some((t) => d.msg.split(" ").some((w) => tokenHits(w, [t]))
    || (country && country.includes(t)) || (d.cont || "").toUpperCase() === t);
}

function basePasses(d) {
  if (filter === "cq") return d.p.kind === "cq";
  if (filter === "new") return d.new;
  if (filter === "mine") {
    const dx = S && S.qso.dxcall;
    return d.to_me || d.from_me || (dx && (sameStation(d.p.from, dx) || sameStation(d.p.to, dx)));
  }
  return true;
}

// Messages to you always show, whatever the filters - never miss a caller.
const passes = (d, terms) => d.to_me || (basePasses(d) && matchesCustom(d, terms));

function renderCustomFilter() {
  const has = !!custom.text.trim();
  $("#cfToggle").classList.toggle("on", custom.on && has);
  $("#cfToggle").setAttribute("aria-pressed", String(custom.on && has));
  $("#cfRow").classList.toggle("active", custom.on && has);
  $("#cfClear").hidden = !has;
  if (document.activeElement !== $("#cfText")) $("#cfText").value = custom.text;
}

function saveCustom() {
  try { localStorage.setItem("procyon.filter", JSON.stringify(custom)); } catch { /* not persisted */ }
  renderCustomFilter();
  renderDecodes();
}

$("#cfText").addEventListener("input", (e) => {
  custom.text = e.target.value;
  custom.on = !!custom.text.trim();   // typing switches the filter on
  saveCustom();
});
$("#cfText").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.stopPropagation(); e.target.blur(); }   // leave the box, don't halt TX
});
$("#cfToggle").addEventListener("click", () => {
  if (!custom.text.trim()) return $("#cfText").focus();
  custom.on = !custom.on;
  saveCustom();
});
$("#cfClear").addEventListener("click", () => {
  custom = { text: "", on: false };
  saveCustom();
  $("#cfText").focus();
});

function renderDecodes(fresh) {   // fresh: a Set of decodes that just arrived
  const list = $("#decodes");
  const dx = S ? S.qso.dxcall : "";
  const rx = S ? S.settings.rx_freq : 0;
  const terms = customTerms();
  let html = "";
  let total = 0, all = 0;
  const nowBand = S ? S.band : "";
  let newerBand = null;   // band of the cycle just above, to mark where it changed
  for (let i = batches.length - 1; i >= 0; i--) {
    const b = batches[i];
    all += b.items.length;
    if (b.band && newerBand && b.band !== newerBand) {
      html += `<div class="band-change">Changed to ${esc(newerBand)} · earlier activity below is from ${esc(b.band)}</div>`;
    }
    if (b.band) newerBand = b.band;
    const items = b.items.filter((d) => passes(d, terms));
    if (!items.length) continue;
    const otherBand = b.band && nowBand && b.band !== nowBand;
    // Messages to me first, then the rest by frequency.
    const meFirst = (d) => d.to_me && !d.uncertain;
    items.sort((a, c) => (meFirst(c) - meFirst(a)) || a.freq - c.freq);
    const t = b.time;
    html += `<div class="period-head${otherBand ? " other-band" : ""}"><span>${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4)}</span>
      <span class="slot">${slotName(b.period % 2)}</span>
      ${b.band ? `<span class="band-tag" title="${otherBand ? "Received on a different band than you're on now" : "Band"}">${esc(b.band)}</span>` : ""}
      <span>${items.length} decode${items.length > 1 ? "s" : ""}</span></div>`;
    for (const d of items) {
      total++;
      const cls = ["row", d.p.kind === "cq" && "cq", d.to_me && !d.uncertain && "to-me",
        d.uncertain && "uncertain", d.from_me && "from-me",
        dx && sameStation(d.p.from, dx) && !d.to_me && "is-dx", Math.abs(d.freq - rx) < 60 && "near-rx",
        fresh && fresh.has(d) && "fresh", otherBand && "other-band"].filter(Boolean).join(" ");
      const canAnswer = d.p.from && !d.from_me;
      const meta = [d.uncertain ? `<span class="badge unsure" title="Uncertain: the decoder filled in part of this message by guessing (tagged ${esc(d.uncertain)}). Now and then that turns noise into a call that was never sent. Wait for a clean decode before replying.">?</span>` : "",
        d.p.cq_tag === "POTA" ? `<span class="badge pota" title="Activating a park (Parks on the Air)">POTA</span>` : "",
        d.new ? `<span class="badge new">NEW</span>` : d.new_band ? `<span class="badge newband">NEW BAND</span>` : "",
        d.country ? `<span class="country" title="${esc(d.country)}">${esc(d.country)}</span>` : "",
        d.km != null ? `<span class="km">${dist(d.km, d.approx)}</span>` : ""].join("");
      html += `<div class="${cls}" data-i="${i}" data-msg="${esc(d.msg)}">
        <span class="snr" style="--c:${snrColor(d.snr)}">${d.snr > 0 ? "+" : ""}${d.snr}</span>
        <span class="dt">${d.dt.toFixed(1)}</span>
        <span class="hz">${d.freq}</span>
        <span class="msg">${fmtMsg(d, terms)}</span>
        <span class="meta">${meta}</span>
        ${canAnswer ? `<button class="answer">${d.p.kind === "cq" ? "Answer" : d.to_me ? "Reply" : "Call"} ${esc(d.p.from)}</button>` : ""}
      </div>`;
    }
  }
  $("#cfCount").textContent = terms.length ? `${total} of ${all}` : "";
  if (!total) {
    list.innerHTML = batches.length && (filter !== "all" || terms.length)
      ? `<div class="empty"><p>Nothing matches this filter yet.</p></div>`
      : `<div class="empty"><div class="pulse-ring"></div><p><b>Listening…</b><br>Decodes appear here near the end of each ${period()}-second cycle.</p></div>`;
    return;
  }
  list.innerHTML = html;
}

$("#decodes").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  const b = batches[+row.dataset.i];
  const d = b && b.items.find((x) => x.msg === row.dataset.msg);
  if (!d || !d.p.from || d.from_me) return;
  if (!S.settings.mycall) { openSettings(true); return; }
  send({ type: "reply", decode: { msg: d.msg, snr: d.snr, freq: d.freq, parity: d.parity, period: d.period } });
});

$("#clearActivity").addEventListener("click", () => send({ type: "clear_activity" }));

$("#filter").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  filter = b.dataset.f;
  [...$("#filter").children].forEach((x) => x.classList.toggle("on", x === b));
  renderDecodes();
});

// ------------------------------------------------------------------- QSO

const TX_NAMES = ["Tx1 · Grid", "Tx2 · Report", "Tx3 · Roger + report", "Tx4 · RR73", "Tx5 · 73", "Tx6 · CQ"];

// Distances arrive in km. Settings → Display: miles, km, or "auto" - which
// follows your callsign (miles in the US and UK).
const useMiles = () => !!S && (S.settings.units === "mi" || (S.settings.units === "auto" && S.home.miles));

// Spelling follows your callsign too: the page is written in American English,
// and the few words that differ carry their British form in data-uk.
let shownSpelling = null;
function applySpelling(us) {
  if (us === shownSpelling) return;
  shownSpelling = us;
  for (const el of document.querySelectorAll("[data-uk]")) {
    if (el.dataset.us === undefined) el.dataset.us = el.textContent;
    el.textContent = us ? el.dataset.us : el.dataset.uk;
  }
}

function dist(km, approx, gap = "") {
  const miles = useMiles();
  const n = Math.round(miles ? km / 1.609344 : km);
  return `${approx ? "≈" + gap : ""}${n.toLocaleString()} ${miles ? "mi" : "km"}`;
}

function renderQso() {
  const q = S.qso;
  if (document.activeElement !== $("#dxCall")) $("#dxCall").value = q.dxcall;
  if (document.activeElement !== $("#dxGrid")) $("#dxGrid").value = q.dxgrid;

  const info = q.info || {};
  const bits = [];
  if (teamOf(q.dxcall)) bits.push(`${badge(q.dxcall)} ${esc(teamOf(q.dxcall))}`);
  if (info.country) bits.push(`<b>${esc(info.country)}</b>`);
  if (info.km != null) bits.push(dist(info.km, info.approx, " "), `${info.az}°`);
  if (lookupable(q.dxcall)) bits.push(`<a class="qrz-link" href="${esc(qrzUrl(q.dxcall))}" target="_blank" rel="noopener" title="Open ${esc(baseCall(q.dxcall))} on QRZ.com in a new tab">QRZ ↗</a>`);
  const infoHtml = q.dxcall ? (bits.join(" · ") || "&nbsp;")
    : (q.next === 5 && q.tx_enabled ? "Calling CQ — the first station to answer will be worked automatically."
      : "Click a station in Band activity to answer it, or call CQ.");
  if ($("#dxInfo").dataset.html !== infoHtml) { $("#dxInfo").innerHTML = infoHtml; $("#dxInfo").dataset.html = infoHtml; }   // keep clicks landing

  const steps = [...$("#stepper").children];
  steps.forEach((li, i) => {
    li.classList.toggle("done", q.stage > i + 1 || (q.stage === 4));
    li.classList.toggle("current", q.stage === i + 1 && q.stage < 4);
  });
  $("#stepper").classList.toggle("complete", q.stage === 4);
  $("#hdrDx").textContent = q.dxcall || (q.next === 5 && q.tx_enabled ? "Calling CQ" : "No QSO");
  $("#hdrProgress").classList.toggle("idle", !q.dxcall);
  $("#rptSent").textContent = q.report_sent && q.stage >= 2 ? q.report_sent : "—";
  $("#rptRcvd").textContent = q.report_rcvd || "—";

  const tx = S.tx;
  const live = tx && !tx.tune;
  $("#nextMsg").textContent = live ? tx.msg : (q.msgs[q.next] || "—");
  $("#nextLabel").textContent = live ? `Transmitting now${tx.late ? ` · started ${tx.late} s late` : ""}` : q.tx_enabled ? `Next transmission · ${TX_NAMES[q.next]}` : `Ready · ${TX_NAMES[q.next]}`;
  $("#nextBox").classList.toggle("armed", q.tx_enabled && !live);
  $("#nextBox").classList.toggle("live", !!live);
  if (!tx) $("#txProgress").style.width = "0";
  $("#qsoCard").classList.toggle("transmitting", !!tx);

  renderExchange(q);

  const box = $("#msgs");
  if (!box.contains(document.activeElement)) {
    const html = q.msgs.map((m, i) => `<div class="mrow">
      <button data-n="${i}" class="${i === q.next ? "on" : ""}" title="Send this next">Tx${i + 1}</button>
      <input data-n="${i}" value="${esc(m)}" spellcheck="false" maxlength="37"></div>`).join("");
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }   // keep clicks landing
  }

  const txb = $("#txBtn");
  txb.classList.toggle("on", q.tx_enabled);
  $("#txBtnLabel").textContent = q.tx_enabled ? "TX enabled" : "Enable TX";
  [...$("#paritySeg").children].forEach((b) => b.classList.toggle("on", +b.dataset.p === q.parity));
}

// The exchange log: what we sent and what we heard from the DX station, cycle by cycle.
function renderExchange(q) {
  const t = q.thread || [];
  $("#exTitle").textContent = q.dxcall ? `Exchange with ${q.dxcall}` : "Exchange";
  $("#exClear").hidden = !t.length;
  const list = $("#exList");
  if (!t.length) {
    list.innerHTML = `<div class="ex-empty">Messages you send and receive during a QSO will appear here.</div>`;
    $("#exStatus").hidden = true;
    return;
  }
  const time = (p) => utcStr(new Date(p * period() * 1000));
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 20;
  list.innerHTML = t.map((e) => {
    if (e.silent) {
      return `<div class="xrow rx silent"><span class="xt">${time(e.period)}</span><span class="xd"></span>
        <span class="xm">No reply heard</span></div>`;
    }
    const rep = e.repeat > 1
      ? (e.dir === "rx"
        ? `<span class="xr warn" title="They sent the same message again - they probably didn't copy your last transmission">repeat ×${e.repeat}</span>`
        : `<span class="xr" title="You've sent this ${e.repeat} times in a row">×${e.repeat}</span>`)
      : "";
    const busy = e.busy ? `<span class="xr busy" title="They're working another station">busy</span>` : "";
    const snr = e.snr != null ? `<span class="xs">${e.snr > 0 ? "+" : ""}${e.snr}</span>` : "";
    return `<div class="xrow ${e.dir}${e.busy ? " is-busy" : ""}"><span class="xt">${time(e.period)}</span><span class="xd"></span>
      <span class="xm">${esc(e.msg)}${e.dir === "rx" ? badge(q.dxcall) : ""}</span>${snr}${rep}${busy}</div>`;
  }).join("");
  if (atBottom || list.dataset.dx !== q.dxcall) list.scrollTop = list.scrollHeight;
  list.dataset.dx = q.dxcall;

  // A plain-language hint about what the last cycle means.
  const last = t[t.length - 1];
  let hint = "";
  if (last.silent) hint = `Nothing heard from ${q.dxcall} last cycle. Procyon keeps repeating your message.`;
  else if (last.dir === "rx" && last.busy) hint = `${q.dxcall} is working another station right now.`;
  else if (last.dir === "rx" && last.repeat > 1) hint = `${q.dxcall} is repeating their last message, so they haven't copied your reply yet.`;
  const st = $("#exStatus");
  st.hidden = !hint || q.stage === 4;
  st.textContent = hint;
  maybeBetrayal(q, t, last);
}

$("#exClear").addEventListener("click", () => send({ type: "clear_exchange" }));

// --------------------------------------------------------- the quiet quotes
// A few lines that show up for a moment at rare times and then disappear.
// They exist only on this screen: never in a transmitted message, the
// exchange log or the logbook.
function flashEgg(el, html, ms) {
  el.innerHTML = html;
  el.hidden = false;
  clearTimeout(el.eggTimer);
  el.eggTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// The radio connection comes back after dropping out (Kaylee, Firefly).
const SHINY = "Everything's shiny, Cap'n. Not to fret.";
let rigSeenUp = false, rigDropped = false, rigShinyUntil = 0, rigShinyLast = 0;
function noteRig(rig) {
  if (!rig.enabled) { rigSeenUp = rigDropped = false; return; }
  if (!rig.connected) { if (rigSeenUp) rigDropped = true; return; }
  if (rigDropped && Date.now() - rigShinyLast > 30 * 60e3) {   // at most once in half an hour
    rigShinyLast = Date.now();
    rigShinyUntil = rigShinyLast + 6000;
    setTimeout(() => {
      if (S && S.rig.connected && S.rig.answering !== false) $("#rigLabel").textContent = "Rig";
      fitHeader(true);
    }, 6100);
    setTimeout(() => fitHeader(true), 0);
  }
  rigSeenUp = true;
  rigDropped = false;
}

// Every 1,000th QSO in the real logbook (Mr. Universe, Serenity).
let logCountSeen = null;
function noteLogCount() {
  if (S.settings.demo) { logCountSeen = null; return; }   // practice QSOs don't count
  const n = S.qso_count;
  if (logCountSeen !== null && Math.floor(n / 1000) > Math.floor(logCountSeen / 1000)) {
    flashEgg($("#logEgg"), `<b>You can't stop the signal!</b> ${(Math.floor(n / 1000) * 1000).toLocaleString()} QSOs.`, 10000);
  }
  logCountSeen = n;
}

// The station you were working answers someone else instead (Wash, Firefly) -
// only now and then, and at most once per QSO.
const betrayed = new Set();
function maybeBetrayal(q, t, last) {
  if (!last || last.dir !== "rx" || !last.busy || q.stage === 4 || !q.dxcall) return;
  const key = `${q.dxcall}:${last.period}`;
  if (betrayed.has(key) || [...betrayed].some((k) => k.startsWith(q.dxcall + ":"))) return;
  // They had already sent us a message of this QSO (to us = our call first) -
  // not just a CQ we answered while they picked someone else.
  const toUs = (e) => e.dir === "rx" && !e.silent && e.msg
    && sameStation(e.msg.split(" ")[0].replace(/[<>]/g, ""), S.settings.mycall);
  const theyWereWorkingUs = t.some((e) => e !== last && toUs(e));
  if (!theyWereWorkingUs) return;
  betrayed.add(key);
  if (Math.random() < 1 / 3) flashEgg($("#qsoEgg"), "Curse your sudden but inevitable betrayal!", 8000);
}

$("#msgs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) send({ type: "select_msg", n: +b.dataset.n });
});
$("#msgs").addEventListener("change", (e) => {
  if (e.target.matches("input")) send({ type: "edit_msg", n: +e.target.dataset.n, text: e.target.value });
});
$("#msgs").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

$("#cqBtn").addEventListener("click", () => {
  if (!S.settings.mycall) return openSettings(true);
  send({ type: "cq" });
});
$("#txBtn").addEventListener("click", () => {
  if (!S.settings.mycall) return openSettings(true);
  send({ type: "tx_enable", on: !S.qso.tx_enabled });
});
$("#haltBtn").addEventListener("click", () => send({ type: "halt" }));
$("#clearDx").addEventListener("click", () => send({ type: "clear_dx" }));
$("#tuneBtn").addEventListener("click", () => send({ type: "tune", on: !S.tuning }));
$("#paritySeg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) send({ type: "parity", p: +b.dataset.p });
});
$("#txFreq").addEventListener("change", (e) => send({ type: "freq", which: "tx", hz: +e.target.value }));
$("#txWatts").addEventListener("change", (e) => send({ type: "tx_watts", w: e.target.value }));
$("#qsoNote").addEventListener("change", (e) => send({ type: "qso_note", text: e.target.value }));
$("#qsoNote").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
// Audio drive applies live, even mid-transmission: send it while the slider moves.
let levelTimer = null;
$("#txLevel").addEventListener("input", (e) => {
  $("#txLevelOut").textContent = `${e.target.value}%`;
  if (!levelTimer) {
    levelTimer = setTimeout(() => { levelTimer = null; send({ type: "tx_level", v: +$("#txLevel").value }); }, 120);
  }
});
$("#txLevel").addEventListener("change", (e) => send({ type: "tx_level", v: +e.target.value }));
for (const id of ["#dxCall", "#dxGrid"]) {
  $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
  $(id).addEventListener("change", () => send({ type: "set_dx", call: $("#dxCall").value.trim(), grid: $("#dxGrid").value.trim() }));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !document.querySelector("dialog[open]")) send({ type: "halt" });
});

// --------------------------------------------------------------- logbook

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

let editingIdx = null;   // logbook entry being edited, if any

// One button per online logbook that's set up, next to Export ADIF.
function renderUploads() {
  const el = $("#uploadBtns");
  const html = Object.entries(S.uploads || {}).filter(([, u]) => u.ready).map(([svc, u]) => {
    if (u.busy) return `<button class="link-btn" type="button" disabled>Uploading to ${esc(u.name)}…</button>`;
    if (!u.pending) return `<button class="link-btn" type="button" disabled title="Every QSO is on ${esc(u.name)}">${esc(u.name)} ✓</button>`;
    return `<button class="link-btn" type="button" data-upload="${svc}"
      title="Upload ${u.pending} new QSO${u.pending === 1 ? "" : "s"} to ${esc(u.name)}">Upload to ${esc(u.name)}<span class="n">${u.pending}</span></button>`;
  }).join("");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }   // keep clicks landing
  // The demo log only ever leaves as plain text, so it can't be imported anywhere
  const exp = $("#exportLog"), demo = !!S.settings.demo;
  const ex = S.export || {}, newOnly = !demo && ex.new_only;
  exp.textContent = demo ? "Export practice log (.txt)"
    : !newOnly ? "Export ADIF"
    : ex.pending ? `Export ${ex.pending} new QSO${ex.pending === 1 ? "" : "s"}` : "No new QSOs to export";
  exp.classList.toggle("disabled", newOnly && !ex.pending);
  exp.title = demo ? "Demo band: simulated QSOs, saved as a plain text list - not a logbook file, so they can't be uploaded anywhere by mistake"
    : newOnly ? "Only the QSOs not exported before (Settings → Advanced → Logbook export)"
    : "Download your logbook as an ADIF file";
}
$("#uploadBtns").addEventListener("click", (e) => {
  const b = e.target.closest("[data-upload]");
  if (b) { b.disabled = true; send({ type: "upload", service: b.dataset.upload }); }
});

// Same operator? Portable QSOs send reports with base calls (P4/WE9V -> WE9V).
const STD_CALL = /^[A-Z0-9]{1,2}\d[A-Z]{1,3}$/;
function baseCall(c) {
  c = (c || "").replace(/[<>]/g, "").toUpperCase();
  if (!c.includes("/")) return c;
  const parts = c.split("/").filter(Boolean), std = parts.filter((x) => STD_CALL.test(x));
  return (std.length ? std : parts).reduce((a, b) => (b.length > a.length ? b : a), "");
}
function sameStation(a, b) {
  a = (a || "").replace(/[<>]/g, "").toUpperCase();
  b = (b || "").replace(/[<>]/g, "").toUpperCase();
  if (!a || !b || a.startsWith("...") || b.startsWith("...")) return false;
  return a === b || ((a.includes("/") || b.includes("/")) && baseCall(a) === baseCall(b));
}

// ------------------------------------------------------------ look up on QRZ
// Right-click a callsign (press and hold on a touch screen) in Band activity, a
// caller alert or the logbook for a small menu that opens its QRZ.com page in a
// new tab. Only a link: Procyon itself never contacts QRZ for this.
const qrzUrl = (call) => `https://www.qrz.com/db/${encodeURIComponent(baseCall(call))}`;
function lookupable(c) {
  c = (c || "").replace(/[<>]/g, "");
  return !c.startsWith("...") && /\d/.test(c) && /[A-Z]/.test(c);
}

// The calls under the pointer: the one clicked first, then the rest of that row.
function callsAt(target) {
  const calls = [];
  const add = (c) => {
    c = (c || "").replace(/[<>]/g, "").toUpperCase();
    if (lookupable(c) && !calls.some((x) => baseCall(x) === baseCall(c))) calls.push(c);
  };
  add(target.closest("[data-call]")?.dataset.call);
  const row = target.closest("#decodes .row, #callers .caller, #logList .lrow");
  if (!row) return calls;
  if (row.matches(".lrow")) add(row.querySelector(".l1 .c")?.textContent);
  else if (row.matches(".caller")) add(row.dataset.call);
  else for (const el of row.querySelectorAll(".msg [data-call]")) add(el.dataset.call);
  const my = S ? S.settings.mycall : "";
  return calls.length > 1 ? calls.filter((c) => !sameStation(c, my)) : calls;
}

const callMenu = document.createElement("div");
callMenu.className = "call-menu";
callMenu.hidden = true;
document.body.append(callMenu);

function openCallMenu(calls, x, y) {
  callMenu.innerHTML = calls.map((c) => `<a href="${esc(qrzUrl(c))}" target="_blank" rel="noopener">
    Look up <b>${esc(baseCall(c))}</b> on QRZ <span>↗</span></a>`).join("");
  callMenu.hidden = false;
  const w = callMenu.offsetWidth, h = callMenu.offsetHeight;
  callMenu.style.left = `${Math.max(8, Math.min(x, innerWidth - w - 8))}px`;
  callMenu.style.top = `${Math.max(8, y + h + 8 > innerHeight ? y - h : y)}px`;
}
const closeCallMenu = () => { callMenu.hidden = true; };
callMenu.addEventListener("click", closeCallMenu);   // the link opens its tab, then the menu goes

document.addEventListener("contextmenu", (e) => {
  if (e.target.closest("input, textarea, .call-menu")) return;   // keep the browser's own menu there
  const calls = callsAt(e.target);
  if (!calls.length) return;
  e.preventDefault();
  openCallMenu(calls, e.clientX, e.clientY);
});

// Press and hold for touch screens that don't send "contextmenu" (iPhone, iPad).
// The click that follows the hold must not answer the station.
let holdCalls = null, holdTimer2 = 0, holdDone = false, holdAt = null;
document.addEventListener("pointerdown", (e) => {
  if (e.pointerType !== "touch") return;
  holdDone = false;
  const calls = callsAt(e.target);
  if (!calls.length) return;
  holdCalls = calls; holdAt = [e.clientX, e.clientY];
  holdTimer2 = setTimeout(() => {
    holdDone = true;
    if (callMenu.hidden) openCallMenu(holdCalls, holdAt[0], holdAt[1]);
    if (navigator.vibrate) navigator.vibrate(30);
  }, 550);
});
document.addEventListener("pointermove", (e) => {
  if (holdAt && Math.hypot(e.clientX - holdAt[0], e.clientY - holdAt[1]) > 10) clearTimeout(holdTimer2);
});
for (const t of ["pointerup", "pointercancel"]) document.addEventListener(t, () => { clearTimeout(holdTimer2); holdAt = null; });
document.addEventListener("click", (e) => {
  if (holdDone && !e.target.closest(".call-menu")) { holdDone = false; e.stopPropagation(); e.preventDefault(); }
}, true);

document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".call-menu")) closeCallMenu(); }, true);
addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !callMenu.hidden) { e.stopPropagation(); closeCallMenu(); }   // close the menu, don't halt TX
}, true);
for (const t of ["scroll", "resize", "blur"]) addEventListener(t, closeCallMenu, true);

// ------------------------------------------------------------------ POTA

const potaForm = $("#potaForm");
let potaEndArmed = false;
function renderPota() {
  const p = S.pota;
  const chip = $("#chipPota");
  chip.hidden = !p.active;
  $("#potaChipCount").textContent = p.active ? `${p.today}/${p.needed}` : "";
  chip.classList.toggle("done", p.active && p.today >= p.needed);
  $("#potaBtn").textContent = p.active ? "POTA ●" : "POTA";
  if (!$("#potaDlg").open) return;
  $("#potaProgress").hidden = !p.active;
  $("#potaParksNow").textContent = p.parks.join(" · ");
  $("#potaToday").textContent = p.today >= p.needed
    ? `${p.today} QSOs today - valid activation ✓` : `${p.today} of ${p.needed} QSOs today`;
  $("#potaBar").style.width = `${Math.min(100, 100 * p.today / p.needed)}%`;
  $("#potaIntro").hidden = p.active;
  $("#potaStart").textContent = p.active ? "Update" : "Start activation";
  $("#potaEnd").hidden = !p.active;
  const files = p.files.map((f) => `<a href="/pota/${encodeURIComponent(f.park)}.adi" download="${esc(f.name)}">${esc(f.name)}<span>${f.count} QSO${f.count === 1 ? "" : "s"}</span></a>`).join("");
  $("#potaFiles").hidden = !p.files.some((f) => f.count);
  $("#potaFolder").textContent = p.folder || "";
  const list = $("#potaFileList");
  if (list.dataset.html !== files) { list.innerHTML = files; list.dataset.html = files; }   // keep clicks landing
}
function potaMsg(text, ok) {
  const el = $("#potaMsg");
  el.textContent = text;
  el.className = `hint pota-msg${text ? (ok ? " ok" : " bad") : ""}`;
}
function openPota() {
  if (!S.settings.mycall) return openSettings(true);
  const p = S.pota;
  potaForm.elements.parks.value = p.parks.join(", ");
  potaForm.elements.grid.value = p.grid;
  potaForm.elements.grid.placeholder = S.settings.mygrid ? `Home: ${S.settings.mygrid}` : "e.g. EN80ma";
  potaForm.elements.state.value = p.state;
  potaMsg(p.active ? "" : p.files.some((f) => f.count) ? "Your last activation's park logs are below." : "");
  disarmPotaEnd();
  $("#potaDlg").showModal();
  renderPota();
}
function disarmPotaEnd() { potaEndArmed = false; $("#potaEnd").textContent = "End activation"; }
$("#potaBtn").addEventListener("click", openPota);
$("#chipPota").addEventListener("click", openPota);
$("#potaStart").addEventListener("click", () => {
  potaMsg("…", true);
  send({ type: "pota_start", parks: potaForm.elements.parks.value,
    grid: potaForm.elements.grid.value.trim(), state: potaForm.elements.state.value.trim() });
});
potaForm.addEventListener("keydown", (e) => {   // Enter in a field starts/updates, never closes
  if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); $("#potaStart").click(); }
});
$("#potaEnd").addEventListener("click", () => {
  if (!potaEndArmed) {          // two clicks, so it can't end by accident
    potaEndArmed = true;
    $("#potaEnd").textContent = "Click again to end";
    setTimeout(disarmPotaEnd, 4000);
    return;
  }
  disarmPotaEnd();
  send({ type: "pota_end" });
  potaMsg(S.pota.total ? "Activation ended. Your park logs are below, ready for pota.app."
    : "Activation ended. No QSOs were logged, so there's no park log to upload.", true);
});
function onPota(m) {
  potaMsg(m.text, m.ok);
  if (m.ok) {    // show the parks and grid as Procyon understood them
    potaForm.elements.parks.value = S.pota.parks.join(", ");
    potaForm.elements.grid.value = S.pota.grid;
    potaForm.elements.state.value = S.pota.state;
  }
}

function renderLog() {
  renderUploads();
  noteLogCount();
  $("#qsoCount").textContent = S.qso_count;
  $("#logDemo").hidden = !S.settings.demo;
  if (editingIdx !== null) return;   // don't redraw under the user while they edit
  const el = $("#logList");
  if (!S.log.length) {
    el.innerHTML = `<div class="muted pad">No QSOs yet. Completed contacts are logged automatically.</div>`;
    el.dataset.html = "";
    return;
  }
  const html = S.log.map((q) => {
    const d = q.date, t = q.time;
    const when = d.length === 8 ? `${MONTHS[+d.slice(4, 6) - 1]} ${+d.slice(6)}, ${d.slice(0, 4)}` : "";
    const utc = t.length >= 4 ? `${t.slice(0, 2)}:${t.slice(2, 4)}` : "";
    const where = [q.country && esc(q.country),
      q.km != null && dist(q.km, q.approx)].filter(Boolean).join(" · ");
    return `<div class="lrow" data-idx="${q.idx}" title="Click to edit the comment or power, or delete the QSO">
      <div class="l1"><span class="c">${esc(q.call)}</span><span class="g">${esc(q.grid)}</span>
        ${q.band ? `<span class="b">${esc(q.band)}</span>` : ""}
        ${q.tx_pwr ? `<span class="pw">${esc(q.tx_pwr)} W</span>` : ""}
        <span class="r" title="Report sent / received">${esc(q.sent || "—")} / ${esc(q.rcvd || "—")}</span></div>
      <div class="l2"><span class="where">${where}</span><span class="w">${when} · ${utc} UTC${q.qrz ? `<span class="up" title="Uploaded to QRZ">QRZ</span>` : ""}${q.lotw ? `<span class="up" title="Uploaded to LoTW">LoTW</span>` : ""}${q.park ? `<span class="park" title="POTA activation">${esc(q.park)}</span>` : ""}</span></div>
      ${q.comment ? `<div class="l3">${esc(q.comment)}</div>` : `<div class="add">+ Add comment or power</div>`}
    </div>`;
  }).join("");
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }   // keep clicks landing
}

function editLogEntry(row) {
  const q = S.log.find((x) => x.idx === +row.dataset.idx);
  if (!q) return;
  editingIdx = q.idx;
  row.classList.add("editing");
  row.title = "";
  row.querySelector(".l3, .add")?.remove();
  row.insertAdjacentHTML("beforeend", `<div class="ledit">
    <input class="le-comment" maxlength="200" placeholder="Comment" value="${esc(q.comment)}">
    <div class="num"><input class="le-pwr" type="number" min="0" step="1" placeholder="—" value="${esc(q.tx_pwr)}"><span>W</span></div>
    <div class="acts"><button class="btn ghost small le-delete" type="button"
        title="Remove this QSO from your log, e.g. a second entry for the same contact">Delete QSO</button>
      <button class="btn ghost small le-cancel" type="button">Cancel</button>
      <button class="btn primary small le-save" type="button">Save</button></div>
  </div>`);
  row.querySelector(".le-comment").focus();
}

function closeLogEdit(save) {
  const row = $(`.lrow.editing`);
  if (row && save) {
    send({ type: "log_edit", idx: editingIdx, comment: row.querySelector(".le-comment").value,
      tx_pwr: row.querySelector(".le-pwr").value });
  }
  editingIdx = null;
  $("#logList").dataset.html = "";   // the editor changed the list: redraw it in full
  renderLog();
}

function deleteLogEntry(btn) {
  if (!btn.classList.contains("armed")) {   // two clicks, so it can't go by accident
    btn.classList.add("armed", "danger");
    btn.classList.remove("ghost");
    btn.textContent = "Are you sure?";
    setTimeout(() => {
      if (!btn.isConnected) return;
      btn.classList.remove("armed", "danger");
      btn.classList.add("ghost");
      btn.textContent = "Delete QSO";
    }, 4000);
    return;
  }
  const q = S.log.find((x) => x.idx === editingIdx);
  if (q) send({ type: "log_delete", idx: q.idx, call: q.call, when: q.date + q.time });
  editingIdx = null;
  $("#logList").dataset.html = "";
  renderLog();
}

$("#logList").addEventListener("click", (e) => {
  if (e.target.closest(".le-delete")) return deleteLogEntry(e.target.closest(".le-delete"));
  if (e.target.closest(".le-save")) return closeLogEdit(true);
  if (e.target.closest(".le-cancel")) return closeLogEdit(false);
  const row = e.target.closest(".lrow");
  if (!row || row.classList.contains("editing")) return;
  if (editingIdx !== null) closeLogEdit(false);
  editLogEntry($(`.lrow[data-idx="${row.dataset.idx}"]`));
});
$("#logList").addEventListener("keydown", (e) => {
  if (!e.target.closest(".ledit")) return;
  if (e.key === "Enter") closeLogEdit(true);
  if (e.key === "Escape") { e.stopPropagation(); closeLogEdit(false); }  // cancel the edit, don't halt TX
});

// ----------------------------------------------------- movable panels
// Panels sit side by side or stacked. Automatic picks by window width; each
// device can also choose (Settings > Layout), e.g. stacked for a phone or a
// portrait monitor. Side by side, a panel is dragged by its grip into another
// column; stacked, its ▲▼ buttons move it. Panels can also be resized: side by
// side by dragging the gap between two columns, stacked by dragging the bottom
// edge of Band activity or the Logbook. Order, choice and sizes are per device.

const PANELS = { act: ".activity", qso: ".side", log: ".logbook" };
const COLS = { act: "minmax(540px, 1.2fr)", qso: "380px", log: "minmax(280px, 1fr)" };
const DEFAULT_ORDER = ["act", "qso", "log"];
const wideScreen = matchMedia("(min-width: 1341px)");
const narrowScreen = matchMedia("(max-width: 1050px)");
const LAYOUT_MODES = ["auto", "side", "stack"];
const MIN_W = { act: 540, qso: 340, log: 280 };    // narrower and their rows get cut off
const MIN_H = { act: 200, log: 120 };
let order = loadOrder();
let layoutMode = loadMode();
let sizes = loadSizes();   // { w: {panel: px} side by side, h: {panel: px} stacked }

function loadSizes() {
  try {
    const s = JSON.parse(localStorage.getItem("procyon.sizes"));
    if (s && typeof s === "object") return { w: s.w || {}, h: s.h || {} };
  } catch { /* storage unavailable */ }
  return { w: {}, h: {} };
}

function saveSizes() {
  try { localStorage.setItem("procyon.sizes", JSON.stringify(sizes)); } catch { /* not persisted */ }
}

// Side by side: every column but the last can have a width the user dragged it
// to; the last one takes what's left. If the window is too narrow for those
// widths, the default proportions are used until it's wide enough again.
function sideColumns() {
  const L = $(".layout");
  const room = L.clientWidth - 40 - 16 * (order.length - 1);
  const fixed = order.slice(0, -1).reduce((t, k) => t + (sizes.w[k] || parseInt(COLS[k].match(/\d+/))), 0);
  const useSizes = Object.keys(sizes.w).length && fixed + MIN_W[order.at(-1)] <= room;
  return order.map((k, i) => {
    if (!useSizes) return COLS[k];
    if (i === order.length - 1) return `minmax(${MIN_W[k]}px, 1fr)`;
    return sizes.w[k] ? `${sizes.w[k]}px` : COLS[k];
  }).join(" ");
}

function loadMode() {
  try {
    const m = localStorage.getItem("procyon.layoutMode");
    if (LAYOUT_MODES.includes(m)) return m;
  } catch { /* storage unavailable */ }
  return "auto";
}

function saveMode(m) {
  layoutMode = LAYOUT_MODES.includes(m) ? m : "auto";
  try { localStorage.setItem("procyon.layoutMode", layoutMode); } catch { /* not persisted */ }
  applyLayout();
}

// How the panels are arranged right now: "side", "stack", or "medium" (the
// automatic in-between: Band activity on the left, QSO and Logbook on the right).
function arrangement() {
  if (layoutMode !== "auto") return layoutMode;
  return wideScreen.matches ? "side" : narrowScreen.matches ? "stack" : "medium";
}

function loadOrder() {
  try {
    const o = JSON.parse(localStorage.getItem("procyon.layout") ?? localStorage.getItem("skywave.layout"));
    if (Array.isArray(o) && o.length === 3 && DEFAULT_ORDER.every((k) => o.includes(k))) return o;
  } catch { /* storage unavailable: use the default */ }
  return [...DEFAULT_ORDER];
}

function applyLayout() {
  const L = $(".layout"), a = arrangement();
  L.classList.toggle("lay-side", a === "side");
  L.classList.toggle("lay-stack", a === "stack");
  L.style.gridTemplateColumns = a === "side" ? sideColumns()
    : a === "stack" ? "minmax(0, 1fr)" : "";
  L.style.gridTemplateAreas = a === "side" ? `"${order.join(" ")}"`
    : a === "stack" ? order.map((k) => `"${k}"`).join(" ") : "";
  for (const [key, sel] of Object.entries(PANELS)) {
    const up = $(sel).querySelector('.movers [data-d="-1"]'), down = $(sel).querySelector('.movers [data-d="1"]');
    if (up) up.disabled = order.indexOf(key) === 0;
    if (down) down.disabled = order.indexOf(key) === order.length - 1;
    // the last column has no gap to its right to drag
    $(sel).classList.toggle("last-col", order.indexOf(key) === order.length - 1);
    if (key in MIN_H) {
      const h = a === "stack" && sizes.h[key];
      $(sel).style.height = h ? `${h}px` : "";
      $(sel).style.maxHeight = h ? "none" : "";
      $(sel).style.minHeight = h ? "0" : "";   // the drag keeps it above MIN_H
    }
  }
}

function saveOrder(o) {
  order = o;
  try { localStorage.setItem("procyon.layout", JSON.stringify(o)); } catch { /* not persisted */ }
  applyLayout();
}

for (const [key, sel] of Object.entries(PANELS)) {
  const panel = $(sel);
  const grip = document.createElement("button");
  grip.className = "grip";
  grip.type = "button";
  grip.title = "Drag to move this panel";
  grip.innerHTML = `<svg viewBox="0 0 10 16"><circle cx="2.5" cy="3" r="1.4"/><circle cx="7.5" cy="3" r="1.4"/><circle cx="2.5" cy="8" r="1.4"/><circle cx="7.5" cy="8" r="1.4"/><circle cx="2.5" cy="13" r="1.4"/><circle cx="7.5" cy="13" r="1.4"/></svg>`;
  panel.querySelector(".card-head").prepend(grip);

  // Stacked: ▲▼ instead of dragging (dragging fights with scrolling on a phone)
  const movers = document.createElement("span");
  movers.className = "movers";
  movers.innerHTML = `<button type="button" data-d="-1" title="Move this panel up">▲</button>`
    + `<button type="button" data-d="1" title="Move this panel down">▼</button>`;
  movers.addEventListener("click", (e) => {
    const d = Number(e.target.closest("button")?.dataset.d);
    const i = order.indexOf(key), j = i + d;
    if (!d || j < 0 || j >= order.length) return;
    const o = [...order];
    [o[i], o[j]] = [o[j], o[i]];
    saveOrder(o);
  });
  panel.querySelector(".card-head").append(movers);

  // Resize handles: the gap to the panel's right (side by side) and, for Band
  // activity and the Logbook, its bottom edge (stacked). Double-click resets.
  for (const axis of key in MIN_H ? ["x", "y"] : ["x"]) {
    const h = document.createElement("div");
    h.className = `sizer sizer-${axis}`;
    h.title = axis === "x" ? "Drag to change the width (double-click to reset)"
      : "Drag to change the height (double-click to reset)";
    panel.append(h);
    let start = 0, startSize = 0;
    h.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      const r = panel.getBoundingClientRect();
      start = axis === "x" ? e.clientX : e.clientY;
      startSize = axis === "x" ? r.width : r.height;
      h.classList.add("active");
      document.body.classList.add(axis === "x" ? "sizing-x" : "sizing-y");
    });
    h.addEventListener("pointermove", (e) => {
      if (!h.classList.contains("active")) return;
      if (axis === "x") {
        // the other columns keep their widths; only the last one gives way
        const room = $(".layout").clientWidth - 40 - 16 * (order.length - 1);
        const max = room - order.filter((k) => k !== key).reduce((t, k) => t
          + (k === order.at(-1) ? MIN_W[k] : $(PANELS[k]).getBoundingClientRect().width), 0);
        sizes.w[key] = Math.round(Math.min(max, Math.max(MIN_W[key], startSize + e.clientX - start)));
      } else {
        sizes.h[key] = Math.round(Math.max(MIN_H[key], startSize + e.clientY - start));
      }
      applyLayout();
    });
    const done = () => {
      if (!h.classList.contains("active")) return;
      h.classList.remove("active");
      document.body.classList.remove("sizing-x", "sizing-y");
      saveSizes();
    };
    h.addEventListener("pointerup", done);
    h.addEventListener("pointercancel", done);
    h.addEventListener("dblclick", () => {
      delete sizes[axis === "x" ? "w" : "h"][key];
      saveSizes();
      applyLayout();
    });
  }

  let startX = 0, target = null;
  const targetAt = (x) => Object.keys(PANELS).find((k) => {
    if (k === key) return false;
    const r = $(PANELS[k]).getBoundingClientRect();
    return x >= r.left && x <= r.right;
  }) || null;

  grip.addEventListener("pointerdown", (e) => {
    if (arrangement() !== "side") return;
    e.preventDefault();
    startX = e.clientX;
    grip.setPointerCapture(e.pointerId);
    panel.classList.add("dragging");
    document.body.classList.add("reordering");
  });
  grip.addEventListener("pointermove", (e) => {
    if (!panel.classList.contains("dragging")) return;
    panel.style.transform = `translateX(${e.clientX - startX}px)`;
    const t = targetAt(e.clientX);
    if (t !== target) {
      if (target) $(PANELS[target]).classList.remove("drop-target");
      target = t;
      if (target) $(PANELS[target]).classList.add("drop-target");
    }
  });
  const finish = () => {
    if (!panel.classList.contains("dragging")) return;
    panel.classList.remove("dragging");
    panel.style.transform = "";
    document.body.classList.remove("reordering");
    if (target) {
      $(PANELS[target]).classList.remove("drop-target");
      const o = order.filter((k) => k !== key);
      o.splice(order.indexOf(target), 0, key);
      saveOrder(o);
      target = null;
    }
  };
  grip.addEventListener("pointerup", finish);
  grip.addEventListener("pointercancel", finish);
}
addEventListener("resize", applyLayout);   // covers both width thresholds
applyLayout();

// ------------------------------------------------------------- settings

const dlg = $("#settings");
const form = $("#settingsForm");

// Settings tabs. Each device remembers the last one; a first start opens on Station.
const setTabs = [...document.querySelectorAll(".set-tabs [role=tab]")];
function showSetTab(name) {
  if (!setTabs.some((t) => t.dataset.tab === name)) name = "station";
  for (const t of setTabs) {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
  }
  for (const p of document.querySelectorAll(".set-pane")) p.hidden = p.dataset.tab !== name;
  try { localStorage.setItem("procyon.settingsTab", name); } catch { /* not persisted */ }
}
$(".set-tabs").addEventListener("click", (e) => {
  const t = e.target.closest("[role=tab]");
  if (t) showSetTab(t.dataset.tab);
});
$(".set-tabs").addEventListener("keydown", (e) => {   // arrow keys move between tabs
  const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
  if (!step) return;
  e.preventDefault();
  const i = setTabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
  const next = setTabs[(i + step + setTabs.length) % setTabs.length];
  showSetTab(next.dataset.tab);
  next.focus();
});
// A field that needs fixing (e.g. the callsign) may be on another tab: show
// that tab so the browser can point at it.
form.addEventListener("invalid", (e) => {
  const pane = e.target.closest(".set-pane");
  if (pane && pane.hidden) showSetTab(pane.dataset.tab);
}, true);

function showDepth() {   // jt9's depth only means something with jt9
  $("#depthRow").hidden = form.elements.decoder.value !== "jt9";
}

function openSettings(welcome) {
  let tab = "station";
  try { tab = localStorage.getItem("procyon.settingsTab") || tab; } catch { /* default */ }
  showSetTab(welcome ? "station" : tab);
  $("#welcome").hidden = !welcome;
  $("#rigChoice").hidden = true;
  const st = S.settings;
  for (const el of form.elements) {
    if (!el.name || !(el.name in st)) continue;
    if (el.type === "checkbox") el.checked = !!st[el.name];
    else if (el.tagName !== "SELECT" || ["ptt", "depth", "rig_mode", "units", "clock_sync", "decoder"].includes(el.name)) el.value = st[el.name];
    else if (el.name === "rig_baud") setSelect(el, st.rig_baud);
  }
  // Decoder: WSJT-X's jt9 only offered when it's installed.
  const hasJt9 = !!(S.tools && S.tools.jt9);
  $("#decoderJt9").hidden = !hasJt9 && st.decoder !== "jt9";
  $("#decoderJt9").disabled = !hasJt9;
  showDepth();
  // Remote access: only editable on the computer running Procyon.
  $("#remoteLocal").hidden = !isLocal;
  $("#remoteRemote").hidden = isLocal;
  form.elements.remote_pin.value = "";
  // Online logbooks: set up on the computer running Procyon only (the key stays there).
  $("#onlineLocal").hidden = !isLocal;
  $("#onlineRemote").hidden = isLocal;
  // Logbook import: on the computer running Procyon only.
  $("#importLocal").hidden = !isLocal;
  $("#importRemote").hidden = isLocal;
  $("#ctyLocal").hidden = !isLocal;
  $("#ctyRemote").hidden = isLocal;
  if (!S.cty.updating) $("#ctyMsg").textContent = "";
  showCty();
  showOnlineLogbooks();
  form.elements.remote_pin.placeholder = S.remote.pin_set ? "PIN is set - type a new one to change it" : "Choose a PIN";
  $("#remoteUrls").innerHTML = S.remote.urls.length
    ? `When allowed, open this on the other device: ${S.remote.urls.map((u) => `<b>${esc(u)}</b>`).join(" or ")}`
    : "This computer doesn't seem to be on a network right now.";
  $("#importNotes").textContent = "";
  $("#chimeToggle").checked = chimeOn;
  $("#layoutMode").value = layoutMode;
  $("#wfScheme").value = wfScheme;
  $("#palette").value = palette;
  $("#textures").checked = !("plain" in document.documentElement.dataset);
  showRigModel(st.rig_model);
  send({ type: "devices" });
  dlg.showModal();
}

// A select may need an option for an unusual saved value (e.g. baud 1200).
function setSelect(sel, v) {
  v = String(v ?? "");
  if (v && ![...sel.options].some((o) => o.value === v)) sel.add(new Option(v, v));
  sel.value = v;
}

let rigs = [];
const rigLabel = (r) => `${r.mfg} ${r.name}`;
// Radio names without spaces, hyphens or slashes: "ftdx10" finds Hamlib's "FTDX-10".
const squash = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");

function fillRadioLists(m) {
  rigs = m.rigs || [];
  $("#rigList").innerHTML = rigs.map((r) => `<option value="${esc(rigLabel(r))}">#${r.model}${squash(r.name) !== r.name.toLowerCase() ? " " + esc(squash(r.name).toUpperCase()) : ""}</option>`).join("");
  $("#serialList").innerHTML = (m.serial || []).map((p) => `<option value="${esc(p.path)}">${esc(p.desc)}</option>`).join("");
  showRigModel(form.elements.rig_model.value);
}

function showRigModel(model) {
  const r = rigs.find((x) => String(x.model) === String(model));
  form.elements.rig_pick.value = r ? rigLabel(r) : (model ? `Model #${model}` : "");
  rigPicked();
}

// Picking a radio sets its Hamlib model number and turns on what it needs.
// A quiet status line explains what's happening (no pop-ups while typing).
function rigPicked(fromUser) {
  const el = form.elements.rig_pick, status = $("#rigStatus");
  const text = el.value.trim().toLowerCase();
  const flat = squash(text);
  // An exact name, typed with or without the maker and the punctuation.
  let r = rigs.find((x) => rigLabel(x).toLowerCase() === text);
  if (!r && flat) {
    const same = rigs.filter((x) => squash(rigLabel(x)) === flat || squash(x.name) === flat);
    if (same.length === 1) r = same[0];
  }
  if (r) {
    if (fromUser && String(form.elements.rig_model.value) !== String(r.model)) {
      form.elements.rig_enabled.checked = true;
      form.elements.rig_spawn.checked = true;
    }
    form.elements.rig_model.value = r.model;
    el.setCustomValidity("");
    status.textContent = `✓ ${rigLabel(r)} · Hamlib #${r.model}`;
    status.className = "field-status ok";
  } else if (text && !/^model #\d+$/.test(text)) {
    const n = rigs.filter((x) => rigLabel(x).toLowerCase().includes(text) || (flat && squash(rigLabel(x)).includes(flat))).length;
    el.setCustomValidity("Pick a radio from the list");
    status.textContent = !rigs.length
      ? "Procyon has no list of radios: it comes with WSJT-X (Hamlib), which Procyon can't find on this computer. Install WSJT-X, then close and reopen Settings."
      : n ? `${n} match${n > 1 ? "es" : ""} - pick one from the list` : "No radio by that name - try the model, e.g. 7300";
    status.className = "field-status";
  } else {
    if (!text) form.elements.rig_model.value = "";
    el.setCustomValidity("");
    status.textContent = "";
  }
}
for (const ev of ["input", "change"]) form.elements.rig_pick.addEventListener(ev, () => rigPicked(true));
form.elements.decoder.addEventListener("change", showDepth);

$("#importWsjtx").addEventListener("click", () => send({ type: "wsjtx_import" }));

// ---- Finding and testing the radio, and installing WSJT-X
function radioMsg(text, ok) {
  const el = $("#radioMsg");
  el.textContent = text;
  el.classList.toggle("ok", !!ok);
}
$("#detectRadio").addEventListener("click", () => {
  radioMsg("Looking for your radio on the USB ports…");
  $("#detectRadio").disabled = true;
  send({ type: "detect_radio" });
});
function onDetected(m) {
  $("#detectRadio").disabled = false;
  if (m.unsupported) {
    radioMsg("Automatic detection isn't available on this computer - choose your radio from the list, "
             + "pick its serial (COM) port, then press Test radio.");
    return;
  }
  const r = (m.found || [])[0];
  $("#rigChoice").hidden = true;
  if (!r && m.help) return radioMsg(m.help);
  if (!r) {
    radioMsg("No radio answered. Check it's on and connected by USB, then try again. " +
             "With a Digirig, Signalink or similar, choose the radio by hand.");
    return;
  }
  form.elements.rig_enabled.checked = true;
  form.elements.rig_spawn.checked = true;
  form.elements.rig_model.value = r.model || "";
  form.elements.rig_device.value = r.path;
  setSelect(form.elements.rig_baud, r.baud);
  setSelect(form.elements.ptt, r.ptt);
  showRigModel(r.model || "");
  const more = m.found.length > 1 ? ` (${m.found.length - 1} more found; this is the first)` : "";
  radioMsg(`✓ Found ${r.make} ${r.name} on ${r.path} at ${r.baud} baud.${more} ` +
           (r.note ? r.note + " " : "") + "Press Test radio, then Save.", !!r.model);
  // Several models answer the same way (Elecraft): a short list to pick from
  const choices = (r.choices || []).map((c) => rigs.find((x) => String(x.model) === String(c))).filter(Boolean);
  if (!r.model && choices.length) {
    $("#rigChoice").innerHTML = `<option value="">Which one?</option>`
      + choices.map((x) => `<option value="${x.model}">${esc(rigLabel(x))}</option>`).join("");
    $("#rigChoice").hidden = false;
  }
}
$("#rigChoice").addEventListener("change", (e) => {
  if (!e.target.value) return;
  form.elements.rig_model.value = e.target.value;
  showRigModel(e.target.value);
  e.target.hidden = true;
  radioMsg(`✓ ${form.elements.rig_pick.value} on ${form.elements.rig_device.value}. Press Test radio, then Save.`, true);
});
$("#testRadio").addEventListener("click", () => {
  const f = form.elements;
  if (!f.rig_model.value && f.rig_spawn.checked) { radioMsg("Pick your radio first (or press Detect my radio)."); return; }
  radioMsg("Asking the radio for its frequency…");
  $("#testRadio").disabled = true;
  send({ type: "rig_test", model: f.rig_model.value, device: f.rig_device.value,
         baud: f.rig_baud.value, spawn: f.rig_spawn.checked });
});
function onRigTest(m) {
  $("#testRadio").disabled = false;
  if (m.ok) radioMsg(`✓ Radio answered: ${(m.freq / 1e6).toFixed(6)} MHz${m.mode ? " " + m.mode : ""}. Radio control works.`, true);
  else radioMsg("✗ " + (m.error || "The radio didn't answer."));
}

$("#installWsjtx").addEventListener("click", () => {
  $("#installWsjtx").disabled = true;
  $("#installStatus").className = "";
  $("#installStatus").textContent = "Starting…";
  send({ type: "install_wsjtx" });
});
function onInstall(m) {
  const st = $("#installStatus");
  if (m.error) {
    $("#installWsjtx").disabled = false;
    st.className = "err";
    st.textContent = m.error + " You can install it yourself from wsjt.sourceforge.io.";
  } else {
    st.className = "";
    st.textContent = m.text || "";
  }
}
$("#chimeToggle").addEventListener("change", (e) => {
  chimeOn = e.target.checked;
  try { localStorage.setItem("procyon.chime", chimeOn ? "on" : "off"); } catch { /* not persisted */ }
  if (chimeOn) chime();   // a sample, so you know what it sounds like
});

function applyImport(m) {
  const notes = $("#importNotes");
  if (!m.settings) { notes.textContent = m.notes.join(" "); return; }
  for (const [k, v] of Object.entries(m.settings)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.tagName === "SELECT") setSelect(el, v);
    else el.value = v;
  }
  showRigModel(form.elements.rig_model.value);
  notes.textContent = ["Filled in from WSJT-X. Check it over, then press Save.", ...m.notes].join(" ");
}

function fillDevices(m) {
  const st = S.settings;
  for (const [name, devs] of [["audio_in", m.inputs], ["audio_out", m.outputs]]) {
    const sel = form.elements[name];
    sel.innerHTML = `<option value="">System default</option>` +
      devs.map((d) => `<option value="${esc(d.name)}">${esc(d.desc)}</option>`).join("");
    // Same rule as the engine: PipeWire's numbered copy of a name ("....9")
    // is the same device, so select the device itself (saving tidies it up).
    const saved = st[name] || "";
    const dev = devs.find((d) => d.name === saved) || devs.find((d) => d.name === saved.replace(/\.\d+$/, ""));
    if (saved && !dev) {
      sel.insertAdjacentHTML("beforeend", `<option value="${esc(saved)}">${esc(saved)} (not found)</option>`);
    }
    sel.value = dev ? dev.name : saved;
  }
}

$("#settingsBtn").addEventListener("click", () => openSettings(false));

// Quit: stops Procyon the same careful way as Ctrl-C (only on the station computer)
$("#quitBtn").addEventListener("click", () => {
  $("#quitText").textContent = S && S.tx
    ? "Procyon is transmitting - quitting stops the transmission, then closes Procyon (and its window, if it has one). Your settings and logbook are saved."
    : "Procyon stops listening and closes (with its window, if it has one). Your settings and logbook are saved.";
  $("#quitDlg").returnValue = "";   // Esc must never count as "Quit"
  $("#quitDlg").showModal();
});
$("#updateQuit").addEventListener("click", () => $("#quitBtn").click());
$("#quitDlg").addEventListener("close", () => {
  if ($("#quitDlg").returnValue === "quit") send({ type: "quit" });
});

// Full screen, for those who don't know F11. Hidden where the browser can't
// do it (e.g. iPhone). F11's own full screen isn't visible to the page, so the
// button may offer full screen then; pressing it is harmless.
const fsBtn = $("#fullscreenBtn");
fsBtn.hidden = !document.fullscreenEnabled;
fsBtn.addEventListener("click", () => {
  const p = document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
  p.catch(() => toast("This browser didn't allow full screen - try F11", "warn"));
});
document.addEventListener("fullscreenchange", () => {
  const on = !!document.fullscreenElement;
  fsBtn.classList.toggle("on", on);
  fsBtn.title = on ? "Leave full screen (Esc)" : "Full screen (F11)";
});

function openAbout() {
  const a = (S && S.about) || {};
  // ProcyonEX test builds: "1.1.0~ex1" reads as "1.1.0 EX1"
  $("#aboutVersion").textContent = (a.version || "—").replace("~ex", " EX");
  $("#aboutDate").textContent = a.date
    ? new Date(`${a.date}T12:00:00Z`).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "";
  $("#about").showModal();
}
$("#brandBtn").addEventListener("click", openAbout);
$("#aboutLink").addEventListener("click", () => { $("#settings").close(); openAbout(); });
$("#layoutMode").addEventListener("change", (e) => saveMode(e.target.value));
$("#wfScheme").addEventListener("change", (e) => setWfScheme(e.target.value));
$("#palette").addEventListener("change", (e) => setPalette(e.target.value));
$("#textures").addEventListener("change", (e) => setTextures(e.target.checked));
$("#demoOff").addEventListener("click", () => send({ type: "settings", settings: { demo: false } }));
$("#exportMarkAll").addEventListener("click", () => { $("#exportMsg").textContent = "…"; send({ type: "export_mark_all" }); });
$("#resetLayout").addEventListener("click", () => {
  sizes = { w: {}, h: {} };
  saveSizes();
  saveOrder([...DEFAULT_ORDER]);
  saveMode("auto");
  $("#layoutMode").value = "auto";
  toast("Panel layout reset", "good");
});
function showOnlineLogbooks() {
  form.elements.qrz_key_new.value = "";
  form.elements.qrz_key_new.placeholder = S.qrz_key_set ? "Key is saved - type a new one to change it" : "Paste your key here";
  $("#qrzForget").hidden = !S.qrz_key_set;
  qrzMsg("");
  // LoTW off (0.9.60):
  // $("#tqslHint").innerHTML = S.tqsl_found
  //   ? "Type the name exactly as it appears in TQSL's \"Station Locations\" tab. Procyon hands your QSOs to TQSL, which signs and uploads them."
  //   : "TQSL, ARRL's LoTW program, isn't installed on this computer. Install it from lotw.arrl.org and set up your callsign certificate and a station location, then come back here.";
}
// Results show inside Settings: notices (toasts) would be hidden behind it.
function qrzMsg(text, ok) {
  $("#qrzMsg").textContent = text;
  $("#qrzMsg").classList.toggle("ok", !!ok);
}
$("#qrzCheck").addEventListener("click", () => {
  qrzMsg("Asking QRZ…");
  $("#qrzCheck").disabled = true;
  send({ type: "qrz_check", key: form.elements.qrz_key_new.value.trim() });
});
function onQrzCheck(m) {
  $("#qrzCheck").disabled = false;
  qrzMsg((m.ok ? "✓ " : "✗ ") + m.text, m.ok);
}
// Logbook import (Settings → Advanced): results show inline - notices would be
// hidden behind the Settings window.
function importMsg(m) {
  const el = $("#importMsg");
  el.textContent = m.busy ? m.text : (m.ok ? "" : "✗ ") + m.text;
  el.classList.toggle("ok", !!m.ok);
  $("#qrzImport").disabled = $("#adifImportBtn").disabled = !!m.busy;
}
// Country data (Settings → Advanced): which Big CTY is in use, and the update button.
function showCty() {
  const c = S.cty || {};
  const el = $("#ctyNow");
  $("#ctyUpdate").disabled = !!c.updating;
  if (!c.found) {
    el.textContent = "No country file found - countries and NEW-country tags are missing. Press Update to download one.";
    return;
  }
  const where = { own: "downloaded with the button below", bundled: "the copy that came with Procyon" }[c.source]
    || "the copy that came with WSJT-X";
  if (!c.version) { el.textContent = `In use: ${where} (its date isn't recorded in the file).`; return; }
  const d = new Date(c.version + "T12:00:00Z");
  const date = d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const months = Math.floor((Date.now() - d) / (30.44 * 86400e3));
  const age = months >= 3 ? ` - about ${months} months old, worth updating` : "";
  el.textContent = `In use: Big CTY of ${date}, ${where}${age}.`;
}
function ctyMsg(m) {
  const el = $("#ctyMsg");
  el.textContent = m.busy ? m.text : (m.ok ? "✓ " : "✗ ") + m.text;
  el.classList.toggle("ok", !!m.ok);
  $("#ctyUpdate").disabled = !!m.busy;
}
$("#ctyUpdate").addEventListener("click", () => {
  ctyMsg({ busy: true, text: "Asking country-files.com…" });
  send({ type: "cty_update" });
});
$("#qrzImport").addEventListener("click", () => {
  importMsg({ busy: true, text: "Asking QRZ for your logbook…" });
  send({ type: "qrz_import" });
});
$("#adifImportBtn").addEventListener("click", () => $("#adifFile").click());
$("#adifFile").addEventListener("change", async () => {
  const f = $("#adifFile").files[0];
  $("#adifFile").value = "";
  if (!f) return;
  importMsg({ busy: true, text: `Reading ${f.name}…` });
  try {
    const r = await fetch(`/import.adi?name=${encodeURIComponent(f.name)}`, {
      method: "POST", body: f, headers: { "X-Procyon-Import": "1", "Content-Type": "application/octet-stream" } });
    importMsg(await r.json());
  } catch {
    importMsg({ ok: false, text: "Couldn't send the file to Procyon - is it still running?" });
  }
});
$("#qrzForget").addEventListener("click", () => {
  send({ type: "settings", settings: { qrz_key_forget: true } });
  S.qrz_key_set = false;
  showOnlineLogbooks();
  qrzMsg("✓ QRZ key removed.", true);
});
form.addEventListener("submit", (e) => {
  if (e.submitter && e.submitter.value !== "save") return;
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked
      : ["rig_port", "depth"].includes(el.name) ? +el.value : el.value.trim();
  }
  send({ type: "settings", settings: out });
  toast("Settings saved", "good");
});

// ----------------------------------------------------------------- toasts

function toast(text, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.transition = "opacity .4s"; el.style.opacity = "0"; }, 3600);
  setTimeout(() => el.remove(), 4100);
}

// ------------------------------------------------------------------- boot

// Header: compact, then wrapped, only when its contents don't fit (which
// depends on how many status chips are showing).
let headerKey = "";
function fitHeader(force) {
  const h = $(".top");
  const key = [...document.querySelectorAll(".status > *")].map((c) => (c.hidden ? 0 : 1)).join("") + innerWidth
    + $("#clockLabel").textContent.length;   // the clock chip's width changes with its reading
  if (!force && key === headerKey) return;
  headerKey = key;
  h.classList.remove("tight", "wrap");
  if (h.scrollWidth > h.clientWidth + 1) h.classList.add("tight");
  if (h.scrollWidth > h.clientWidth + 1) h.classList.add("wrap");
}
window.addEventListener("resize", () => fitHeader(true));
window.addEventListener("resize", sizeWaterfall);
sizeWaterfall();
renderScale();
renderCustomFilter();
requestAnimationFrame(tick);
connect();
