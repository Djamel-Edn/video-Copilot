// Timeline : outils (sélection, lame, texte), en-têtes de piste (masquer, muet,
// verrou), vignettes, formes d'onde, images clés, magnétisme, glisser-déposer.
import { contextMenu } from './widgets.js';

const HEAD = 28, ROW = 58, LABEL = 150, EDGE = 7, SNAP = 8;
const COLORS = { video: '#2f5f9e', image: '#4a6f9e', audio: '#2c7a55', text: '#9c6a2a', solid: '#6a4f9e' };

export class Timeline {
  constructor(canvas, app) {
    this.cv = canvas;
    this.app = app;
    this.pps = 60;
    this.scroll = 0;
    this.vscroll = 0;
    this.drag = null;
    this.flashes = new Map();
    this.hoverX = null;
    this.dropHint = null;
    canvas.addEventListener('pointerdown', e => this.down(e));
    canvas.addEventListener('pointermove', e => this.move(e));
    canvas.addEventListener('pointerup', e => this.up(e));
    canvas.addEventListener('pointerleave', () => { this.hoverX = null; this.dirty = true; });
    canvas.addEventListener('wheel', e => this.wheel(e), { passive: false });
    canvas.addEventListener('dblclick', e => this.dbl(e));
    canvas.addEventListener('contextmenu', e => this.context(e));
    canvas.addEventListener('dragover', e => this.dragover(e));
    canvas.addEventListener('dragleave', () => { this.dropHint = null; this.dirty = true; });
    canvas.addEventListener('drop', e => this.drop(e));
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  resize() {
    const r = this.cv.getBoundingClientRect(), d = devicePixelRatio || 1;
    this.cv.width = Math.max(1, r.width * d); this.cv.height = Math.max(1, r.height * d);
    this.dirty = true;
  }

  get width() { return this.cv.width / (devicePixelRatio || 1); }

  rows() {
    const doc = this.app.doc;
    if (!doc) return [];
    return [...doc.tracks.filter(t => t.kind === 'video').reverse(), ...doc.tracks.filter(t => t.kind === 'audio')];
  }

  rowY(i) { return HEAD + i * ROW - this.vscroll; }
  x(t) { return LABEL + (t - this.scroll) * this.pps; }
  t(x) { return (x - LABEL) / this.pps + this.scroll; }
  flash(id) { this.flashes.set(id, performance.now()); this.dirty = true; }
  kind(c) { return this.app.clipKind(c); }

  setZoom(pps, anchorX = LABEL) {
    const tAt = this.t(anchorX);
    this.pps = Math.min(1200, Math.max(2, pps));
    this.scroll = Math.max(0, tAt - (anchorX - LABEL) / this.pps);
    this.dirty = true;
    this.app.onZoom?.(this.pps);
  }

  fitAll() {
    const d = Math.max(5, this.app.duration());
    this.pps = Math.max(2, (this.width - LABEL - 30) / d);
    this.scroll = 0; this.dirty = true;
    this.app.onZoom?.(this.pps);
  }

  view(c) {
    return this.drag && this.drag.ids?.includes(c.id) && this.drag.preview?.[c.id] ? { ...c, ...this.drag.preview[c.id] } : c;
  }

  rowAt(py) {
    const rows = this.rows(), i = Math.floor((py - HEAD + this.vscroll) / ROW);
    return i >= 0 && i < rows.length ? rows[i] : null;
  }

  // Rectangles des transitions actives : centrés sur la coupe, de la durée de la transition.
  transitions() {
    const sp = this.app.spans(), out = [];
    for (const c of Object.values(this.app.doc.clips)) {
      if (!c.trans_in || !sp[c.id] || sp[c.id][0] >= c.start - 1e-6) continue;
      out.push({ clip: c, a: c.start - c.trans_in.duration / 2, b: c.start + c.trans_in.duration / 2 });
    }
    return out;
  }

  hit(px, py) {
    const track = this.rowAt(py);
    if (!track) return {};
    if (px < LABEL) return { track, header: true };
    const ri = this.rows().indexOf(track), y0 = this.rowY(ri);
    if (py - y0 < 22) {
      for (const tr of this.transitions()) {
        if (tr.clip.track === track.id && px >= this.x(tr.a) - 2 && px <= this.x(tr.b) + 2) return { track, clip: tr.clip, zone: 'transition' };
      }
    }
    for (const c of Object.values(this.app.doc.clips)) {
      if (c.track !== track.id) continue;
      const x0 = this.x(c.start), x1 = this.x(c.start + c.duration);
      if (px >= x0 - 2 && px <= x1 + 2) {
        const zone = px - x0 < EDGE && x1 - x0 > 3 * EDGE ? 'left' : x1 - px < EDGE && x1 - x0 > 3 * EDGE ? 'right' : 'body';
        return { track, clip: c, zone };
      }
    }
    return { track };
  }

  headerButtons(track, y) {
    const b = [];
    let x = LABEL - 26;
    for (const k of track.kind === 'video' ? ['locked', 'muted', 'hidden'] : ['locked', 'muted']) {
      b.push({ k, x, y: y + ROW / 2 - 11, w: 22, h: 22 });
      x -= 25;
    }
    return b;
  }

  pos(e) {
    const r = this.cv.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  snapT(t, ignore = []) {
    if (!this.app.snap) return t;
    const pts = [0, this.app.playhead];
    for (const c of Object.values(this.app.doc.clips)) {
      if (ignore.includes(c.id)) continue;
      pts.push(c.start, c.start + c.duration);
    }
    let best = t, bd = SNAP / this.pps;
    for (const p of pts) if (Math.abs(p - t) < bd) { bd = Math.abs(p - t); best = p; }
    return best;
  }

  locked(trackId) { return !!this.app.doc.tracks.find(t => t.id === trackId)?.locked; }

  // ------------------------------------------------------------ souris
  down(e) {
    const app = this.app;
    if (!app.doc || e.button === 2) return;
    this.cv.setPointerCapture(e.pointerId);
    const [px, py] = this.pos(e);
    if (py < HEAD) { this.drag = { mode: 'scrub' }; app.seek(Math.max(0, this.t(px))); return; }
    const h = this.hit(px, py);
    if (h.header) {
      const i = this.rows().indexOf(h.track);
      const btn = this.headerButtons(h.track, this.rowY(i)).find(b => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h);
      if (btn) app.send([{ op: 'update_track', track: h.track.id, [btn.k]: !h.track[btn.k] }],
                        `${{ locked: 'Verrou', muted: 'Muet', hidden: 'Masquer' }[btn.k]} ${h.track.name}`);
      return;
    }
    const tAt = Math.max(0, this.t(px));
    if (app.tool === 'razor') {
      if (h.clip && !this.locked(h.clip.track)) {
        const at = this.snapT(tAt);
        if (at > h.clip.start + 1e-3 && at < h.clip.start + h.clip.duration - 1e-3) {
          const ids = app.oneByLink(e.shiftKey ? Object.values(app.doc.clips).filter(c => c.start < at && at < c.start + c.duration && !this.locked(c.track)).map(c => c.id) : [h.clip.id]);
          app.send(ids.map(id => ({ op: 'split_clip', clip: id, at })), 'Lame');
        }
      }
      return;
    }
    if (app.tool === 'text') {
      if (h.track?.kind === 'video' && !h.clip) app.addText(tAt, h.track.id).then(() => app.setTool('select'));
      return;
    }
    if (!h.clip) {
      if (!e.shiftKey) app.select([]);
      this.drag = { mode: 'scrub' };
      app.seek(tAt);
      return;
    }
    const c = h.clip;
    const group = e.altKey ? [c.id] : app.group(c.id);
    if (e.shiftKey || e.ctrlKey) {
      const s = new Set(app.selection);
      const on = s.has(c.id);
      for (const id of group) on ? s.delete(id) : s.add(id);
      app.select([...s]);
      return;
    }
    if (h.zone === 'transition') { app.select([c.id]); this.app.inspector.openTransition = true; return; }
    if (!app.selection.includes(c.id)) app.select(group);
    if (this.locked(c.track)) return;
    // Rogner un clip lié rogne aussi ses partenaires (sauf Alt) — seulement ceux dont le bord
    // tombe au même endroit (à une image près) : image + son suivent ensemble, mais dans une
    // scène liée (rendu, voix, étiquettes), l'étiquette du milieu ne bouge pas.
    const tol = 1 / (app.doc.settings.fps || 30) + 1e-6;
    const bord = x => h.zone === 'left' ? x.start : x.start + x.duration;
    const aligne = id => { const o = app.doc.clips[id]; return o && Math.abs(bord(o) - bord(c)) <= tol; };
    const ids = (h.zone === 'body' ? app.selection : group.filter(aligne)).filter(id => !this.locked(app.doc.clips[id]?.track));
    this.drag = { mode: h.zone, id: c.id, ids, x0: px, y0: py, origs: Object.fromEntries(ids.map(id => [id, { ...app.doc.clips[id] }])), preview: null };
    app.busy = true;
  }

  move(e) {
    const [px, py] = this.pos(e);
    const app = this.app;
    this.hoverX = px > LABEL ? px : null;
    if (app.tool !== 'select') this.dirty = true;
    if (!this.drag) {
      const h = app.doc ? this.hit(px, py) : {};
      this.cv.style.cursor = app.tool === 'razor' ? 'crosshair' : app.tool === 'text' ? 'text'
        : h.clip ? (h.zone === 'body' ? 'grab' : 'ew-resize') : 'default';
      return;
    }
    const d = this.drag;
    if (d.mode === 'scrub') { app.seek(Math.max(0, this.t(px))); return; }
    const doc = app.doc, dt = (px - d.x0) / this.pps, fps = doc.settings.fps, minDur = 1 / fps;
    const o = d.origs[d.id];
    const m = o.type === 'media' ? doc.media[o.media] : null;
    const maxSrc = m && m.kind !== 'image' ? m.duration : Infinity;
    d.preview = {};
    if (d.mode === 'body') {
      let start = Math.max(0, o.start + dt);
      const s1 = this.snapT(start, d.ids), s2 = this.snapT(start + o.duration, d.ids) - o.duration;
      start = Math.abs(s1 - start) <= Math.abs(s2 - start) ? s1 : s2;
      const delta = Math.max(start - o.start, -Math.min(...d.ids.map(id => d.origs[id].start)));
      const rows = this.rows(), r0 = this.rowAt(d.y0), r1 = this.rowAt(py);
      const shift = r0 && r1 ? rows.indexOf(r1) - rows.indexOf(r0) : 0;
      for (const id of d.ids) {
        const oc = d.origs[id];
        let track = oc.track;
        if (shift) {
          const ni = rows.findIndex(r => r.id === oc.track) + shift;
          const nr = rows[ni];
          if (nr && (nr.kind === 'audio') === (this.kind(oc) === 'audio') && !nr.locked) track = nr.id;
        }
        d.preview[id] = { start: oc.start + delta, track };
      }
    } else if (d.mode === 'left') {
      let delta = this.snapT(o.start + dt, [o.id]) - o.start;
      delta = Math.max(delta, -o.start, -o.in / o.speed);
      delta = Math.min(delta, o.duration - minDur);
      for (const id of d.ids) {
        const oc = d.origs[id];
        d.preview[id] = { start: oc.start + delta, in: oc.in + delta * oc.speed, duration: oc.duration - delta };
      }
    } else if (d.mode === 'right') {
      let dur = Math.max(minDur, this.snapT(o.start + o.duration + dt, [o.id]) - o.start);
      dur = Math.min(dur, (maxSrc - o.in) / o.speed);
      for (const id of d.ids) d.preview[id] = { duration: d.origs[id].duration + (dur - o.duration) };
    }
    this.dirty = true;
  }

  up(e) {
    const d = this.drag, app = this.app;
    this.drag = null;
    app.busy = false;
    this.dirty = true;
    if (!d || d.mode === 'scrub' || !d.preview) return;
    const ops = [];
    const dup = e.altKey && d.mode === 'body';
    for (const [id, p] of Object.entries(d.preview)) {
      const o = d.origs[id];
      const ch = Object.fromEntries(Object.entries(p).filter(([k, v]) => v !== o[k]));
      if (!Object.keys(ch).length) continue;
      if (dup) ops.push({ op: 'duplicate_clip', clip: id, start: p.start, track: p.track, overwrite: true });
      else ops.push({ op: 'update_clip', clip: id, ...ch, overwrite: e.ctrlKey });
    }
    if (ops.length) {
      const label = dup ? 'Dupliquer (Alt + glisser)' : d.mode === 'body' ? `Déplacer ${ops.length > 1 ? ops.length + ' clips' : d.origs[d.id].name}` : `Rogner ${d.origs[d.id].name}`;
      app.send(ops, label);
    }
  }

  dbl(e) {
    const [px, py] = this.pos(e);
    const h = this.hit(px, py);
    if (h.header) {
      const name = prompt('Nom de la piste', h.track.name);
      if (name) this.app.send([{ op: 'update_track', track: h.track.id, name }], 'Renommer la piste');
    } else if (h.clip?.type === 'text') {
      this.app.select([h.clip.id]);
      setTimeout(() => document.querySelector('.textcontent')?.focus(), 50);
    }
  }

  context(e) {
    e.preventDefault();
    const app = this.app, [px, py] = this.pos(e), h = this.hit(px, py);
    if (!h.clip) return;
    if (!app.selection.includes(h.clip.id)) app.select(app.group(h.clip.id));
    const c = h.clip;
    const group = app.group(c.id);
    const m = c.type === 'media' ? app.doc.media[c.media] : null;
    const linkItems = [];
    if (app.clipKind(c) === 'video' && m?.has_audio && !c.audio_muted) {
      linkItems.push({ label: 'Séparer le son', action: () => app.separateAudio(c.id) });
    }
    if (c.link) linkItems.push({ label: group.length > 2 ? `Délier le groupe (${group.length} clips)` : 'Délier image et son', action: () => app.send([{ op: 'unlink_clips', clips: group }], 'Délier') });
    if (app.selection.length >= 2 && !c.link) {
      linkItems.push({ label: 'Lier la sélection', action: () => app.send([{ op: 'link_clips', clips: app.selection }], 'Lier') });
    }
    if (app.clipKind(c) === 'audio') {
      const ids = app.selection.filter(id => app.doc.clips[id] && app.clipKind(app.doc.clips[id]) === 'audio');
      linkItems.push({ label: 'Baisser sous la voix', action: () => app.duckMusic(ids.length ? ids : [c.id]) });
    }
    if (c.audio_muted) {
      linkItems.push({ label: 'Réactiver le son de la vidéo', action: () => app.send([{ op: 'update_clip', clip: c.id, audio_muted: false }], 'Réactiver le son') });
    }
    contextMenu(e.clientX, e.clientY, [
      { label: 'Scinder ici', action: () => app.send([{ op: 'split_clip', clip: c.id, at: this.snapT(this.t(px)) }], 'Scinder') },
      { label: 'Dupliquer à la suite', action: () => app.send(group.map(id => ({ op: 'duplicate_clip', clip: id, overwrite: true })), 'Dupliquer') },
      { label: 'Copier (Ctrl+C)', action: () => app.copy() },
      ...(app.neighbor(c, -1) && app.clipKind(c) !== 'audio' ? [{ label: c.trans_in ? 'Retirer la transition d\'entrée' : 'Fondu enchaîné avec le clip précédent',
        action: () => c.trans_in ? app.send([{ op: 'set_transition', clip: c.id, type: null }], 'Retirer la transition')
                                  : app.applyTransition('fondu_enchaine', c.id) }] : []),
      ...(linkItems.length ? ['-', ...linkItems] : []),
      '-',
      { label: 'Vitesse…', action: () => {
        const v = parseFloat(prompt('Vitesse en % (100 = normale)', Math.round(c.speed * 100)));
        if (v > 0) app.send(group.map(id => { const g = app.doc.clips[id];
          return { op: 'update_clip', clip: id, speed: v / 100, duration: g.duration * g.speed / (v / 100) }; }), 'Vitesse');
      } },
      { label: 'Renommer…', action: () => { const n = prompt('Nom du clip', c.name); if (n) app.send([{ op: 'update_clip', clip: c.id, name: n }], 'Renommer'); } },
      '-',
      { label: 'Supprimer (Suppr)', action: () => app.deleteSelection(false) },
      { label: 'Supprimer et resserrer (Maj+Suppr)', action: () => app.deleteSelection(true) },
    ]);
  }

  wheel(e) {
    e.preventDefault();
    const [px] = this.pos(e);
    if (e.ctrlKey || e.altKey) this.setZoom(this.pps * (e.deltaY < 0 ? 1.15 : 1 / 1.15), Math.max(LABEL, px));
    else if (e.shiftKey) {
      const maxV = Math.max(0, HEAD + this.rows().length * ROW - this.cv.height / (devicePixelRatio || 1) + 20);
      this.vscroll = Math.min(maxV, Math.max(0, this.vscroll + e.deltaY));
    } else this.scroll = Math.max(0, this.scroll + (e.deltaY + e.deltaX) / this.pps);
    this.dirty = true;
  }

  // ------------------------------------------------------------ glisser-déposer depuis les panneaux
  dragover(e) {
    const types = [...e.dataTransfer.types];
    if (!types.some(t => t.startsWith('application/x-'))) return;
    e.preventDefault();
    const [px, py] = this.pos(e);
    this.dropHint = { t: Math.max(0, this.snapT(this.t(px))), track: this.rowAt(py)?.id };
    this.dirty = true;
  }

  drop(e) {
    const app = this.app, dt = e.dataTransfer;
    const [px, py] = this.pos(e);
    this.dropHint = null; this.dirty = true;
    const t = Math.max(0, this.snapT(this.t(px)));
    const h = this.hit(px, py);
    if (dt.types.includes('application/x-media')) {
      e.preventDefault(); e.stopPropagation();
      app.addMedia(dt.getData('application/x-media'), t, h.track?.id);
    } else if (dt.types.includes('application/x-effect')) {
      e.preventDefault(); e.stopPropagation();
      const name = dt.getData('application/x-effect');
      if (!h.clip) app.toast('Dépose l\'effet sur un clip.', true);
      else if (app.defs()[name]?.kind === 'transition') {
        // Moitié droite du clip : transition vers le clip suivant ; moitié gauche : depuis le précédent.
        app.applyTransition(name, h.clip.id, px > (this.x(h.clip.start) + this.x(h.clip.start + h.clip.duration)) / 2);
      } else app.applyEffect(name, [h.clip.id]);
    } else if (dt.types.includes('application/x-textpreset')) {
      e.preventDefault(); e.stopPropagation();
      app.addTextPreset(dt.getData('application/x-textpreset'), t, h.track?.kind === 'video' ? h.track.id : null);
    }
  }

  // ------------------------------------------------------------ dessin
  draw() {
    const now = performance.now();
    const flashing = [...this.flashes.values()].some(t0 => now - t0 < 1400);
    if (!this.dirty && !flashing && !this.app.playing) return;
    this.dirty = false;
    const g = this.cv.getContext('2d'), d = devicePixelRatio || 1;
    const W = this.cv.width / d, H = this.cv.height / d;
    g.setTransform(d, 0, 0, d, 0, 0);
    g.fillStyle = '#18191c'; g.fillRect(0, 0, W, H);
    const app = this.app, doc = app.doc;
    if (!doc) return;
    const rows = this.rows(), fps = doc.settings.fps;
    // pistes
    rows.forEach((tr, i) => {
      const y = this.rowY(i);
      if (y + ROW < HEAD || y > H) return;
      g.fillStyle = tr.kind === 'audio' ? (i % 2 ? '#17201b' : '#19231d') : (i % 2 ? '#1a1c21' : '#1c1e24');
      g.fillRect(LABEL, y, W - LABEL, ROW);
      g.fillStyle = '#212329'; g.fillRect(0, y, LABEL, ROW);
      g.fillStyle = '#2a2c33'; g.fillRect(0, y + ROW - 1, W, 1);
      g.fillStyle = tr.kind === 'audio' ? '#3aa676' : '#4e8fe0'; g.fillRect(0, y, 3, ROW - 1);
      g.fillStyle = '#d6d9e0'; g.font = '600 12px system-ui'; g.textBaseline = 'middle';
      g.fillText(tr.name, 12, y + ROW / 2);
      for (const b of this.headerButtons(tr, y)) {
        const on = !!tr[b.k];
        g.fillStyle = on ? (b.k === 'locked' ? '#7a5a1f' : '#5a2a32') : '#2b2e36';
        g.beginPath(); g.roundRect(b.x, b.y, b.w, b.h, 4); g.fill();
        g.fillStyle = on ? '#fff' : '#9aa0ad'; g.font = '12px "Segoe UI Symbol", system-ui'; g.textAlign = 'center';
        g.fillText({ locked: on ? '🔒' : '🔓', muted: on ? '🔇' : '🔊', hidden: on ? '◌' : '◉' }[b.k], b.x + b.w / 2, b.y + b.h / 2 + 1);
        g.textAlign = 'left';
      }
    });
    // clips
    const sel = new Set(app.selection);
    g.save(); g.beginPath(); g.rect(LABEL, HEAD, W - LABEL, H - HEAD); g.clip();
    for (const c0 of Object.values(doc.clips)) {
      const c = this.view(c0);
      const ri = rows.findIndex(r => r.id === c.track);
      if (ri < 0) continue;
      const x0 = this.x(c.start), x1 = this.x(c.start + c.duration);
      if (x1 < LABEL || x0 > W) continue;
      const y = this.rowY(ri) + 3, h = ROW - 7;
      const kind = this.kind(c);
      g.fillStyle = COLORS[kind] || '#555';
      g.beginPath(); g.roundRect(x0, y, Math.max(2, x1 - x0), h, 4); g.fill();
      g.save(); g.beginPath(); g.roundRect(x0, y, Math.max(2, x1 - x0), h, 4); g.clip();
      if (kind === 'video' || kind === 'image') this.drawThumbs(g, c, x0, x1, y + 16, h - 16);
      const m = c.type === 'media' ? doc.media[c.media] : null;
      if (kind === 'audio' || (kind === 'video' && m?.has_audio && !c.audio_muted && h > 40)) {
        const wh = kind === 'audio' ? h - 16 : 12;
        this.drawWave(g, c, m, x0, x1, y + h - wh, wh, kind === 'audio' ? '#8fe3b5' : 'rgba(255,255,255,.45)');
      }
      g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(x0, y, x1 - x0, 16);
      g.fillStyle = '#fff'; g.font = '11px system-ui';
      const tags = [];
      if (c.speed !== 1) tags.push(`${Math.round(c.speed * 100)} %`);
      if (c.effects.length) tags.push('fx');
      if (c.audio_muted) tags.push('son séparé');
      g.fillText(`${c.link ? '🔗 ' : ''}${c.name}${tags.length ? '  ·  ' + tags.join(' · ') : ''}`, Math.max(x0, LABEL) + 5, y + 8);
      // images clés
      const times = new Set();
      for (const ks of Object.values(c.keyframes)) for (const k of ks) times.add(k.t);
      g.fillStyle = '#f0b43c';
      for (const kt of times) {
        const kx = this.x(c.start + kt);
        g.save(); g.translate(kx, y + h - 5); g.rotate(Math.PI / 4); g.fillRect(-3, -3, 6, 6); g.restore();
      }
      g.restore();
      if (this.locked(c.track)) { g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(x0, y, x1 - x0, h); }
      const fl = this.flashes.get(c0.id);
      if (fl && now - fl < 1400) {
        g.save(); g.globalAlpha = 1 - (now - fl) / 1400; g.strokeStyle = '#ffb547'; g.lineWidth = 3;
        g.beginPath(); g.roundRect(x0, y, x1 - x0, h, 4); g.stroke(); g.restore();
      }
      if (sel.has(c0.id)) { g.strokeStyle = '#fff'; g.lineWidth = 2; g.beginPath(); g.roundRect(x0 + 1, y + 1, x1 - x0 - 2, h - 2, 4); g.stroke(); }
    }
    // transitions
    for (const tr of this.transitions()) {
      const ri = rows.findIndex(r => r.id === tr.clip.track);
      if (ri < 0) continue;
      const xa = this.x(tr.a), xb = this.x(tr.b), y = this.rowY(ri) + 4;
      if (xb < LABEL || xa > W) continue;
      const grad = g.createLinearGradient(xa, 0, xb, 0);
      grad.addColorStop(0, 'rgba(180,150,255,.25)'); grad.addColorStop(0.5, 'rgba(200,175,255,.9)'); grad.addColorStop(1, 'rgba(180,150,255,.25)');
      g.fillStyle = grad;
      g.beginPath(); g.roundRect(xa, y, Math.max(6, xb - xa), 14, 3); g.fill();
      g.strokeStyle = sel.has(tr.clip.id) ? '#fff' : 'rgba(255,255,255,.5)'; g.lineWidth = 1; g.stroke();
      g.fillStyle = '#1b1530'; g.font = '600 10px system-ui'; g.textAlign = 'center';
      if (xb - xa > 16) g.fillText('⇄', (xa + xb) / 2, y + 7.5);
      g.textAlign = 'left';
    }
    if (this.dropHint) {
      const x = this.x(this.dropHint.t);
      g.fillStyle = '#4ea3ff'; g.fillRect(x - 1, HEAD, 2, H - HEAD);
    }
    if (this.hoverX !== null && app.tool === 'razor') {
      const x = this.x(this.snapT(this.t(this.hoverX)));
      g.strokeStyle = '#ffd166'; g.setLineDash([4, 3]); g.beginPath(); g.moveTo(x, HEAD); g.lineTo(x, H); g.stroke(); g.setLineDash([]);
    }
    g.restore();
    // règle
    g.fillStyle = '#202227'; g.fillRect(0, 0, W, HEAD);
    g.fillStyle = '#2c2f36'; g.fillRect(0, HEAD - 1, W, 1);
    const steps = [1 / fps, 5 / fps, 10 / fps, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find(s => s * this.pps >= 80) || 1200;
    const minor = step / 5;
    g.strokeStyle = '#4a4e58'; g.fillStyle = '#9aa0ad'; g.font = '10px system-ui'; g.textBaseline = 'middle';
    for (let s = Math.floor(this.scroll / minor) * minor; this.x(s) < W; s += minor) {
      const x = this.x(s);
      if (x < LABEL) continue;
      const major = Math.abs(s / step - Math.round(s / step)) < 1e-6;
      g.beginPath(); g.moveTo(x + 0.5, HEAD - (major ? 10 : 5)); g.lineTo(x + 0.5, HEAD - 1); g.stroke();
      if (major) g.fillText(tcShort(s, fps, step), x + 3, 9);
    }
    g.fillStyle = '#212329'; g.fillRect(0, 0, LABEL, HEAD);
    g.fillStyle = '#9aa0ad'; g.font = '600 11px system-ui';
    g.fillText(app.snap ? 'Magnétisme : oui' : 'Magnétisme : non', 10, HEAD / 2);
    // tête de lecture
    const px = this.x(app.playhead);
    if (px >= LABEL) {
      g.strokeStyle = '#ff4d5e'; g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(px, HEAD); g.lineTo(px, H); g.stroke();
      g.fillStyle = '#ff4d5e'; g.beginPath(); g.moveTo(px - 7, 4); g.lineTo(px + 7, 4); g.lineTo(px + 7, 14); g.lineTo(px, 22); g.lineTo(px - 7, 14); g.fill();
    }
  }

  drawThumbs(g, c, x0, x1, y, h) {
    const img = this.app.thumb(c.media || null);
    if (!img || !img.complete || !img.naturalWidth) return;
    const w = h * img.naturalWidth / img.naturalHeight;
    g.globalAlpha = 0.85;
    for (let x = x0; x < x1; x += w + 1) {
      if (x + w < LABEL) continue;
      if (x > this.width) break;
      g.drawImage(img, x, y, w, h);
    }
    g.globalAlpha = 1;
  }

  drawWave(g, c, m, x0, x1, y, h, color) {
    const pk = this.app.peaksFor(m?.id);
    if (!pk) return;
    const vol = c.props.volume;
    g.fillStyle = color;
    const a = Math.max(x0, LABEL), b = Math.min(x1, this.width);
    for (let x = a; x < b; x += 2) {
      const src = c.in + (this.t(x) - c.start) * c.speed;
      const i = Math.floor(src * pk.per_sec);
      let v = 0;
      const span = Math.max(1, Math.floor(c.speed * pk.per_sec * 2 / this.pps));
      for (let j = 0; j < span; j++) v = Math.max(v, pk.peaks[i + j] || 0);
      const bh = Math.min(1, v * vol * 1.6) * h;
      g.fillRect(x, y + (h - bh) / 2, 1.5, Math.max(1, bh));
    }
  }

  follow() {
    const px = this.x(this.app.playhead);
    if (px > this.width - 40) this.scroll = this.app.playhead - (this.width - LABEL) * 0.15 / this.pps;
    if (px < LABEL) this.scroll = Math.max(0, this.app.playhead - 1);
  }
}

function tcShort(s, fps, step) {
  const m = Math.floor(s / 60), r = s - m * 60;
  if (step < 1) return `${m}:${String(Math.floor(r)).padStart(2, '0')}:${String(Math.round((r % 1) * fps)).padStart(2, '0')}`;
  return `${m}:${String(Math.round(r)).padStart(2, '0')}`;
}
