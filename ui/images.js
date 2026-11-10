// Onglet « Images IA » : génération locale d'illustrations (DreamShaper 8 + LCM).
// Le formulaire est construit une fois (rien n'est perdu quand la timeline change) ;
// l'état du générateur et des travaux arrive du serveur en messages « images ».
import { el } from './widgets.js';

const ASPECTS = { sequence: 'Comme la séquence', '16:9': '16:9 paysage', '9:16': '9:16 vertical', '1:1': '1:1 carré', '4:5': '4:5 portrait' };
const DEVICES = { auto: 'Auto', cuda: 'Carte graphique', cpu: 'Processeur' };

function duree(s) {
  if (s == null) return '';
  s = Math.round(s);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')}`;
}

export class PanneauImages {
  constructor(app) {
    this.app = app;
    this.st = { prompt: '', negative: '', style: 'illustration', aspect: 'sequence', quality: 'rapide', count: 1,
                seed: '', place: false, duration: 4, zoom: true };
    this.seen = new Map();
    this.form = null;
  }

  async load() {
    try {
      const r = await fetch('/api/images/status');
      if (r.ok) this.update(await r.json(), true);
    } catch { /* serveur indisponible : réessai à l'ouverture suivante */ }
  }

  // Message du serveur : états des travaux, toasts de fin, repeinture si l'onglet est ouvert.
  update(s, initial = false) {
    for (const j of s.jobs || []) {
      const before = this.seen.get(j.id);
      if (!initial && before !== j.state) {
        if (!before && j.origin === 'ia') this.app.toast(`IA · génère une image : ${j.prompt.slice(0, 50)}`);
        if (j.state === 'done') this.app.toast(`Image prête (${duree(j.seconds)}) : ${j.prompt.slice(0, 50)}`);
        if (j.state === 'error') this.app.toast(`Image en échec : ${(j.error || '').split('\n')[0].slice(0, 120)}`, true);
      }
      this.seen.set(j.id, j.state);
    }
    this.s = s;
    if (this.app.panels.tab === 'images') this.paint();
  }

  render(l) {
    if (!this.s) this.load();
    if (!this.form) this.form = this.buildForm();
    const active = document.activeElement, keep = this.form.contains(active) ? active : null;
    const range = keep && 'selectionStart' in keep ? [keep.selectionStart, keep.selectionEnd] : null;
    this.statusEl = el('div', { class: 'img-status' });
    this.jobsEl = el('div', { class: 'img-jobs' });
    this.galleryEl = el('div', {});
    l.append(this.statusEl, this.form, this.jobsEl, this.galleryEl);
    if (keep) { keep.focus(); if (range) keep.setSelectionRange(...range); }
    this.paint();
  }

  field(key, input, ev = 'input', cast = v => v) {
    input.addEventListener(ev, () => { this.st[key] = input.type === 'checkbox' ? input.checked : cast(input.value); this.paintEstimate(); });
    input.addEventListener('keydown', e => e.stopPropagation());
    return input;
  }

  buildForm() {
    const st = this.st;
    const prompt = this.field('prompt', el('textarea', { rows: 4, class: 'img-prompt',
      placeholder: 'Décris l\'image, en anglais de préférence (le modèle comprend mal le français).\nEx. : a cozy desk with a laptop showing code, warm lamp light' }));
    prompt.value = st.prompt;
    prompt.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.generate(); } });
    const neg = this.field('negative', el('textarea', { rows: 2, placeholder: 'Par défaut : flou, filigrane, texte, déformations…' }));
    neg.value = st.negative;
    const sel = (key, opts) => {
      const s = el('select', {}, Object.entries(opts).map(([v, l]) => el('option', { value: v, selected: String(st[key]) === v }, l)));
      return this.field(key, s, 'change', key === 'count' ? Number : v => v);
    };
    this.styleSel = el('select', {});
    this.field('style', this.styleSel, 'change');
    const seed = this.field('seed', el('input', { type: 'number', min: 0, step: 1, placeholder: 'au hasard', class: 'img-seed',
      title: 'Même graine + même description = même image. Vide : une nouvelle à chaque fois.' }));
    seed.value = st.seed;
    const place = this.field('place', el('input', { type: 'checkbox', checked: st.place }), 'change');
    const dur = this.field('duration', el('input', { type: 'number', min: 0.5, step: 0.5, value: st.duration, class: 'gapin' }), 'input', Number);
    const zoom = this.field('zoom', el('input', { type: 'checkbox', checked: st.zoom }), 'change');
    this.btn = el('button', { class: 'primary', onclick: () => this.generate() }, 'Générer');
    this.estimateEl = el('span', { class: 'muted small' });
    return el('div', { class: 'img-form' },
      prompt,
      el('details', {}, el('summary', { class: 'muted small' }, 'À éviter (prompt négatif)'), neg),
      el('div', { class: 'img-grid' },
        el('label', {}, 'Style', this.styleSel),
        el('label', {}, 'Format', sel('aspect', ASPECTS)),
        el('label', {}, 'Qualité', sel('quality', { rapide: 'Rapide · 4 étapes', fine: 'Fine · 6 étapes' })),
        el('label', {}, 'Nombre', sel('count', { 1: '1', 2: '2', 3: '3', 4: '4' })),
        el('label', {}, 'Graine', seed)),
      el('div', { class: 'img-row' },
        el('label', { class: 'chk' }, place, 'Poser à la tête de lecture'), dur, el('span', { class: 'muted small' }, 's'),
        el('label', { class: 'chk', title: 'Zoom lent de 102 % à 110 % (effet Ken Burns)' }, zoom, 'Zoom lent')),
      el('div', { class: 'img-row' }, this.btn, this.estimateEl));
  }

  body() {
    const st = this.st, b = { prompt: st.prompt, style: st.style, aspect: st.aspect, quality: st.quality, count: st.count };
    if (st.negative.trim()) b.negative = st.negative.trim();
    if (st.seed !== '' && st.seed != null) b.seed = Number(st.seed);
    if (st.place) b.place = { at: this.app.playhead, duration: st.duration || 4, zoom: st.zoom };
    return b;
  }

  async post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { this.app.toast(d.detail || 'Échec', true); return null; }
    return d;
  }

  generate(body = this.body()) {
    if (!body.prompt?.trim()) { this.app.toast('Décris l\'image à générer.', true); return; }
    return this.post('/api/images/generate', body);
  }

  paintEstimate() {
    if (!this.estimateEl || !this.s) return;
    const s = this.s, perf = s.perf?.[s.calcul]?.v;
    if (!perf) { this.estimateEl.textContent = ''; return; }
    // même formule que moteur/images.py (aire ~912×512)
    const steps = this.st.quality === 'fine' ? 6 : 4, mpx = 912 * 512 / 1e6;
    const dec = this.st.quality === 'fine' ? (s.calcul === 'cpu' ? 33 : 5) : 0;
    const one = perf * steps * mpx + dec + 2;
    this.estimateEl.textContent = `≈ ${duree(one)} par image (${s.calcul === 'cpu' ? 'processeur' : 'carte graphique'})`;
  }

  paint() {
    const s = this.s;
    if (!this.statusEl || !s) return;
    // styles (une fois)
    if (!this.styleSel.options.length) {
      for (const [v, l] of Object.entries(s.styles)) this.styleSel.append(el('option', { value: v, selected: v === this.st.style }, l));
    }
    // ligne d'état
    const st = this.statusEl;
    st.innerHTML = '';
    if (!s.available) {
      st.append(el('p', { class: 'warn' }, 'Aucun Python avec torch + diffusers trouvé : définis EDITEUR_IMAGE_PY.'));
    }
    const devSel = el('select', { title: 'Où calculer les images' },
      Object.entries(DEVICES).map(([v, l]) => el('option', { value: v, selected: v === s.pref }, v === 'auto' ? `Auto (${s.calcul === 'cpu' ? 'processeur' : 'carte graphique'})` : l)));
    devSel.onchange = () => this.post('/api/images/settings', { device: devSel.value });
    const g = s.gpus?.[0];
    st.append(el('div', { class: 'img-head' }, el('b', {}, s.model), el('span', { class: 'muted small' }, ` · ${s.licence}`)),
      el('div', { class: 'img-row' }, el('span', { class: 'muted small' }, 'Calcul'), devSel,
        el('span', { class: 'muted small' }, g ? `${g.name.replace('NVIDIA GeForce ', '')} · ${Math.round(g.vram_mo / 1024)} Go` : 'pas de carte compatible')));
    const pc = s.perf?.cuda?.v, pp = s.perf?.cpu?.v;
    if (s.pref === 'auto' && s.calcul === 'cpu' && pc && pp) {
      st.append(el('p', { class: 'muted small' }, `La carte graphique a été mesurée ${Math.round(pc / pp)}× plus lente que le processeur (elle chauffe et se bride) : calcul sur le processeur.`));
    }
    if (!s.weights) st.append(el('p', { class: 'muted small' }, `Premier lancement : téléchargement d'environ ${String(s.poids_go).replace('.', ',')} Go de poids.`));
    const etat = { off: '', starting: 'Démarrage du générateur…', loading: 'Chargement du modèle en mémoire…',
                   downloading: `Téléchargement du modèle : ${Math.round((s.detail?.progress || 0) * 100)} % (${s.detail?.done_mo || 0} / ${s.detail?.total_mo || '?'} Mo)`,
                   ready: 'Modèle chargé.', busy: 'Génération en cours…', error: '' }[s.state] || '';
    if (etat) st.append(el('p', { class: 'muted small' }, etat));
    if (s.state === 'error' && s.error) st.append(el('p', { class: 'warn small' }, s.error.split('\n').slice(0, 3).join(' · ')));
    this.btn.disabled = !s.available;
    this.paintEstimate();
    // travaux en cours
    this.jobsEl.innerHTML = '';
    const now = Date.now() / 1000;
    for (const j of [...s.jobs].reverse()) {
      if (!['queued', 'running'].includes(j.state) && !(j.state === 'error' && now - j.created < 600)) continue;
      const fill = el('div', { class: 'bar-fill', style: `width:${Math.round(j.progress * 100)}%` });
      const wait = { starting: 'Démarrage du générateur…', loading: 'Chargement du modèle (une fois par session)…',
                     downloading: `Téléchargement du modèle : ${Math.round((s.detail?.progress || 0) * 100)} %` }[s.state];
      const info = j.state === 'queued' ? (wait || 'En attente') : j.state === 'error' ? `Échec : ${(j.error || '').split('\n')[0]}`
        : `${j.step || 'préparation'} · ${duree(now - (j.started || now))}${j.estimate_s ? ` / ≈ ${duree(j.estimate_s)}` : ''}`;
      this.jobsEl.append(el('div', { class: 'img-job' + (j.state === 'error' ? ' err' : '') },
        el('div', { class: 'img-row' }, el('span', { class: 'mname', title: j.full_prompt }, (j.origin === 'ia' ? 'IA · ' : '') + j.prompt),
          j.state !== 'error' ? el('button', { class: 'mini', title: 'Annuler', onclick: () => this.post('/api/images/cancel', { job: j.id }) }, '✕') : null),
        j.state !== 'error' ? el('div', { class: 'bar' }, fill) : null,
        el('small', { class: j.state === 'error' ? 'warn' : 'muted' }, info)));
    }
    this.paintGallery();
  }

  paintGallery() {
    const app = this.app, g = this.galleryEl;
    g.innerHTML = '';
    const ms = Object.values(app.doc.media).filter(m => m.generated).reverse();
    if (!ms.length) {
      g.append(el('p', { class: 'muted pad small' }, 'Les images générées arrivent ici et dans Médias. Glisse-les sur la timeline. Le copilote peut aussi illustrer un passage : « illustre 0:12-0:18 avec un bureau la nuit ».'));
      return;
    }
    g.append(el('div', { class: 'pgroup' }, `Générées (${ms.length})`));
    const grid = el('div', { class: 'mgrid' });
    for (const m of ms) {
      const gen = m.generated;
      const card = el('div', { class: 'mcard', draggable: 'true',
        title: `${gen.prompt}\nStyle : ${gen.style} · graine ${gen.seed} · ${m.width}×${m.height}\nGlisser sur la timeline · double-clic : à la tête de lecture` },
        el('img', { class: 'mthumb', src: `/media/${m.id}/thumb`, draggable: 'false' }),
        el('div', { class: 'mmeta' }, el('span', { class: 'mname' }, gen.prompt),
          el('div', { class: 'img-actions' },
            el('button', { class: 'mini', title: 'Variante : même description, autre graine',
              onclick: e => { e.stopPropagation(); this.generate({ prompt: gen.prompt, style: gen.style, aspect: gen.aspect, quality: gen.quality, negative: gen.negative, count: 1 }); } }, '↻ Variante'),
            el('button', { class: 'mini', title: 'Reprendre ces réglages dans le formulaire',
              onclick: e => { e.stopPropagation(); this.reuse(gen); } }, '✎'))));
      card.addEventListener('dragstart', e => { e.dataTransfer.setData('application/x-media', m.id); e.dataTransfer.effectAllowed = 'copy'; });
      card.addEventListener('dblclick', () => app.addMedia(m.id, app.playhead));
      grid.append(card);
    }
    g.append(grid);
  }

  reuse(gen) {
    Object.assign(this.st, { prompt: gen.prompt, style: gen.style, aspect: gen.aspect, quality: gen.quality, seed: String(gen.seed) });
    this.form = null;                      // reconstruit avec les nouvelles valeurs
    this.app.panels.refresh();
    this.form.querySelector('textarea').focus();
  }
}
