// Panneaux de gauche : Médias, Effets, Textes (préréglages), Journal ; boîte « Séquence ».
import { el, fmtTC } from './widgets.js';

export const TEXT_PRESETS = {
  titre: { label: 'Titre principal', desc: 'Grand, centré, apparition en fondu glissé', dur: 4,
    text: { content: 'Titre principal', size: 128, weight: 800, shadow_opacity: 0.55, shadow_blur: 24, shadow_y: 6 },
    keys: { opacity: [{ t: 0, v: 0, ease: 'ease_out' }, { t: 0.5, v: 1 }], y: [{ t: 0, v: 40, ease: 'ease_out' }, { t: 0.5, v: 0 }] } },
  soustitre: { label: 'Sous-titre', desc: 'Sous un titre, plus discret', dur: 4, props: { y: 130 },
    text: { content: 'Sous-titre', size: 60, weight: 500, color: '#d9dde5' },
    keys: { opacity: [{ t: 0, v: 0 }, { t: 0.4, v: 1 }] } },
  bandeau: { label: 'Bandeau (lower third)', desc: 'Nom ou fonction en bas à gauche, glisse depuis la gauche', dur: 5, props: { y: 360 },
    text: { content: 'Prénom Nom\nFonction', size: 46, weight: 600, align: 'left', line_height: 1.25,
            background: '#101215', background_opacity: 0.85, padding: 22, radius: 6 },
    keys: { x: [{ t: 0, v: -120, ease: 'ease_out' }, { t: 0.45, v: 0 }], opacity: [{ t: 0, v: 0 }, { t: 0.3, v: 1 }] } },
  citation: { label: 'Citation', desc: 'Italique, police à empattements', dur: 5,
    text: { content: '« Une phrase qui compte. »', font: 'Georgia', size: 76, weight: 400, italic: true, line_height: 1.3 },
    keys: { opacity: [{ t: 0, v: 0 }, { t: 0.6, v: 1 }] } },
  soustitres: { label: 'Sous-titres', desc: 'Bas de l\'écran, fond semi-transparent', dur: 3, props: { y: 400 },
    text: { content: 'Ligne de sous-titre', size: 52, weight: 700, background: '#000000', background_opacity: 0.6, padding: 14, radius: 4 } },
  impact: { label: 'Gros impact', desc: 'Majuscules, contour épais, arrivée en zoom', dur: 2,
    text: { content: 'Impact', font: 'Impact', size: 190, weight: 400, uppercase: true, stroke_width: 8, letter_spacing: 2 },
    keys: { scale: [{ t: 0, v: 1.35, ease: 'ease_out' }, { t: 0.25, v: 1 }], opacity: [{ t: 0, v: 0 }, { t: 0.12, v: 1 }] } },
};

export class Panneaux {
  constructor(app) {
    this.app = app;
    this.tab = 'media';
    this.filter = '';
    this.body = document.getElementById('left-body');
    for (const b of document.querySelectorAll('#left-tabs button')) {
      b.onclick = () => { this.tab = b.dataset.tab; this.refresh(); };
    }
    // Fin d'une sélection de mots (un seul écouteur pour toute la vie du panneau).
    window.addEventListener('pointerup', () => {
      if (!this.dragging) return;
      this.dragging = false;
      if (!this.moved && this.sel && this.sel.a === this.sel.b && this.wordList?.[this.sel.a]) this.app.seek(this.wordList[this.sel.a].start);
    });
  }

  refresh() {
    for (const b of document.querySelectorAll('#left-tabs button')) b.classList.toggle('on', b.dataset.tab === this.tab);
    if (this.body.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') {
      this.renderList(); return;
    }
    this.body.innerHTML = '';
    const search = el('input', { class: 'search', placeholder: 'Rechercher…', value: this.filter });
    search.addEventListener('input', () => { this.filter = search.value; this.renderList(); });
    search.addEventListener('keydown', e => e.stopPropagation());
    this.list = el('div', { class: 'plist' });
    if (this.tab === 'media') {
      this.body.append(el('div', { class: 'ptools' },
        el('label', { class: 'btn' }, 'Importer…', el('input', { type: 'file', multiple: true, hidden: true,
          onchange: e => this.app.upload(e.target.files) })), search));
    } else if (this.tab !== 'journal' && this.tab !== 'transcript') this.body.append(el('div', { class: 'ptools' }, search));
    this.body.append(this.list);
    this.renderList();
  }

  match(s) { return !this.filter || s.toLowerCase().includes(this.filter.toLowerCase()); }

  renderList() {
    const l = this.list;
    if (!l) return;
    l.innerHTML = '';
    ({ media: () => this.media(l), effects: () => this.effects(l), text: () => this.texts(l), journal: () => this.journal(l),
       transcript: () => this.transcript(l) })[this.tab]();
  }

  media(l) {
    const app = this.app, doc = app.doc;
    const ms = Object.values(doc.media).filter(m => this.match(m.name));
    if (!ms.length) l.append(el('p', { class: 'muted pad' }, 'Glisse des vidéos, sons ou images ici, ou clique sur Importer.'));
    const grid = el('div', { class: 'mgrid' });
    for (const m of ms) {
      const state = m.proxy_error ? 'proxy en échec' : m.kind === 'image' || m.proxy || m.browser_ok ? '' : 'proxy…';
      const thumb = m.kind === 'audio' ? el('div', { class: 'mthumb audio' }, '♪') : el('img', { class: 'mthumb', src: `/media/${m.id}/thumb`, draggable: 'false' });
      const card = el('div', { class: 'mcard', draggable: 'true', title: `${m.name}\nDouble-clic : ajouter à la tête de lecture\nGlisser : déposer sur la timeline` },
        thumb, el('div', { class: 'mmeta' }, el('span', { class: 'mname' }, m.name),
          el('small', {}, `${m.kind === 'image' ? 'Image' : fmtTC(m.duration, doc.settings.fps)}${m.width ? ` · ${m.width}×${m.height}` : ''} ${state}`)));
      card.addEventListener('dragstart', e => { e.dataTransfer.setData('application/x-media', m.id); e.dataTransfer.effectAllowed = 'copy'; });
      card.addEventListener('dblclick', () => app.addMedia(m.id, app.playhead));
      grid.append(card);
    }
    l.append(grid);
  }

  effects(l) {
    const app = this.app, defs = app.defs(), groups = {};
    for (const d of Object.values(defs)) {
      if (!this.match(d.label + ' ' + d.name)) continue;
      (groups[d.builtin ? (d.category || 'Divers') : 'Sur mesure (IA)'] ||= []).push(d);
    }
    for (const [g, ds] of Object.entries(groups)) {
      l.append(el('div', { class: 'pgroup' }, g));
      for (const d of ds) {
        const it = el('div', { class: 'pitem', draggable: 'true', title: 'Glisser sur un clip · double-clic : appliquer à la sélection' },
          el('span', { class: 'fxicon' + (d.builtin ? '' : ' custom') }, d.builtin ? 'fx' : '★'), el('span', {}, d.label),
          d.blend === 'replace' ? el('small', { class: 'muted' }, ' fusion') : null);
        it.addEventListener('dragstart', e => e.dataTransfer.setData('application/x-effect', d.name));
        it.addEventListener('dblclick', () => app.applyEffect(d.name, app.selection));
        l.append(it);
      }
    }
    l.append(el('p', { class: 'muted pad small' }, 'Besoin d\'un effet qui n\'existe pas ? Demande-le au copilote : il l\'écrit en direct (★).'));
  }

  texts(l) {
    const app = this.app;
    for (const [id, p] of Object.entries(TEXT_PRESETS)) {
      if (!this.match(p.label)) continue;
      const it = el('div', { class: 'tpreset', draggable: 'true', title: 'Glisser sur la timeline · double-clic : ajouter à la tête de lecture' },
        el('div', { class: 'tsample', style: `font-family:${p.text.font || 'Segoe UI'};font-weight:${p.text.weight};font-style:${p.text.italic ? 'italic' : 'normal'};${p.text.uppercase ? 'text-transform:uppercase;' : ''}` }, p.label),
        el('small', { class: 'muted' }, p.desc));
      it.addEventListener('dragstart', e => e.dataTransfer.setData('application/x-textpreset', id));
      it.addEventListener('dblclick', () => app.addTextPreset(id, app.playhead));
      l.append(it);
    }
  }

  // ------------------------------------------------------------ parole (montage par le texte)
  words() {
    const doc = this.app.doc, out = [], seen = new Set();
    for (const c of Object.values(doc.clips).sort((a, b) => a.start - b.start)) {
      if (c.type !== 'media') continue;
      const ws = this.app.transcripts.get(c.media);
      if (!ws) continue;
      const a = c.in, b = c.in + c.duration * c.speed;
      for (const w of ws) {
        const mid = (w.s + w.e) / 2;
        if (!(a <= mid && mid < b)) continue;
        const s = c.start + (Math.max(w.s, a) - a) / c.speed, e = c.start + (Math.min(w.e, b) - a) / c.speed;
        const key = `${c.media}|${w.s}|${s.toFixed(2)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ w: w.w, start: s, end: e, p: w.p ?? 1, clip: c.id });
      }
    }
    return out.sort((x, y) => x.start - y.start);
  }

  loadTranscripts() {
    const app = this.app;
    for (const m of Object.values(app.doc.media)) {
      if (!m.transcript || app.transcriptPaths.get(m.id) === m.transcript) continue;
      app.transcriptPaths.set(m.id, m.transcript);
      fetch(`/api/transcript/${m.id}`).then(r => (r.ok ? r.json() : null)).then(d => {
        if (d) { app.transcripts.set(m.id, d.words); if (this.tab === 'transcript') this.renderList(); }
      });
    }
  }

  async textEdit(body) {
    const r = await fetch('/api/text_edit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok || !d.ok) { this.app.toast(d.error || d.detail || 'Échec', true); return; }
    this.app.toast(d.cuts ? `${d.cuts} coupe(s), ${String(d.removed_seconds).replace('.', ',')} s retirées` : 'Rien à couper.');
    this.sel = null;
  }

  subtitlesBox() {
    const app = this.app;
    const STYLES = [['classique', 'Classique (bas, fond)'], ['shorts', 'Shorts (gros, 1 à 3 mots)'], ['minimal', 'Minimal (blanc, ombre)']];
    this.subStyle ||= app.doc.settings.height > app.doc.settings.width ? 'shorts' : 'classique';
    const style = el('select', {}, STYLES.map(([v, l]) => el('option', { value: v, selected: v === this.subStyle }, l)));
    style.onchange = () => { this.subStyle = style.value; };
    const words = el('input', { type: 'number', min: '1', max: '12', step: '1', placeholder: 'auto', class: 'gapin',
                                title: 'Nombre maximal de mots par sous-titre (vide = selon le style)' });
    words.addEventListener('keydown', e => e.stopPropagation());
    const post = async (url, body) => {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok || d.ok === false) { app.toast(d.error || d.detail || 'Échec', true); return null; }
      return d;
    };
    const hasTrack = app.doc.tracks.some(t => t.role === 'subtitles');
    return el('div', { class: 'tr-subs' },
      el('div', { class: 'tr-subs-title' }, 'Sous-titres automatiques'),
      el('div', { class: 'tr-subs-row' }, style, el('span', { class: 'muted small' }, 'mots max'), words),
      el('div', { class: 'tr-subs-row' },
        el('button', { class: 'primary', title: 'Remplace les sous-titres existants de la piste « Sous-titres »', onclick: async () => {
          const body = { style: style.value };
          if (+words.value > 0) body.max_words = +words.value;
          const d = await post('/api/subtitles', body);
          if (d) app.toast(`${d.count} sous-titres posés sur la piste « Sous-titres »`);
        } }, hasTrack ? 'Régénérer les sous-titres' : 'Générer les sous-titres'),
        el('button', { disabled: !hasTrack, title: 'Fichier .srt à envoyer à YouTube avec la vidéo', onclick: async () => {
          const d = await post('/api/subtitles/save_srt', {});
          if (d) {
            app.toast(`${d.count} sous-titres enregistrés : ${d.path}`);
            post('/api/reveal', { path: d.path });
          }
        } }, 'Enregistrer le .srt')),
      el('p', { class: 'muted small' }, 'Chaque sous-titre est un clip texte : clique-le dans la timeline pour corriger le texte ou le style.'));
  }

  hasSelection() { return this.tab === 'transcript' && this.sel && this.sel.a !== null; }

  cutSelection() {
    if (!this.hasSelection()) return false;
    const i = Math.min(this.sel.a, this.sel.b), j = Math.max(this.sel.a, this.sel.b);
    this.textEdit({ action: 'words', from: i, to: j });
    return true;
  }

  transcript(l) {
    const app = this.app, doc = app.doc;
    this.loadTranscripts();
    const sounding = Object.values(doc.media).filter(m => m.kind === 'audio' || (m.kind === 'video' && m.has_audio));
    const box = el('div', { class: 'tr-media' });
    if (!sounding.length) box.append(el('p', { class: 'muted small pad' }, 'Importe une vidéo ou un son avec de la voix.'));
    for (const m of sounding) {
      const st = m.transcript_state || {};
      let right;
      if (st.state === 'running') right = el('small', { class: 'muted' }, `transcription… ${Math.round((st.progress || 0) * 100)} %`);
      else if (m.transcript) right = el('small', { class: 'muted' }, `✓ ${st.words ?? ''} mots`);
      else right = el('button', { class: 'mini on', onclick: async () => {
        const r = await fetch('/api/transcribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ media: m.id }) });
        const d = await r.json();
        app.toast(r.ok ? d.message : d.detail, !r.ok);
      } }, 'Transcrire');
      if (st.state === 'error') right = el('small', { class: 'err', title: st.error }, 'échec — réessayer ?');
      box.append(el('div', { class: 'tr-row' }, el('span', { class: 'mname' }, m.name), right));
    }
    l.append(box);
    const gap = el('input', { type: 'number', value: '0.7', step: '0.1', min: '0.2', class: 'gapin', title: 'Blanc minimal (s)' });
    gap.addEventListener('keydown', e => e.stopPropagation());
    l.append(el('div', { class: 'tr-actions' },
      el('button', { onclick: () => this.textEdit({ action: 'fillers' }), title: 'Coupe euh, hum, ben… et resserre' }, 'Retirer les hésitations'),
      el('span', { class: 'tr-gap' }, el('button', { onclick: () => this.textEdit({ action: 'silences', min_gap: +gap.value || 0.7, keep: 0.25 }),
        title: 'Réduit chaque blanc plus long que la valeur à 0,25 s' }, 'Raccourcir les silences >'), gap, ' s'),
      el('button', { onclick: () => this.cutSelection(), title: 'Ou touche Suppr' }, 'Couper la sélection')));
    l.append(this.subtitlesBox());
    const words = this.words();
    this.wordList = words;
    const text = el('div', { class: 'ttext' });
    if (!words.length) text.append(el('p', { class: 'muted small' }, 'Aucun mot dans la séquence : transcris un média puis place-le sur la timeline.'));
    words.forEach((w, i) => {
      if (i > 0 && w.start - words[i - 1].end > 1.5) text.append(el('br'), el('br'));
      const sp = el('span', { class: 'wd' + (w.p < 0.5 ? ' low' : ''), 'data-i': i, title: `${w.start.toFixed(2)} s` }, w.w.trim());
      // Recoller comme Whisper l'a découpé : « n » + « 'a » → « n'a », pas d'espace avant la ponctuation.
      const prev = i > 0 ? words[i - 1].w.trim() : '';
      if (i > 0 && !/^['’,.!?;:…)\-]/.test(w.w.trim()) && !/['’\-(]$/.test(prev)) text.append(' ');
      text.append(sp);
    });
    const idx = e => { const t = e.target.closest('.wd'); return t ? +t.dataset.i : null; };
    text.addEventListener('pointerdown', e => {
      const i = idx(e); if (i === null) return;
      e.preventDefault();
      if (e.shiftKey && this.sel) this.sel.b = i; else this.sel = { a: i, b: i };
      this.dragging = true; this.moved = false; this.paintSel();
    });
    text.addEventListener('pointerover', e => {
      if (!this.dragging) return;
      const i = idx(e); if (i === null) return;
      if (i !== this.sel.b) { this.sel.b = i; this.moved = true; this.paintSel(); }
    });
    l.append(text);
    this.textEl = text;
    this.paintSel();
    this.tick(true);
  }

  paintSel() {
    if (!this.textEl) return;
    const s = this.sel, i = s ? Math.min(s.a, s.b) : -1, j = s ? Math.max(s.a, s.b) : -2;
    for (const sp of this.textEl.querySelectorAll('.wd')) { const k = +sp.dataset.i; sp.classList.toggle('sel', k >= i && k <= j); }
  }

  tick(force = false) {
    if (this.tab !== 'transcript' || !this.wordList || !this.textEl) return;
    const t = this.app.playhead, ws = this.wordList;
    let lo = 0, hi = ws.length - 1, cur = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (ws[m].start <= t) { cur = m; lo = m + 1; } else hi = m - 1; }
    if (cur >= 0 && t > ws[cur].end + 0.3) cur = -1;
    if (cur === this.curWord && !force) return;
    this.textEl.querySelector('.wd.cur')?.classList.remove('cur');
    this.curWord = cur;
    const sp = cur >= 0 ? this.textEl.querySelector(`.wd[data-i="${cur}"]`) : null;
    if (sp) { sp.classList.add('cur'); if (this.app.playing) sp.scrollIntoView({ block: 'nearest' }); }
  }

  journal(l) {
    const app = this.app;
    l.append(el('div', { class: 'copilot' },
      el('strong', {}, 'Copilote IA'),
      el('p', { class: 'small muted' }, 'Ouvre Claude Code dans le dossier de l\'éditeur : il se connecte tout seul (serveur MCP « editeur »). Ce qu\'il fait apparaît ici en orange et s\'annule avec Ctrl+Z.')));
    for (const e of [...app.journal].reverse().slice(0, 80)) {
      l.append(el('div', { class: 'jitem' + (e.origin === 'ia' ? ' ia' : '') }, el('b', {}, e.origin === 'ia' ? 'IA' : 'Toi'), ' ', e.label));
    }
  }
}

// ------------------------------------------------------------ boîte « Séquence »
const PRESETS = [['1920x1080', 'HD 1080p (16:9)'], ['3840x2160', '4K UHD (16:9)'], ['1280x720', 'HD 720p (16:9)'],
                 ['1080x1920', 'Vertical 9:16 (Shorts, Reels, TikTok)'], ['1080x1080', 'Carré 1:1'], ['1080x1350', 'Portrait 4:5']];

export function openSettings(app) {
  const s = app.doc.settings, modal = document.getElementById('modal');
  const w = el('input', { type: 'number', value: s.width, min: 16, step: 2 });
  const h = el('input', { type: 'number', value: s.height, min: 16, step: 2 });
  const fps = el('select', {}, [23.976, 24, 25, 29.97, 30, 50, 59.94, 60].map(f => el('option', { value: f, selected: Math.abs(f - s.fps) < 1e-3 }, `${f} i/s`)));
  const bg = el('input', { type: 'color', value: s.background });
  const preset = el('select', {}, el('option', { value: '' }, 'Personnalisé'),
    PRESETS.map(([v, l]) => el('option', { value: v, selected: v === `${s.width}x${s.height}` }, l)));
  preset.onchange = () => { if (preset.value) { const [a, b] = preset.value.split('x'); w.value = a; h.value = b; } };
  const close = () => { modal.hidden = true; modal.innerHTML = ''; };
  modal.append(el('div', { class: 'dialog' },
    el('h3', {}, 'Paramètres de la séquence'),
    el('label', {}, 'Format', preset),
    el('div', { class: 'two' }, el('label', {}, 'Largeur', w), el('label', {}, 'Hauteur', h)),
    el('label', {}, 'Images par seconde', fps),
    el('label', {}, 'Couleur de fond', bg),
    el('div', { class: 'actions' },
      el('button', { onclick: close }, 'Annuler'),
      el('button', { class: 'primary', onclick: () => {
        app.send([{ op: 'update_settings', width: +w.value, height: +h.value, fps: +fps.value, background: bg.value }], 'Paramètres de la séquence');
        close();
      } }, 'Appliquer'))));
  modal.hidden = false;
  modal.onclick = e => { if (e.target === modal) close(); };
  modal.querySelectorAll('input, select').forEach(i => i.addEventListener('keydown', e => e.stopPropagation()));
}
