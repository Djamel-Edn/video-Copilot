// Bus de lecture (WebAudio) : crêtes gauche / droite et volume perçu (LUFS court terme, 3 s)
// pour les vu-mètres du moniteur. Le son de l'aperçu passe par ce bus une fois la lecture lancée.

const DB_MIN = -60;

function db(x) { return x > 0 ? Math.max(DB_MIN, 20 * Math.log10(x)) : DB_MIN; }

export class Mixeur {
  constructor() {
    this.ctx = null;
    this.pending = new Set();
    this.attached = new WeakSet();
    this.hist = [];       // [{t, ms}] énergie pondérée K par bloc
    this.peakHold = [DB_MIN, DB_MIN];
    this.holdAt = [0, 0];
  }

  // À appeler sur un geste de l'utilisateur (lecture) : le navigateur l'exige pour le son.
  start() {
    if (!this.ctx) {
      const ctx = this.ctx = new AudioContext({ latencyHint: 'interactive' });
      this.master = ctx.createGain();
      // Bus en stéréo forcée : une voix mono est copiée à plein niveau sur les deux canaux,
      // comme à l'export (sinon le canal droit resterait vide et la mesure perdrait 3 dB).
      this.master.channelCount = 2;
      this.master.channelCountMode = 'explicit';
      this.master.channelInterpretation = 'speakers';
      this.master.connect(ctx.destination);
      const split = ctx.createChannelSplitter(2);
      this.master.connect(split);
      this.an = [ctx.createAnalyser(), ctx.createAnalyser()];
      this.an.forEach((a, i) => { a.fftSize = 2048; split.connect(a, i); });
      // Pondération K (ITU-R BS.1770) : plateau aigu +4 dB puis passe-haut 38 Hz.
      const shelf = ctx.createBiquadFilter();
      shelf.type = 'highshelf'; shelf.frequency.value = 1681.97; shelf.gain.value = 4.0;
      const hp = ctx.createBiquadFilter();
      hp.type = 'highpass'; hp.frequency.value = 38.13; hp.Q.value = 0.5;
      const ksplit = ctx.createChannelSplitter(2);
      this.master.connect(shelf); shelf.connect(hp); hp.connect(ksplit);
      this.k = [ctx.createAnalyser(), ctx.createAnalyser()];
      this.k.forEach((a, i) => { a.fftSize = 4096; ksplit.connect(a, i); });
      this.buf = new Float32Array(4096);
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
    for (const el of this.pending) this.attach(el);
    this.pending.clear();
  }

  attach(el) {
    if (this.attached.has(el)) return;
    if (!this.ctx || this.ctx.state !== 'running') { this.pending.add(el); return; }
    try {
      this.ctx.createMediaElementSource(el).connect(this.master);
      this.attached.add(el);
    } catch { /* déjà branché ailleurs */ }
  }

  // Mesures actuelles : crêtes (dBFS) et LUFS court terme ; null avant la première lecture.
  read() {
    if (!this.ctx || this.ctx.state !== 'running') return null;
    const now = performance.now(), b = this.buf;
    const peaks = this.an.map((a, i) => {
      const d = b.subarray(0, a.fftSize);
      a.getFloatTimeDomainData(d);
      let p = 0;
      for (let j = 0; j < d.length; j++) p = Math.max(p, Math.abs(d[j]));
      const v = db(p);
      if (v >= this.peakHold[i] || now - this.holdAt[i] > 1500) { this.peakHold[i] = v; this.holdAt[i] = now; }
      return v;
    });
    let ms = 0;
    for (const a of this.k) {
      a.getFloatTimeDomainData(b);
      let s = 0;
      for (let j = 0; j < b.length; j++) s += b[j] * b[j];
      ms += s / b.length;
    }
    this.hist.push({ t: now, ms });
    while (this.hist.length && now - this.hist[0].t > 3000) this.hist.shift();
    const mean = this.hist.reduce((x, h) => x + h.ms, 0) / this.hist.length;
    const lufs = mean > 1e-10 ? -0.691 + 10 * Math.log10(mean) : -Infinity;
    return { peaks, hold: [...this.peakHold], lufs };
  }

  reset() { this.hist = []; this.peakHold = [DB_MIN, DB_MIN]; }
}

export { DB_MIN };
