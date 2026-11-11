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
      if (ok) { await uneImage(); return true; }
      await new Promise(r => setTimeout(r, 30));
    }
    return false;
  }

  textBounds(id) { return this.texts.get(id)?.bounds || null; }

  // Carte de transparence réduite (≤ 320 px) d'une image ou d'une vidéo transparente, avec le
  // rectangle de son contenu visible : le moniteur s'en sert pour encadrer CE QUI SE VOIT d'un
  // calque plein cadre (PNG, vidéo alpha) et pour savoir si un clic tombe sur lui.
  alphaMap(doc, clip) {
    if (clip.type !== 'media') return null;
    const m = doc.media[clip.media];
    if (!m || !(m.kind === 'image' || (m.kind === 'video' && m.alpha))) return null;
    let el, cle;
    if (m.kind === 'image') {
      if (!this.images.get(m.id)) this.get(doc, clip);       // crée l'image si elle n'a jamais été dessinée
      const im = this.images.get(m.id);
      if (!im?.el.complete || !im.el.naturalWidth) return null;
      el = im.el; cle = 'i:' + m.id;
    } else {
      const v = this.videos.get(clip.id);
      if (!v || v.el.readyState < 2) return null;
      el = v.el; cle = `v:${clip.id}:${v.el.currentTime.toFixed(3)}`;
    }
    this.cartes ||= new Map();
    const deja = this.cartes.get(clip.id);
    if (deja?.cle === cle) return deja;
    const sw = el.naturalWidth || el.videoWidth, sh = el.naturalHeight || el.videoHeight;
    const k = Math.min(1, 320 / Math.max(sw, sh)), w = Math.max(1, Math.round(sw * k)), h = Math.max(1, Math.round(sh * k));
    const cv = this._carte ||= document.createElement('canvas');
    cv.width = w; cv.height = h;
    const g = cv.getContext('2d', { willReadFrequently: true });
    g.clearRect(0, 0, w, h);
    g.drawImage(el, 0, 0, w, h);
    const px = g.getImageData(0, 0, w, h).data, a = new Uint8Array(w * h);
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = px[(y * w + x) * 4 + 3];
      a[y * w + x] = v;
      if (v > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    }
    const carte = { cle, w, h, sw, sh, a, boite: x1 < 0 ? null : [x0 / k, y0 / k, (x1 + 1) / k, (y1 + 1) / k] };
    this.cartes.set(clip.id, carte);
    return carte;
  }

  videoSize(id) {
    const v = this.videos.get(id);
    return v && v.el.videoWidth ? [v.el.videoWidth, v.el.videoHeight] : null;
  }

  // Fournit au moteur de rendu la source d'un clip, ou null si pas prête.
  get(doc, clip, t = null) {
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
      // Texte mot à mot : l'image dépend de l'instant (mots déjà apparus, entrée en cours).
      const lt = t === null ? null : t - clip.start;
      const etat = clip.text.mots?.length && lt !== null ? etatMots(clip.text, lt).map(e => e.cle).join('') : '';
      const key = JSON.stringify(clip.text) + W + 'x' + H + etat;
      let s = this.texts.get(clip.id);
      if (!s) { s = { canvas: document.createElement('canvas'), key: null }; this.texts.set(clip.id, s); }
      if (s.key !== key) { s.bounds = drawText(s.canvas, clip.text, W, H, lt); s.key = key; }
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

// Laisse passer une image d'affichage (la vidéo présente l'image cherchée). Une fenêtre
// cachée ou réduite ne produit plus d'images (requestAnimationFrame suspendu) : on
// n'attend alors que 50 ms, sinon snapshot restait bloqué jusqu'au délai du serveur.
export function uneImage() {
  return new Promise(r => {
    let fait = false;
    const fin = () => { if (!fait) { fait = true; r(); } };
    requestAnimationFrame(fin);
    setTimeout(fin, 50);
  });
}

// Dessine le texte sur un canvas plein cadre ; renvoie son rectangle {x, y, w, h} (px, origine en haut à gauche).
// Apparition mot à mot : pour chaque mot (dans l'ordre du texte, lignes comprises), visible ou
// non à l'instant lt du clip, et où il en est de son entrée. tx.mots[i] = {t, fin?, couleur?} ;
// tx.entree = {duree, dy, echelle, opacite} (état de départ d'un mot, ramené à l'état final).
// Un mot sans entrée dans tx.mots (texte modifié depuis) est affiché dès le début du clip.
export function etatMots(tx, lt) {
  const n = String(tx.content).split(/\s+/).filter(Boolean).length;
  const en = tx.entree || {};
  const out = [];
  for (let i = 0; i < n; i++) {
    const m = tx.mots?.[i] || { t: 0 };
    if (lt < (m.t || 0) - 1e-6 || (m.fin != null && lt >= m.fin - 1e-6)) { out.push({ vis: false, cle: '0' }); continue; }
    const p = en.duree > 0 ? Math.min(1, (lt - (m.t || 0)) / en.duree) : 1;
    // Courbe d'arrivée relevée sur la composition d'origine (une valeur par image), sinon
    // décélération générique.
    let e;
    if (en.courbe?.length > 1) {
      const q = p * (en.courbe.length - 1), i0 = Math.floor(q), i1 = Math.min(en.courbe.length - 1, i0 + 1);
      e = en.courbe[i0] + (en.courbe[i1] - en.courbe[i0]) * (q - i0);
    } else e = 1 - (1 - p) ** 3;
    out.push({ vis: true, e, couleur: m.couleur, cle: p >= 1 ? '1' : `(${p.toFixed(2)})` });
  }
  return out;
}

export function drawText(cv, tx, W, H, lt = null) {
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
  const trace = (s, xx, y, couleur) => {
    if (tx.stroke_width > 0) {
      setShadow(shadow);
      g.lineJoin = 'round';
      g.lineWidth = tx.stroke_width * 2;
      g.strokeStyle = tx.stroke_color;
      g.strokeText(s, xx, y);
    }
    setShadow(shadow && !(tx.stroke_width > 0));
    g.fillStyle = couleur || tx.color;
    g.fillText(s, xx, y);
  };
  if (tx.mots?.length && lt !== null) {
    // Mot à mot : chaque mot à sa place dans la ligne (mesurée avec ce qui le précède), dessiné
    // seulement s'il est apparu, avec son entrée (montée, échelle, opacité) et sa couleur.
    const etats = etatMots(tx, lt), en = tx.entree || {};
    g.textAlign = 'left';
    let k = 0;
    lines.forEach((l, i) => {
      const y = y0 + i * lh;
      const lw = g.measureText(l).width;
      const gauche = tx.align === 'left' ? x : tx.align === 'right' ? x - lw : x - lw / 2;
      for (const m of l.matchAll(/\S+/g)) {
        const st = etats[k++];
        if (!st?.vis) continue;
        const wx = gauche + g.measureText(l.slice(0, m.index)).width;
        const ww = g.measureText(m[0]).width;
        const sc = (en.echelle ?? 1) + (1 - (en.echelle ?? 1)) * st.e;
        g.save();
        g.globalAlpha = (en.opacite ?? 1) + (1 - (en.opacite ?? 1)) * st.e;
        g.translate(wx + ww / 2, y + (en.dy || 0) * (1 - st.e));
        g.scale(sc, sc);
        trace(m[0], -ww / 2, 0, st.couleur);
        g.restore();
      }
    });
    return box;
  }
  lines.forEach((l, i) => trace(l, x, y0 + i * lh));
  return box;
}
