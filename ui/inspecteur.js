// Contrôles d'effets : chaque propriété numérique a son chronomètre, ses images
// clés (◀ ◆ ▶) et une mini-timeline où l'on glisse les losanges.
import { el, scrub, colorInput, selectInput, segmented, toggleBtn, contextMenu, EASE_LABELS,
         valueAt, animated, keyAt, liveSet, commitSet, toggleAnim, toggleKey, jumpKey, localTime, fmtTC } from './widgets.js';

const WEIGHTS = [[100, 'Fin 100'], [200, 'Extra-léger 200'], [300, 'Léger 300'], [400, 'Normal 400'], [500, 'Moyen 500'],
                 [600, 'Demi-gras 600'], [700, 'Gras 700'], [800, 'Extra-gras 800'], [900, 'Noir 900']];

export class Inspecteur {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.rows = [];
    this.selKey = null;      // {clip, key, t}
    this.closed = new Set(); // sections repliées
  }

  refresh() {
    const a = document.activeElement;
    if (this.app.busy || (this.root.contains(a) && a.matches('input:not([type=color]), textarea, select'))) {
      this.stale = true; return;
    }
    this.stale = false;
    this.build();
  }

  tick() { for (const r of this.rows) r(); }

  clip() {
    const s = this.app.selection, doc = this.app.doc;
    if (s.length === 1) return doc.clips[s[0]];
    // Une paire image + son liée se règle comme un seul clip : on montre la partie image.
    if (this.isOneGroup()) return s.map(id => doc.clips[id]).find(c => c && this.app.clipKind(c) !== 'audio') || doc.clips[s[0]];
    return null;
  }

  isOneGroup() {
    const s = this.app.selection;
    if (s.length < 2) return false;
    const g = new Set(this.app.group(s[0]));
    return g.size === s.length && s.every(id => g.has(id));
  }

  build() {
    const app = this.app, root = this.root;
    const keepScroll = root.scrollTop, sameClip = this._lastId === app.selection.join(',');
    this._lastId = app.selection.join(',');
    queueMicrotask(() => { if (sameClip) root.scrollTop = keepScroll; });
    root.innerHTML = '';
    this.rows = [];
    const doc = app.doc;
    if (!doc) return;
    if (app.selection.length > 1 && !this.isOneGroup()) {
      root.append(el('div', { class: 'insp-empty' },
        el('p', {}, `${app.selection.length} clips sélectionnés.`),
        el('p', { class: 'muted' }, 'Glisse un effet sur l\'un d\'eux, ou double-clique un effet pour l\'appliquer à tous.'),
        el('button', { onclick: () => app.deleteSelection(false) }, 'Supprimer'),
        el('button', { onclick: () => app.deleteSelection(true) }, 'Supprimer et resserrer')));
      return;
    }
    const c = this.clip();
    if (!c) {
      const s = doc.settings;
      root.append(el('div', { class: 'insp-empty' },
        el('p', {}, 'Aucun clip sélectionné.'),
        el('p', { class: 'muted' }, `Séquence ${s.width}×${s.height} · ${s.fps} i/s · ${fmtTC(app.duration(), s.fps)}`),
        el('button', { onclick: () => app.openSettings() }, 'Paramètres de la séquence…')));
      return;
    }
    const id = c.id;
    const get = () => app.doc.clips[id];
    const kind = app.clipKind(c);
    const visual = kind !== 'audio';
    const media = c.type === 'media' ? doc.media[c.media] : null;
    const hasAudio = kind === 'audio' || (kind === 'video' && media?.has_audio && !c.audio_muted);

    // en-tête
    const name = el('input', { class: 'clipname', value: c.name });
    name.addEventListener('change', () => app.send([{ op: 'update_clip', clip: id, name: name.value }], 'Renommer'));
    root.append(el('div', { class: 'insp-head' }, el('span', { class: `badge ${kind}` }, kindLabel(kind)), name));
    const quick = el('div', { class: 'quick' },
      el('button', { title: 'Fondu d\'entrée de 0,5 s', onclick: () => this.fade(get(), 'in', visual ? 'opacity' : 'volume') }, '◢ Fondu entrée'),
      el('button', { title: 'Fondu de sortie de 0,5 s', onclick: () => this.fade(get(), 'out', visual ? 'opacity' : 'volume') }, 'Fondu sortie ◣'));
    root.append(quick);

    // montage
    const sec = this.section('Montage', root);
    // Sur une paire image + son liée, le montage s'applique aux deux clips.
    const both = (label, f) => app.send(app.group(id).map(gid => ({ op: 'update_clip', clip: gid, ...f(app.doc.clips[gid]) })), label);
    sec.append(this.plainRow('Début', get().start, { step: 1 / doc.settings.fps, decimals: 2, unit: ' s', min: 0 },
      v => both('Début du clip', () => ({ start: v }))));
    sec.append(this.plainRow('Durée', get().duration, { step: 1 / doc.settings.fps, decimals: 2, unit: ' s', min: 1 / doc.settings.fps },
      v => both('Durée du clip', () => ({ duration: v }))));
    if (c.type === 'media' && kind !== 'image') {
      sec.append(this.plainRow("Point d'entrée", get().in, { step: 1 / doc.settings.fps, decimals: 2, unit: ' s', min: 0 },
        v => both("Point d'entrée", () => ({ in: v }))));
      sec.append(this.plainRow('Vitesse', get().speed, { step: 0.005, decimals: 0, mul: 100, unit: ' %', min: 0.05, max: 16 },
        v => both('Vitesse', g => ({ speed: v, duration: g.duration * g.speed / v }))));
    }

    if (visual) {
      const mv = this.section('Mouvement', root, () => app.send([{ op: 'update_clip', clip: id, props: { x: 0, y: 0, scale: 1, rotation: 0 } },
        ...['x', 'y', 'scale', 'rotation'].map(k => ({ op: 'set_keyframes', clip: id, prop: k, keys: [] }))], 'Réinitialiser le mouvement'));
      mv.append(this.propRow(get, 'x', 'Position X', { step: 1, decimals: 0, unit: ' px' }));
      mv.append(this.propRow(get, 'y', 'Position Y', { step: 1, decimals: 0, unit: ' px' }));
      mv.append(this.propRow(get, 'scale', 'Échelle', { step: 0.005, decimals: 1, mul: 100, unit: ' %', min: 0 }));
      mv.append(this.propRow(get, 'rotation', 'Rotation', { step: 0.5, decimals: 1, unit: ' °' }));
      const op = this.section('Opacité', root);
      op.append(this.propRow(get, 'opacity', 'Opacité', { step: 0.005, decimals: 1, mul: 100, unit: ' %', min: 0, max: 1 }));
    }
    if (c.type === 'text') this.textSection(get, root);
    if (c.type === 'solid') {
      const s = this.section('Aplat de couleur', root);
      s.append(this.line('Couleur', colorInput(c.color, v => app.send([{ op: 'update_clip', clip: id, color: v }], 'Couleur de l\'aplat'),
        v => { get().color = v; app.dirty = true; })));
    }
    if (hasAudio || (kind === 'video' && media?.has_audio)) {
      const s = this.section('Audio', root);
      if (hasAudio) s.append(this.propRow(get, 'volume', 'Volume', { step: 0.005, decimals: 0, mul: 100, unit: ' %', min: 0, max: 2 }));
      if (kind === 'audio') {
        s.append(this.line('Musique', el('button', { title: "Écrit des images clés de volume qui baissent ce clip de 12 dB pendant que quelqu'un parle",
          onclick: () => app.duckMusic([id]) }, 'Baisser sous la voix')));
      }
      if (kind === 'video' && media?.has_audio && !c.audio_muted) {
        s.append(this.line('Son', el('button', { onclick: () => app.separateAudio(id), title: 'Le son passe sur une piste audio, lié à l\'image' }, 'Séparer le son')));
      } else if (c.audio_muted) {
        s.append(this.line('Son', el('span', { class: 'muted small' }, 'séparé sur une piste audio'),
          el('button', { class: 'mini', onclick: () => app.send([{ op: 'update_clip', clip: id, audio_muted: false }], 'Réactiver le son') }, 'Réactiver')));
      }
      if (c.link) {
        s.append(this.line('Lien', el('span', { class: 'muted small' }, '🔗 image et son liés'),
          el('button', { class: 'mini', onclick: () => app.send([{ op: 'unlink_clips', clips: app.group(id) }], 'Délier') }, 'Délier')));
      }
    }
    if (visual && (app.neighbor(c, -1) || c.trans_in)) this.transitionSection(get, root);
    if (visual) this.effectsSection(get, root);
  }

  // ------------------------------------------------------------ sections
  section(title, parent, onReset) {
    const body = el('div', { class: 'sec-body' });
    const closed = this.closed.has(title);
    const head = el('div', { class: 'sec-head' },
      el('span', { class: 'chev' }, closed ? '▸' : '▾'), el('span', { class: 'sec-title' }, title),
      onReset ? el('button', { class: 'mini', title: 'Réinitialiser', onclick: e => { e.stopPropagation(); onReset(); } }, '⟲') : null);
    head.addEventListener('click', () => { closed ? this.closed.delete(title) : this.closed.add(title); this.build(); });
    if (closed) body.hidden = true;
    parent.append(el('div', { class: 'sec' }, head, body));
    return body;
  }

  line(label, ...ctrl) {
    return el('div', { class: 'prow plain' }, el('span', { class: 'sw-space' }), el('span', { class: 'plabel' }, label),
      el('span', { class: 'pctrl' }, ...ctrl));
  }

  plainRow(label, value, cfg, commit) {
    const sc = scrub({ ...cfg, value, app: this.app, onCommit: commit });
    return this.line(label, sc);
  }

  propRow(get, key, label, cfg) {
    const app = this.app;
    const c0 = get();
    const sw = el('button', { class: 'sw' + (animated(c0, key) ? ' on' : ''), title: 'Activer / arrêter l\'animation (chronomètre)',
                              onclick: () => toggleAnim(app, get(), key, label) }, '⏱');
    const sc = scrub({ ...cfg, value: valueAt(c0, key, localTime(app, c0)), app,
                       onLive: v => liveSet(app, get(), key, v), onCommit: v => commitSet(app, get(), key, v, label) });
    const prev = el('button', { class: 'kn', title: 'Image clé précédente', onclick: () => jumpKey(app, get(), key, -1) }, '◀');
    const dia = el('button', { class: 'kd', title: 'Ajouter / retirer une image clé ici', onclick: () => toggleKey(app, get(), key, label) }, '◆');
    const next = el('button', { class: 'kn', title: 'Image clé suivante', onclick: () => jumpKey(app, get(), key, 1) }, '▶');
    const lane = el('canvas', { class: 'lane', width: 120, height: 18 });
    this.bindLane(lane, get, key, label);
    const row = el('div', { class: 'prow' }, sw, el('span', { class: 'plabel', title: label }, label), el('span', { class: 'pctrl' }, sc),
                   el('span', { class: 'knav' + (animated(c0, key) ? '' : ' off') }, prev, dia, next), lane);
    const update = () => {
      const c = get();
      if (!c) return;
      sc.set(valueAt(c, key, localTime(app, c)));
      dia.classList.toggle('on', !!keyAt(app, c, key));
      this.drawLane(lane, c, key);
    };
    update();
    this.rows.push(update);
    return row;
  }

  // ------------------------------------------------------------ mini-timeline d'images clés
  drawLane(cv, c, key) {
    const g = cv.getContext('2d'), w = cv.width, h = cv.height;
    g.clearRect(0, 0, w, h);
    g.fillStyle = '#1a1b1f'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#26282e'; g.fillRect(0, h / 2 - 1, w, 2);
    const ks = c.keyframes[key] || [];
    for (let i = 0; i < ks.length - 1; i++) {
      g.fillStyle = 'rgba(240,180,60,.35)';
      g.fillRect(ks[i].t / c.duration * w, h / 2 - 1, (ks[i + 1].t - ks[i].t) / c.duration * w, 2);
    }
    for (const k of ks) {
      const x = k.t / c.duration * w, sel = this.selKey && this.selKey.clip === c.id && this.selKey.key === key && Math.abs(this.selKey.t - k.t) < 1e-4;
      g.save(); g.translate(x, h / 2); g.rotate(Math.PI / 4);
      g.fillStyle = sel ? '#ffffff' : k.ease === 'hold' ? '#e07b5a' : '#f0b43c';
      g.fillRect(-3.5, -3.5, 7, 7); g.restore();
    }
    const px = (this.app.playhead - c.start) / c.duration * w;
    if (px >= 0 && px <= w) { g.fillStyle = '#ff4d5e'; g.fillRect(px - 0.5, 0, 1.5, h); }
  }

  bindLane(cv, get, key, label) {
    const app = this.app;
    const pick = (c, x) => (c.keyframes[key] || []).find(k => Math.abs(k.t / c.duration * cv.width - x) <= 5);
    cv.addEventListener('pointerdown', e => {
      const c = get(); if (!c) return;
      const r = cv.getBoundingClientRect(), x = (e.clientX - r.left) * cv.width / r.width;
      const k = pick(c, x);
      if (e.button === 2) return;
      if (!k) { app.seek(c.start + Math.max(0, Math.min(1, x / cv.width)) * c.duration); return; }
      cv.setPointerCapture(e.pointerId);
      app.busy = true;
      const t0 = k.t;
      this.selKey = { clip: c.id, key, t: k.t };
      let moved = false;
      const move = ev => {
        const nx = (ev.clientX - r.left) * cv.width / r.width;
        const fps = app.doc.settings.fps;
        const t = Math.max(0, Math.min(c.duration, Math.round(nx / cv.width * c.duration * fps) / fps));
        if (Math.abs(t - t0) > 1e-6) moved = true;
        k.t = t; this.selKey.t = t;
        app.dirty = true; this.drawLane(cv, c, key);
      };
      const up = () => {
        cv.removeEventListener('pointermove', move); cv.removeEventListener('pointerup', up);
        app.busy = false;
        if (moved) {
          const keys = [...c.keyframes[key]].sort((a, b) => a.t - b.t);
          app.send([{ op: 'set_keyframes', clip: c.id, prop: key, keys }], `Déplacer une image clé (${label})`);
        } else app.seek(c.start + k.t);
      };
      cv.addEventListener('pointermove', move); cv.addEventListener('pointerup', up);
    });
    cv.addEventListener('contextmenu', e => {
      e.preventDefault();
      const c = get(); if (!c) return;
      const r = cv.getBoundingClientRect(), k = pick(c, (e.clientX - r.left) * cv.width / r.width);
      if (!k) return;
      contextMenu(e.clientX, e.clientY, [
        ...EASE_LABELS.map(([v, l]) => ({ label: l, on: k.ease === v, action: () =>
          app.send([{ op: 'set_keyframe', clip: c.id, prop: key, t: k.t, v: k.v, ease: v }], `Interpolation : ${l}`) })),
        '-',
        { label: 'Supprimer l\'image clé', action: () => app.send([{ op: 'remove_keyframe', clip: c.id, prop: key, t: k.t }], 'Supprimer une image clé') },
      ]);
    });
  }

  deleteSelectedKey() {
    const s = this.selKey;
    if (!s || !this.app.doc.clips[s.clip]) return false;
    this.app.send([{ op: 'remove_keyframe', clip: s.clip, prop: s.key, t: s.t }], 'Supprimer une image clé');
    this.selKey = null;
    return true;
  }

  fade(c, dir, prop) {
    const d = Math.min(0.5, c.duration / 2);
    const peak = prop === 'opacity' ? 1 : c.props.volume || 1;
    const others = (c.keyframes[prop] || []).filter(k => dir === 'in' ? k.t > d + 1e-3 : k.t < c.duration - d - 1e-3);
    const add = dir === 'in' ? [{ t: 0, v: 0, ease: 'ease_out' }, { t: d, v: peak }] : [{ t: c.duration - d, v: peak, ease: 'ease_in' }, { t: c.duration, v: 0 }];
    this.app.send([{ op: 'set_keyframes', clip: c.id, prop, keys: [...others, ...add].sort((a, b) => a.t - b.t) }],
                  dir === 'in' ? 'Fondu d\'entrée' : 'Fondu de sortie');
  }

  // ------------------------------------------------------------ texte
  textSection(get, root) {
    const app = this.app, id = get().id, tx = get().text;
    const set = (k, v, label) => app.send([{ op: 'update_clip', clip: id, text: { [k]: v } }], label);
    const live = (k, v) => { get().text[k] = v; app.dirty = true; };
    const num = (label, k, cfg) => this.plainRow(label, tx[k], { ...cfg, app }, v => set(k, v, label));
    const s = this.section('Texte', root);
    const ta = el('textarea', { rows: 3, class: 'textcontent' }, tx.content);
    ta.addEventListener('input', () => live('content', ta.value));
    ta.addEventListener('change', () => set('content', ta.value, 'Modifier le texte'));
    s.append(ta);
    const font = el('input', { list: 'fontlist', value: tx.font, class: 'fontpick', spellcheck: 'false' });
    font.addEventListener('change', () => set('font', font.value, 'Police'));
    s.append(this.line('Police', font));
    s.append(this.line('Graisse', selectInput(WEIGHTS, tx.weight, v => set('weight', +v, 'Graisse')),
      toggleBtn('I', tx.italic, v => set('italic', v, 'Italique'), 'Italique'),
      toggleBtn('AA', tx.uppercase, v => set('uppercase', v, 'Majuscules'), 'Tout en majuscules')));
    s.append(num('Taille', 'size', { step: 1, decimals: 0, unit: ' px', min: 1 }));
    s.append(this.line('Alignement', segmented([['left', '⯇', 'Gauche'], ['center', '≡', 'Centre'], ['right', '⯈', 'Droite']], tx.align,
      v => set('align', v, 'Alignement'))));
    s.append(num('Interligne', 'line_height', { step: 0.01, decimals: 2, min: 0.5, max: 4 }));
    s.append(num('Espacement', 'letter_spacing', { step: 0.2, decimals: 1, unit: ' px' }));
    s.append(this.line('Couleur', colorInput(tx.color, v => set('color', v, 'Couleur du texte'), v => live('color', v))));

    const st = this.section('Contour', root);
    st.append(this.line('Couleur', colorInput(tx.stroke_color, v => set('stroke_color', v, 'Couleur du contour'), v => live('stroke_color', v))));
    st.append(num('Épaisseur', 'stroke_width', { step: 0.2, decimals: 1, unit: ' px', min: 0 }));

    const sh = this.section('Ombre portée', root);
    sh.append(this.line('Couleur', colorInput(tx.shadow_color, v => set('shadow_color', v, 'Couleur de l\'ombre'), v => live('shadow_color', v))));
    sh.append(num('Opacité', 'shadow_opacity', { step: 0.005, decimals: 0, mul: 100, unit: ' %', min: 0, max: 1 }));
    sh.append(num('Flou', 'shadow_blur', { step: 0.5, decimals: 0, unit: ' px', min: 0 }));
    sh.append(num('Décalage X', 'shadow_x', { step: 0.5, decimals: 0, unit: ' px' }));
    sh.append(num('Décalage Y', 'shadow_y', { step: 0.5, decimals: 0, unit: ' px' }));

    const bg = this.section('Fond', root);
    bg.append(this.line('Afficher', toggleBtn(tx.background ? 'Oui' : 'Non', !!tx.background,
      v => set('background', v ? '#000000' : null, v ? 'Fond du texte' : 'Retirer le fond'))));
    if (tx.background) {
      bg.append(this.line('Couleur', colorInput(tx.background, v => set('background', v, 'Couleur du fond'), v => live('background', v))));
      bg.append(num('Opacité', 'background_opacity', { step: 0.005, decimals: 0, mul: 100, unit: ' %', min: 0, max: 1 }));
      bg.append(num('Marge', 'padding', { step: 0.5, decimals: 0, unit: ' px', min: 0 }));
      bg.append(num('Arrondi', 'radius', { step: 0.5, decimals: 0, unit: ' px', min: 0 }));
    }
  }

  // ------------------------------------------------------------ transition d'entrée
  transitionSection(get, root) {
    const app = this.app, c = get(), id = c.id, defs = app.defs();
    const tr = c.trans_in;
    const s = this.section("Transition d'entrée", root);
    const left = app.neighbor(c, -1);
    if (!left) { s.append(el('p', { class: 'muted small' }, 'Inactive : aucun clip ne touche le début de celui-ci.')); }
    const list = Object.values(defs).filter(d => d.kind === 'transition');
    const send = (patch, label) => app.send([{ op: 'set_transition', clip: id, type: tr?.type, duration: tr?.duration ?? 0.5, ...patch }], label);
    s.append(this.line('Type', selectInput([['', 'Aucune (coupe franche)'], ...list.map(d => [d.name, (d.builtin ? '' : '★ ') + d.label])], tr?.type || '',
      v => v ? send({ type: v, params: undefined, duration: Math.min(tr?.duration ?? 0.5, left?.duration ?? 0.5, c.duration) }, `Transition : ${defs[v].label}`)
             : app.send([{ op: 'set_transition', clip: id, type: null }], 'Retirer la transition'))));
    if (!tr) return;
    const maxi = Math.min(left?.duration ?? c.duration, c.duration);
    s.append(this.plainRow('Durée', tr.duration, { step: 1 / app.doc.settings.fps, decimals: 2, unit: ' s', min: 1 / app.doc.settings.fps, max: maxi },
      v => send({ duration: v }, 'Durée de la transition')));
    const def = defs[tr.type];
    for (const [pn, pd] of Object.entries(def?.params || {})) {
      if (pn === 'progress') continue;
      const v = tr.params?.[pn] ?? pd.default;
      if (pd.type === 'color') {
        s.append(this.line(pd.label || pn, colorInput(rgbHex(v), hv => send({ params: { [pn]: hv } }, `Transition · ${pd.label || pn}`))));
      } else {
        const span = (pd.max ?? 1) - (pd.min ?? 0) || 1;
        s.append(this.plainRow(pd.label || pn, v, { step: span / 300, decimals: span >= 50 ? 0 : 2, min: pd.min, max: pd.max },
          nv => send({ params: { [pn]: nv } }, `Transition · ${pd.label || pn}`)));
      }
    }
    s.append(el('p', { class: 'muted small' }, 'Sans assez d\'images avant ce clip ou après le précédent, la transition fige l\'image du bord.'));
  }

  // ------------------------------------------------------------ effets
  effectsSection(get, root) {
    const app = this.app, c = get(), id = c.id, defs = app.defs();
    const s = this.section('Effets', root);
    c.effects.forEach((inst, idx) => {
      const def = defs[inst.effect];
      const title = def ? def.label : `${inst.effect} (introuvable)`;
      const key = 'fx:' + inst.id;
      const closed = this.closed.has(key);
      const body = el('div', { class: 'fx-body' });
      if (closed) body.hidden = true;
      const head = el('div', { class: 'fx-head' },
        el('input', { type: 'checkbox', checked: inst.enabled, title: 'Activer / désactiver',
                      onchange: e => app.send([{ op: 'update_effect', clip: id, fx: inst.id, enabled: e.target.checked }], 'Activer un effet') }),
        el('span', { class: 'chev', onclick: () => { closed ? this.closed.delete(key) : this.closed.add(key); this.build(); } }, closed ? '▸' : '▾'),
        el('span', { class: 'fx-title' + (def?.builtin === false ? ' custom' : '') }, title),
        el('button', { class: 'mini', title: 'Monter', disabled: idx === 0,
                       onclick: () => app.send([{ op: 'update_effect', clip: id, fx: inst.id, index: idx - 1 }], 'Réordonner les effets') }, '↑'),
        el('button', { class: 'mini', title: 'Descendre', disabled: idx === c.effects.length - 1,
                       onclick: () => app.send([{ op: 'update_effect', clip: id, fx: inst.id, index: idx + 1 }], 'Réordonner les effets') }, '↓'),
        el('button', { class: 'mini', title: 'Valeurs par défaut', onclick: () => {
          const params = Object.fromEntries(Object.entries(def?.params || {}).map(([k, p]) => [k, p.default]));
          app.send([{ op: 'update_effect', clip: id, fx: inst.id, params },
                    ...Object.keys(params).map(p => ({ op: 'set_keyframes', clip: id, prop: `fx.${inst.id}.${p}`, keys: [] }))], `Réinitialiser ${title}`);
        } }, '⟲'),
        el('button', { class: 'mini', title: 'Retirer l\'effet', onclick: () => app.send([{ op: 'remove_effect', clip: id, fx: inst.id }], `Retirer ${title}`) }, '×'));
      const err = app.renderer.errors[inst.effect];
      if (err) body.append(el('pre', { class: 'err' }, err));
      if (def?.description) body.append(el('p', { class: 'muted small' }, def.description));
      for (const [pn, pd] of Object.entries(def?.params || {})) {
        const label = pd.label || pn;
        if (pd.type === 'color') {
          const v = inst.params[pn] ?? pd.default;
          body.append(this.line(label, colorInput(rgbHex(v),
            hv => app.send([{ op: 'update_effect', clip: id, fx: inst.id, params: { [pn]: hv } }], `${title} · ${label}`),
            hv => { const i = get().effects.find(e => e.id === inst.id); if (i) { i.params[pn] = hexRgb(hv); app.dirty = true; } })));
        } else {
          const span = (pd.max ?? 1) - (pd.min ?? 0) || 1;
          body.append(this.propRow(get, `fx.${inst.id}.${pn}`, label,
            { step: span / 300, decimals: span >= 50 ? 0 : span >= 5 ? 1 : 2, min: undefined }));
        }
      }
      s.append(el('div', { class: 'fx-card' }, head, body));
    });
    const groups = {};
    for (const d of Object.values(defs)) if (d.kind !== 'transition') (groups[d.builtin ? (d.category || 'Divers') : 'Sur mesure (IA)'] ||= []).push(d);
    const sel = el('select', { class: 'addfx' }, el('option', { value: '' }, '+ Ajouter un effet…'),
      Object.entries(groups).map(([g, ds]) => el('optgroup', { label: g }, ds.map(d => el('option', { value: d.name }, d.label)))));
    sel.addEventListener('change', () => sel.value && app.send([{ op: 'add_effect', clip: id, effect: sel.value }], `Effet ${defs[sel.value].label}`));
    s.append(sel, el('p', { class: 'muted small' }, 'Ou glisse un effet depuis l\'onglet Effets sur un clip.'));
  }
}

function kindLabel(k) {
  return { video: 'Vidéo', audio: 'Audio', image: 'Image', text: 'Texte', solid: 'Aplat' }[k] || k;
}

export function rgbHex(v) {
  return '#' + v.map(x => Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16).padStart(2, '0')).join('');
}

function hexRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}
