// Sources d'image pour chaque clip : vidéo (synchronisée sur la tête de lecture),
// image fixe, texte (dessiné sur un canvas), aplat de couleur.
import { clipProps } from './rendu.js';

const PRELOAD = 1.5;   // s : préparer les clips qui arrivent
const KEEP = 12;       // s : libérer les vidéos loin de la tête de lecture

export class Sources {
  constructor(onFrame) {
    this.videos = new Map();   // clipId → {el, url}
    this.images = new Map();   // mediaId → {el, url}
    this.texts = new Map();    // clipId → {canvas, key}
    this.solids = new Map();   // clipId → {canvas, key}
    this.onFrame = onFrame;    // appelé quand une nouvelle image vidéo est prête
  }

  url(m) {
    return `/media/${m.id}?preview=${m.proxy ? 1 : 0}`;
  }

  usable(m) {
    return m && (m.proxy || m.browser_ok);
  }

  video(clip, m) {
    let v = this.videos.get(clip.id);
    const url = this.url(m);
    if (v && v.url !== url) { this.drop(clip.id); v = null; }
    if (!v) {
      const el = document.createElement('video');
      el.preload = 'auto';
      el.playsInline = true;
      el.src = url;
      for (const ev of ['seeked', 'loadeddata', 'canplay']) el.addEventListener(ev, () => this.onFrame());
      this.onElement?.(el);
      v = { el, url };
      this.videos.set(clip.id, v);
    }
    return v.el;
  }

  drop(id) {
    const v = this.videos.get(id);
    if (!v) return;
    v.el.pause(); v.el.removeAttribute('src'); v.el.load();
    this.videos.delete(id);
  }

  target(clip, t) {
    return clip.in + (t - clip.start) * clip.speed;
  }

  // Aligne chaque élément vidéo/audio sur la tête de lecture.
  sync(doc, t, playing, sp = null) {
    const fps = doc.settings.fps;
    const live = new Set();
    for (const clip of Object.values(doc.clips)) {
      if (clip.type !== 'media') continue;
      const m = doc.media[clip.media];
      if (!this.usable(m) || m.kind === 'image') continue;
      const track = doc.tracks.find(tr => tr.id === clip.track);
      const end = clip.start + clip.duration;
      const [sa, sb] = sp?.[clip.id] || [clip.start, end];
      if (t < sa - PRELOAD - KEEP || t > sb + KEEP) continue;
      live.add(clip.id);
      const el = this.video(clip, m);
      const active = sa <= t && t < sb;
      const dur = m.duration || Infinity;
      const clampT = x => Math.min(Math.max(x, 0), Math.max(0, dur - 0.04));
      if (!active) {
        if (!el.paused) el.pause();
        const first = clampT(this.target(clip, sa));
        if (t < sa && t > sa - PRELOAD && Math.abs(el.currentTime - first) > 0.05) el.currentTime = first;
        continue;
      }
      const tgt = this.target(clip, t);
      // Hors de la source (marge de transition insuffisante) : image figée sur le bord.
      const frozen = tgt < 0 || tgt > dur - 0.04;
      const vol = clipProps(clip, t - clip.start).volume;
      el.volume = Math.max(0, Math.min(1, vol));
      // La partie prolongée par une transition est muette (à l'export, le son coupe net).
      const own = clip.start <= t && t < end;
      el.muted = !!track?.muted || vol <= 0 || !!clip.audio_muted || !own;
      if (frozen) {
        if (!el.paused) el.pause();
        if (Math.abs(el.currentTime - clampT(tgt)) > 0.5 / fps && !el.seeking) el.currentTime = clampT(tgt);
      } else if (playing) {
        if (Math.abs(el.playbackRate - clip.speed) > 1e-3) el.playbackRate = clip.speed;
        if (el.paused) { el.currentTime = tgt; el.play().catch(() => {}); }
        else if (Math.abs(el.currentTime - tgt) > 0.25) el.currentTime = tgt;
      } else {
        if (!el.paused) el.pause();
        if (Math.abs(el.currentTime - tgt) > 0.5 / fps && !el.seeking) el.currentTime = tgt;
      }
    }
    for (const id of [...this.videos.keys()]) if (!live.has(id)) this.drop(id);
  }

  pauseAll() {
    for (const v of this.videos.values()) v.el.pause();
  }

  // Attend que toutes les vidéos actives aient décodé l'image demandée.
  async ready(doc, t, timeout = 4000, sp = null) {
    const t0 = performance.now();
    while (performance.now() - t0 < timeout) {
      this.sync(doc, t, false, sp);
      let ok = true;
      for (const clip of Object.values(doc.clips)) {
        const [sa, sb] = sp?.[clip.id] || [clip.start, clip.start + clip.duration];
        if (!(sa <= t && t < sb)) continue;
        const v = this.videos.get(clip.id);
        if (!v) continue;
        const m = doc.media[clip.media], dur = m?.duration || Infinity;
        const want = Math.min(Math.max(this.target(clip, t), 0), Math.max(0, dur - 0.04));
        if (v.el.seeking || v.el.readyState < 2 || Math.abs(v.el.currentTime - want) > 0.06) ok = false;
      }
      if (ok) { await new Promise(r => requestAnimationFrame(r)); return true; }
      await new Promise(r => setTimeout(r, 30));
    }
    return false;
  }

  textBounds(id) { return this.texts.get(id)?.bounds || null; }

  videoSize(id) {
    const v = this.videos.get(id);
    return v && v.el.videoWidth ? [v.el.videoWidth, v.el.videoHeight] : null;
  }

  // Fournit au moteur de rendu la source d'un clip, ou null si pas prête.
  get(doc, clip) {
    const W = doc.settings.width, H = doc.settings.height;
    if (clip.type === 'media') {
      const m = doc.media[clip.media];
      if (!this.usable(m)) return null;
      if (m.kind === 'video') {
        const v = this.videos.get(clip.id);
        if (!v || v.el.readyState < 2) return null;
        return { id: 'v:' + clip.id, el: v.el, w: v.el.videoWidth, h: v.el.videoHeight, dynamic: true };
      }
      if (m.kind === 'image') {
        let im = this.images.get(m.id);
        if (!im) {
          const el = new Image();
          el.onload = () => this.onFrame();
          el.src = this.url(m);
          im = { el };
          this.images.set(m.id, im);
        }
        if (!im.el.complete || !im.el.naturalWidth) return null;
        return { id: 'i:' + m.id, el: im.el, w: im.el.naturalWidth, h: im.el.naturalHeight, key: m.id };
      }
      return null;
    }
    if (clip.type === 'text') {
      const key = JSON.stringify(clip.text) + W + 'x' + H;
      let s = this.texts.get(clip.id);
      if (!s) { s = { canvas: document.createElement('canvas'), key: null }; this.texts.set(clip.id, s); }
      if (s.key !== key) { s.bounds = drawText(s.canvas, clip.text, W, H); s.key = key; }
      return { id: 't:' + clip.id, el: s.canvas, w: W, h: H, key };
    }
    if (clip.type === 'solid') {
      const key = clip.color;
      let s = this.solids.get(clip.id);
      if (!s) { s = { canvas: document.createElement('canvas'), key: null }; this.solids.set(clip.id, s); }
      if (s.key !== key) {
        s.canvas.width = 4; s.canvas.height = 4;
        const g = s.canvas.getContext('2d'); g.fillStyle = clip.color; g.fillRect(0, 0, 4, 4);
        s.key = key;
      }
      return { id: 's:' + clip.id, el: s.canvas, w: W, h: H, key };
    }
    return null;
  }
}

export function fontStack(f) {
  return f.includes(',') ? f : `"${f}", "Segoe UI", sans-serif`;
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}

// Dessine le texte sur un canvas plein cadre ; renvoie son rectangle {x, y, w, h} (px, origine en haut à gauche).
export function drawText(cv, tx, W, H) {
  cv.width = W; cv.height = H;
  const g = cv.getContext('2d');
  g.clearRect(0, 0, W, H);
  g.font = `${tx.italic ? 'italic ' : ''}${tx.weight} ${tx.size}px ${fontStack(tx.font)}`;
  if ('letterSpacing' in g) g.letterSpacing = `${tx.letter_spacing || 0}px`;
  g.textBaseline = 'middle';
  g.textAlign = tx.align;
  const text = tx.uppercase ? String(tx.content).toUpperCase() : String(tx.content);
  const lines = text.split('\n');
  const lh = tx.size * tx.line_height;
  const x = tx.align === 'left' ? W * 0.08 : tx.align === 'right' ? W * 0.92 : W / 2;
  const y0 = H / 2 - (lh * lines.length) / 2 + lh / 2;
  const wmax = Math.max(1, ...lines.map(l => g.measureText(l).width));
  const left = tx.align === 'left' ? x : tx.align === 'right' ? x - wmax : x - wmax / 2;
  const top = y0 - lh / 2;
  const p = tx.background ? tx.padding : Math.max(4, tx.stroke_width);
  const box = { x: left - p, y: top - p, w: wmax + 2 * p, h: lh * lines.length + 2 * p };
  if (tx.background) {
    g.save();
    g.globalAlpha = tx.background_opacity ?? 1;
    g.fillStyle = tx.background;
    g.beginPath();
    g.roundRect ? g.roundRect(box.x, box.y, box.w, box.h, tx.radius || 0) : g.rect(box.x, box.y, box.w, box.h);
    g.fill();
    g.restore();
  }
  const shadow = (tx.shadow_opacity || 0) > 0;
  const setShadow = on => {
    g.shadowColor = on ? rgba(tx.shadow_color || '#000000', tx.shadow_opacity) : 'transparent';
    g.shadowBlur = on ? tx.shadow_blur : 0;
    g.shadowOffsetX = on ? tx.shadow_x : 0;
    g.shadowOffsetY = on ? tx.shadow_y : 0;
  };
  lines.forEach((l, i) => {
    const y = y0 + i * lh;
    if (tx.stroke_width > 0) {
      setShadow(shadow);
      g.lineJoin = 'round';
      g.lineWidth = tx.stroke_width * 2;
      g.strokeStyle = tx.stroke_color;
      g.strokeText(l, x, y);
    }
    setShadow(shadow && !(tx.stroke_width > 0));
    g.fillStyle = tx.color;
    g.fillText(l, x, y);
  });
  return box;
}
