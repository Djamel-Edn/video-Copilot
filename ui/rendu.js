// Moteur de rendu WebGL2 : pistes → calque → chaîne d'effets (shaders) → composition.
// Le même moteur servira à l'export : ce que tu vois est ce qui sort.

const VS_QUAD = `#version 300 es
in vec2 a_pos; out vec2 v_uv;
void main(){ v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }`;

const VS_LAYER = `#version 300 es
in vec2 a_pos; uniform mat3 u_m; out vec2 v_uv;
void main(){ v_uv = a_pos * 0.5 + 0.5; vec3 p = u_m * vec3(a_pos, 1.0); gl_Position = vec4(p.xy, 0.0, 1.0); }`;

const FS_TEX = `#version 300 es
precision highp float; in vec2 v_uv; out vec4 o; uniform sampler2D u_tex; uniform float u_opacity;
void main(){ vec4 c = texture(u_tex, v_uv); o = vec4(c.rgb, c.a * u_opacity); }`;

const FS_MIX = `#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_src; uniform sampler2D u_below; uniform float u_opacity;
void main(){ o = mix(texture(u_below, v_uv), texture(u_src, v_uv), u_opacity); }`;

// YUV 4:2:0 BT.709 plage TV → RVB (images vidéo envoyées par le serveur pendant l'export).
const FS_YUV2RGB = `#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_y; uniform sampler2D u_u; uniform sampler2D u_v;
void main(){
  float y = (texture(u_y, v_uv).r * 255.0 - 16.0) / 219.0;
  float u = (texture(u_u, v_uv).r * 255.0 - 128.0) / 224.0;
  float v = (texture(u_v, v_uv).r * 255.0 - 128.0) / 224.0;
  o = vec4(clamp(vec3(y + 1.5748 * v, y - 0.1873 * u - 0.4681 * v, y + 1.8556 * u), 0.0, 1.0), 1.0);
}`;

// RVB → YUV 4:2:0 en UNE passe : chaque pixel RGBA de sortie contient 4 octets
// consécutifs du fichier yuv420p (plan Y, puis U, puis V), rangées de haut en bas.
const FS_RGB2YUV = `#version 300 es
precision highp float; out vec4 o;
uniform sampler2D u_tex; uniform vec2 u_res;
float luma(vec3 c){ return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec3 at(float x, float y){ return texture(u_tex, vec2(x / u_res.x, 1.0 - y / u_res.y)).rgb; }
void main(){
  vec2 f = floor(gl_FragCoord.xy);
  float W = u_res.x, H = u_res.y;
  float base = (f.y * (W / 4.0) + f.x) * 4.0;
  vec4 r;
  for (int k = 0; k < 4; k++) {
    float o = base + float(k);
    float val;
    if (o < W * H) {
      float y = floor(o / W), x = o - y * W;
      val = (16.0 + 219.0 * luma(at(x + 0.5, y + 0.5))) / 255.0;
    } else {
      float o2 = o - W * H;
      float plane = o2 < W * H / 4.0 ? 1.0 : 2.0;
      if (plane > 1.5) o2 -= W * H / 4.0;
      float cw = W / 2.0;
      float y = floor(o2 / cw), x = o2 - y * cw;
      vec3 c = at(x * 2.0 + 1.0, y * 2.0 + 1.0);
      float l = luma(c);
      val = plane < 1.5 ? (128.0 + 224.0 * (c.b - l) / 1.8556) / 255.0 : (128.0 + 224.0 * (c.r - l) / 1.5748) / 255.0;
    }
    r[k] = val;
  }
  o = r;
}`;

export function wrapEffect(def) {
  const uniforms = Object.entries(def.params || {})
    .map(([n, p]) => `uniform ${p.type === 'color' ? 'vec3' : 'float'} p_${n};`).join('\n');
  return `#version 300 es
precision highp float;
in vec2 v_uv; out vec4 o_color;
uniform sampler2D u_src; uniform sampler2D u_below;
uniform vec2 u_res; uniform float u_time; uniform float u_dur;
${uniforms}
vec4 src(vec2 uv){ return texture(u_src, uv); }
vec4 below(vec2 uv){ return texture(u_below, uv); }
#line 1
${def.glsl}
void main(){ o_color = effect(v_uv); }`;
}

export function evalKey(keys, t, base) {
  if (!keys || !keys.length) return base;
  if (t <= keys[0].t) return keys[0].v;
  const last = keys[keys.length - 1];
  if (t >= last.t) return last.v;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      let u = (t - a.t) / (b.t - a.t);
      if (a.ease === 'hold') return a.v;
      if (a.ease === 'ease_in') u = u * u;
      else if (a.ease === 'ease_out') u = 1 - (1 - u) * (1 - u);
      else if (a.ease === 'ease_in_out') u = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
      return a.v + (b.v - a.v) * u;
    }
  }
  return last.v;
}

// Intervalle affiché de chaque clip (même règle que moteur/projet.py : spans) : une
// transition prolonge de sa demi-durée le clip qui sort et le clip qui entre.
export function spansOf(doc) {
  const out = {}, byTrack = {};
  for (const c of Object.values(doc.clips)) {
    out[c.id] = [c.start, c.start + c.duration];
    (byTrack[c.track] ||= []).push(c);
  }
  for (const cs of Object.values(byTrack)) {
    cs.sort((a, b) => a.start - b.start);
    for (let i = 0; i + 1 < cs.length; i++) {
      const a = cs[i], b = cs[i + 1], tr = b.trans_in;
      if (tr && Math.abs(a.start + a.duration - b.start) < 1e-3) {
        out[a.id][1] += tr.duration / 2;
        out[b.id][0] -= tr.duration / 2;
      }
    }
  }
  return out;
}

// Progression (0 → 1) de la transition d'entrée d'un clip à l'instant t, ou null.
export function transitionAt(clip, sp, t) {
  const tr = clip.trans_in;
  if (!tr || !sp || sp[0] >= clip.start - 1e-6) return null;   // pas de voisin : inactive
  const a = clip.start - tr.duration / 2;
  if (t < a || t >= clip.start + tr.duration / 2) return null;
  return Math.min(1, Math.max(0, (t - a) / tr.duration));
}

export function clipProps(clip, lt) {
  const out = {};
  for (const [k, v] of Object.entries(clip.props)) out[k] = evalKey(clip.keyframes[k], lt, v);
  return out;
}

function hex(c) {
  const n = parseInt(c.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
}

function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(36);
}

export class Rendu {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: false, alpha: false, antialias: false,
                                              preserveDrawingBuffer: true });
    if (!gl) throw new Error('WebGL2 indisponible dans ce navigateur.');
    this.gl = gl;
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.pLayer = this.program(VS_LAYER, FS_TEX);
    this.pCopy = this.program(VS_QUAD, FS_TEX);
    this.pMix = this.program(VS_QUAD, FS_MIX);
    this.pYuv = this.program(VS_QUAD, FS_YUV2RGB);
    this.pPack = this.program(VS_QUAD, FS_RGB2YUV);
    this.yuv = new Map();       // id de source → {planes, target, w, h}
    this.packTargets = null;
    this.fx = new Map();        // clé → {prog, error}
    this.textures = new Map();  // id de source → {tex, key}
    this.targets = null;
    this.errors = {};           // nom d'effet → message de compilation
  }

  program(vs, fs) {
    const gl = this.gl;
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(s);
        gl.deleteShader(s);
        throw new Error(log);
      }
      return s;
    };
    const v = sh(gl.VERTEX_SHADER, vs), f = sh(gl.FRAGMENT_SHADER, fs);
    const p = gl.createProgram();
    gl.attachShader(p, v); gl.attachShader(p, f);
    gl.bindAttribLocation(p, 0, 'a_pos');
    gl.linkProgram(p);
    gl.deleteShader(v); gl.deleteShader(f);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const loc = {};
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) {
      const name = gl.getActiveUniform(p, i).name;
      loc[name] = gl.getUniformLocation(p, name);
    }
    return { p, loc };
  }

  tryCompile(def) {
    try {
      const prog = this.program(VS_QUAD, wrapEffect(def));
      this.gl.deleteProgram(prog.p);
      return { ok: true, log: '' };
    } catch (e) {
      return { ok: false, log: String(e.message || e) };
    }
  }

  getFx(def) {
    const key = def.name + '|' + hash(def.glsl + JSON.stringify(def.params));
    let fx = this.fx.get(key);
    if (!fx) {
      try { fx = { prog: this.program(VS_QUAD, wrapEffect(def)) }; delete this.errors[def.name]; }
      catch (e) { fx = { error: String(e.message || e) }; this.errors[def.name] = fx.error; }
      this.fx.set(key, fx);
    }
    return fx;
  }

  makeTarget(w, h) {
    const gl = this.gl;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    this.texParams();
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    return { tex, fb };
  }

  texParams() {
    const gl = this.gl;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  ensureTargets(w, h) {
    if (this.targets && this.targets.w === w && this.targets.h === h) return;
    const gl = this.gl;
    if (this.targets) for (const t of [...this.targets.out, ...this.targets.layer]) {
      gl.deleteTexture(t.tex); gl.deleteFramebuffer(t.fb);
    }
    this.targets = { w, h, out: [this.makeTarget(w, h), this.makeTarget(w, h)],
                     layer: [this.makeTarget(w, h), this.makeTarget(w, h)] };
  }

  upload(id, src) {
    const gl = this.gl;
    let t = this.textures.get(id);
    if (!t) { t = { tex: gl.createTexture(), key: null }; this.textures.set(id, t); }
    gl.bindTexture(gl.TEXTURE_2D, t.tex);
    if (src.data) {
      // Pixels bruts (export) : rangées de haut en bas, retournées par UNPACK_FLIP_Y comme une vidéo.
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, src.w, src.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, src.data);
      this.texParams();
    } else if (src.dynamic || t.key !== src.key) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src.el);
      this.texParams();
      t.key = src.key;
    }
    return t.tex;
  }

  planeTex() {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    this.texParams();
    return t;
  }

  // Images YUV 4:2:0 : trois plans R8, convertis en RVB dans une texture du clip.
  uploadYUV(src) {
    const gl = this.gl, { w, h, data } = src;
    let e = this.yuv.get(src.id);
    if (!e || e.w !== w || e.h !== h) {
      if (e) { e.planes.forEach(t => gl.deleteTexture(t)); gl.deleteTexture(e.target.tex); gl.deleteFramebuffer(e.target.fb); }
      e = { w, h, planes: [this.planeTex(), this.planeTex(), this.planeTex()], target: this.makeTarget(w, h) };
      this.yuv.set(src.id, e);
    }
    const ys = w * h, cs = (w / 2) * (h / 2);
    const parts = [[data.subarray(0, ys), w, h], [data.subarray(ys, ys + cs), w / 2, h / 2], [data.subarray(ys + cs, ys + 2 * cs), w / 2, h / 2]];
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    parts.forEach(([d, pw, ph], i) => {
      gl.bindTexture(gl.TEXTURE_2D, e.planes[i]);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, pw, ph, 0, gl.RED, gl.UNSIGNED_BYTE, d);
    });
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.useProgram(this.pYuv.p);
    ['u_y', 'u_u', 'u_v'].forEach((n, i) => this.bindTex(i, e.planes[i], this.pYuv.loc[n]));
    gl.viewport(0, 0, w, h);
    this.draw(e.target.fb);
    return e.target.tex;
  }

  // Lit la dernière image rendue en YUV 4:2:0 (Y, puis U, puis V), rangées de haut en bas, en une lecture.
  readYUV(buf) {
    const gl = this.gl, W = this.canvas.width, H = this.canvas.height;
    const w = W / 4, h = H * 3 / 2;
    if (!this.packTarget || this.packTarget.W !== W || this.packTarget.H !== H) {
      this.packTarget = { W, H, t: this.makeTarget(w, h) };
    }
    gl.useProgram(this.pPack.p);
    this.bindTex(0, this.lastOut, this.pPack.loc.u_tex);
    gl.uniform2f(this.pPack.loc.u_res, W, H);
    gl.viewport(0, 0, w, h);
    this.draw(this.packTarget.t.fb);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  }

  forget(ids) {
    for (const [id, t] of this.textures) if (!ids.has(id)) { this.gl.deleteTexture(t.tex); this.textures.delete(id); }
  }

  bindTex(unit, tex, loc) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    if (loc) gl.uniform1i(loc, unit);
  }

  draw(fb) {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  // doc : projet ; t : temps timeline ; sources(clip) → {el, w, h, key, dynamic} | null ; defs : effets
  render(doc, t, sources, defs, sp = spansOf(doc)) {
    const gl = this.gl;
    const W = doc.settings.width, H = doc.settings.height;
    const rw = this.canvas.width, rh = this.canvas.height;
    this.ensureTargets(rw, rh);
    const T = this.targets;
    gl.viewport(0, 0, rw, rh);
    gl.disable(gl.BLEND);
    let cur = 0;
    const bg = hex(doc.settings.background || '#000000');
    gl.bindFramebuffer(gl.FRAMEBUFFER, T.out[0].fb);
    gl.clearColor(bg[0], bg[1], bg[2], 1); gl.clear(gl.COLOR_BUFFER_BIT);

    const used = new Set();
    for (const track of doc.tracks) {
      if (track.kind !== 'video' || track.hidden) continue;
      const clips = Object.values(doc.clips)
        .filter(c => c.track === track.id && sp[c.id] && sp[c.id][0] <= t && t < sp[c.id][1])
        .sort((a, b) => a.start - b.start);
      for (const clip of clips) {
        const src = sources(clip);
        if (!src) continue;
        used.add(src.id);
        const tex = src.yuv ? this.uploadYUV(src) : this.upload(src.id, src);
        gl.viewport(0, 0, rw, rh);
        const lt = t - clip.start;
        const pr = clipProps(clip, lt);
        // 1. calque : ajusté au cadre, puis position / échelle / rotation
        const sa = src.w / src.h, fa = W / H;
        const dw = sa > fa ? W : H * sa, dh = sa > fa ? W / sa : H;
        const th = -pr.rotation * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
        const hw = dw / 2 * pr.scale, hh = dh / 2 * pr.scale;
        const m = [hw * cs / (W / 2), hw * sn / (H / 2), 0,
                   -hh * sn / (W / 2), hh * cs / (H / 2), 0,
                   pr.x / (W / 2), -pr.y / (H / 2), 1];
        gl.bindFramebuffer(gl.FRAMEBUFFER, T.layer[0].fb);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(this.pLayer.p);
        gl.uniformMatrix3fv(this.pLayer.loc.u_m, false, m);
        gl.uniform1f(this.pLayer.loc.u_opacity, 1);
        this.bindTex(0, tex, this.pLayer.loc.u_tex);
        this.draw(T.layer[0].fb);
        // 2. chaîne d'effets
        let li = 0, replace = false;
        for (const inst of clip.effects) {
          if (!inst.enabled) continue;
          const def = defs[inst.effect];
          if (!def) continue;
          const fx = this.getFx(def);
          if (fx.error) continue;
          const { p, loc } = fx.prog;
          gl.useProgram(p);
          gl.bindFramebuffer(gl.FRAMEBUFFER, T.layer[1 - li].fb);
          gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
          this.bindTex(0, T.layer[li].tex, loc.u_src);
          this.bindTex(1, T.out[cur].tex, loc.u_below);
          if (loc.u_res) gl.uniform2f(loc.u_res, W, H);
          if (loc.u_time) gl.uniform1f(loc.u_time, lt);
          if (loc.u_dur) gl.uniform1f(loc.u_dur, clip.duration);
          for (const [name, pd] of Object.entries(def.params || {})) {
            const l = loc['p_' + name];
            if (!l) continue;
            const v = inst.params[name] ?? pd.default;
            if (pd.type === 'color') gl.uniform3fv(l, v);
            else gl.uniform1f(l, evalKey(clip.keyframes[`fx.${inst.id}.${name}`], lt, v));
          }
          this.draw(T.layer[1 - li].fb);
          li = 1 - li;
          if (def.blend === 'replace') replace = true;
        }
        // 2 bis. transition d'entrée : mélange ce clip (src) avec l'image qui contient le clip sortant (below)
        const prog = transitionAt(clip, sp[clip.id], t);
        const tdef = prog !== null ? defs[clip.trans_in.type] : null;
        if (tdef) {
          const fx = this.getFx(tdef);
          if (!fx.error) {
            const { p, loc } = fx.prog;
            gl.useProgram(p);
            gl.bindFramebuffer(gl.FRAMEBUFFER, T.layer[1 - li].fb);
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
            this.bindTex(0, T.layer[li].tex, loc.u_src);
            this.bindTex(1, T.out[cur].tex, loc.u_below);
            if (loc.u_res) gl.uniform2f(loc.u_res, W, H);
            if (loc.u_time) gl.uniform1f(loc.u_time, lt);
            if (loc.u_dur) gl.uniform1f(loc.u_dur, clip.trans_in.duration);
            for (const [name, pd] of Object.entries(tdef.params || {})) {
              const l = loc['p_' + name];
              if (!l) continue;
              const v = name === 'progress' ? prog : (clip.trans_in.params?.[name] ?? pd.default);
              if (pd.type === 'color') gl.uniform3fv(l, v); else gl.uniform1f(l, v);
            }
            this.draw(T.layer[1 - li].fb);
            li = 1 - li;
            replace = true;
          }
        }
        // 3. composition
        const op = Math.max(0, Math.min(1, pr.opacity));
        if (replace) {
          gl.useProgram(this.pMix.p);
          this.bindTex(0, T.layer[li].tex, this.pMix.loc.u_src);
          this.bindTex(1, T.out[cur].tex, this.pMix.loc.u_below);
          gl.uniform1f(this.pMix.loc.u_opacity, op);
          this.draw(T.out[1 - cur].fb);
          cur = 1 - cur;
        } else {
          gl.enable(gl.BLEND);
          gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
          gl.useProgram(this.pCopy.p);
          this.bindTex(0, T.layer[li].tex, this.pCopy.loc.u_tex);
          gl.uniform1f(this.pCopy.loc.u_opacity, op);
          this.draw(T.out[cur].fb);
          gl.disable(gl.BLEND);
        }
      }
    }
    // 4. affichage
    this.lastOut = T.out[cur].tex;
    gl.useProgram(this.pCopy.p);
    this.bindTex(0, T.out[cur].tex, this.pCopy.loc.u_tex);
    gl.uniform1f(this.pCopy.loc.u_opacity, 1);
    this.draw(null);
    return used;
  }
}
