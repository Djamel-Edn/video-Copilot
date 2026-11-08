// Composants d'interface et logique commune des propriétés animables.
import { evalKey } from './rendu.js';

export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'style') e.style.cssText = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}

export const ANIM = ['x', 'y', 'scale', 'rotation', 'opacity', 'volume'];

// ------------------------------------------------------------ temps
export function fmtTC(t, fps) {
  t = Math.max(0, t);
  const f = Math.round(t * fps);
  const fr = Math.round(fps);
  const ff = f % fr, s = Math.floor(f / fr), ss = s % 60, m = Math.floor(s / 60) % 60, h = Math.floor(s / 3600);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}:${String(ff).padStart(2, '0')}`;
}

export function parseTC(str, fps) {
  const parts = str.trim().split(/[:;.,]/).map(Number);
  if (parts.some(n => !Number.isFinite(n))) return null;
  if (parts.length === 1) return parts[0];
  while (parts.length < 4) parts.unshift(0);
  const [h, m, s, f] = parts;
  return h * 3600 + m * 60 + s + f / fps;
}

// ------------------------------------------------------------ propriétés animables
// key : 'x' | 'opacity' | … ou 'fx.<id>.<param>'
export function baseValue(clip, key) {
  if (ANIM.includes(key)) return clip.props[key];
  const [, id, p] = key.split('.');
  return clip.effects.find(e => e.id === id)?.params[p];
}

export function valueAt(clip, key, lt) {
  return evalKey(clip.keyframes[key], lt, baseValue(clip, key));
}

export function localTime(app, clip) {
  return Math.min(Math.max(app.playhead - clip.start, 0), clip.duration);
}

export function animated(clip, key) { return !!clip.keyframes[key]?.length; }

export function keyAt(app, clip, key, lt = localTime(app, clip)) {
  const tol = 0.5 / app.doc.settings.fps;
  return (clip.keyframes[key] || []).find(k => Math.abs(k.t - lt) <= tol) || null;
}

// Aperçu local immédiat (avant validation serveur).
export function liveSet(app, clip, key, v) {
  if (animated(clip, key)) {
    const lt = localTime(app, clip);
    const tol = 0.5 / app.doc.settings.fps;
    const ks = clip.keyframes[key].filter(k => Math.abs(k.t - lt) > tol);
    ks.push({ t: lt, v, ease: keyAt(app, clip, key, lt)?.ease || 'linear' });
    clip.keyframes[key] = ks.sort((a, b) => a.t - b.t);
  } else if (ANIM.includes(key)) clip.props[key] = v;
  else {
    const [, id, p] = key.split('.');
    const inst = clip.effects.find(e => e.id === id);
    if (inst) inst.params[p] = v;
  }
  app.dirty = true;
}

export function commitOps(app, clip, key, v) {
  if (animated(clip, key)) {
    const k = keyAt(app, clip, key);
    return [{ op: 'set_keyframe', clip: clip.id, prop: key, t: k ? k.t : localTime(app, clip), v }];
  }
  if (ANIM.includes(key)) return [{ op: 'update_clip', clip: clip.id, props: { [key]: v } }];
  const [, id, p] = key.split('.');
  return [{ op: 'update_effect', clip: clip.id, fx: id, params: { [p]: v } }];
}

export function commitSet(app, clip, key, v, label) {
  app.send(commitOps(app, clip, key, v), label);
}

export function toggleAnim(app, clip, key, label) {
  const lt = localTime(app, clip);
  const cur = valueAt(clip, key, lt);
  if (animated(clip, key)) {
    // On arrête l'animation en gardant la valeur affichée, comme le chronomètre de Premiere.
    const base = ANIM.includes(key) ? { op: 'update_clip', clip: clip.id, props: { [key]: cur } }
      : { op: 'update_effect', clip: clip.id, fx: key.split('.')[1], params: { [key.split('.')[2]]: cur } };
    app.send([base, { op: 'set_keyframes', clip: clip.id, prop: key, keys: [] }], `Arrêter l'animation (${label})`);
  } else {
    app.send([{ op: 'set_keyframe', clip: clip.id, prop: key, t: lt, v: cur }], `Animer ${label}`);
  }
}

export function toggleKey(app, clip, key, label) {
  const k = keyAt(app, clip, key);
  if (k) app.send([{ op: 'remove_keyframe', clip: clip.id, prop: key, t: k.t }], `Retirer une image clé (${label})`);
  else app.send([{ op: 'set_keyframe', clip: clip.id, prop: key, t: localTime(app, clip),
                   v: valueAt(clip, key, localTime(app, clip)) }], `Image clé (${label})`);
}

export function jumpKey(app, clip, key, dir) {
  const lt = app.playhead - clip.start, tol = 0.5 / app.doc.settings.fps;
  const ks = clip.keyframes[key] || [];
  const k = dir > 0 ? ks.find(k => k.t > lt + tol) : [...ks].reverse().find(k => k.t < lt - tol);
  if (k) app.seek(clip.start + k.t);
}

// ------------------------------------------------------------ valeur à glisser (style « chiffres bleus »)
// cfg : {value, min, max, step, decimals, mul, unit, onLive(v), onCommit(v)}
export function scrub(cfg) {
  const mul = cfg.mul ?? 1, dec = cfg.decimals ?? 1;
  const span = el('span', { class: 'scrub', title: 'Glisser pour régler · clic pour saisir · Maj = ×10 · Ctrl = fin' });
  let value = cfg.value;
  const show = v => { span.textContent = (v * mul).toFixed(dec) + (cfg.unit || ''); };
  const clamp = v => Math.min(cfg.max ?? Infinity, Math.max(cfg.min ?? -Infinity, v));
  show(value);
  span.set = v => { value = v; if (!span.classList.contains('drag')) show(v); };
  span.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const x0 = e.clientX, v0 = value;
    let moved = false;
    span.setPointerCapture(e.pointerId);
    span.classList.add('drag');
    cfg.app && (cfg.app.busy = true);
    const move = ev => {
      const dx = ev.clientX - x0;
      if (Math.abs(dx) > 2) moved = true;
      if (!moved) return;
      const st = (cfg.step ?? 0.01) * (ev.shiftKey ? 10 : 1) * (ev.ctrlKey ? 0.1 : 1);
      value = clamp(v0 + dx * st);
      show(value);
      cfg.onLive?.(value);
    };
    const up = () => {
      span.removeEventListener('pointermove', move);
      span.removeEventListener('pointerup', up);
      span.classList.remove('drag');
      cfg.app && (cfg.app.busy = false);
      if (moved) cfg.onCommit(value);
      else edit();
    };
    span.addEventListener('pointermove', move);
    span.addEventListener('pointerup', up);
  });
  const edit = () => {
    const inp = el('input', { class: 'scrub-input', value: (value * mul).toFixed(dec) });
    span.replaceWith(inp);
    inp.focus(); inp.select();
    const done = ok => {
      if (!inp.isConnected) return;
      inp.replaceWith(span);
      const v = parseFloat(inp.value.replace(',', '.'));
      if (ok && Number.isFinite(v)) { value = clamp(v / mul); show(value); cfg.onCommit(value); }
    };
    inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') done(true); if (ev.key === 'Escape') done(false); ev.stopPropagation(); });
    inp.addEventListener('blur', () => done(true));
  };
  return span;
}

export function colorInput(value, onCommit, onLive) {
  const i = el('input', { type: 'color', value, class: 'color' });
  i.addEventListener('input', () => onLive?.(i.value));
  i.addEventListener('change', () => onCommit(i.value));
  return i;
}

export function selectInput(options, value, onChange) {
  const s = el('select', {}, options.map(([v, label]) => el('option', { value: v, selected: String(v) === String(value) }, label)));
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

export function segmented(options, value, onChange) {
  const box = el('div', { class: 'seg' });
  for (const [v, label, title] of options) {
    box.append(el('button', { class: String(v) === String(value) ? 'on' : '', title: title || '', onclick: () => onChange(v) }, label));
  }
  return box;
}

export function toggleBtn(label, on, onChange, title = '') {
  return el('button', { class: 'tog' + (on ? ' on' : ''), title, onclick: () => onChange(!on) }, label);
}

// ------------------------------------------------------------ menu contextuel
export function contextMenu(x, y, items) {
  const m = document.getElementById('menu');
  m.innerHTML = '';
  for (const it of items) {
    if (it === '-') { m.append(el('hr')); continue; }
    m.append(el('button', { class: it.on ? 'on' : '', onclick: () => { hide(); it.action(); } }, it.label));
  }
  m.style.left = `${Math.min(x, innerWidth - 220)}px`;
  m.style.top = `${Math.min(y, innerHeight - 30 * items.length - 10)}px`;
  m.hidden = false;
  const hide = () => { m.hidden = true; removeEventListener('pointerdown', out, true); };
  const out = e => { if (!m.contains(e.target)) hide(); };
  setTimeout(() => addEventListener('pointerdown', out, true), 0);
}

export const EASE_LABELS = [['linear', 'Linéaire'], ['ease_in', 'Accélération'], ['ease_out', 'Décélération'],
                            ['ease_in_out', 'Lissé (entrée + sortie)'], ['hold', 'Maintien (marche)']];
