// Procyon JS8 chat - pretend stations for the simulator. They listen on the
// fake link like everyone else (so lost/damaged messages affect them too),
// send heartbeats and CQs, chat with each other, answer questions, and answer
// you. Made-up operators; the calls are just examples.
'use strict';

const BOT_STATIONS = [
  { call: 'N8GHI',  grid: 'EN81', freq: 1450, snr: -8,  name: 'DAVE',  qth: 'CLEVELAND OH',   rig: 'IC-7300 50W DIPOLE',      wx: 'RAIN 9C',   chatty: 1, cq: true },
  { call: 'VE3JKL', grid: 'FN03', freq: 1210, snr: -14, name: 'MARIE', qth: 'TORONTO',        rig: 'FT-891 40W EFHW',         wx: 'CLEAR 4C',  chatty: .6 },
  { call: 'W5ABC',  grid: 'EM12', freq: 2100, snr: -19, name: 'BUBBA', qth: 'DALLAS TX',      rig: 'TS-590 100W HEXBEAM',     wx: 'SUNNY 24C', chatty: .8, cq: true },
  { call: 'KF9XYZ', grid: 'EN52', freq: 980,  snr: -11, name: 'AMY',   qth: 'CHICAGO IL',     rig: 'IC-705 10W VERTICAL',     wx: 'WINDY 7C',  chatty: .7 },
  { call: 'G4MNO',  grid: 'IO91', freq: 1600, snr: -21, name: 'NIGEL', qth: 'READING UK',     rig: 'K3 100W LOOP',            wx: 'DRIZZLE 8C', chatty: .3 },
  { call: 'K1ABC',  grid: 'FN42', freq: 1820, snr: 2,   name: 'SAM',   qth: 'BOSTON MA',      rig: 'IC-7610 100W YAGI',       wx: 'CLOUDY 6C', chatty: .5, chess: true },
];

class Bot {
  constructor(link, info) {
    Object.assign(this, info);
    this.link = link;
    this.id = 'bot-' + info.call;
    this.talk = new Map();   // partner -> how far our chat has got
    this.timers = [];
    this.heardCalls = new Map();
    link.attach(this.id, { call: this.call, freq: this.freq, snr: this.snr, hear: (...a) => this.hear(...a) });
  }

  // Times scale with the speed (and shrink a lot in Fast test mode).
  get scale() { return this.link.frameSec / 15; }
  later(sec, fn) { this.timers.push(setTimeout(fn, sec * 1000 * this.scale)); }
  // Like a person typing: wait a frame or two before answering.
  reply(text) { this.later(15 + Math.random() * 20, () => this.link.send(this.id, text)); }
  stop() { this.timers.forEach(clearTimeout); this.timers = []; this.link.detach(this.id); }

  start() {
    this.later(5 + Math.random() * 90, () => this.heartbeat());
    if (this.cq) this.later(60 + Math.random() * 180, () => this.callCQ());
    if (this.chess) this.later(40 + Math.random() * 120, () => this.chessCQ());
    this.later(120 + Math.random() * 240, () => this.chatSomeone());
  }

  heartbeat() {
    this.link.send(this.id, `@HB HEARTBEAT ${this.grid}`);
    this.later(300 + Math.random() * 300, () => this.heartbeat());
  }
  callCQ() {
    this.link.send(this.id, `CQ CQ ${this.grid}`);
    this.later(360 + Math.random() * 360, () => this.callCQ());
  }
  chessCQ() {
    this.link.send(this.id, '@CHESS CQ');
    this.later(480 + Math.random() * 300, () => this.chessCQ());
  }
  // Now and then start a chat with another station we've heard.
  chatSomeone() {
    const others = [...this.heardCalls.keys()].filter(c => !this.talk.has(c));
    if (others.length && Math.random() < this.chatty) {
      const c = others[Math.floor(Math.random() * others.length)];
      this.link.send(this.id, `${c} ${Math.random() < .5 ? 'SNR?' : 'GE, HW CPY?'}`);
      this.talk.set(c, 0);
    }
    this.later(200 + Math.random() * 300, () => this.chatSomeone());
  }

  hear(text, from, snr) {
    this.heardCalls.set(from, snr);
    const p = JS8.parse(text);
    // A CQ: sometimes answer it (not if we're chatting already)
    if (!p.to && /\bCQ\b/.test(p.body) && !this.talk.has(from) && Math.random() < this.chatty * .5) {
      this.talk.set(from, 1);
      return this.reply(`${from} GE, UR ${JS8.fmtSnr(snr)} HERE. NAME ${this.name}, QTH ${this.qth}`);
    }
    if (p.to !== this.call) return;
    if (this.chess && !p.cmd) return this.reply(`${from} TNX. CHESS NOT HOOKED UP YET, SOON. 73`);

    switch (p.cmd) {
      case 'SNR?': return this.reply(`${from} SNR ${JS8.fmtSnr(snr)}`);
      case 'GRID?': return this.reply(`${from} GRID ${this.grid}`);
      case 'INFO?': return this.reply(`${from} INFO ${this.rig}`);
      case 'HEARING?': return this.reply(`${from} HEARING ${[...this.heardCalls.keys()].filter(c => c !== from).slice(-3).join(' ') || 'NONE'}`);
      case 'HEARTBEAT': return Math.random() < .5 && this.reply(`${from} HEARTBEAT SNR ${JS8.fmtSnr(snr)}`);
      case 'SNR': case 'GRID': case 'INFO': case 'HEARING': case 'ACK': case 'QSL':
        if (this.talk.has(from)) return this.chatOn(from, p.body);
        return;
    }
    this.chatOn(from, p.body);
  }

  // A simple chat: name and QTH, rig, weather, then 73.
  chatOn(from, body) {
    if (/\b73\b/.test(body)) {
      if (this.talk.get(from) !== 'done') this.reply(`${from} TNX FER THE CHAT, 73 ${this.talk.get(from) >= 1 ? '' : 'GL'}`.trim());
      this.talk.set(from, 'done');
      return;
    }
    if (this.talk.get(from) === 'done') this.talk.set(from, 0);
    const step = this.talk.get(from) || 0;
    const lines = [
      `GE, NAME HERE IS ${this.name}, QTH ${this.qth}`,
      `RIG HERE IS ${this.rig}`,
      `WX ${this.wx}. FB CHAT`,
      `GOOD TO MEET YOU. 73`,
    ];
    if (/\?/.test(body) && /RIG|ANT/.test(body)) this.talk.set(from, Math.max(step, 2)), this.reply(`${from} ${lines[1]}`);
    else if (/\?/.test(body) && /WX|WEATHER/.test(body)) this.talk.set(from, Math.max(step, 3)), this.reply(`${from} ${lines[2]}`);
    else {
      this.talk.set(from, step + 1);
      this.reply(`${from} ${lines[Math.min(step, lines.length - 1)]}`);
      if (step >= lines.length - 1) this.talk.set(from, 'done');
    }
  }
}
