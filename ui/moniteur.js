// Moniteur programme : affichage, repères de zone sûre, manipulation directe
// du clip sélectionné (déplacer, mettre à l'échelle, tourner) comme dans Premiere.
import { el, valueAt, localTime, liveSet, commitOps, fmtTC, parseTC } from './widgets.js';

const HANDLE = 6;

export class Moniteur {
  constructor(app) {
    this.app = app;
    this.screen = document.getElementById('screen');
    this.preview = document.getElementById('preview');
    this.ov = document.getElementById('overlay');
    this.zoom = 'fit';
    this.quality = 1;
    this.safe = false;
    this.drag = null;
    this.ov.addEventListener('pointerdown', e => this.down(e));
    this.ov.addEventListener('pointermove', e => this.move(e));
    this.ov.addEventListener('pointerup', () => this.up());
    this.ov.addEventListener('dblclick', e => this.dbl(e));
    new ResizeObserver(() => this.fit()).observe(document.getElementById('stage'));
    this.buildTransport();
  }

  buildTransport() {
    const app = this.app;
    const t = document.getElementById('transport');
    const fps = () => app.doc?.settings.fps || 30;
    this.tc = el('input', { class: 'tc', value: '00:00:00:00', title: 'Saisir un timecode puis Entrée' });
    this.tc.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Enter') { const v = parseTC(this.tc.value, fps()); if (v !== null) app.seek(v); this.tc.blur(); }
    });
    this.dur = el('span', { class: 'tcdur' }, '00:00:00:00');
    this.meter = el('canvas', { class: 'meter', width: 170, height: 28,
      title: 'Crêtes gauche / droite (dBFS) et volume perçu sur 3 s (LUFS). Cible YouTube : −14 LUFS, crêtes sous −1 dB.' });
    this.playBtn = el('button', { class: 'tp big', title: 'Lecture / pause (Espace)', onclick: () => app.toggle() }, '▶');
    const zoomSel = el('select', { title: 'Zoom du moniteur' },
      [['fit', 'Ajuster'], ['0.25', '25 %'], ['0.5', '50 %'], ['1', '100 %']].map(([v, l]) => el('option', { value: v }, l)));
    zoomSel.onchange = () => { this.zoom = zoomSel.value; this.fit(); };
    const qSel = el('select', { title: 'Qualité de l\'aperçu' },
      [['1', 'Plein'], ['0.5', '1/2'], ['0.25', '1/4']].map(([v, l]) => el('option', { value: v }, l)));
    qSel.onchange = () => { this.quality = +qSel.value; this.fit(); };
    const safeBtn = el('button', { class: 'tp', title: 'Zones de sécurité titre / action', onclick: () => {
      this.safe = !this.safe; safeBtn.classList.toggle('on', this.safe); this.drawOverlay(); } }, '⬚');
    t.append(
      el('div', { class: 'tp-left' }, this.tc),
      el('div', { class: 'tp-mid' },
        el('button', { class: 'tp', title: 'Début (Origine)', onclick: () => app.seek(0) }, '⏮'),
        el('button', { class: 'tp', title: 'Point de montage précédent (↑)', onclick: () => app.jumpEdit(-1) }, '⇤'),
        el('button', { class: 'tp', title: 'Image précédente (←)', onclick: () => app.seek(app.playhead - 1 / fps()) }, '◀|'),
        this.playBtn,
        el('button', { class: 'tp', title: 'Image suivante (→)', onclick: () => app.seek(app.playhead + 1 / fps()) }, '|▶'),
        el('button', { class: 'tp', title: 'Point de montage suivant (↓)', onclick: () => app.jumpEdit(1) }, '⇥'),
        el('button', { class: 'tp', title: 'Fin (Fin)', onclick: () => app.seek(app.duration()) }, '⏭')),
      el('div', { class: 'tp-right' }, this.meter, safeBtn, zoomSel, qSel, this.dur));
    this.drawMeter(null);
  }

  // Vu-mètres : deux barres de crête (−60 à 0 dBFS) et le LUFS court terme.
  drawMeter(m) {
    const cv = this.meter, d = devicePixelRatio || 1;
    if (cv.width !== 170 * d) { cv.width = 170 * d; cv.height = 28 * d; }
    const g = cv.getContext('2d');
    g.setTransform(d, 0, 0, d, 0, 0);
    g.clearRect(0, 0, 170, 28);
    const W = 108, x0 = 2, X = v => x0 + Math.max(0, Math.min(1, (v + 60) / 60)) * W;
    [[3, 0], [15, 1]].forEach(([y, i]) => {
      g.fillStyle = '#111215'; g.fillRect(x0, y, W, 9);
      if (m) {
        const v = m.peaks[i];
        const grad = g.createLinearGradient(x0, 0, x0 + W, 0);
        grad.addColorStop(0, '#2f9e62'); grad.addColorStop(0.7, '#2f9e62'); grad.addColorStop(0.9, '#d9b13b'); grad.addColorStop(1, '#e0484f');
        g.fillStyle = grad; g.fillRect(x0, y, X(v) - x0, 9);
        g.fillStyle = m.hold[i] > -1 ? '#ff4d5e' : '#e6e8ee'; g.fillRect(X(m.hold[i]) - 1, y, 2, 9);
      }
    });
    g.fillStyle = 'rgba(255,255,255,.35)';
    for (const v of [-40, -20, -14, -6, -1]) g.fillRect(X(v), 13, 1, 2);
    const l = m?.lufs;
    const txt = m && Number.isFinite(l) ? `${l.toFixed(1).replace('.', ',')}` : '—';
    g.font = '600 12px system-ui'; g.textBaseline = 'middle'; g.textAlign = 'right';
    g.fillStyle = !m || !Number.isFinite(l) ? '#6c717d' : l > -11 ? '#ff9a6b' : l >= -17 ? '#7ee0a5' : '#b9bdc6';
    g.fillText(txt, 152, 11);
    g.font = '10px system-ui'; g.fillStyle = '#8b909c';
    g.fillText('LUFS', 166, 23);
    g.textAlign = 'left';
  }

  update() {
    const app = this.app, fps = app.doc.settings.fps;
    if (document.activeElement !== this.tc) this.tc.value = fmtTC(app.playhead, fps);
    this.dur.textContent = fmtTC(app.duration(), fps);
    this.playBtn.textContent = app.playing ? '❚❚' : '▶';
  }

  fit() {
    const app = this.app;
    if (!app.doc) return;
    const st = document.getElementById('stage').getBoundingClientRect();
    const W = app.doc.settings.width, H = app.doc.settings.height;
    const s = this.zoom === 'fit' ? Math.min((st.width - 16) / W, (st.height - 16) / H) : +this.zoom;
    const cw = Math.max(16, Math.floor(W * s)), ch = Math.max(16, Math.floor(H * s));
    this.screen.style.width = `${cw}px`; this.screen.style.height = `${ch}px`;
    const internal = Math.min(1, 1600 / W) * this.quality;
    const rw = Math.max(16, Math.round(W * internal)), rh = Math.max(16, Math.round(H * internal));
    if (this.preview.width !== rw || this.preview.height !== rh) { this.preview.width = rw; this.preview.height = rh; }
    const d = devicePixelRatio || 1;
    this.ov.width = cw * d; this.ov.height = ch * d;
    this.cssScale = cw / W;
    app.dirty = true;
    this.drawOverlay();
  }

  // ------------------------------------------------------------ géométrie d'un clip
  visible(c) {
    const app = this.app, doc = app.doc, t = app.playhead;
    if (!(c.start <= t && t < c.start + c.duration)) return false;
    const tr = doc.tracks.find(x => x.id === c.track);
    if (!tr || tr.kind !== 'video' || tr.hidden) return false;
    return c.type !== 'media' || doc.media[c.media]?.kind !== 'audio';
  }

  // Rectangle local (px projet, centré sur le cadre) avant transformation.
  localRect(c) {
    const doc = this.app.doc, W = doc.settings.width, H = doc.settings.height;
    if (c.type === 'text') {
      const b = this.app.sources.textBounds(c.id);
      if (b) return [b.x - W / 2, b.y - H / 2, b.w, b.h];
      return [-W / 4, -60, W / 2, 120];
    }
    if (c.type === 'solid') return [-W / 2, -H / 2, W, H];
    const m = doc.media[c.media];
    const [sw, sh] = this.app.sources.videoSize(c.id) || [m.width || W, m.height || H];
    const sa = sw / sh, fa = W / H;
    const dw = sa > fa ? W : H * sa, dh = sa > fa ? W / sa : H;
    // Image ou vidéo transparente : seulement la partie visible (un PNG plein cadre ne doit
    // pas s'encadrer, ni se sélectionner, sur toute l'image).
    const carte = this.app.sources.alphaMap(doc, c);
    if (carte?.boite) {
      const k = dw / carte.sw, [x0, y0, x1, y1] = carte.boite;
      return [-dw / 2 + x0 * k, -dh / 2 + y0 * k, (x1 - x0) * k, (y1 - y0) * k];
    }
    return [-dw / 2, -dh / 2, dw, dh];
  }

  // Le point (px écran du moniteur) tombe-t-il sur un pixel visible du calque ?
  touche(c, pt) {
    const doc = this.app.doc, W = doc.settings.width, H = doc.settings.height;
    const lt = localTime(this.app, c);
    if (valueAt(c, 'opacity', lt) < 0.05) return false;
    const carte = this.app.sources.alphaMap(doc, c);
    if (!carte) return true;                       // vidéo opaque, texte, aplat : son rectangle suffit
    if (!carte.boite) return false;                // entièrement transparent à cet instant
    const p = this.props(c), a = -p.rotation * Math.PI / 180;
    const X = pt[0] / this.cssScale - (W / 2 + p.x), Y = pt[1] / this.cssScale - (H / 2 + p.y);
    const u = (X * Math.cos(a) - Y * Math.sin(a)) / p.scale, v = (X * Math.sin(a) + Y * Math.cos(a)) / p.scale;
    const sa = carte.sw / carte.sh, fa = W / H;
    const dw = sa > fa ? W : H * sa, dh = sa > fa ? W / sa : H;
    // Petite tolérance autour du pointeur : un trait fin de 3 px reste cliquable.
    const cx = (u + dw / 2) / dw * carte.w, cy = (v + dh / 2) / dh * carte.h;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      const x = Math.round(cx + dx), y = Math.round(cy + dy);
      if (x >= 0 && y >= 0 && x < carte.w && y < carte.h && carte.a[y * carte.w + x] > 24) return true;
    }
    return false;
  }

  props(c) {
    const lt = localTime(this.app, c);
    return Object.fromEntries(['x', 'y', 'scale', 'rotation'].map(k => [k, valueAt(c, k, lt)]));
  }

  corners(c, p = this.props(c)) {
    const doc = this.app.doc, W = doc.settings.width, H = doc.settings.height;
    const [x, y, w, h] = this.localRect(c);
    const a = p.rotation * Math.PI / 180, cs = Math.cos(a), sn = Math.sin(a);
    const cx = W / 2 + p.x, cy = H / 2 + p.y;
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]].map(([u, v]) => {
      u *= p.scale; v *= p.scale;
      return [(cx + u * cs - v * sn) * this.cssScale, (cy + u * sn + v * cs) * this.cssScale];
    });
  }

  center(c, p = this.props(c)) {
    const doc = this.app.doc;
    const [x, y, w, h] = this.localRect(c);
    const a = p.rotation * Math.PI / 180, u = (x + w / 2) * p.scale, v = (y + h / 2) * p.scale;
    return [(doc.settings.width / 2 + p.x + u * Math.cos(a) - v * Math.sin(a)) * this.cssScale,
            (doc.settings.height / 2 + p.y + u * Math.sin(a) + v * Math.cos(a)) * this.cssScale];
  }

  inside(pt, poly) {
    let ins = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) ins = !ins;
    }
    return ins;
  }

  // ------------------------------------------------------------ dessin
  drawOverlay() {
    const app = this.app;
    if (!app.doc || !this.cssScale) return;
    const g = this.ov.getContext('2d'), d = devicePixelRatio || 1;
    g.setTransform(d, 0, 0, d, 0, 0);
    const cw = this.ov.width / d, ch = this.ov.height / d;
    g.clearRect(0, 0, cw, ch);
    if (this.safe) {
      g.strokeStyle = 'rgba(255,255,255,.35)'; g.setLineDash([4, 4]); g.lineWidth = 1;
      for (const f of [0.9, 0.8]) g.strokeRect(cw * (1 - f) / 2, ch * (1 - f) / 2, cw * f, ch * f);
      g.beginPath(); g.moveTo(cw / 2 - 8, ch / 2); g.lineTo(cw / 2 + 8, ch / 2); g.moveTo(cw / 2, ch / 2 - 8); g.lineTo(cw / 2, ch / 2 + 8); g.stroke();
      g.setLineDash([]);
    }
    for (const id of app.selection) {
      const c = app.doc.clips[id];
      if (!c || !this.visible(c)) continue;
      const pts = this.corners(c);
      g.strokeStyle = '#4ea3ff'; g.lineWidth = 1.5;
      g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath(); g.stroke();
      g.fillStyle = '#fff';
      for (const [x, y] of pts) { g.fillRect(x - HANDLE / 2, y - HANDLE / 2, HANDLE, HANDLE); g.strokeRect(x - HANDLE / 2, y - HANDLE / 2, HANDLE, HANDLE); }
      const r = this.rotHandle(pts);
      g.beginPath(); g.moveTo((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2); g.lineTo(r[0], r[1]); g.stroke();
      g.beginPath(); g.arc(r[0], r[1], 5, 0, Math.PI * 2); g.fill(); g.stroke();
      const [cx, cy] = this.center(c);
      g.beginPath(); g.arc(cx, cy, 4, 0, Math.PI * 2); g.stroke();
    }
  }

  rotHandle(pts) {
    const mx = (pts[0][0] + pts[1][0]) / 2, my = (pts[0][1] + pts[1][1]) / 2;
    const vx = pts[0][0] - pts[3][0], vy = pts[0][1] - pts[3][1], n = Math.hypot(vx, vy) || 1;
    return [mx + vx / n * 26, my + vy / n * 26];
  }

  // ------------------------------------------------------------ interaction
  pos(e) {
    const r = this.ov.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  // Tous les calques visibles sous le point, du dessus vers le dessous.
  clipsAt(pt) {
    const doc = this.app.doc, out = [];
    const order = doc.tracks.filter(t => t.kind === 'video').map(t => t.id).reverse();
    for (const tid of order) {
      const tr = doc.tracks.find(t => t.id === tid);
      if (tr.locked) continue;
      for (const c of Object.values(doc.clips)) {
        if (c.track === tid && this.visible(c) && this.inside(pt, this.corners(c)) && this.touche(c, pt)) out.push(c);
      }
    }
    return out;
  }

  topClipAt(pt) { return this.clipsAt(pt)[0] || null; }

  down(e) {
    const app = this.app;
    if (!app.doc) return;
    const pt = this.pos(e);
    // Alt + clic : l'élément juste EN DESSOUS de la sélection à cet endroit (pour atteindre un
    // plan vidéo recouvert par un voile, un fond sous un graphisme…). Chaque Alt + clic descend.
    if (e.altKey) {
      const pile = this.clipsAt(pt);
      if (!pile.length) return;
      const i = pile.findIndex(x => app.selection.includes(x.id));
      const c2 = pile[(i + 1) % pile.length];
      app.select([c2.id]);
      this.choixAlt = c2.id;           // reste déplaçable tant qu'on clique sur lui
      app.timeline.reveler(c2.id);
      app.toast?.(`${c2.name}  ·  ${pile.indexOf(c2) + 1}/${pile.length} sous le pointeur (Alt + clic : suivant)`);
      return;
    }
    let c = null, mode = null;
    // Poignées de la sélection d'abord (échelle, rotation).
    for (const id of app.selection) {
      const cc = app.doc.clips[id];
      if (!cc || !this.visible(cc)) continue;
      const pts = this.corners(cc);
      const r = this.rotHandle(pts);
      if (Math.hypot(pt[0] - r[0], pt[1] - r[1]) < 8) { c = cc; mode = 'rotate'; break; }
      if (pts.some(([x, y]) => Math.hypot(pt[0] - x, pt[1] - y) < 8)) { c = cc; mode = 'scale'; break; }
    }
    // Sinon : l'élément visible du DESSUS sous le pointeur (un calque plein cadre sélectionné ne
    // doit pas capter tous les clics) — sauf celui choisi par Alt + clic, qu'on peut déplacer.
    if (!c) {
      const pile = this.clipsAt(pt);
      const alt = pile.find(x => x.id === this.choixAlt && app.selection.includes(x.id));
      const dessus = alt || pile[0];
      if (dessus && app.selection.includes(dessus.id)) { c = dessus; mode = 'move'; }
    }
    if (!c) {
      this.choixAlt = null;
      c = this.topClipAt(pt);
      app.select(c ? [c.id] : []);
      if (!c) return;
      app.timeline.reveler(c.id);      // le clip choisi dans l'image est montré dans la timeline
      mode = 'move';
    }
    if (app.doc.tracks.find(t => t.id === c.track)?.locked) return;
    this.ov.setPointerCapture(e.pointerId);
    app.busy = true;
    this.drag = { id: c.id, mode, pt, p0: this.props(c), center: this.center(c), moved: false };
  }

  move(e) {
    const d = this.drag;
    if (!d) {
      const app = this.app;
      if (!app.doc) return;
      const pt = this.pos(e);
      let cur = 'default';
      for (const id of app.selection) {
        const c = app.doc.clips[id];
        if (!c || !this.visible(c)) continue;
        const pts = this.corners(c), r = this.rotHandle(pts);
        if (Math.hypot(pt[0] - r[0], pt[1] - r[1]) < 8) cur = 'grab';
        else if (pts.some(([x, y]) => Math.hypot(pt[0] - x, pt[1] - y) < 8)) cur = 'nwse-resize';
        else if (this.inside(pt, pts)) cur = 'move';
      }
      this.ov.style.cursor = cur;
      return;
    }
    const app = this.app, c = app.doc.clips[d.id];
    if (!c) return;
    const pt = this.pos(e), k = 1 / this.cssScale;
    d.moved = true;
    if (d.mode === 'move') {
      let x = d.p0.x + (pt[0] - d.pt[0]) * k, y = d.p0.y + (pt[1] - d.pt[1]) * k;
      if (e.shiftKey) { if (Math.abs(x - d.p0.x) > Math.abs(y - d.p0.y)) y = d.p0.y; else x = d.p0.x; }
      if (Math.abs(x) < 6 * k) x = 0;   // magnétisme au centre
      if (Math.abs(y) < 6 * k) y = 0;
      liveSet(app, c, 'x', Math.round(x)); liveSet(app, c, 'y', Math.round(y));
      d.out = { x: Math.round(x), y: Math.round(y) };
    } else if (d.mode === 'scale') {
      const r0 = Math.hypot(d.pt[0] - d.center[0], d.pt[1] - d.center[1]) || 1;
      const r1 = Math.hypot(pt[0] - d.center[0], pt[1] - d.center[1]);
      const s = Math.max(0.01, +(d.p0.scale * r1 / r0).toFixed(4));
      liveSet(app, c, 'scale', s); d.out = { scale: s };
    } else {
      const a0 = Math.atan2(d.pt[1] - d.center[1], d.pt[0] - d.center[0]);
      const a1 = Math.atan2(pt[1] - d.center[1], pt[0] - d.center[0]);
      let r = d.p0.rotation + (a1 - a0) * 180 / Math.PI;
      if (e.shiftKey) r = Math.round(r / 15) * 15;
      r = +r.toFixed(2);
      liveSet(app, c, 'rotation', r); d.out = { rotation: r };
    }
    app.inspector.tick();
    this.drawOverlay();
  }

  up() {
    const d = this.drag, app = this.app;
    this.drag = null;
    app.busy = false;
    if (!d || !d.moved || !d.out) return;
    const c = app.doc.clips[d.id];
    const ops = Object.entries(d.out).flatMap(([k, v]) => commitOps(app, c, k, v));
    const label = { move: 'Déplacer dans le cadre', scale: 'Mettre à l\'échelle', rotate: 'Tourner' }[d.mode];
    app.send(ops, label);
  }

  dbl(e) {
    const c = this.topClipAt(this.pos(e));
    if (c?.type === 'text') { this.app.select([c.id]); setTimeout(() => document.querySelector('.textcontent')?.focus(), 50); }
  }
}
