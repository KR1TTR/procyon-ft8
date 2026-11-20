// Procyon JS8 chat - your side of the chat. Keeps the Heard list, the band
// activity and the conversations, and decides what to do with every message
// heard. It knows nothing about the screen or the radio: give it send(text),
// and call hear(text, fromCall, snr, freq) for each message received (the same
// plug-in point as the chess station, plus SNR and audio frequency).
// Like JS8Call, the text starts with the call or @group it's for; the radio
// adds our own call.
//
// Nothing goes out by itself: answers to SNR? / GRID? etc. are put in the
// typing box (this.suggest) and only sent when you press Send.
'use strict';

class ChatStation {
  constructor({ call, grid, name = '', info = '', send, onChange = () => {} }) {
    Object.assign(this, { call, grid, name, info, sendText: send, onChange });
    this.heard = new Map();     // call -> {call, snr, freq, grid, last, hbOnly}
    this.groups = new Set(['@ALLCALL', '@HB', '@CHESS']);   // groups we listen to
    this.band = [];             // every complete message heard or sent
    this.convos = new Map();    // call or @group -> {key, msgs, unread}
    this.arriving = new Map();  // fromCall -> {text, snr, freq, t} (frames so far)
    this.suggest = null;        // {to, text, why}: a reply waiting in the typing box
    this.seq = 0;
  }

  convo(key) {
    if (!this.convos.has(key)) this.convos.set(key, { key, msgs: [], unread: 0 });
    return this.convos.get(key);
  }

  // ---- the radio calls these

  hearPart(text, from, snr, freq) {
    if (!from) from = this.unknownKey(freq);   // real radio: we missed the frame with the sender's call
    this.arriving.set(from, { text: JS8.clean(text), snr, freq, t: Date.now() });
    this.onChange();
  }

  // t / old: a message heard earlier, replayed (e.g. after a page reload) -
  // shown at its own time, and no answer is suggested for it.
  hear(text, from, snr, freq, t, old) {
    const p = JS8.parse(text), now = t || Date.now();
    if (!from) {                // sender unknown (its first frame was missed): band activity only
      this.arriving.delete(this.unknownKey(freq));
      this.band.push({ id: ++this.seq, t: now, from: '?', to: p.to, body: p.body, text: p.text, snr, freq, hb: false, toMe: false, old });
      if (this.band.length > 500) this.band.shift();
      this.onChange();
      return;
    }
    this.arriving.delete(from);
    const toMe = p.to === this.call;
    const hb = p.cmd === 'HEARTBEAT' && (p.to === '@HB' || p.to === '@ALLCALL');

    // Heard list
    const h = this.heard.get(from) || { call: from, hbOnly: true };
    Object.assign(h, { snr, freq, last: now });
    if (!hb) h.hbOnly = false;
    const grid = p.body.match(/\b([A-R]{2}[0-9]{2}([A-X]{2})?)$/);
    if (grid && (hb || p.cmd === 'GRID' || /\bCQ\b/.test(p.body))) h.grid = grid[1];
    this.heard.set(from, h);
    if (JS8.isGroup(p.to) && !this.groups.has(p.to) && p.to !== '@HB') this.heardGroups = (this.heardGroups || new Set()).add(p.to);

    // Band activity
    this.band.push({ id: ++this.seq, t: now, from, to: p.to, body: p.body, text: p.text, snr, freq, hb, toMe, old });
    if (this.band.length > 500) this.band.shift();

    // Conversations: messages to us, and to groups we listen to (not heartbeats)
    const key = toMe ? from : (this.groups.has(p.to) && !hb ? p.to : null);
    if (key) {
      const c = this.convo(key);
      c.msgs.push({ id: ++this.seq, dir: 'rx', from, body: p.body, t: now, snr, old });
      c.unread++;
    }
    // Anything from a station we sent to means our message got there.
    if (toMe || p.to === '') {
      for (const m of this.convos.get(from)?.msgs || []) if (m.dir === 'tx' && m.end && m.end <= now && !m.heardBy) m.heardBy = from;
    }
    if (toMe && !old) this.answer(p, from, snr);
    this.onChange();
  }

  // A message whose sender we didn't catch is kept by its audio frequency ("?1137").
  unknownKey(freq) {
    for (const k of this.arriving.keys()) if (k.startsWith('?') && Math.abs(+k.slice(1) - freq) <= 10) return k;
    return '?' + Math.round(freq);
  }

  // A question to us: put the answer in the typing box (never sent by itself).
  answer(p, from, snr) {
    const top = [...this.heard.values()].filter(x => x.call !== from).sort((a, b) => b.last - a.last).slice(0, 3).map(x => x.call);
    const replies = {
      'SNR?': [`SNR ${JS8.fmtSnr(snr)}`, `${from} asked how strong they are here`],
      'GRID?': [`GRID ${this.grid}`, `${from} asked for your grid`],
      'INFO?': [`INFO ${this.info || 'NO INFO'}`, `${from} asked about your station`],
      'HEARING?': [`HEARING ${top.join(' ') || 'NONE'}`, `${from} asked who you're hearing`],
      'HEARTBEAT': [`HEARTBEAT SNR ${JS8.fmtSnr(snr)}`, `${from} sent you a heartbeat`],
    };
    const r = replies[p.cmd];
    if (r) this.suggest = { to: from, text: r[0], why: r[1] };
  }

  // ---- things you do

  // to = a call, a @group, or '' for everyone. Returns the message, or null.
  send(to, body) {
    body = JS8.clean(body);
    if (!body) return null;
    const text = to ? `${to} ${body}` : body;
    const air = this.sendText(text) || {};   // the radio may say when it's on the air
    const now = Date.now();
    const m = { id: ++this.seq, dir: 'tx', from: this.call, body, t: now, start: air.start, end: air.end, n: air.n || JS8.frames(text) };
    if (to) this.convo(to).msgs.push(m);   // to nobody: band activity only
    this.band.push({ id: m.id, t: now, from: this.call, to, body, text, mine: true, msg: m });
    if (this.suggest && this.suggest.to === to) this.suggest = null;
    this.onChange();
    return m;
  }

  // Clear what a view shows: 'all' = band activity, else one conversation.
  clear(key) {
    if (key === 'all') this.band = [];
    else { const c = this.convos.get(key); if (c) { c.msgs = []; c.unread = 0; } }
    this.onChange();
  }

  read(key) { const c = this.convos.get(key); if (c) c.unread = 0; }
}
