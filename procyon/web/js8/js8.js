// Procyon JS8 chat - small helpers about JS8 messages: speeds, how many frames
// a message takes, splitting "TO TEXT" apart, grids and distances.
// Inside Procyon the exact frame count comes from its packer; this is the estimate.
'use strict';

const JS8 = {
  // Seconds per frame (one frame = one chunk of text on the air) and how wide
  // the signal is, per JS8 speed.
  SPEEDS: { Slow: 30, Normal: 15, Fast: 10, Turbo: 6 },
  WIDTH: { Slow: 25, Normal: 50, Fast: 80, Turbo: 160 },

  // Rough guess: JS8 squeezes common letters, so a frame carries about 13
  // characters of text; a directed message spends its first frame on the calls.
  CHARS_PER_FRAME: 13,

  // Commands JS8Call understands (shown in blue; queries get an answer).
  // what fits whole in the directed frame after the call (JS8Call's directed commands)
  ONE_FRAME: ['SNR?', '?', 'HEARING?', 'GRID?', 'STATUS?', 'STATUS', 'HEARING', 'GRID', 'INFO?', 'INFO', 'FB',
    'HW CPY?', 'SK', 'RR', 'QSL?', 'QSL', 'SNR', 'NO', 'YES', '73', 'NACK', 'ACK', 'AGN?', 'QUERY CALL', 'QUERY MSGS?'],
  COMMANDS: ['SNR?', 'GRID?', 'INFO?', 'HEARING?', 'HEARTBEAT', 'SNR', 'GRID', 'INFO', 'HEARING', 'ACK', 'QSL?', 'QSL'],

  // Upper case, and only characters JS8 can send.
  clean(text) {
    return String(text).toUpperCase().replace(/[^A-Z0-9 .,?\/+\-@!'#&()=:;]/g, '').replace(/\s+/g, ' ').trim();
  },

  isCall(w) {
    return /^([A-Z0-9]{1,4}\/)?[A-Z0-9]{0,3}[0-9][A-Z0-9]{0,3}[A-Z](\/[A-Z0-9]{1,4})?$/.test(w) && w.length >= 3 && w.length <= 12
      && (w.match(/[A-Z]/g) || []).length >= 2;   // so "50W" or "20M" isn't taken for a call
  },
  isGroup(w) { return /^@[A-Z0-9]{2,10}$/.test(w); },

  // "N8GHI SNR?" -> { to: 'N8GHI', body: 'SNR?', cmd: 'SNR?' }.
  // Text that doesn't start with a call or @group goes to nobody in particular.
  parse(text) {
    const t = JS8.clean(text);
    const [first, ...rest] = t.split(' ');
    let to = '', body = t;
    if (rest.length && (JS8.isCall(first) || JS8.isGroup(first))) { to = first; body = rest.join(' '); }
    const word = body.split(' ')[0];
    const cmd = JS8.COMMANDS.includes(word) ? word : '';
    return { to, body, cmd, text: t };
  },

  frames(text) {
    const p = JS8.parse(text);
    if (!p.text) return 0;
    if (p.to && JS8.ONE_FRAME.includes(p.body)) return 1;   // a call + a JS8 command fits in one frame
    if (p.to && /^(HEARTBEAT )?SNR [+-]?\d{1,2}$/.test(p.body)) return 1;   // ... and so does a report
    return (p.to ? 1 : 0) + Math.max(1, Math.ceil(p.body.length / JS8.CHARS_PER_FRAME));
  },

  // a JS8 report holds only -30..+31 (JS8Call splits bigger ones into an extra frame)
  fmtSnr(s) { s = Math.max(-30, Math.min(31, Math.round(s))); return (s >= 0 ? '+' : '-') + String(Math.abs(s)).padStart(2, '0'); },

  // Maidenhead grid -> [lat, lon] of the square's middle.
  gridLatLon(g) {
    g = String(g || '').toUpperCase();
    if (!/^[A-R]{2}[0-9]{2}([A-X]{2})?$/i.test(g)) return null;
    let lon = (g.charCodeAt(0) - 65) * 20 - 180 + (+g[2]) * 2;
    let lat = (g.charCodeAt(1) - 65) * 10 - 90 + (+g[3]);
    if (g.length === 6) { lon += (g.charCodeAt(4) - 65) / 12 + 1 / 24; lat += (g.charCodeAt(5) - 65) / 24 + 1 / 48; }
    else { lon += 1; lat += 0.5; }
    return [lat, lon];
  },
  distanceKm(g1, g2) {
    const a = JS8.gridLatLon(g1), b = JS8.gridLatLon(g2);
    if (!a || !b) return null;
    const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
    return Math.round(6371 * 2 * Math.asin(Math.sqrt(h)));
  },
};
