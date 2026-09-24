// Anodised brushed-aluminium plates, rendered in WebGL2 behind the page content.
// Every `.plate` gets a rounded-rect surface on one fixed canvas: Blender-baked brush normals,
// roughness and tint (assets/metal, see blender/build_metal.py), anisotropic GGX with the brush
// along +X, a machined chamfer and rolled edge from the rect SDF, Torx fasteners, a studio
// environment reflection and a soft drop shadow. It redraws only when something changes.
// On the first good frame it adds `metal-gl` to <html>; CSS then clears the fallback plate surface.

const DIR = new URL('./assets/metal/', import.meta.url);
const root = document.documentElement;
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const small = matchMedia('(max-width: 760px)');
const coarse = matchMedia('(pointer: coarse)');
const MAX = 32;
const STRIDE = 12;

const VS = `#version 300 es
layout(location=0) in vec2 aCorner;
layout(location=1) in vec4 aRect;    // css px, viewport-relative
layout(location=2) in vec4 aParam;   // radius, opacity, seed, fastener mode
layout(location=3) in vec4 aExtra;   // blur px
uniform vec2 uView;
uniform float uPad;
out vec2 vLocal;
flat out vec4 vRect, vParam, vExtra;
void main() {
  float pad = uPad + aExtra.x * 2.0;
  vec2 pos = aRect.xy - pad + aCorner * (aRect.zw + 2.0 * pad);
  vLocal = pos - aRect.xy;
  vRect = aRect; vParam = aParam; vExtra = aExtra;
  gl_Position = vec4(pos.x / uView.x * 2.0 - 1.0, 1.0 - pos.y / uView.y * 2.0, 0.0, 1.0);
}`;

const COMMON = `#version 300 es
precision highp float;
in vec2 vLocal;
flat in vec4 vRect, vParam, vExtra;
out vec4 o;
const float PI = 3.14159265;
// Rounded-rect SDF (q relative to centre, y down). g = outward unit gradient.
float sdRound(vec2 q, vec2 h, float r, out vec2 g) {
  vec2 d = abs(q) - h + r;
  float sd = length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
  g = (d.x > 0.0 && d.y > 0.0) ? normalize(d) : (d.x > d.y ? vec2(1, 0) : vec2(0, 1));
  g *= sign(q + 1e-5);
  return sd;
}
float erfc2(float x) { return 0.5 - 0.5 * tanh(1.2 * x); }
`;

const FS_SHADOW = COMMON + `
void main() {
  vec2 size = vRect.zw;
  float r = min(vParam.x, 0.5 * min(size.x, size.y));
  vec2 g;
  // wide, soft key-light shadow falling downward, plus a tight contact shadow
  float s1 = sdRound(vLocal - size * 0.5 - vec2(0.0, 20.0), size * 0.5 - vec2(8.0, 2.0), r, g);
  float s2 = sdRound(vLocal - size * 0.5 - vec2(0.0, 2.5), size * 0.5, r, g);
  float a1 = erfc2(s1 / 30.0), a2 = erfc2(s2 / (3.5 + vExtra.x));
  float a = vParam.y * (1.0 - (1.0 - 0.78 * a1) * (1.0 - 0.85 * a2));
  o = vec4(0.0, 0.0, 0.0, a);
}`;

const FS_PLATE = COMMON + `
uniform vec2 uView;
uniform float uDpr, uTexel, uTexSize, uCamZ, uGrain, uEnvI, uExposure, uCap, uKey;
uniform vec3 uTint;
uniform vec3 uPtr;        // pointer light, y-up world coords
uniform sampler2D uNormal, uRough, uAlbedo, uEnv, uEnvSoft, uBoltN, uBoltA;

vec3 envDecode(vec3 e) { vec3 x = pow(e, vec3(2.2)); return x / max(1.0 - x, vec3(1e-3)); }
vec2 envUV(vec3 r) { return vec2(0.5 + atan(r.x, r.z) / (2.0 * PI), 0.5 - asin(clamp(r.y, -1.0, 1.0)) / PI); }
vec3 envAt(vec3 r, float lod, float soft) {
  vec2 uv = envUV(r);
  return mix(envDecode(textureLod(uEnv, uv, lod).rgb), envDecode(textureLod(uEnvSoft, uv, 0.0).rgb), soft);
}

// Burley anisotropic GGX with height-correlated Smith visibility (Filament form).
vec3 ggxAniso(vec3 N, vec3 T, vec3 B, vec3 V, vec3 L, float ax, float ay, vec3 F0) {
  float NL = dot(N, L);
  if (NL <= 0.0) return vec3(0.0);
  vec3 H = normalize(V + L);
  float NV = max(dot(N, V), 1e-4);
  float TH = dot(T, H), BH = dot(B, H), NH = dot(N, H);
  float d = TH * TH / (ax * ax) + BH * BH / (ay * ay) + NH * NH;
  float D = 1.0 / (PI * ax * ay * d * d);
  float lv = NL * length(vec3(ax * dot(T, V), ay * dot(B, V), NV));
  float ll = NV * length(vec3(ax * dot(T, L), ay * dot(B, L), NL));
  float Vis = 0.5 / (lv + ll);
  vec3 F = F0 + (1.0 - F0) * pow(1.0 - max(dot(V, H), 0.0), 5.0);
  return D * Vis * F * NL;
}
// Sphere light: the lobe widens by the light's angular size (energy stays normalised by D).
vec3 sphereLight(vec3 P, vec3 N, vec3 T, vec3 B, vec3 V, vec3 Lp, float rad, vec3 col, float ax, float ay, vec3 F0) {
  vec3 Ld = Lp - P; float dist = length(Ld);
  float w = rad / (2.0 * dist);
  return ggxAniso(N, T, B, V, Ld / dist, min(ax + w, 1.0), min(ay + w, 1.0), F0) * col;
}
vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
float hash(vec2 p) { p = fract(p * vec2(443.897, 441.423)); p += dot(p, p.yx + 19.19); return fract((p.x + p.y) * p.x); }

// Soft light: one very large key high on the left, a dim fill low right, the Blender studio env.
vec3 shade(vec3 P, vec3 N, vec3 T, vec3 V, float ax, float ay, vec3 F0, float ao) {
  vec3 B = cross(N, T);
  vec3 c = sphereLight(P, N, T, B, V, vec3(uView.x * 0.1, uView.y * 0.6, 1000.0), 1300.0, vec3(1.0, 0.99, 0.97) * uKey, ax, ay, F0);
  c += sphereLight(P, N, T, B, V, vec3(uView.x * 1.1, -uView.y * 0.7, 800.0), 1000.0, vec3(0.92, 0.95, 1.0) * 0.22 * uKey, ax, ay, F0);
  vec3 R = reflect(-V, N);
  float NV = max(dot(N, V), 1e-4);
  vec3 e = vec3(0.0);
  for (int i = -1; i <= 1; i++) e += envAt(normalize(R + B * (float(i) * ay * 0.8)), 4.0, 0.75);
  vec3 Fe = F0 + (1.0 - F0) * pow(1.0 - NV, 5.0) * 0.35;
  return (c + e / 3.0 * Fe * uEnvI) * ao;
}

void main() {
  vec2 size = vRect.zw;
  float r = min(vParam.x, 0.5 * min(size.x, size.y));
  float blur = vExtra.x;
  vec2 g;
  float sd = sdRound(vLocal - size * 0.5, size * 0.5, r, g);
  float px = 1.0 / uDpr;
  float cov = clamp(0.5 - sd / max(px, 2.0 * blur), 0.0, 1.0);
  if (cov <= 0.0) discard;
  float e = max(-sd, 0.0);
  vec2 gs = vec2(g.x, -g.y);                   // y-up

  // Edge profile: a thin 1.25px chamfer with a soft shoulder, then a gentle 8px roll.
  float cw = 1.25;
  float chamf = 1.0 - smoothstep(cw - 0.6, cw + 0.9, e);
  float t = clamp((e - cw) / 8.0, 0.0, 1.0);
  float slope = mix(0.3 * (1.0 - t) * (1.0 - t), 0.85, chamf);
  vec3 N0 = normalize(vec3(gs * slope, 1.0));

  vec2 vp = vRect.xy + vLocal;
  vec3 P = vec3(vp.x, -vp.y, 0.0);
  vec3 V = normalize(vec3(uView.x * 0.5, -uView.y * 0.5, uCamZ) - P);

  // Bead-blast + faint brush maps, locked to the plate in css px (texel density = DPR).
  vec2 uv = (vLocal * uTexel + vParam.z * vec2(577.0, 313.0)) / uTexSize;
  vec3 nb = texture(uNormal, uv).xyz * 2.0 - 1.0;
  float grain = uGrain * (1.0 - clamp(blur / 5.0, 0.0, 0.9));
  vec3 nbs = normalize(vec3(nb.xy * grain, max(nb.z, 0.2)));
  vec3 T0 = normalize(vec3(1, 0, 0) - N0 * N0.x);
  vec3 N = normalize(T0 * nbs.x + cross(N0, T0) * nbs.y + N0 * nbs.z);
  vec3 T = normalize(vec3(1, 0, 0) - N * N.x);
  vec3 B = cross(N, T);

  float rough = texture(uRough, uv).r;                 // ~0.42
  vec3 alb = pow(texture(uAlbedo, uv).rgb, vec3(2.2));
  vec3 F0 = alb * uTint;                               // graphite anodise
  float ax = mix(rough * 0.8, 0.2, chamf), ay = mix(rough * 1.2, 0.24, chamf);
  float ao = mix(0.6, 1.0, smoothstep(0.0, 9.0, e));
  vec3 col = shade(P, N, T, V, ax, ay, F0, ao);

  // Hard light: ONE narrow anisotropic sheen band from the pointer light (narrow across the
  // faint brush, long along it), on the face only, fading softly.
  float faceW = smoothstep(4.0, 12.0, e);
  vec3 Lp = vec3(uPtr.xy, 520.0) - P;
  col += ggxAniso(N, T, B, V, normalize(Lp), 0.075, 0.6, F0) * uPtr.z * faceW;

  // Edges lit by the key: bright top-left, falling into shadow along the bottom.
  float keyFacing = dot(gs, normalize(vec2(-0.45, 0.89)));
  col *= mix(1.0, mix(0.12, 1.0, smoothstep(-0.35, 0.85, keyFacing)), 1.0 - faceW);

  // Fasteners: tiny flush countersunk screws, cards only (mode 1).
  float fmask = 0.0;
  if (vParam.w > 0.5) {
    float fr = 4.5;
    float ci = r * 0.72 + 2.0;
    vec2 c = vec2(vLocal.x < size.x * 0.5 ? ci : size.x - ci, vLocal.y < size.y * 0.5 ? ci : size.y - ci);
    vec2 d = vLocal - c;
    float dl = length(d);
    col *= 1.0 - 0.18 * (1.0 - smoothstep(fr * 1.0, fr * 1.5, dl));
    if (dl < fr * 1.15) {
      vec4 bn = texture(uBoltN, d / (2.3 * fr) + 0.5), ba = texture(uBoltA, d / (2.3 * fr) + 0.5);
      if (bn.a > 0.0) {
        vec3 fn = normalize(bn.xyz * 2.0 - 1.0);
        vec3 bf0 = pow(ba.rgb, vec3(2.2)) * 0.5;
        vec3 bc = shade(P, fn, normalize(vec3(1, 0, 0) - fn * fn.x), V, 0.35, 0.4, bf0, mix(0.4, 1.0, ba.a));
        col = mix(col, bc, bn.a * 0.9);
        fmask = bn.a;
      }
    }
  }

  col = aces(col * uExposure);
  // Readability: the face behind text is capped (soft knee) so body text keeps >= 7:1;
  // the thin chamfer may go brighter but is soft-clipped so it never becomes a white rule.
  float face = smoothstep(7.0, 12.0, e);
  float cap = mix(0.3, mix(uCap, uCap * 1.6, fmask), face);
  float L = dot(col, vec3(0.2126, 0.7152, 0.0722));
  float knee = cap * 0.55;
  float Lc = L <= knee ? L : knee + (cap - knee) * (1.0 - exp(-(L - knee) / (cap - knee)));
  col *= Lc / max(L, 1e-6);
  // Fine grain, applied after the cap so it can only darken (about 2 percent).
  float micro = 1.0 - 0.014 * clamp(length(nb.xy) * 4.0, 0.0, 1.0) - 0.006 * clamp((rough - 0.42) / 0.03, -1.0, 1.0);
  col *= mix(1.0, micro, face * grain / max(uGrain, 1e-3));

  vec3 srgb = mix(col * 12.92, 1.055 * pow(col, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, col));
  srgb += (hash(gl_FragCoord.xy) + hash(gl_FragCoord.yx + 7.3) - 1.0) / 255.0;   // TPDF dither
  float a = cov * vParam.y;
  o = vec4(clamp(srgb, 0.0, 1.0) * a, a);
}`;

// ------------------------------------------------------------------ state
const state = {
  gl: null, canvas: null, progs: null, vao: null, inst: null, tex: {},
  bitmaps: null, big: false, dpr: 1, w: 0, h: 0, plates: [], cache: new Map(), ok: false,
  queued: false, lost: false,
  ptr: { tx: 0, ty: 0, x: 0, y: 0, seen: false, touch: false, last: 0 },
  drift: true, t0: performance.now(),
};
const data = new Float32Array(MAX * STRIDE);

function paused() { return reduce.matches || root.dataset.motion === 'paused'; }

// Compiles and links without blocking the main thread where KHR_parallel_shader_compile exists:
// the driver compiles in the background and we poll COMPLETION_STATUS once per frame.
async function compile(gl, vs, fs) {
  const p = gl.createProgram();
  const shaders = [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]].map(([type, src]) => {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src); gl.compileShader(sh); gl.attachShader(p, sh);
    return sh;
  });
  gl.linkProgram(p);
  const ext = gl.getExtension('KHR_parallel_shader_compile');
  if (ext) {
    while (!gl.getProgramParameter(p, ext.COMPLETION_STATUS_KHR)) {
      if (gl.isContextLost()) throw new Error('context lost while compiling');
      await new Promise(r => requestAnimationFrame(r));
    }
  }
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    const log = shaders.map(sh => gl.getShaderInfoLog(sh)).join('\n') + gl.getProgramInfoLog(p);
    throw new Error(log);
  }
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; u[name] = gl.getUniformLocation(p, name); }
  return { p, u };
}

async function loadBitmap(name) {
  const res = await fetch(new URL(name, DIR));
  if (!res.ok) throw new Error(name + ' ' + res.status);
  return createImageBitmap(await res.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
}

function upload(gl, bmp, repeat, mips = true) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, bmp);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, repeat === 'x' || !repeat ? gl.CLAMP_TO_EDGE : gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, mips ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
  if (mips) gl.generateMipmap(gl.TEXTURE_2D);
  const an = gl.getExtension('EXT_texture_filter_anisotropic');
  if (an && mips) gl.texParameterf(gl.TEXTURE_2D, an.TEXTURE_MAX_ANISOTROPY_EXT, 8);
  return t;
}

async function initGL() {
  const gl = state.canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'high-performance' });
  if (!gl) throw new Error('no webgl2');
  state.gl = gl;
  const [shadow, plate] = await Promise.all([compile(gl, VS, FS_SHADOW), compile(gl, VS, FS_PLATE)]);
  state.progs = { shadow, plate };
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const quad = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, quad);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const inst = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, inst);
  gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
  for (let i = 0; i < 3; i++) {
    gl.enableVertexAttribArray(1 + i);
    gl.vertexAttribPointer(1 + i, 4, gl.FLOAT, false, STRIDE * 4, i * 16);
    gl.vertexAttribDivisor(1 + i, 1);
  }
  state.vao = vao; state.inst = inst;
  const b = state.bitmaps;
  state.tex = {
    uNormal: upload(gl, b.normal, true), uRough: upload(gl, b.rough, true), uAlbedo: upload(gl, b.albedo, true),
    uEnv: upload(gl, b.env, 'x'), uEnvSoft: upload(gl, b.envSoft, 'x', false),
    uBoltN: upload(gl, b.boltN, false), uBoltA: upload(gl, b.boltA, false),
  };
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
}

function resize() {
  const cap = coarse.matches && small.matches ? 1.5 : 2;
  state.dpr = Math.min(devicePixelRatio || 1, cap);
  state.w = innerWidth; state.h = innerHeight;
  const W = Math.round(state.w * state.dpr), H = Math.round(state.h * state.dpr);
  if (state.canvas.width !== W || state.canvas.height !== H) { state.canvas.width = W; state.canvas.height = H; }
  state.plates = Array.from(document.querySelectorAll('.plate')).slice(0, MAX);
  state.cache.clear();
  request();
}

// ------------------------------------------------------------------ frame
// Plate geometry and style are measured once and kept in page coordinates, so a scroll frame costs no
// layout: screen position = cached page position - scroll. Only a plate that is animating (its reveal)
// is re-measured each frame; resize, layout and style changes clear its entry.
function collect() {
  let n = 0, animating = false;
  const { w, h } = state, sx = scrollX, sy = scrollY;
  state.plates.forEach((el, i) => {
    const live = el.getAnimations().some(a => a.playState === 'running' || a.pending);
    if (live) animating = true;
    let c = state.cache.get(el);
    if (!c || live || c.live) {
      const b = el.getBoundingClientRect(), cs = getComputedStyle(el);
      c = { x: b.left + sx, y: b.top + sy, w: b.width, h: b.height, op: parseFloat(cs.opacity), r: parseFloat(cs.borderTopLeftRadius) || 28, live };
      state.cache.set(el, c);
    }
    const left = c.x - sx, top = c.y - sy;
    if (c.w < 2 || c.h < 2 || top + c.h < -90 || top > h + 90 || left + c.w < -90 || left > w + 90) return;
    if (!(c.op > 0.004)) return;
    // tiny flush screws on the cards only; none on the stint rows or the portrait (the photo covers its corners)
    const mode = el.classList.contains('portrait') || el.classList.contains('stint') || el.dataset.fasteners === 'none' || c.h < 160 ? 0 : 1;
    data.set([left, top, c.w, c.h, c.r, c.op, (i * 0.6180339) % 1, mode, 0, 0, 0, 0], n * STRIDE);
    n++;
  });
  return { n, animating };
}

function stepLight(now) {
  const p = state.ptr, { w, h } = state;
  const driftOn = !paused() && (!p.seen || (p.touch && now - p.last > 3000));
  if (driftOn) {
    const t = (now - state.t0) / 1000;
    p.tx = w * (0.5 + 0.36 * Math.sin(t * 0.43)); p.ty = h * (0.3 + 0.1 * Math.sin(t * 0.31));
  } else if (!p.seen) {
    p.tx = w * 0.62; p.ty = h * 0.28;
  }
  if (!state.lightInit) { p.x = p.tx; p.y = p.ty; state.lightInit = true; }
  const k = reduce.matches ? 1 : 0.2;
  p.x += (p.tx - p.x) * k; p.y += (p.ty - p.y) * k;
  return driftOn || Math.abs(p.tx - p.x) + Math.abs(p.ty - p.y) > 0.4;
}

function frame(now) {
  state.queued = false;
  if (!state.ok || state.lost || document.hidden) return;
  const gl = state.gl;
  const { n, animating } = collect();
  const moving = stepLight(now);
  gl.viewport(0, 0, state.canvas.width, state.canvas.height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  if (n) {
    gl.bindVertexArray(state.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, state.inst);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, data, 0, n * STRIDE);
    const sh = state.progs.shadow;
    gl.useProgram(sh.p);
    gl.uniform2f(sh.u.uView, state.w, state.h);
    gl.uniform1f(sh.u.uPad, 90);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n);
    const pl = state.progs.plate, u = pl.u;
    gl.useProgram(pl.p);
    gl.uniform2f(u.uView, state.w, state.h);
    gl.uniform1f(u.uPad, 2);
    gl.uniform1f(u.uDpr, state.dpr);
    gl.uniform1f(u.uTexel, state.dpr);
    gl.uniform1f(u.uTexSize, 1024);
    gl.uniform1f(u.uCamZ, 1.6 * Math.max(state.w, state.h));
    gl.uniform1f(u.uGrain, 0.3);
    gl.uniform1f(u.uKey, 1.0);
    gl.uniform3f(u.uTint, 0.55, 0.555, 0.575);
    gl.uniform1f(u.uEnvI, 0.6);
    gl.uniform1f(u.uExposure, 1.8);
    gl.uniform1f(u.uCap, 0.027);   // linear luminance (+dither headroom): #b7c0cc body text >= 7:1
    gl.uniform3f(u.uPtr, state.ptr.x, -state.ptr.y, 0.22);
    let unit = 0;
    for (const [name, tex] of Object.entries(state.tex)) {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1i(u[name], unit++);
    }
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n);
  }
  if (!root.classList.contains('metal-gl') && !gl.isContextLost()) root.classList.add('metal-gl');
  if (animating || (moving && n)) request();
}

function request() {
  if (state.queued || !state.ok) return;
  state.queued = true;
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ boot
// Nothing (no context, no texture fetch, no shader compile) until a plate is within about one
// viewport of being visible.
function whenNear() {
  const plates = document.querySelectorAll('.plate');
  if (!plates.length) return new Promise(() => {});
  if (!('IntersectionObserver' in window)) return Promise.resolve();
  return new Promise(resolve => {
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) { io.disconnect(); resolve(); }
    }, { rootMargin: '100% 0px 100% 0px' });
    plates.forEach(el => io.observe(el));
  });
}

async function boot() {
  await whenNear();
  if (!window.WebGL2RenderingContext || !window.createImageBitmap) return;
  const canvas = document.createElement('canvas');
  canvas.className = 'metal-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:2;display:block;';
  state.canvas = canvas;
  const s = '1k';   // fine bead-blast grain tiles well at 1k; the 2k set is no longer shipped
  try {
    const [normal, rough, albedo, env, envSoft, boltN, boltA] = await Promise.all([
      `brushed_normal_${s}.webp`, `brushed_rough_${s}.webp`, `brushed_albedo_${s}.webp`,
      'studio_env.webp', 'studio_env_soft.webp', 'fastener_normal.webp', 'fastener_albedo.webp',
    ].map(loadBitmap));
    state.bitmaps = { normal, rough, albedo, env, envSoft, boltN, boltA };
    document.body.appendChild(canvas);
    await initGL();
  } catch (err) {
    console.warn('[metal] WebGL metal disabled:', err);
    canvas.remove();
    return;
  }
  state.ok = true;

  canvas.addEventListener('webglcontextlost', e => {
    e.preventDefault(); state.lost = true; root.classList.remove('metal-gl');
  });
  canvas.addEventListener('webglcontextrestored', async () => {
    try { await initGL(); state.lost = false; request(); } catch (err) { console.warn('[metal]', err); }
  });

  const onPointer = e => {
    const p = state.ptr;
    p.tx = e.clientX; p.ty = e.clientY; p.seen = true; p.touch = e.pointerType === 'touch'; p.last = performance.now();
    request();
  };
  addEventListener('pointermove', onPointer, { passive: true });
  addEventListener('pointerdown', onPointer, { passive: true });
  addEventListener('scroll', request, { passive: true });
  addEventListener('resize', resize);
  for (const ev of ['transitionrun', 'transitionend', 'animationstart', 'load']) addEventListener(ev, request, true);
  addEventListener('motion:toggle', request);
  reduce.addEventListener('change', request);
  document.addEventListener('visibilitychange', request);
  if (document.fonts) document.fonts.ready.then(resize);
  const ro = new ResizeObserver(() => { state.cache.clear(); request(); });
  ro.observe(document.body);
  document.querySelectorAll('.plate').forEach(el => ro.observe(el));
  // Reveal animations are started with el.animate() right after the inline opacity is cleared;
  // watching style/class wakes the loop, which then follows getAnimations() until they finish.
  const mo = new MutationObserver(rs => { rs.forEach(r => state.cache.delete(r.target)); request(); });
  document.querySelectorAll('.plate').forEach(el => mo.observe(el, { attributes: true, attributeFilter: ['style', 'class'] }));
  resize();
}

boot();
