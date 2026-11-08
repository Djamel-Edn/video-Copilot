// Application : connexion temps réel, état, boucle de rendu, actions, raccourcis.
import { Rendu } from './rendu.js';
import { Sources } from './sources.js';
import { Timeline } from './timeline.js';
import { Inspecteur } from './inspecteur.js';
import { Moniteur } from './moniteur.js';
import { Panneaux, TEXT_PRESETS, openSettings } from './panneaux.js';
import { el } from './widgets.js';
import { openExport, runExport } from './export.js';

const $ = s => document.querySelector(s);

class App {
  constructor() {
    this.doc = null;
    this.builtin = {};
    this.playhead = 0;
    this.playing = false;
    this.selection = [];
    this.journal = [];
    this.tool = 'select';
    this.snap = true;
    this.dirty = true;
    this.busy = false;
    this.pending = new Map();
    this.thumbs = new Map();
    this.peaks = new Map();
    this.clipboard = [];
    this.transcripts = new Map();
    this.transcriptPaths = new Map();
    this.renderer = new Rendu($('#preview'));
    this.sources = new Sources(() => { this.dirty = true; });
    this.timeline = new Timeline($('#timeline'), this);
    this.inspector = new Inspecteur($('#inspector'), this);
    this.monitor = new Moniteur(this);
    this.panels = new Panneaux(this);
    this.bindUI();
    this.loadFonts();
    this.connect();
    requestAnimationFrame(t => this.tick(t));
  }

  // ------------------------------------------------------------ réseau
  connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => this.status('Connecté', true);
    ws.onclose = () => { this.status('Déconnecté — reconnexion…', false); setTimeout(() => this.connect(), 1000); };
    ws.onmessage = ev => this.onMessage(JSON.parse(ev.data));
  }

  status(txt, ok) { const s = $('#status'); s.textContent = txt; s.className = ok ? 'ok' : 'ko'; }
  sendRaw(msg) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(msg)); }

  // Envoie un lot d'opérations ; la promesse donne les identifiants créés.
  send(ops, label) {
    const req = Math.random().toString(36).slice(2);
    return new Promise(res => {
      this.pending.set(req, res);
      this.sendRaw({ type: 'ops', ops, label, req });
      setTimeout(() => { if (this.pending.delete(req)) res(null); }, 5000);
    });
  }

  onMessage(msg) {
    if (msg.type === 'snapshot') {
      this.doc = msg.project;
      this.builtin = msg.builtin_effects;
      this.journal = msg.journal;
      this.monitor.fit();
      this.timeline.fitAll();
      this.refreshAll(true);
    } else if (msg.type === 'patch') {
      for (const p of msg.patches) applyPatch(this.doc, p);
      const roots = new Set(msg.patches.map(p => p.path[0]));
      if (msg.origin === 'ia') {
        msg.patches.filter(p => p.path[0] === 'clips').forEach(p => this.timeline.flash(p.path[1]));
        this.toast(`IA · ${msg.label}`);
        this.aiPulse();
      }
      if (!msg.silent) {
        if (msg.history === 'undo') this.journal.pop();
        else this.journal.push({ label: msg.label.replace(/^Rétablir : /, ''), origin: msg.origin });
      }
      this.selection = this.selection.filter(id => this.doc.clips[id]);
      if (roots.has('settings')) this.monitor.fit();
      this.refreshAll(roots.has('media') || roots.has('effects') || this.panels.tab === 'journal' || this.panels.tab === 'transcript');
    } else if (msg.type === 'ops_result') {
      this.pending.get(msg.req)?.(msg.results); this.pending.delete(msg.req);
    } else if (msg.type === 'error') {
      this.toast(msg.error, true);
      if (msg.req) { this.pending.get(msg.req)?.(null); this.pending.delete(msg.req); }
      this.refreshAll(false);
    } else if (msg.type === 'compile_request') {
      this.sendRaw({ type: 'reply', id: msg.id, ...this.renderer.tryCompile(msg.effect) });
    } else if (msg.type === 'snapshot_request') {
      this.snapshot(msg);
    } else if (msg.type === 'export_request') {
      this.toast("IA · lance l'export");
      runExport(this, msg.options || {});
    }
  }

  async snapshot(msg) {
    try {
      this.pause();
      this.playhead = Math.max(0, msg.t);
      await this.sources.ready(this.doc, this.playhead);
      this.draw();
      const W = this.doc.settings.width, H = this.doc.settings.height;
      const c = document.createElement('canvas');
      c.width = msg.width; c.height = Math.round(msg.width * H / W);
      c.getContext('2d').drawImage($('#preview'), 0, 0, c.width, c.height);
      this.sendRaw({ type: 'reply', id: msg.id, ok: true, t: this.playhead, data: c.toDataURL('image/jpeg', 0.85) });
      this.toast('IA · regarde l\'image');
    } catch (e) {
      this.sendRaw({ type: 'reply', id: msg.id, ok: false, log: String(e) });
    }
  }

  refreshAll(panels = true) {
    this.dirty = true;
    this.timeline.dirty = true;
    this.forgetTextures();
    if (panels) this.panels.refresh();
    this.inspector.refresh();
  }

  forgetTextures() {
    const keep = new Set();
    for (const id of Object.keys(this.doc.clips)) for (const p of ['v:', 't:', 's:']) keep.add(p + id);
    for (const id of Object.keys(this.doc.media)) keep.add('i:' + id);
    this.renderer.forget(keep);
  }

  defs() { return { ...this.builtin, ...(this.doc?.effects || {}) }; }
  duration() { return Math.max(0, ...Object.values(this.doc?.clips || {}).map(c => c.start + c.duration)); }

  thumb(mid) {
    if (!mid) return null;
    let im = this.thumbs.get(mid);
    if (!im) {
      im = new Image();
      im.onload = () => { this.timeline.dirty = true; };
      im.src = `/media/${mid}/thumb`;
      this.thumbs.set(mid, im);
    }
    return im;
  }

  peaksFor(mid) {
    if (!mid) return null;
    if (!this.peaks.has(mid)) {
      this.peaks.set(mid, null);
      fetch(`/media/${mid}/peaks`).then(r => (r.ok ? r.json() : null)).then(d => { if (d) { this.peaks.set(mid, d); this.timeline.dirty = true; } });
    }
    return this.peaks.get(mid);
  }

  async loadFonts() {
    try {
      const { fonts } = await (await fetch('/api/fonts')).json();
      $('#fontlist').replaceChildren(...fonts.map(f => el('option', { value: f })));
    } catch { /* liste par défaut du navigateur */ }
  }

  // ------------------------------------------------------------ lecture
  seek(t) {
    this.playhead = Math.max(0, t);
    if (this.playing) { this.playT0 = this.playhead; this.playW0 = performance.now(); }
    this.dirty = true; this.timeline.dirty = true; this.pushState();
  }

  play() {
    if (!this.doc) return;
    if (this.playhead >= this.duration() - 1e-3) this.playhead = 0;
    this.playing = true; this.playT0 = this.playhead; this.playW0 = performance.now();
    this.pushState();
  }

  pause() { this.playing = false; this.sources.pauseAll(); this.dirty = true; this.pushState(); }
  toggle() { this.playing ? this.pause() : this.play(); }

  jumpEdit(dir) {
    const pts = new Set([0]);
    for (const c of Object.values(this.doc.clips)) { pts.add(c.start); pts.add(c.start + c.duration); }
    const s = [...pts].sort((a, b) => a - b), e = 1e-3;
    const t = dir > 0 ? s.find(p => p > this.playhead + e) : s.reverse().find(p => p < this.playhead - e);
    if (t !== undefined) this.seek(t);
  }

  pushState() {
    clearTimeout(this._st);
    this._st = setTimeout(() => this.sendRaw({ type: 'ui_state', playhead: this.playhead, selection: this.selection, playing: this.playing }), 120);
  }

  select(ids) {
    this.selection = ids;
    this.inspector.selKey = null;
    this.timeline.dirty = true;
    this.inspector.refresh();
    this.monitor.drawOverlay();
    this.pushState();
  }

  tick(now) {
    if (this.doc) {
      if (this.playing) {
        this.playhead = this.playT0 + (now - this.playW0) / 1000;
        if (this.playhead >= this.duration()) { this.playhead = this.duration(); this.pause(); }
        this.timeline.follow();
      }
      this.sources.sync(this.doc, this.playhead, this.playing);
      const changed = this.dirty || this.playing || this.playhead !== this._lastT;
      if (this.playing || this.dirty) this.draw();
      if (changed) {
        this._lastT = this.playhead;
        this.inspector.tick();
        this.panels.tick();
        if (!this.monitor.drag) this.monitor.drawOverlay();
        this.monitor.update();
      }
      this.timeline.draw();
    }
    requestAnimationFrame(t => this.tick(t));
  }

  draw() {
    this.dirty = false;
    this.renderer.render(this.doc, this.playhead, c => this.sources.get(this.doc, c), this.defs());
  }

  // ------------------------------------------------------------ actions
  firstTrack(kind) { return this.doc.tracks.find(t => (t.kind === 'audio') === (kind === 'audio') && !t.locked); }

  freeTrack(kind, a, b) {
    const tracks = this.doc.tracks.filter(t => t.kind === kind && !t.locked);
    const free = t => !Object.values(this.doc.clips).some(c => c.track === t.id && c.start < b - 1e-6 && c.start + c.duration > a + 1e-6);
    return (kind === 'video' ? tracks.slice(1) : tracks).find(free) || null;
  }

  async addMedia(mid, t, trackId) {
    const m = this.doc.media[mid];
    if (!m) return;
    const isAudio = m.kind === 'audio';
    let tr = this.doc.tracks.find(x => x.id === trackId);
    if (!tr || (tr.kind === 'audio') !== isAudio || tr.locked) tr = this.firstTrack(m.kind);
    const r = await this.send([{ op: 'add_clip', media: mid, track: tr.id, start: t, overwrite: true }], `Ajouter ${m.name}`);
    if (r) this.select([r[0]]);
  }

  async addText(t, trackId) {
    const ops = [];
    let track = trackId || this.freeTrack('video', t, t + 3)?.id;
    if (!track) { ops.push({ op: 'add_track', kind: 'video' }); track = '$0'; }
    ops.push({ op: 'add_clip', type: 'text', track, start: t, duration: 3, text: { content: 'Texte' } });
    const r = await this.send(ops, 'Ajouter un texte');
    if (r) this.select([r.at(-1)]);
    return r;
  }

  async addTextPreset(id, t, trackId) {
    const p = TEXT_PRESETS[id];
    const k = this.doc.settings.height / 1080;
    const ops = [];
    let track = trackId || this.freeTrack('video', t, t + p.dur)?.id;
    if (!track) { ops.push({ op: 'add_track', kind: 'video' }); track = '$0'; }
    const text = { ...p.text, size: Math.round(p.text.size * k) };
    const props = Object.fromEntries(Object.entries(p.props || {}).map(([a, v]) => [a, Math.round(v * k)]));
    const ci = ops.length;
    ops.push({ op: 'add_clip', type: 'text', track, start: t, duration: p.dur, text, props, name: p.label, overwrite: !!trackId });
    for (const [prop, keys] of Object.entries(p.keys || {})) {
      const scaled = keys.map(x => ({ ...x, v: prop === 'x' || prop === 'y' ? Math.round(x.v * k) : x.v }));
      ops.push({ op: 'set_keyframes', clip: `$${ci}`, prop, keys: scaled });
    }
    const r = await this.send(ops, `Texte : ${p.label}`);
    if (r) this.select([r[ci]]);
  }

  applyEffect(name, ids) {
    const ok = ids.filter(id => {
      const c = this.doc.clips[id];
      return c && !(c.type === 'media' && this.doc.media[c.media]?.kind === 'audio');
    });
    if (!ok.length) { this.toast('Sélectionne d\'abord un clip visuel (ou glisse l\'effet dessus).', true); return; }
    this.send(ok.map(id => ({ op: 'add_effect', clip: id, effect: name })), `Effet ${this.defs()[name]?.label || name}`);
  }

  deleteSelection(ripple) {
    if (!this.selection.length) return;
    this.send(ripple ? [{ op: 'ripple_delete', clips: this.selection }] : this.selection.map(id => ({ op: 'delete_clip', clip: id })),
              ripple ? 'Supprimer et resserrer' : 'Supprimer');
  }

  splitAtPlayhead() {
    const t = this.playhead;
    let ids = this.selection.length ? this.selection : Object.keys(this.doc.clips);
    ids = ids.filter(id => {
      const c = this.doc.clips[id];
      return c && c.start < t - 1e-3 && t < c.start + c.duration - 1e-3 && !this.doc.tracks.find(x => x.id === c.track)?.locked;
    });
    if (ids.length) this.send(ids.map(id => ({ op: 'split_clip', clip: id, at: t })), 'Scinder à la tête de lecture');
  }

  copy() {
    this.clipboard = [...this.selection];
    if (this.clipboard.length) this.toast(`${this.clipboard.length} clip(s) copié(s)`);
  }

  async paste() {
    const cs = this.clipboard.map(id => this.doc.clips[id]).filter(Boolean);
    if (!cs.length) return;
    const t0 = Math.min(...cs.map(c => c.start));
    const r = await this.send(cs.map(c => ({ op: 'duplicate_clip', clip: c.id, start: this.playhead + c.start - t0, overwrite: true })), 'Coller');
    if (r) this.select(r);
  }

  setTool(t) {
    this.tool = t;
    for (const b of document.querySelectorAll('[data-tool]')) b.classList.toggle('on', b.dataset.tool === t);
    this.timeline.dirty = true;
  }

  async upload(files) {
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f);
      this.toast(`Import de ${f.name}…`);
      const r = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!r.ok) this.toast(`Échec de l'import : ${(await r.json()).detail}`, true);
    }
  }

  openSettings() { openSettings(this); }

  aiPulse() {
    const b = $('#ai');
    b.classList.remove('pulse'); void b.offsetWidth; b.classList.add('pulse');
  }

  toast(text, bad = false) {
    const t = el('div', { class: 'toast' + (bad ? ' bad' : '') }, text);
    $('#toasts').append(t);
    setTimeout(() => t.remove(), bad ? 6000 : 2400);
  }

  onZoom(pps) { $('#tlzoom').value = Math.log(pps); }

  // ------------------------------------------------------------ interface
  bindUI() {
    $('#undo').onclick = () => this.sendRaw({ type: 'undo' });
    $('#redo').onclick = () => this.sendRaw({ type: 'redo' });
    $('#settings').onclick = () => this.openSettings();
    $('#export').onclick = () => openExport(this);
    $('#import').onchange = e => this.upload(e.target.files);
    for (const b of document.querySelectorAll('[data-tool]')) b.onclick = () => this.setTool(b.dataset.tool);
    $('#snap').onclick = () => { this.snap = !this.snap; $('#snap').classList.toggle('on', this.snap); this.timeline.dirty = true; };
    $('#addv').onclick = () => this.send([{ op: 'add_track', kind: 'video' }], 'Ajouter une piste vidéo');
    $('#adda').onclick = () => this.send([{ op: 'add_track', kind: 'audio' }], 'Ajouter une piste audio');
    $('#tlfit').onclick = () => this.timeline.fitAll();
    $('#tlzoom').oninput = e => this.timeline.setZoom(Math.exp(+e.target.value));
    $('#tlsplit').onclick = () => this.splitAtPlayhead();
    window.addEventListener('dragover', e => e.preventDefault());
    window.addEventListener('drop', e => { e.preventDefault(); if (e.dataTransfer.files.length) this.upload(e.dataTransfer.files); });
    // séparateur moniteur / timeline
    const sp = $('#splitter');
    sp.addEventListener('pointerdown', e => {
      sp.setPointerCapture(e.pointerId);
      const move = ev => document.body.style.setProperty('--tl', `${Math.max(160, Math.min(innerHeight - 260, innerHeight - ev.clientY))}px`);
      const up = () => { sp.removeEventListener('pointermove', move); sp.removeEventListener('pointerup', up); };
      sp.addEventListener('pointermove', move); sp.addEventListener('pointerup', up);
    });
    $('#inspector').addEventListener('focusout', () => setTimeout(() => {
      if (this.inspector.stale) this.inspector.refresh();
    }, 0));
    window.addEventListener('keydown', e => this.key(e));
  }

  key(e) {
    if (e.target.closest('input, textarea, select') || !this.doc) return;
    const k = e.key.toLowerCase(), fps = this.doc.settings.fps, ctrl = e.ctrlKey || e.metaKey;
    const stop = () => e.preventDefault();
    if (k === ' ') { stop(); this.toggle(); }
    else if (ctrl && k === 'z') { stop(); this.sendRaw({ type: e.shiftKey ? 'redo' : 'undo' }); }
    else if (ctrl && k === 'y') { stop(); this.sendRaw({ type: 'redo' }); }
    else if (ctrl && k === 'k') { stop(); this.splitAtPlayhead(); }
    else if (ctrl && k === 'c') { stop(); this.copy(); }
    else if (ctrl && k === 'v') { stop(); this.paste(); }
    else if (ctrl && k === 'd') { stop(); this.send(this.selection.map(id => ({ op: 'duplicate_clip', clip: id, overwrite: true })), 'Dupliquer'); }
    else if (ctrl && k === 'm') { stop(); openExport(this); }
    else if (ctrl && k === 'a') { stop(); this.select(Object.keys(this.doc.clips)); }
    else if (k === 'delete' || k === 'backspace') { stop(); if (!this.panels.cutSelection() && !this.inspector.deleteSelectedKey()) this.deleteSelection(e.shiftKey); }
    else if (k === 'v' && !ctrl) this.setTool('select');
    else if (k === 'c' && !ctrl) this.setTool('razor');
    else if (k === 't' && !ctrl) this.setTool('text');
    else if (k === 's' && !ctrl) $('#snap').click();
    else if (k === 'k') this.pause();
    else if (k === 'l') this.play();
    else if (k === 'j') this.seek(this.playhead - 1);
    else if (k === 'arrowleft') { stop(); this.seek(this.playhead - (e.shiftKey ? 5 : 1) / fps); }
    else if (k === 'arrowright') { stop(); this.seek(this.playhead + (e.shiftKey ? 5 : 1) / fps); }
    else if (k === 'arrowup') { stop(); this.jumpEdit(-1); }
    else if (k === 'arrowdown') { stop(); this.jumpEdit(1); }
    else if (k === 'home') this.seek(0);
    else if (k === 'end') this.seek(this.duration());
    else if (k === '+' || k === '=') this.timeline.setZoom(this.timeline.pps * 1.25, this.timeline.x(this.playhead));
    else if (k === '-') this.timeline.setZoom(this.timeline.pps / 1.25, this.timeline.x(this.playhead));
    else if (k === '\\') this.timeline.fitAll();
    else if (k === 'escape') this.select([]);
  }
}

function applyPatch(doc, p) {
  let cur = doc;
  for (const k of p.path.slice(0, -1)) cur = cur[k];
  const last = p.path.at(-1);
  if (p.op === 'set') cur[last] = structuredClone(p.value);
  else delete cur[last];
}

window.app = new App();
