// Procyon JS8 chat - a pretend radio link for testing. No transmitter.
// Works like JS8 on the air: a message goes out in frames, each frame starts
// on a slot boundary (every 15 s at Normal speed), and listeners decode it a
// frame at a time. The band conditions can lose a message or damage a letter
// or two. Like JS8, the sender's call travels with every message (and isn't
// damaged: JS8 checks it separately).
//
// Each station attaches with hear(text, fromCall, snr, freq) - the one plug-in
// point Procyon's real JS8 modem will feed later - plus an optional
// part(textSoFar, fromCall, snr, freq) for "still arriving" frames.
'use strict';

class FakeLink {
  constructor() {
    this.stations = new Map();   // id -> {call, freq, snr, hear, part}
    this.speed = 'Normal';
    this.fast = false;           // 1 s frames instead, for quick testing
    this.loss = 0;               // chances, 0..1, per message per listener
    this.garble = 0;
    this.busyUntil = new Map();  // id -> ms when its queued messages are done
    this.airing = [];            // {id, freq, start, end, n, text}
    this.timers = [];
    this.onEvent = () => {};
  }

  get frameSec() { return this.fast ? 1 : JS8.SPEEDS[this.speed]; }
  get width() { return JS8.WIDTH[this.speed]; }

  attach(id, st) { this.stations.set(id, st); }
  detach(id) { this.stations.delete(id); }

  reset() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.stations.clear();
    this.busyUntil.clear();
    this.airing = [];
  }

  // How strong a sends to b: each station has a "how well it's heard" figure;
  // a path between two of them sits in between, plus a little fading.
  pathSnr(a, b) {
    const base = a.snr == null ? b.snr : b.snr == null ? a.snr : Math.min(a.snr, b.snr);
    return Math.max(-24, Math.min(15, Math.round((base ?? -10) + (Math.random() * 4 - 2))));
  }

  // What's on the air right now (for the waterfall).
  nowAiring(now = Date.now()) {
    this.airing = this.airing.filter(a => a.end > now - 1000);
    return this.airing.filter(a => a.start <= now && now < a.end);
  }

  // Send text from station id. Returns {start, end, n} in ms (when it's on the air).
  send(id, text) {
    const from = this.stations.get(id);
    if (!from) return null;
    text = JS8.clean(text);
    const n = JS8.frames(text), P = this.frameSec * 1000;
    if (!n) return null;
    // Wait for the next slot, and for anything this station already queued.
    const start = Math.ceil(Math.max(Date.now() + 200, this.busyUntil.get(id) || 0) / P) * P;
    const end = start + n * P;
    this.busyUntil.set(id, end);
    const tx = { id, call: from.call, freq: from.freq, start, end, n, text };
    this.airing.push(tx);
    this.onEvent({ what: 'tx', ...tx });

    for (const [lid, st] of this.stations) {
      if (lid === id) continue;
      if (Math.random() < this.loss) { this.onEvent({ what: 'lost', from: from.call, to: st.call, text }); continue; }
      const heard = Math.random() < this.garble ? FakeLink.damage(text) : text;
      const snr = this.pathSnr(from, st);
      // Frame by frame: the text so far, then the whole message at the end.
      for (let k = 1; k <= n; k++) {
        const at = start + k * P - Date.now();
        this.timers.push(setTimeout(() => {
          if (this.stations.get(lid) !== st) return;   // detached meanwhile
          if (k < n) st.part?.(FakeLink.soFar(heard, k, n), from.call, snr, from.freq);
          else st.hear(heard, from.call, snr, from.freq);
        }, at));
      }
    }
    return { start, end, n };
  }

  // The part of a message decoded after k of n frames (whole words, roughly).
  static soFar(text, k, n) {
    const p = JS8.parse(text);
    if (p.to && k === 1) return p.to;
    const share = (p.to ? (k - 1) / (n - 1) : k / n);
    const body = p.body.slice(0, Math.round(p.body.length * share));
    return (p.to ? p.to + ' ' : '') + body;
  }

  // Swap one or two letters for random ones, like a weak signal might.
  static damage(text) {
    const chars = [...text], abc = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    const spots = chars.map((c, i) => (c === ' ' ? -1 : i)).filter(i => i >= 0);
    const n = Math.random() < 0.3 ? 2 : 1;
    for (let k = 0; k < n; k++) {
      const i = spots.splice(Math.floor(Math.random() * spots.length), 1)[0];   // never the same letter twice
      let c;
      do c = abc[Math.floor(Math.random() * abc.length)]; while (c === chars[i]);
      chars[i] = c;
    }
    return chars.join('');
  }
}
