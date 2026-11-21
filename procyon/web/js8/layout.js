// Procyon JS8 chat - movable, resizable panels ("tiles").
// The layout is a tree: a tile, or a split that lays its parts side by side
// ("row") or stacked ("col"), each part with a share of the space (sizes).
// Drag a tile by its ⠿ gripper onto another tile: left/right edge = beside it,
// top/bottom edge = above/below it, the middle = swap places. Drag the gap
// between two tiles to resize them; double-click a gap to share it evenly.
// Kept per device (localStorage); on phones the tiles simply stack.
'use strict';

class Dock {
  // optional = tiles that may be left out of the layout (shown only when added)
  // tabHome = an optional tile that can also live as a tab inside another tile:
  // {id: {into: tile id, row: selector of that tile's tab row}}; dropping it there
  // takes the panel away again (onTabbed(id) is told).
  // keep = tiles whose width only changes when the user drags their gap: when
  // other panels come, go or move, they keep the width they had.
  constructor(root, { key, defaults, optional = [], tabHome = {}, keep = [], phoneWidth = 700 }) {
    this.root = root;
    this.key = key;
    this.defaults = defaults;
    this.phoneWidth = phoneWidth;
    this.optional = optional;
    this.tabHome = tabHome;
    this.keep = keep;
    this.onTabbed = () => {};
    this.tiles = new Map([...root.querySelectorAll('[data-tile]')].map(el => [el.dataset.tile, el]));
    this.tree = this.load() || Dock.copy(defaults);
    this.onResize = () => {};
    this.onLayout = () => {};
    this.render();
    root.addEventListener('pointerdown', e => this.pointerDown(e));
    root.addEventListener('dblclick', e => this.evenOut(e));
    addEventListener('resize', () => { if (this.phone !== this.isPhone()) this.render(); });
  }

  static copy(t) { return JSON.parse(JSON.stringify(t)); }
  isPhone() { return innerWidth <= this.phoneWidth; }

  // ---------------------------------------------------------------- saving
  load() {
    try {
      const t = JSON.parse(localStorage.getItem(this.key));
      const ids = [];
      (function walk(n) { if (typeof n === 'string') ids.push(n); else n.kids.forEach(walk); })(t);
      const want = [...this.tiles.keys()].filter(id => !this.optional.includes(id));
      const ok = new Set(ids).size === ids.length && ids.every(id => this.tiles.has(id)) && want.every(id => ids.includes(id));
      return ok ? t : null;
    } catch { return null; }
  }
  save() { try { localStorage.setItem(this.key, JSON.stringify(this.tree)); } catch { /* private window etc. */ } }
  reset() {
    this.tree = Dock.copy(this.defaults);
    try { localStorage.removeItem(this.key); } catch { /* ignore */ }
    this.render();
  }

  // ---------------------------------------------------------------- drawing
  render() {
    this.phone = this.isPhone();
    this.root.classList.toggle('flat', this.phone);
    if (this.phone) {   // phones: one column, in the layout's order
      const order = [];
      (function walk(n) { if (typeof n === 'string') order.push(n); else n.kids.forEach(walk); })(this.tree);
      this.root.replaceChildren(...order.map(id => { const el = this.tiles.get(id); el.style.flex = ''; return el; }));
    } else {
      this.root.replaceChildren(this.build(this.tree, 1, []));
    }
    this.onResize();
    this.onLayout();
  }

  // path = where this node sits in the tree (kid numbers), so handles can find it
  build(node, weight, path) {
    if (typeof node === 'string') {
      const el = this.tiles.get(node);
      el.style.flex = `${weight} 1 0`;
      return el;
    }
    const box = document.createElement('div');
    box.className = `split ${node.dir}`;
    box.dataset.path = path.join('.');
    box.style.flex = `${weight} 1 0`;
    node.kids.forEach((kid, i) => {
      if (i) {
        const h = document.createElement('div');
        h.className = `gap ${node.dir}`;
        h.dataset.path = path.join('.');
        h.dataset.i = i;
        h.title = 'Drag to resize · double-click to even out';
        box.append(h);
      }
      box.append(this.build(kid, node.sizes[i], [...path, i]));
    });
    return box;
  }

  nodeAt(path) {
    let n = this.tree;
    for (const i of path) n = n.kids[i];
    return n;
  }

  // ---------------------------------------------------------------- moving tiles
  // Take a tile out of the tree; a split left with one part becomes that part.
  static remove(node, id) {
    if (typeof node === 'string') return node === id ? null : node;
    const kids = [], sizes = [];
    let freed = 0;   // the removed tile's space goes to its neighbour
    node.kids.forEach((k, i) => {
      const r = Dock.remove(k, id);
      if (!r) { freed = node.sizes[i]; if (sizes.length) sizes[sizes.length - 1] += freed, freed = 0; return; }
      kids.push(r); sizes.push(node.sizes[i] + freed); freed = 0;
    });
    if (!kids.length) return null;
    if (kids.length === 1) return kids[0];
    return { dir: node.dir, kids, sizes };
  }

  // Put tile id beside target ('left' | 'right' | 'top' | 'bottom'), taking share r of its space.
  static insert(node, target, id, edge, r = .5) {
    const dir = edge === 'left' || edge === 'right' ? 'row' : 'col';
    const before = edge === 'left' || edge === 'top';
    const pair = () => ({ dir, kids: before ? [id, target] : [target, id], sizes: before ? [r, 1 - r] : [1 - r, r] });
    if (node === target) return pair();
    if (typeof node === 'string') return node;
    const i = node.kids.indexOf(target);
    if (i < 0) return { ...node, kids: node.kids.map(k => Dock.insert(k, target, id, edge, r)) };
    const kids = [...node.kids], sizes = [...node.sizes];
    if (node.dir === dir) {   // same direction: just another part of this split
      const w = sizes[i];
      sizes[i] = w * (1 - r);
      kids.splice(before ? i : i + 1, 0, id);
      sizes.splice(before ? i : i + 1, 0, w * r);
    } else {
      kids[i] = pair();
    }
    return { dir: node.dir, kids, sizes };
  }

  static swap(node, a, b) {
    if (typeof node === 'string') return node === a ? b : node === b ? a : node;
    return { ...node, kids: node.kids.map(k => Dock.swap(k, a, b)) };
  }

  has(id) {
    let found = false;
    (function walk(n) { if (typeof n === 'string') found ||= n === id; else n.kids.forEach(walk); })(this.tree);
    return found;
  }

  // Show an optional tile beside target, or take it away again.
  add(id, target, zone, r = .38) { this.relayout(() => { this.tree = Dock.insert(Dock.remove(this.tree, id), target, id, zone, r); }); }
  drop(id) { this.relayout(() => { this.tree = Dock.remove(this.tree, id); }); }

  // Change the tree, draw it again, and give the "keep" tiles back the width
  // they had (by setting their share in the nearest side-by-side split).
  relayout(change) {
    const before = new Map();
    if (!this.phone) for (const id of this.keep) if (this.has(id)) before.set(id, this.tiles.get(id).getBoundingClientRect().width);
    change();
    this.render();
    if (!this.phone) {
      for (const [id, want] of before) if (this.has(id) && want > 0) this.keepWidth(id, want);
      this.onResize();
    }
    this.save();
  }

  keepWidth(id, want) {
    let kid = this.tiles.get(id), box = kid.parentElement;
    while (box && box !== this.root && !(box.classList.contains('split') && box.classList.contains('row'))) {
      kid = box; box = box.parentElement;
    }
    if (!box || box === this.root) return;          // nothing beside it: it has the whole width
    const node = this.nodeAt(box.dataset.path ? box.dataset.path.split('.').map(Number) : []);
    const kids = [...box.children].filter(el => !el.classList.contains('gap'));
    const k = kids.indexOf(kid);
    if (k < 0 || kids.length !== node.kids.length) return;
    const total = kids.reduce((a, el) => a + el.getBoundingClientRect().width, 0);

    want = Math.max(220, Math.min(total - 220 * (kids.length - 1), want));
    if (want <= 0 || want >= total) return;
    // borders and padding sit outside the shares, so aim, look, and aim again
    let aim = want;
    for (let n = 0; n < 4; n++) {
      const others = node.sizes.reduce((a, w, i) => (i === k ? a : a + w), 0);
      node.sizes[k] = others * aim / (total - aim);
      // shares summing to less than 1 would leave the row part empty
      const sum = node.sizes.reduce((x, y) => x + y, 0);
      node.sizes = node.sizes.map(x => x * kids.length / sum);
      kids.forEach((el, i) => { el.style.flexGrow = node.sizes[i]; });
      const got = kid.getBoundingClientRect().width;
      if (Math.abs(got - want) < 0.25) break;
      aim = Math.max(1, Math.min(total - 1, aim + want - got));
    }
  }

  move(id, target, zone) {
    if (id === target) return;
    if (!this.has(id)) return this.add(id, target, zone === 'middle' ? 'right' : zone);
    if (zone === 'middle') return this.relayout(() => { this.tree = Dock.swap(this.tree, id, target); });
    {
      // the moved tile keeps about its own size (between a fifth and half of the target's space)
      const row = zone === 'left' || zone === 'right';
      const a = this.tiles.get(id).getBoundingClientRect(), b = this.tiles.get(target).getBoundingClientRect();
      const r = Math.max(.2, Math.min(.5, row ? a.width / (a.width + b.width) : a.height / (a.height + b.height)));
      this.relayout(() => { this.tree = Dock.insert(Dock.remove(this.tree, id), target, id, zone, r); });
    }
  }

  // Which part of a tile the pointer is over.
  // (A tile that isn't shown yet can't swap: it only goes beside or above/below.)
  static zoneAt(rect, x, y, middle = true) {
    const fx = (x - rect.left) / rect.width, fy = (y - rect.top) / rect.height;
    if (middle && fx > .3 && fx < .7 && fy > .3 && fy < .7) return 'middle';
    const d = { left: fx, right: 1 - fx, top: fy, bottom: 1 - fy };
    return Object.keys(d).reduce((a, b) => (d[a] <= d[b] ? a : b));
  }

  // ---------------------------------------------------------------- pointer
  pointerDown(e) {
    if (this.phone || e.button !== 0) return;
    const grip = e.target.closest('.grip'), gap = e.target.closest('.gap');
    if (grip) this.dragTile(e, grip.closest('[data-tile]'));
    else if (gap) this.dragGap(e, gap);
  }

  dragTile(e, tile) {
    e.preventDefault();
    const id = tile.dataset.tile, shown = this.has(id);
    const ghost = document.createElement('div');
    ghost.className = 'dock-ghost';
    ghost.textContent = tile.dataset.label || id;
    const hint = document.createElement('div');
    hint.className = 'dock-hint';
    hint.hidden = true;
    document.body.append(ghost, hint);
    tile.classList.add('dragging');
    let drop = null;

    const move = ev => {
      ghost.style.left = ev.clientX + 12 + 'px';
      ghost.style.top = ev.clientY + 12 + 'px';
      const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('[data-tile]');
      drop = null;
      if (over && over !== tile && this.root.contains(over)) {
        let r = over.getBoundingClientRect(), zone = Dock.zoneAt(r, ev.clientX, ev.clientY, shown);
        // back into its home as a tab: over the home's tab row, or the middle of it
        const home = shown && this.tabHome[id];
        if (home && over.dataset.tile === home.into) {
          const row = over.querySelector(home.row), rr = row?.getBoundingClientRect();
          const onRow = rr && ev.clientY >= rr.top && ev.clientY <= rr.bottom;
          if (onRow || zone === 'middle') { zone = 'tab'; if (onRow) r = rr; }
        }
        drop = { target: over.dataset.tile, zone };
        const box = { left: r.left, top: r.top, width: r.width, height: r.height };
        if (zone === 'left' || zone === 'right') box.width = r.width / 2;
        if (zone === 'right') box.left = r.left + r.width / 2;
        if (zone === 'top' || zone === 'bottom') box.height = r.height / 2;
        if (zone === 'bottom') box.top = r.top + r.height / 2;
        Object.assign(hint.style, { left: box.left + 'px', top: box.top + 'px', width: box.width + 'px', height: box.height + 'px' });
        hint.dataset.zone = zone;
        ghost.textContent = zone === 'tab' ? `${tile.dataset.label || id} → back to a tab in ${over.dataset.label}`
          : `${tile.dataset.label || id} → ${zone === 'middle' ? 'swap with' : zone === 'left' ? 'left of' : zone === 'right' ? 'right of' : zone === 'top' ? 'above' : 'below'} ${over.dataset.label || drop.target}`;
      } else ghost.textContent = tile.dataset.label || id;
      hint.hidden = !drop;
    };
    const end = ev => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', end);
      removeEventListener('keydown', esc);
      ghost.remove(); hint.remove();
      tile.classList.remove('dragging');
      if (ev && drop?.zone === 'tab') { this.drop(id); this.onTabbed(id); }
      else if (ev && drop) this.move(id, drop.target, drop.zone);
    };
    const esc = ev => { if (ev.key === 'Escape') { drop = null; end(); } };
    addEventListener('pointermove', move);
    addEventListener('pointerup', end);
    addEventListener('keydown', esc);
    move(e);
  }

  dragGap(e, gap) {
    e.preventDefault();
    const node = this.nodeAt(gap.dataset.path ? gap.dataset.path.split('.').map(Number) : []);
    const i = +gap.dataset.i, row = node.dir === 'row';
    const a = gap.previousElementSibling, b = gap.nextElementSibling;
    const size = el => (row ? el.getBoundingClientRect().width : el.getBoundingClientRect().height);
    const sa = size(a), sb = size(b), wa = node.sizes[i - 1], wb = node.sizes[i];
    const min = row ? 220 : 70, start = row ? e.clientX : e.clientY;
    gap.classList.add('active');
    document.body.classList.add(row ? 'resizing-row' : 'resizing-col');
    const move = ev => {
      const d = Math.max(min - sa, Math.min(sb - min, (row ? ev.clientX : ev.clientY) - start));
      node.sizes[i - 1] = (wa + wb) * (sa + d) / (sa + sb);
      node.sizes[i] = (wa + wb) * (sb - d) / (sa + sb);
      a.style.flexGrow = node.sizes[i - 1];
      b.style.flexGrow = node.sizes[i];
      this.onResize();
    };
    const end = () => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', end);
      gap.classList.remove('active');
      document.body.classList.remove('resizing-row', 'resizing-col');
      this.save();
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', end);
  }

  evenOut(e) {
    const gap = e.target.closest('.gap');
    if (!gap) return;
    const node = this.nodeAt(gap.dataset.path ? gap.dataset.path.split('.').map(Number) : []);
    const i = +gap.dataset.i, w = (node.sizes[i - 1] + node.sizes[i]) / 2;
    node.sizes[i - 1] = node.sizes[i] = w;
    this.save();
    this.render();
  }
}
