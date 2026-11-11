// Export : la boîte de réglages, puis le rendu image par image avec le même
// moteur que l'aperçu. Le serveur fournit les images vidéo exactes (FFmpeg) et
// encode le résultat avec le son mixé.
import { Rendu, spansOf } from './rendu.js';
import { Sources } from './sources.js';
import { el, fmtTC, parseTC } from './widgets.js';

const QUALITIES = [['haute', 'Haute (YouTube, archivage)'], ['moyenne', 'Moyenne (fichier plus léger)'], ['legere', 'Légère (aperçu, envoi rapide)']];

function modal() { return document.getElementById('modal'); }
function closeModal() { const m = modal(); m.hidden = true; m.innerHTML = ''; m.onclick = null; }

export async function openExport(app) {
  if (!app.doc || app.duration() <= 0) { app.toast('La séquence est vide : rien à exporter.', true); return; }
  const o = await (await fetch('/api/export/options')).json();
  const s = app.doc.settings, fps = s.fps;
  const sizes = [[1, `Séquence (${s.width}×${s.height})`]];
  const short = Math.min(s.width, s.height);
  for (const target of [2160, 1440, 1080, 720, 480]) {
    if (target !== short && target < short * 2.01) sizes.push([target / short, `${target}p (${even(s.width * target / short)}×${even(s.height * target / short)})`]);
  }
  const path = el('input', { value: o.default_path, class: 'wide', spellcheck: 'false' });
  const size = el('select', {}, sizes.map(([k, l]) => el('option', { value: k }, l)));
  const quality = el('select', {}, QUALITIES.map(([v, l]) => el('option', { value: v }, l)));
  const loudness = el('select', {}, [['youtube', 'YouTube, Spotify : -14 LUFS (recommandé)'], ['podcast', 'Podcast : -16 LUFS'],
    ['broadcast', 'Télévision (EBU R128) : -23 LUFS'], ['off', 'Ne pas toucher au volume']].map(([v, l]) => el('option', { value: v }, l)));
  const encoder = el('select', {}, o.encoders.map(e => el('option', { value: e }, e === 'nvenc' ? 'NVIDIA NVENC (carte graphique, rapide)' : 'x264 (processeur, meilleure compression)')));
  const start = el('input', { value: fmtTC(0, fps) });
  const end = el('input', { value: fmtTC(o.duration, fps) });
  const info = el('p', { class: 'muted small' });
  const upd = () => {
    const a = parseTC(start.value, fps) ?? 0, b = parseTC(end.value, fps) ?? o.duration;
    info.textContent = `Durée exportée : ${fmtTC(Math.max(0, b - a), fps)} · ${Math.ceil(Math.max(0, b - a) * fps)} images · MP4 H.264 + AAC`;
  };
  start.oninput = end.oninput = upd; upd();
  const m = modal();
  m.innerHTML = '';
  m.append(el('div', { class: 'dialog wide' },
    el('h3', {}, 'Exporter la vidéo'),
    el('label', {}, 'Fichier de sortie', path),
    el('div', { class: 'two' }, el('label', {}, 'Résolution', size), el('label', {}, 'Qualité', quality)),
    el('label', {}, 'Volume final', loudness),
    el('label', {}, 'Encodeur', encoder),
    o.encoders.includes('nvenc') ? null : el('p', { class: 'muted small' }, 'NVENC (encodage par la carte graphique) indisponible : pilote NVIDIA trop ancien ou absent.'),
    el('div', { class: 'two' }, el('label', {}, 'Début', start), el('label', {}, 'Fin', end)),
    info,
    el('div', { class: 'actions' },
      el('button', { onclick: closeModal }, 'Annuler'),
      el('button', { class: 'primary', onclick: () => {
        const k = +size.value;
        runExport(app, { path: path.value, width: even(s.width * k), height: even(s.height * k), quality: quality.value,
                         encoder: encoder.value, loudness: loudness.value, start: parseTC(start.value, fps) ?? 0, end: parseTC(end.value, fps) ?? o.duration });
      } }, 'Exporter'))));
  m.hidden = false;
  m.querySelectorAll('input, select').forEach(i => i.addEventListener('keydown', e => e.stopPropagation()));
}

function even(x) { return Math.max(2, Math.round(x / 2) * 2); }

function fmtDur(sec) {
  if (!Number.isFinite(sec)) return '…';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return m ? `${m} min ${String(s).padStart(2, '0')} s` : `${s} s`;
}

// Lance l'export ; renvoie une promesse résolue à la fin (résultat ou erreur).
export function runExport(app, opts) {
  return new Promise(resolve => {
    app.pause();
    const m = modal();
    const bar = el('div', { class: 'bar-fill' });
    const line = el('p', { class: 'small' }, 'Préparation…');
    const cancel = el('button', {}, 'Annuler l\'export');
    m.innerHTML = '';
    m.append(el('div', { class: 'dialog wide' }, el('h3', {}, 'Export en cours'), el('div', { class: 'bar' }, bar), line,
      el('p', { class: 'muted small' }, 'Le rendu utilise la carte graphique de cette fenêtre : garde-la ouverte.'),
      el('div', { class: 'actions' }, cancel)));
    m.hidden = false;
    m.onclick = null;

    const ws = new WebSocket(`ws://${location.host}/ws/export`);
    ws.binaryType = 'arraybuffer';
    let job = null, cancelled = false;
    const t0 = performance.now();
    cancel.onclick = () => { cancelled = true; try { ws.send('cancel'); } catch { /* déjà fermé */ } ws.close(); };

    ws.onopen = () => ws.send(JSON.stringify(opts));
    let chain = Promise.resolve();
    ws.onmessage = ev => { chain = chain.then(() => handle(ev)).catch(e => { fail(String(e)); ws.close(); }); };
    const handle = async ev => {
      if (typeof ev.data === 'string') {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'plan') job = await prepare(app, msg);
        else if (msg.type === 'done') { finish(app, msg); resolve(msg); }
        else if (msg.type === 'error') { fail(msg.error); resolve(msg); }
        return;
      }
      if (!job) return;
      const T = job.timing, a0 = performance.now();
      if (job.sentAt) T.attente += a0 - job.sentAt;
      const buf = ev.data, dv = new DataView(buf);
      const hl = dv.getUint32(0, true);
      const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
      let off = 4 + hl;
      const frames = {};
      for (const c of head.clips) {
        const yuv = c.fmt === 'yuv420p';
        const n = yuv ? c.w * c.h * 3 / 2 : c.w * c.h * 4;
        frames[c.id] = { id: 'xv:' + c.id, data: new Uint8Array(buf, off, n), w: c.w, h: c.h, yuv };
        off += n;
      }
      const a1 = performance.now();
      job.renderer.render(job.doc, head.t, clip => (clip.type === 'media' && job.doc.media[clip.media]?.kind === 'video')
        ? frames[clip.id] || null : job.sources.get(job.doc, clip, head.t), job.defs, job.sp);
      const gl = job.renderer.gl;
      const a2 = performance.now();
      if (job.fmt === 'yuv420p') job.renderer.readYUV(job.px);
      else gl.readPixels(0, 0, job.w, job.h, gl.RGBA, gl.UNSIGNED_BYTE, job.px);
      const a3 = performance.now();
      ws.send(job.px);
      const a4 = performance.now();
      T.lecture += a1 - a0; T.rendu += a2 - a1; T.readPixels += a3 - a2; T.envoi += a4 - a3;
      job.sentAt = a4;
      window.__exportTiming = T;
      const done = head.i + 1;
      if (done % 3 === 0 || done === job.frames) {
        const el_s = (performance.now() - t0) / 1000, rate = done / el_s;
        bar.style.width = `${(100 * done / job.frames).toFixed(1)}%`;
        line.textContent = `Image ${done} / ${job.frames} · ${rate.toFixed(1)} i/s · reste environ ${fmtDur((job.frames - done) / rate)}`;
      }
    };
    ws.onclose = () => { if (cancelled) { closeModal(); app.toast('Export annulé.'); resolve({ type: 'cancelled' }); } };

    function fail(err) {
      m.innerHTML = '';
      m.append(el('div', { class: 'dialog wide' }, el('h3', {}, 'L\'export a échoué'), el('pre', { class: 'err' }, err),
        el('div', { class: 'actions' }, el('button', { onclick: closeModal }, 'Fermer'))));
    }
  });
}

async function prepare(app, msg) {
  const canvas = document.createElement('canvas');
  canvas.width = msg.width; canvas.height = msg.height;
  const renderer = new Rendu(canvas);
  const sources = new Sources(() => {});
  const doc = msg.doc;
  // Les images fixes doivent être chargées avant la première image.
  await Promise.all(Object.values(doc.clips).filter(c => c.type === 'media' && doc.media[c.media]?.kind === 'image').map(c =>
    new Promise(res => {
      const tryGet = () => (sources.get(doc, c) ? res() : setTimeout(tryGet, 30));
      tryGet();
    })));
  return { doc, renderer, sources, defs: { ...app.builtin, ...doc.effects }, sp: spansOf(doc), w: msg.width, h: msg.height,
           frames: msg.frames, fmt: msg.fmt,
           px: new Uint8Array(msg.fmt === 'yuv420p' ? msg.width * msg.height * 3 / 2 : msg.width * msg.height * 4),
           timing: { attente: 0, lecture: 0, rendu: 0, readPixels: 0, envoi: 0 }, sentAt: 0 };
}

function finish(app, msg) {
  const m = modal();
  m.innerHTML = '';
  m.append(el('div', { class: 'dialog wide' },
    el('h3', {}, 'Export terminé'),
    el('p', {}, el('code', { class: 'path' }, msg.path)),
    el('p', { class: 'muted small' }, `${msg.frames} images en ${fmtDur(msg.seconds)} · ${msg.size_mb} Mo · encodeur ${msg.encoder}`),
    msg.loudness ? el('p', { class: 'muted small' }, `Son : ${String(msg.loudness.mesure).replace('.', ',')} LUFS mesurés → ${String(msg.loudness.cible).replace('.', ',')} LUFS`) : null,
    el('div', { class: 'actions' },
      el('button', { onclick: () => fetch('/api/reveal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: msg.path }) }) }, 'Afficher dans l\'explorateur'),
      el('button', { class: 'primary', onclick: closeModal }, 'Fermer'))));
  m.hidden = false;
  app.toast('Export terminé');
}
