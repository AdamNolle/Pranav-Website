// Wind tunnel: full-page smoke visualisation + PIV tracer layer.
// Real-time 2D stable fluids on WebGL2 half-float targets, in viewport space:
// free stream from the left, obstacles from [data-obstacle] / the car / the pointer,
// smoke filaments from a nozzle rake, textured with Cycles-rendered wisps (assets/smoke/),
// plus GPU tracer motes and volumetric puffs advected by transform feedback.
// Text obstacles are glyph-accurate (Canvas2D raster -> GPU jump-flood SDF); the car uses __carMask
// (silhouette) or __carBounds. Reads window.__speed; writes window.__windFlow = { speed, gust }.
// Listens for race:go / race:reset / motion:toggle.

const REDUCE = matchMedia('(prefers-reduced-motion: reduce)').matches;
const FINE = matchMedia('(pointer: fine)').matches;
// Frozen = reduced motion, or the page's "Pause motion" toggle (WCAG 2.2.2). Still frames only.
const userPaused = () => document.documentElement.dataset.motion === 'paused';
let frozen = REDUCE || userPaused();
const MAXR = 48;          // obstacle + dim rects per frame
const VU = 100;           // velocity unit: 1 = 100 CSS px/s (keeps half-float pressure in range)
const ASSETS = new URL('assets/smoke/', import.meta.url).href;

if (window.__windTunnel) window.__windTunnel.destroy();

// ---------- quality tiers ----------
const TIERS = [
  { name: 'low',  cell: 7, dye: 2.4, iters: 12, parts: 9000,  puffs: 40,  dpr: 1.25 },
  { name: 'mid',  cell: 5, dye: 2.2, iters: 18, parts: 24000, puffs: 80,  dpr: 1.5 },
  { name: 'high', cell: 4, dye: 1.8, iters: 26, parts: 50000, puffs: 120, dpr: 1.5 },
];
function pickTier() {
  const cores = navigator.hardwareConcurrency || 4, mem = navigator.deviceMemory || 8;
  const coarse = matchMedia('(pointer: coarse)').matches, small = Math.min(innerWidth, innerHeight) < 600;
  if (coarse && (small || cores <= 6 || mem <= 4)) return 0;
  if (!coarse && cores >= 8 && mem >= 8 && !small) return 2;
  return 1;
}
let tierIx = pickTier();

// ---------- canvas ----------
let canvas = makeCanvas();
function makeCanvas() {
  const c = document.createElement('canvas');
  c.className = 'wind-tunnel';
  c.setAttribute('aria-hidden', 'true');
  c.tabIndex = -1;
  c.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:1;opacity:0;' +
    (REDUCE ? '' : 'transition:opacity 1.4s ease;');
  document.body.prepend(c);
  return c;
}

// ---------- page inputs ----------
const st = {
  W: innerWidth, H: innerHeight, scroll: scrollY, scrollPrev: scrollY,
  speed: 0, gust: 0, burst: 0, time: 0, frame: 0,
  ptr: { x: -1e4, y: -1e4, vx: 0, vy: 0, t: 0 },
  car: null, carV: [0, 0],
  rects: [], shapes: new Float32Array(MAXR * 4), props: new Float32Array(MAXR * 4), n: 0,
};
const disposers = [];
const on = (t, ev, fn, o) => { t.addEventListener(ev, fn, o); disposers.push(() => t.removeEventListener(ev, fn, o)); };

// Obstacles are cached in document space and only re-measured on layout changes.
// Boxy elements (cards) are rounded rects; text obstacles are rasterised glyph by glyph, so the air
// flows between and around the actual letterforms. Other page text only gets a soft glyph halo
// that keeps smoke luminance down right behind the letters (contrast), never a box.
const OB_SEL = '[data-obstacle]';
const range = document.createRange();
function clearish(c) { return c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c); }
function isTextish(cs) {
  // background-clip computes to one value per layer ("text, text")
  return /\btext\b/.test(cs.backgroundClip || '') || /\btext\b/.test(cs.webkitBackgroundClip || '') ||
    (cs.backgroundImage === 'none' && clearish(cs.backgroundColor) && parseFloat(cs.borderTopWidth) === 0);
}
function hiddenEl(el, cs) {
  if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0' || el.closest('[hidden]')) return true;
  for (let e = el; e && e !== document.body; e = e.parentElement) if (e.style.opacity === '0') return true;   // not yet revealed
  return false;
}
const glyphs = { list: [], fonts: [], adv: new Map(), dirty: true };
function measure() {
  const rects = [], csCache = new Map(), obKind = new Map(), fontIx = new Map();
  const css = el => { let c = csCache.get(el); if (!c) { c = getComputedStyle(el); csCache.set(el, c); } return c; };
  const sy = scrollY;
  document.querySelectorAll(OB_SEL).forEach(el => {
    const cs = css(el);
    if (hiddenEl(el, cs) || cs.position === 'fixed') { obKind.set(el, 'skip'); return; }
    if (isTextish(cs)) { obKind.set(el, 'text'); return; }
    obKind.set(el, 'box');
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    rects.push({ x: r.left - 3, y: r.top + sy - 3, w: r.width + 6, h: r.height + 6, r: Math.min(parseFloat(cs.borderTopLeftRadius) || 0, r.width / 2, r.height / 2), kind: 0 });
  });
  const list = [], fonts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: n => /\S/.test(n.data) ? 1 : 2 });
  for (let n; (n = walker.nextNode());) {
    const el = n.parentElement;
    if (!el || el.closest('script,style,noscript,template,nav,.nav,canvas,svg,button')) continue;
    const ob = el.closest(OB_SEL), kind = ob ? obKind.get(ob) : 'dim';
    if (kind === 'skip' || kind === 'box') continue;      // text on cards sits on the card itself
    const cs = css(el);
    if (hiddenEl(el, cs)) continue;
    const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    let fi = fontIx.get(font);
    if (fi == null) { fi = fonts.length; fonts.push({ font }); fontIx.set(font, fi); }
    const up = cs.textTransform === 'uppercase', lo = cs.textTransform === 'lowercase';
    const d = n.data;
    for (let i = 0; i < d.length; i++) {
      let ch = d[i];
      if (/\s/.test(ch)) continue;
      range.setStart(n, i); range.setEnd(n, i + 1);
      const r = range.getBoundingClientRect();
      if (r.width < 0.5 || r.height < 1) continue;
      if (up) ch = ch.toUpperCase(); else if (lo) ch = ch.toLowerCase();
      list.push({ ch, x: r.left, y: r.top + sy, w: r.width, h: r.height, f: fi, s: kind === 'text' ? 1 : 0 });
    }
  }
  st.rects = rects;
  glyphs.list = list; glyphs.fonts = fonts; glyphs.adv.clear(); glyphs.dirty = true;
}
let measureQueued = false;
function queueMeasure() { if (measureQueued) return; measureQueued = true; requestAnimationFrame(() => { measureQueued = false; measure(); }); }

// Glyph raster (R = solid text + car silhouette, G = halo-only text) at mask resolution.
let gctx = null, tctx = null;
function drawGlyphs() {
  const c = gctx.canvas, W = dims.W, H = dims.H, sx = c.width / W, sy = c.height / H;
  gctx.setTransform(1, 0, 0, 1, 0, 0);
  gctx.globalCompositeOperation = 'source-over';
  gctx.fillStyle = '#000'; gctx.fillRect(0, 0, c.width, c.height);
  gctx.globalCompositeOperation = 'lighter';
  gctx.textBaseline = 'alphabetic';
  let cur = -1, fill = '';
  for (const g of glyphs.list) {
    const y = g.y - st.scroll;
    if (y > H + 4 || y + g.h < -4 || g.x > W + 4 || g.x + g.w < -4) continue;
    const f = glyphs.fonts[g.f];
    if (g.f !== cur) {
      gctx.font = f.font; cur = g.f;
      if (!f.m) { const m = gctx.measureText('Hg'); f.a = m.fontBoundingBoxAscent || m.actualBoundingBoxAscent || 0.8; f.d = m.fontBoundingBoxDescent || m.actualBoundingBoxDescent || 0.2; f.m = 1; }
    }
    const key = g.f + g.ch;
    let adv = glyphs.adv.get(key);
    if (adv == null) { adv = gctx.measureText(g.ch).width; glyphs.adv.set(key, adv); }
    const col = g.s ? '#f00' : '#0f0';
    if (col !== fill) { gctx.fillStyle = col; fill = col; }
    const kx = adv > 0 ? g.w / adv : 1;               // matches wdth variations the canvas can't set
    const base = y + g.h * f.a / (f.a + f.d);
    gctx.setTransform(sx * kx, 0, 0, sy, g.x * sx, base * sy);
    gctx.fillText(g.ch, 0, 0);
  }
  gctx.setTransform(1, 0, 0, 1, 0, 0);
  const cm = st.carMask;
  if (cm) {
    const tc = tctx.canvas;
    if (tc.width !== cm.canvas.width || tc.height !== cm.canvas.height) { tc.width = cm.canvas.width; tc.height = cm.canvas.height; }
    tctx.globalCompositeOperation = 'copy';
    tctx.drawImage(cm.canvas, 0, 0);
    if (st.frame % 8 === 0) findTip(tc, cm);
    tctx.globalCompositeOperation = 'source-in';
    tctx.fillStyle = '#f00'; tctx.fillRect(0, 0, tc.width, tc.height);
    gctx.drawImage(tc, cm.x * sx, cm.y * sy, cm.w * sx, cm.h * sy);
  }
}
// Rear-wing trailing upper corner of the car silhouette: the tip-vortex source.
function findTip(tc, cm) {
  let d;
  try { d = tctx.getImageData(0, 0, tc.width, tc.height).data; } catch (e) { st.tip = null; return; }
  const w = tc.width, h = tc.height;
  let x0 = w, x1 = -1;
  for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) if (d[(y * w + x) * 4 + 3] > 128) { if (x < x0) x0 = x; if (x > x1) x1 = x; }
  if (x1 < 0) { st.tip = null; return; }
  const xr = x1 - (x1 - x0) * 0.3;
  for (let y = 0; y < h; y++) {
    for (let x = x1; x >= xr; x--) {
      if (d[(y * w + x) * 4 + 3] > 128) { st.tip = { x: cm.x + (x / w) * cm.w, y: cm.y + (y / h) * cm.h, s: cm.h }; return; }
    }
  }
  st.tip = null;
}

// Per frame: convert cached rects to viewport space, visible ones only, solids first.
function packRects() {
  const sy = st.scroll, H = st.H, list = [];
  for (const r of st.rects) {
    const y = r.y - sy;
    if (y > H + 80 || y + r.h < -80 || r.x > st.W + 80 || r.x + r.w < -80) continue;
    list.push([r, y]);
  }
  list.sort((a, b) => a[0].kind - b[0].kind);
  let n = 0;
  for (const [r, y] of list) {
    if (n >= MAXR) break;
    st.shapes.set([r.x, y, r.w, r.h], n * 4);
    st.props.set([r.r, r.kind, 0, 0], n * 4);
    n++;
  }
  st.n = n;
}

// ---------- GL bootstrap ----------
let gl, ext, fmt, R = {}, alive = true, raf = 0, glOK = false;
const TEX = {};  // sprite textures (smoke atlas, motes, wisp)

function getGL(c) {
  const g = c.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false, powerPreference: 'high-performance' });
  if (!g) return null;
  const e = g.getExtension('EXT_color_buffer_float') || g.getExtension('EXT_color_buffer_half_float');
  if (!e) return null;
  g.getExtension('OES_texture_float_linear');
  return g;
}

function testFormat(internal, format) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, internal, 4, 4, 0, format, gl.HALF_FLOAT, null);
  const f = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, f);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  gl.deleteFramebuffer(f); gl.deleteTexture(t);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  return ok;
}

// ---------- shaders ----------
const HDR = `#version 300 es
precision highp float;
precision highp sampler2D;
precision highp int;
`;
const FSH = HDR + `in vec2 vUv;
out vec4 o;
`;
const LIB = `
float hash12(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * .1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
  return mix(mix(hash12(i), hash12(i+vec2(1,0)), f.x), mix(hash12(i+vec2(0,1)), hash12(i+vec2(1,1)), f.x), f.y); }
float erfa(float x){ float s = sign(x), a = abs(x); float t = 1. + (.278393 + (.230389 + .078108*a*a)*a)*a; t *= t; return s - s/(t*t); }
`;
// Nozzle rake, anchored to the page so filaments ride with scroll. Box-filtered over a dye texel (alias free).
const RAKE = `
uniform float uSpacing, uSigma, uTexelPx, uTime, uEmit;
vec2 rake(float py){
  float fi = py / uSpacing, idx = floor(fi + .5);
  float d = (fi - idx) * uSpacing;
  float h = hash12(vec2(idx, 7.13));
  float sg = uSigma * (.8 + .5 * hash12(vec2(idx, 3.1))) * 1.4142;
  float hw = uTexelPx * .5;
  float a = .5 * (erfa((d + hw) / sg) - erfa((d - hw) / sg)) * uSpacing / max(uSpacing, 4. * hw);
  a *= step(.09, h) * (.62 + .38 * vnoise(vec2(uTime * (.5 + .9 * h), idx * 1.7))) * (.7 + .6 * hash12(vec2(idx, 5.3))) * uEmit;
  return hash12(vec2(idx, 1.9)) > .8 ? vec2(0., a * 1.15) : vec2(a, 0.);
}
`;

const VS_QUAD = `#version 300 es
out vec2 vUv;
void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); vUv = p; gl_Position = vec4(p * 2. - 1., 0., 1.); }`;

// ---------- obstacle mask: r = signed distance (CSS px), g = text dim, ba = obstacle velocity ----------
const FS_MASK = FSH + `
#define MAXR ${MAXR}
uniform vec2 uView;
uniform vec4 uR[MAXR];
uniform vec4 uP[MAXR];
uniform int uN;
uniform vec4 uCar; uniform vec2 uCarV; uniform float uCarOn;
uniform vec4 uPtr; uniform vec2 uPtrV;
uniform sampler2D uGlyph, uJfa; uniform float uGlyphOn, uPxCss;
float sdRR(vec2 p, vec2 c, vec2 b, float r){ r = min(r, min(b.x, b.y)); vec2 q = abs(p - c) - b + r; return length(max(q, 0.)) + min(max(q.x, q.y), 0.) - r; }
float smin(float a, float b, float k){ float h = clamp(.5 + .5*(b - a)/k, 0., 1.); return mix(b, a, h) - k*h*(1. - h); }
void main(){
  vec2 p = vec2(vUv.x, 1. - vUv.y) * uView;
  float sd = 1e4, dim = 0.; vec2 v = vec2(0.);
  for (int i = 0; i < MAXR; i++){
    if (i >= uN) break;
    vec4 r = uR[i]; vec4 q = uP[i];
    float d = sdRR(p, r.xy + r.zw * .5, r.zw * .5, q.x);
    sd = min(sd, d);
  }
  if (uGlyphOn > .5){
    // glyph SDF from the jump flood; sub-pixel refined with the coverage
    ivec2 ip = ivec2(gl_FragCoord.xy), mx = textureSize(uGlyph, 0) - 1;
    vec4 g = texelFetch(uGlyph, ip, 0);
    vec2 s = texelFetch(uJfa, ip, 0).xy;
    float dd = s.x < -1e3 ? 1e4 : length(s - gl_FragCoord.xy) * uPxCss;
    float gs = (g.r >= .5 ? -dd : dd) + (.5 - g.r) * uPxCss;
    sd = min(sd, gs);
    // halo for page text: dilate the G coverage a few CSS px
    float h = g.g;
    // wider, softer halo so smoke never sits directly behind reading text (contrast)
    float r1 = 4. / uPxCss, r2 = 9. / uPxCss, r3 = 16. / uPxCss;
    for (int k = 0; k < 8; k++){
      float a = float(k) * .785398;
      vec2 dir = vec2(cos(a), sin(a));
      h = max(h, .95 * texelFetch(uGlyph, clamp(ip + ivec2(dir * r1 + .5), ivec2(0), mx), 0).g);
      h = max(h, .7 * texelFetch(uGlyph, clamp(ip + ivec2(dir * r2 + .5), ivec2(0), mx), 0).g);
      h = max(h, .4 * texelFetch(uGlyph, clamp(ip + ivec2(dir * r3 + .5), ivec2(0), mx), 0).g);
    }
    dim = max(dim, h);
  }
  if (uCarOn > 1.5){
    // silhouette already in the glyph SDF; tag its surface with the car's velocity
    vec2 q = max(abs(p - (uCar.xy + uCar.zw * .5)) - uCar.zw * .5, 0.);
    if (length(q) < 12. && sd < 10.) v = uCarV;
  } else if (uCarOn > .5){
    vec2 c0 = uCar.xy, s = uCar.zw;
    float body = sdRR(p, c0 + s * vec2(.5, .72), s * vec2(.49, .2), s.y * .18);
    vec2 hc = c0 + s * vec2(.56, .5), hr = s * vec2(.26, .3);
    float hump = (length((p - hc) / hr) - 1.) * min(hr.x, hr.y);
    float wing = sdRR(p, c0 + s * vec2(.93, .4), s * vec2(.06, .22), 4.);
    float dc = min(smin(body, hump, s.y * .12), wing) - 2.;
    if (dc < sd) sd = dc;
    if (dc < 10.) v = uCarV;
  }
  if (uPtr.w > .5){
    float dp = length(p - uPtr.xy) - uPtr.z;
    if (dp < sd) sd = dp;
    if (dp < 8.) v = uPtrV;
  }
  o = vec4(sd, dim, v);
}`;

// ---------- jump flood: nearest glyph-edge texel for the signed distance field ----------
const FS_SEED = FSH + `
uniform sampler2D uGlyph;
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy), mx = textureSize(uGlyph, 0) - 1;
  bool a = texelFetch(uGlyph, p, 0).r >= .5, e = false;
  e = e || (texelFetch(uGlyph, clamp(p + ivec2(1, 0), ivec2(0), mx), 0).r >= .5) != a;
  e = e || (texelFetch(uGlyph, clamp(p - ivec2(1, 0), ivec2(0), mx), 0).r >= .5) != a;
  e = e || (texelFetch(uGlyph, clamp(p + ivec2(0, 1), ivec2(0), mx), 0).r >= .5) != a;
  e = e || (texelFetch(uGlyph, clamp(p - ivec2(0, 1), ivec2(0), mx), 0).r >= .5) != a;
  o = vec4(e ? gl_FragCoord.xy : vec2(-1e4), 0., 1.);
}`;
const FS_JFA = FSH + `
uniform sampler2D uS; uniform int uStep;
void main(){
  vec2 fc = gl_FragCoord.xy; ivec2 p = ivec2(fc), mx = textureSize(uS, 0) - 1;
  vec2 best = vec2(-1e4); float bd = 1e12;
  for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++){
    ivec2 q = p + ivec2(i, j) * uStep;
    if (q.x < 0 || q.y < 0 || q.x > mx.x || q.y > mx.y) continue;
    vec2 s = texelFetch(uS, q, 0).xy;
    if (s.x < -1e3) continue;
    float d = dot(s - fc, s - fc);
    if (d < bd){ bd = d; best = s; }
  }
  o = vec4(best, 0., 1.);
}`;

// ---------- velocity advection + free-stream relaxation + no-slip ----------
const FS_ADV_VEL = FSH + LIB + `
uniform sampler2D uVel, uMask;
uniform vec2 uInvView, uFree;
uniform float uDtS, uDt, uShift, uRelax, uInlet, uTime, uCell;
vec2 velAt(vec2 uv){ return (uv.x < 0. || uv.y < 0. || uv.y > 1.) ? uFree : texture(uVel, uv).xy; }
void main(){
  vec2 v = texture(uVel, vUv).xy;
  vec2 nv = velAt(vUv - v * uDtS * uInvView - vec2(0., uShift));
  float edge = smoothstep(.07, 0., vUv.x) + .6 * (smoothstep(.035, 0., vUv.y) + smoothstep(.965, 1., vUv.y));
  float n = (vnoise(vec2(vUv.y * 7., uTime * .45)) - .5) + .5 * (vnoise(vec2(vUv.y * 23., uTime * 1.3)) - .5);
  vec2 target = uFree + vec2(0., uInlet * n * length(uFree));
  nv = mix(nv, target, 1. - exp(-uDt * (uRelax + edge * 9.)));
  vec4 m = texture(uMask, vUv);
  nv = mix(nv, m.zw, 1. - smoothstep(-uCell, uCell * .5, m.x));
  o = vec4(nv, 0., 1.);
}`;

const FS_CURL = FSH + `
uniform sampler2D uVel; uniform vec2 uTx;
void main(){
  float L = texture(uVel, vUv - vec2(uTx.x, 0.)).y, R = texture(uVel, vUv + vec2(uTx.x, 0.)).y;
  float B = texture(uVel, vUv - vec2(0., uTx.y)).x, T = texture(uVel, vUv + vec2(0., uTx.y)).x;
  o = vec4(.5 * (R - L - T + B), 0., 0., 1.);
}`;

const FS_VORT = FSH + `
uniform sampler2D uVel, uCurl, uMask; uniform vec2 uTx; uniform float uEps, uDt;
uniform vec2 uView, uTip; uniform float uTipK, uTipR;
void main(){
  float L = texture(uCurl, vUv - vec2(uTx.x, 0.)).x, R = texture(uCurl, vUv + vec2(uTx.x, 0.)).x;
  float B = texture(uCurl, vUv - vec2(0., uTx.y)).x, T = texture(uCurl, vUv + vec2(0., uTx.y)).x;
  float C = texture(uCurl, vUv).x;
  vec2 f = .5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
  f = f / (length(f) + 1e-4) * uEps * C * vec2(1., -1.);
  vec2 v = texture(uVel, vUv).xy + f * uDt;
  // rear-wing tip vortex: clockwise on screen, rolling the flow down into the wake
  vec2 r = vec2(vUv.x, 1. - vUv.y) * uView - uTip;
  v += vec2(-r.y, -r.x) / uTipR * exp(-dot(r, r) / (uTipR * uTipR)) * uTipK * uDt;
  vec4 m = texture(uMask, vUv);
  if (m.x < 0.) v = m.zw;
  o = vec4(clamp(v, -60., 60.), 0., 1.);
}`;

const FS_DIV = FSH + `
uniform sampler2D uVel, uMask; uniform vec2 uTx, uFree;
vec2 V(vec2 uv){
  if (uv.x < 0.) return uFree;
  vec4 m = texture(uMask, uv);
  return m.x < 0. ? m.zw : texture(uVel, clamp(uv, 0., 1.)).xy;
}
void main(){
  float L = V(vUv - vec2(uTx.x, 0.)).x, R = V(vUv + vec2(uTx.x, 0.)).x;
  float B = V(vUv - vec2(0., uTx.y)).y, T = V(vUv + vec2(0., uTx.y)).y;
  o = vec4(.5 * (R - L + T - B), 0., 0., 1.);
}`;

// Jacobi: Neumann at solids and the inlet, p = 0 at the outlet / top / bottom.
const FS_PRES = FSH + `
uniform sampler2D uP, uDiv, uMask; uniform vec2 uTx;
float P(vec2 uv, float pc){
  if (uv.x < 0.) return pc;
  if (uv.x > 1. || uv.y < 0. || uv.y > 1.) return 0.;
  return texture(uMask, uv).x < 0. ? pc : texture(uP, uv).x;
}
void main(){
  float pc = texture(uP, vUv).x;
  float s = P(vUv - vec2(uTx.x, 0.), pc) + P(vUv + vec2(uTx.x, 0.), pc) + P(vUv - vec2(0., uTx.y), pc) + P(vUv + vec2(0., uTx.y), pc);
  o = vec4((s - texture(uDiv, vUv).x) * .25, 0., 0., 1.);
}`;

const FS_GRAD = FSH + `
uniform sampler2D uP, uVel, uMask; uniform vec2 uTx;
float P(vec2 uv, float pc){
  if (uv.x < 0.) return pc;
  if (uv.x > 1. || uv.y < 0. || uv.y > 1.) return 0.;
  return texture(uMask, uv).x < 0. ? pc : texture(uP, uv).x;
}
void main(){
  float pc = texture(uP, vUv).x;
  vec2 g = .5 * vec2(P(vUv + vec2(uTx.x, 0.), pc) - P(vUv - vec2(uTx.x, 0.), pc), P(vUv + vec2(0., uTx.y), pc) - P(vUv - vec2(0., uTx.y), pc));
  vec2 v = texture(uVel, vUv).xy - g;
  vec4 m = texture(uMask, vUv);
  if (m.x < 0.) v = m.zw;
  o = vec4(v, 0., 1.);
}`;

// ---------- dye: BFECC/MacCormack advection keeps filaments razor thin ----------
const DYE_COMMON = LIB + RAKE + `
uniform sampler2D uDye, uVel, uMask;
uniform vec2 uView, uInvView;
uniform float uDtS, uShift, uScrollPrev, uDecayPx;
// Outside the viewport the tunnel is assumed laminar: straight rake filaments.
vec2 synth(vec2 uv){ return rake((1. - uv.y) * uView.y + uScrollPrev) * exp(-uDecayPx * max(uv.x, 0.) * uView.x); }
vec2 dyeAt(vec2 uv){ return (uv.x < 0. || uv.y < 0. || uv.y > 1.) ? synth(uv) : texture(uDye, uv).xy; }
`;
const FS_DYE_A = FSH + DYE_COMMON + `
void main(){
  vec2 v = texture(uVel, vUv).xy;
  o = vec4(dyeAt(vUv - v * uDtS * uInvView - vec2(0., uShift)), 0., 1.);
}`;
const FS_DYE_B = FSH + DYE_COMMON + `
uniform sampler2D uPhi1;
uniform vec2 uDyeRes;
uniform float uDecay, uNozzle, uScrollNow, uDiffuse;
uniform vec4 uCarRect;
void main(){
  vec2 k = uDtS * uInvView;
  vec2 v = texture(uVel, vUv).xy;
  vec2 xb = vUv - v * k - vec2(0., uShift);
  vec2 p1 = texture(uPhi1, vUv).xy;
  vec2 r = p1;
  if (xb.x > 0. && xb.y > 0. && xb.y < 1. && xb.x < 1.){
    vec2 vb = texture(uVel, xb).xy;
    vec2 p2 = texture(uPhi1, xb + vb * k + vec2(0., uShift)).xy;
    r = p1 + .5 * (texture(uDye, xb).xy - p2);
    vec2 tc = xb * uDyeRes - .5, b = (floor(tc) + .5) / uDyeRes, tx = 1. / uDyeRes;
    vec2 a0 = texture(uDye, b).xy, a1 = texture(uDye, b + vec2(tx.x, 0.)).xy, a2 = texture(uDye, b + vec2(0., tx.y)).xy, a3 = texture(uDye, b + tx).xy;
    r = clamp(r, min(min(a0, a1), min(a2, a3)), max(max(a0, a1), max(a2, a3)));
  }
  // downstream diffusion: filaments soften as they age
  vec2 tx = 1. / uDyeRes;
  vec2 avg = .25 * (texture(uPhi1, vUv + vec2(tx.x, 0.)).xy + texture(uPhi1, vUv - vec2(tx.x, 0.)).xy + texture(uPhi1, vUv + vec2(0., tx.y)).xy + texture(uPhi1, vUv - vec2(0., tx.y)).xy);
  r = mix(r, avg, uDiffuse * smoothstep(.05, 1., vUv.x));
  r *= uDecay;
  vec2 px = vec2(vUv.x, 1. - vUv.y) * uView;
  r = max(r, rake(px.y + uScrollNow) * smoothstep(uNozzle, uNozzle * .3, px.x));
  vec4 m = texture(uMask, vUv);
  // smoke clings a few px over the car's outline (visible over the car); text and cards cut it cleanly
  vec2 cq = max(abs(px - (uCarRect.xy + uCarRect.zw * .5)) - uCarRect.zw * .5, 0.);
  r *= (uCarRect.z > 0. && cq == vec2(0.)) ? smoothstep(-7., 1., m.x) : smoothstep(0., 3., m.x);
  o = vec4(max(r, 0.), 0., 1.);
}`;

// Advected texture coordinates (two phases, Neyret-style) so the Cycles wisp sheet flows with the smoke.
const FS_ADV_UV = FSH + `
uniform sampler2D uUV, uVel;
uniform vec2 uInvView, uView;
uniform float uDtS, uShift, uReset0, uReset1, uTile;
uniform vec4 uOff;
vec4 ident(vec2 uv){ vec2 p = vec2(uv.x, 1. - uv.y) * uView / uTile; return vec4(p + uOff.xy, p + uOff.zw); }
void main(){
  vec2 v = texture(uVel, vUv).xy;
  vec2 src = vUv - v * uDtS * uInvView - vec2(0., uShift);
  vec4 c = (src.x < 0. || src.y < 0. || src.y > 1. || src.x > 1.) ? ident(src) : texture(uUV, src);
  vec4 id = ident(vUv);
  if (uReset0 > .5) c.xy = id.xy;
  if (uReset1 > .5) c.zw = id.zw;
  o = c;
}`;

const FS_GLOW = FSH + `
uniform sampler2D uDye; uniform vec2 uTx;
void main(){
  vec2 d = texture(uDye, vUv + uTx * vec2(-1., -1.)).xy + texture(uDye, vUv + uTx * vec2(1., -1.)).xy
         + texture(uDye, vUv + uTx * vec2(-1., 1.)).xy + texture(uDye, vUv + uTx * vec2(1., 1.)).xy;
  o = vec4(d * .25, 0., 1.);
}`;

const FS_PREFILL = FSH + LIB + RAKE + `
uniform vec2 uView; uniform float uScrollPrev, uDecayPx;
void main(){ o = vec4(rake((1. - vUv.y) * uView.y + uScrollPrev) * exp(-uDecayPx * vUv.x * uView.x), 0., 1.); }`;

const FS_COPY = FSH + `uniform sampler2D uSrc; void main(){ o = texture(uSrc, vUv); }`;
const FS_FILL = FSH + `uniform vec4 uVal; void main(){ o = uVal; }`;

// ---------- composite: smoke tone-map, wisp texture, laser sheet ----------
const FS_RENDER = FSH + `
uniform sampler2D uDye, uGlow, uVel, uMask, uUV, uWisp;
uniform vec2 uDyeRes, uGlowRes, uView;
uniform vec4 uCarRect;
uniform float uFreeMag, uTime, uLaserY, uLaserW, uGain, uDim, uPhase, uWispK, uHasWisp, uDebug;
vec4 bspline(sampler2D t, vec2 uv, vec2 res){
  vec2 s = uv * res - .5, i = floor(s), f = s - i, f2 = f * f, f3 = f2 * f;
  vec2 w0 = (1. - 3.*f + 3.*f2 - f3) / 6., w1 = (4. - 6.*f2 + 3.*f3) / 6., w2 = (1. + 3.*f + 3.*f2 - 3.*f3) / 6., w3 = f3 / 6.;
  vec2 g0 = w0 + w1, g1 = w2 + w3;
  vec2 h0 = (i - .5 + w1 / g0) / res, h1 = (i + 1.5 + w3 / g1) / res;
  return g0.y * (g0.x * texture(t, h0) + g1.x * texture(t, vec2(h1.x, h0.y))) + g1.y * (g0.x * texture(t, vec2(h0.x, h1.y)) + g1.x * texture(t, h1));
}
float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
void main(){
  vec2 px = vec2(vUv.x, 1. - vUv.y) * uView;
  vec4 m = texture(uMask, vUv);
  if (uDebug > .5){ o = vec4(m.x < 0. ? .5 : 0., m.y * .3, 0., .5); return; }
  vec2 d = bspline(uDye, vUv, uDyeRes).xy * uGain;
  vec2 g = bspline(uGlow, vUv, uGlowRes).xy * uGain;
  // wisp detail from the Cycles sheet, carried by the flow (two crossfaded phases)
  float wd = 1.;
  if (uHasWisp > .5){
    vec4 c = texture(uUV, vUv);
    float wa = abs(1. - 2. * uPhase), wb = 1. - wa;
    float sa = texture(uWisp, c.xy * vec2(.55, 1.)).a, sb = texture(uWisp, c.zw * vec2(.55, 1.) + .37).a;
    float w = (wa * sa + wb * sb - .5) / sqrt(wa * wa + wb * wb) + .5;
    float k = uWispK * smoothstep(.1, .85, vUv.x);
    wd = mix(1., clamp(.35 + 1.25 * w, 0., 1.6), k);
  }
  d *= wd; g *= mix(1., wd, .5);
  float spd = length(texture(uVel, vUv).xy) / max(uFreeMag, 1e-3);
  float comp = clamp(spd, .2, 2.2);
  float laser = exp(-pow((px.y - uLaserY) / uLaserW, 2.));
  vec3 grey = vec3(.78, .85, .95), blue = vec3(.169, .482, 1.);
  float gi = 1. - exp(-d.x * 1.5), bi = 1. - exp(-d.y * 1.7);
  vec3 col = grey * gi * .5 + blue * bi * .95 + grey * (1. - exp(-g.x * 1.2)) * .16 + blue * (1. - exp(-g.y * 1.4)) * .42;
  col *= (.72 + .3 * comp) * (1. + .9 * laser);
  vec2 cq = max(abs(px - (uCarRect.xy + uCarRect.zw * .5)) - uCarRect.zw * .5, 0.);
  bool inCar = uCarRect.z > 0. && cq == vec2(0.);
  float keep = mix(1., uDim, m.y) * (inCar ? smoothstep(-9., 2., m.x) : smoothstep(-1., 5., m.x));
  col *= keep;
  col += blue * laser * .010 * keep;
  col += (hash(gl_FragCoord.xy + fract(uTime) * 91.) - .5) / 255.;
  col = max(col, 0.);
  o = vec4(col, min(1., max(col.r, max(col.g, col.b)) * .22));
}`;

// ---------- particles: transform-feedback update (GPU only) ----------
const VS_PUPD = HDR + `
layout(location = 0) in vec4 aS;   // x, y (CSS px, viewport), age (s), seed
out vec4 vS;
uniform sampler2D uVel, uMask;
uniform vec2 uView;
uniform float uDtS, uDt, uDy, uSpawnLeft, uLifeMin, uLifeMax, uBurst, uDormant;
uniform vec4 uBurstRect;
uniform uint uFrame;
uint pcg(uint v){ uint s = v * 747796405u + 2891336453u; uint w = ((s >> ((s >> 28u) + 4u)) ^ s) * 277803737u; return (w >> 22u) ^ w; }
float rnd(inout uint s){ s = pcg(s); return float(s) / 4294967296.; }
vec2 uvOf(vec2 p){ return vec2(p.x / uView.x, 1. - p.y / uView.y); }
vec2 vel(vec2 p){ vec2 v = textureLod(uVel, clamp(uvOf(p), 0., 1.), 0.).xy; return vec2(v.x, -v.y) * uDtS; }
void main(){
  vec4 s = aS; vec2 p = s.xy; p.y -= uDy;
  float seed = s.w;
  vec2 k1 = vel(p); p += vel(p + .5 * k1);
  float life = mix(uLifeMin, uLifeMax, fract(seed * 7.31));
  float age = s.z + uDt;
  float sd = textureLod(uMask, clamp(uvOf(p), 0., 1.), 0.).x;
  bool dead = age > life || p.x > uView.x + 60. || p.x < -80. || p.y < -80. || p.y > uView.y + 80. || sd < -2.;
  uint st = uint(gl_VertexID) * 1973u + uFrame * 9277u + 12345u;
  if (uBurst > .5){
    p = uBurstRect.xy + vec2(rnd(st), rnd(st)) * uBurstRect.zw; age = 0.; seed = fract(seed + .618034);
  } else if (dead){
    if (uDormant > .5){ p = vec2(-1e4); age = 1e3; }
    else {
      float r1 = rnd(st), r2 = rnd(st);
      p = rnd(st) < uSpawnLeft ? vec2(r1 * 10. - 4., r2 * uView.y) : vec2(r1 * uView.x, r2 * uView.y);
      if (textureLod(uMask, clamp(uvOf(p), 0., 1.), 0.).x < 3.) p.x = r1 * 10. - 4.;
      age = 0.; seed = fract(seed + .618034);
    }
  }
  if (uDormant > .5 && s.z >= 999.) { p = vec2(-1e4); age = 1e3; if (uBurst > .5){ p = uBurstRect.xy + vec2(rnd(st), rnd(st)) * uBurstRect.zw; age = 0.; } }
  vS = vec4(p, age, seed);
}`;
const FS_NULL = HDR + `out vec4 o; void main(){ o = vec4(0.); }`;

// Tracer motes: Cycles mote sprites (glow / bokeh / streak), stretched along the local velocity.
const VS_PDRAW = HDR + `
layout(location = 0) in vec4 aS;
uniform sampler2D uVel, uMask;
uniform vec2 uView;
uniform float uStreak, uDpr, uLaserY, uLaserW, uAlpha, uDim, uLifeMin, uLifeMax, uSize;
out vec2 vT; out float vA; out vec3 vC; out float vK; out float vLen;
void main(){
  vec2 p = aS.xy; float seed = aS.w;
  vec2 uv = vec2(p.x / uView.x, 1. - p.y / uView.y);
  vec2 v = textureLod(uVel, clamp(uv, 0., 1.), 0.).xy; vec2 vc = vec2(v.x, -v.y) * 100.;
  float sp = length(vc); vec2 dir = sp > 1e-3 ? vc / sp : vec2(1., 0.);
  float kind = fract(seed * 5.17);
  float k = kind < .72 ? 0. : kind < .88 ? 1. : kind < .96 ? 2. : 0.;
  float size = uSize * (k > .5 ? mix(2.2, 5., fract(seed * 11.3)) : mix(.9, 2.2, pow(fract(seed * 13.7), 3.)));
  size = max(size, 1.1 / uDpr);
  float len = k > .5 ? min(sp * uStreak * .25, size) : min(sp * uStreak, 42.);
  int id = gl_VertexID;
  vec2 c = vec2(float(id & 1), float(id >> 1));
  float along = mix(-len - size, size, c.x), across = mix(-size, size, c.y);
  vec2 q = p + dir * along + vec2(-dir.y, dir.x) * across;
  gl_Position = vec4(q.x / uView.x * 2. - 1., 1. - q.y / uView.y * 2., 0., 1.);
  vT = vec2((along + len + size) / (len + 2. * size), c.y);
  vLen = len / (len + 2. * size);
  float life = mix(uLifeMin, uLifeMax, fract(seed * 7.31)), age = aS.z;
  float fade = smoothstep(0., .7, age) * smoothstep(life, life - 1., age);
  vec4 m = textureLod(uMask, clamp(uv, 0., 1.), 0.);
  fade *= smoothstep(0., 6., m.x) * mix(1., uDim, m.y);
  float laser = exp(-pow((p.y - uLaserY) / uLaserW, 2.));
  float e = (k > .5 ? .35 : 1.) * size / (size + len * .3);
  vA = uAlpha * fade * e * (.35 + 1.4 * laser) * mix(.45, 1., fract(seed * 3.3));
  vC = fract(seed * 9.1) < .24 ? vec3(.3, .58, 1.) : vec3(.86, .92, 1.);
  vK = (len > size * 1.5 && k < .5) ? 3. : k;
}`;
const FS_PDRAW = HDR + `
uniform sampler2D uMotes;
in vec2 vT; in float vA; in vec3 vC; in float vK; in float vLen;
out vec4 o;
void main(){
  vec2 t = vT;
  if (vK > 2.5){ t.x = mix(.15, .85, t.x); }            // streak cell: motion-blurred Cycles mote
  vec4 s = texture(uMotes, vec2((vK + t.x) * .25, t.y));
  float tail = vK > 2.5 ? mix(.25, 1., vT.x * vT.x) : 1.;
  o = vec4(vC * s.rgb * vA * tail, 0.);
}`;

// Volumetric puffs: Cycles smoke atlas frames as soft rotating sprites that ride the flow.
const VS_PUFF = HDR + `
layout(location = 0) in vec4 aS;
uniform sampler2D uVel, uMask;
uniform vec2 uView;
uniform float uAlpha, uDim, uLifeMin, uLifeMax, uSize, uGrow, uTime;
out vec2 vT; out float vA; out float vF; out float vBlue;
void main(){
  vec2 p = aS.xy; float seed = aS.w, age = aS.z;
  vec2 uv = vec2(p.x / uView.x, 1. - p.y / uView.y);
  vec2 v = textureLod(uVel, clamp(uv, 0., 1.), 0.).xy; vec2 vc = vec2(v.x, -v.y);
  float sp = length(vc);
  float ang = atan(vc.y, vc.x) + (fract(seed * 4.7) - .5) * .8 + age * (fract(seed * 8.3) - .5) * .9;
  float life = mix(uLifeMin, uLifeMax, fract(seed * 7.31));
  float sz = uSize * mix(.6, 1.3, fract(seed * 2.9)) * (1. + uGrow * age / life);
  vec2 sc = sz * vec2(1. + min(sp * .05, .8), 1.);
  int id = gl_VertexID; vec2 c = vec2(float(id & 1), float(id >> 1));
  vec2 l = (c - .5) * sc;
  vec2 q = p + vec2(cos(ang) * l.x - sin(ang) * l.y, sin(ang) * l.x + cos(ang) * l.y);
  gl_Position = vec4(q.x / uView.x * 2. - 1., 1. - q.y / uView.y * 2., 0., 1.);
  vT = c;
  vF = floor(fract(seed * 17.13) * 32.);
  vec4 m = textureLod(uMask, clamp(uv, 0., 1.), 0.);
  float fade = smoothstep(0., life * .25, age) * smoothstep(life, life * .55, age);
  vA = uAlpha * fade * mix(1., uDim, m.y) * smoothstep(-20., 30., m.x);
  vBlue = step(.8, fract(seed * 6.1));
}`;
const FS_PUFF = HDR + `
uniform sampler2D uAtlas;
in vec2 vT; in float vA; in float vF; in float vBlue;
out vec4 o;
void main(){
  vec2 cell = vec2(mod(vF, 8.), floor(vF / 8.));
  vec4 s = texture(uAtlas, (cell + vec2(vT.x, 1. - vT.y)) / vec2(8., 4.));
  vec3 tint = mix(vec3(.8, .87, 1.), vec3(.3, .55, 1.), vBlue);
  o = vec4(s.rgb * tint * vA, s.a * vA * .25);
}`;

// ---------- GL helpers ----------
function compile(type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) + '\n' + src.split('\n').map((l, i) => (i + 1) + ': ' + l).join('\n'));
  return s;
}
function program(vs, fs, varyings) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
  if (varyings) gl.transformFeedbackVaryings(p, varyings, gl.SEPARATE_ATTRIBS);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name.replace(/\[0\]$/, ''); u[name] = gl.getUniformLocation(p, name); }
  let unit = 0;
  const P = {
    use() { gl.useProgram(p); unit = 0; return P; },
    f(k, ...v) { const l = u[k]; if (l != null) gl['uniform' + v.length + 'f'](l, ...v); return P; },
    i(k, v) { if (u[k] != null) gl.uniform1i(u[k], v); return P; },
    ui(k, v) { if (u[k] != null) gl.uniform1ui(u[k], v); return P; },
    v4(k, a) { if (u[k] != null) gl.uniform4fv(u[k], a); return P; },
    t(k, tex) { if (u[k] == null) return P; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); gl.uniform1i(u[k], unit++); return P; },
  };
  return P;
}
function target(w, h, f) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, f[0], w, h, 0, f[1], gl.HALF_FLOAT, null);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  return { tex, fbo, w, h, free() { gl.deleteTexture(tex); gl.deleteFramebuffer(fbo); } };
}
function pair(w, h, f) {
  const d = { a: target(w, h, f), b: target(w, h, f), get read() { return this.a; }, get write() { return this.b; }, swap() { [this.a, this.b] = [this.b, this.a]; }, free() { this.a.free(); this.b.free(); } };
  return d;
}
function draw(t) {
  if (t) { gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo); gl.viewport(0, 0, t.w, t.h); }
  else { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, canvas.width, canvas.height); }
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}

// ---------- sprite textures from assets/smoke (Cycles renders) ----------
function loadSprite(name, key, repeat) {
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => {
    if (!glOK) return;
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);   // assets are straight alpha
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    const wrap = repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
    TEX[key] = t;
  };
  img.onerror = () => {};
  img.src = ASSETS + name;
}

function glyphTexture(w, h) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  return { tex, w, h, free() { gl.deleteTexture(tex); } };
}

// Redraw + upload the glyph raster and rebuild its SDF only when something moved.
let lastGlyphScroll = NaN;
function updateGlyphs() {
  if (!(glyphs.dirty || st.scroll !== lastGlyphScroll || st.carMask)) return;
  glyphs.dirty = false; lastGlyphScroll = st.scroll;
  drawGlyphs();
  gl.bindTexture(gl.TEXTURE_2D, S.glyph.tex);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, gctx.canvas);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  R.seed.use().t('uGlyph', S.glyph.tex); draw(S.jfa.read);
  // steps cover ~60 texels (> 100 CSS px); a final step of 1 cleans up JFA errors
  for (const k of [32, 16, 8, 4, 2, 1, 1]) { R.jfa.use().t('uS', S.jfa.read.tex).i('uStep', k); draw(S.jfa.write); S.jfa.swap(); }
}

// ---------- build / rebuild ----------
let T, S = {}, dims = {};
function sizes() {
  T = TIERS[tierIx];
  const W = innerWidth, H = innerHeight;
  let cell = T.cell; while ((W / cell) * (H / cell) > 120000) cell += 0.5;
  let dye = T.dye; while ((W / dye) * (H / dye) > 700000) dye += 0.25;
  const dpr = Math.min(devicePixelRatio || 1, T.dpr);
  return { W, H, sw: Math.max(16, Math.round(W / cell)), sh: Math.max(16, Math.round(H / cell)), cell: W / Math.max(16, Math.round(W / cell)),
    dw: Math.max(32, Math.round(W / dye)), dh: Math.max(32, Math.round(H / dye)), dyePx: W / Math.max(32, Math.round(W / dye)),
    cw: Math.round(W * dpr), ch: Math.round(H * dpr), dpr };
}

function initGL() {
  gl = getGL(canvas);
  if (!gl) return false;
  const rg = testFormat(gl.RG16F, gl.RG), r1 = testFormat(gl.R16F, gl.RED), rgba = testFormat(gl.RGBA16F, gl.RGBA);
  if (!rgba) return false;
  fmt = { rgba: [gl.RGBA16F, gl.RGBA], rg: rg ? [gl.RG16F, gl.RG] : [gl.RGBA16F, gl.RGBA], r: r1 ? [gl.R16F, gl.RED] : (rg ? [gl.RG16F, gl.RG] : [gl.RGBA16F, gl.RGBA]) };
  try {
    R = {
      mask: program(VS_QUAD, FS_MASK), advVel: program(VS_QUAD, FS_ADV_VEL), curl: program(VS_QUAD, FS_CURL),
      vort: program(VS_QUAD, FS_VORT), div: program(VS_QUAD, FS_DIV), pres: program(VS_QUAD, FS_PRES),
      grad: program(VS_QUAD, FS_GRAD), dyeA: program(VS_QUAD, FS_DYE_A), dyeB: program(VS_QUAD, FS_DYE_B),
      advUV: program(VS_QUAD, FS_ADV_UV), glow: program(VS_QUAD, FS_GLOW), prefill: program(VS_QUAD, FS_PREFILL),
      copy: program(VS_QUAD, FS_COPY), fill: program(VS_QUAD, FS_FILL), render: program(VS_QUAD, FS_RENDER),
      pupd: program(VS_PUPD, FS_NULL, ['vS']), seed: program(VS_QUAD, FS_SEED), jfa: program(VS_QUAD, FS_JFA), pdraw: program(VS_PDRAW, FS_PDRAW), puff: program(VS_PUFF, FS_PUFF),
    };
  } catch (e) { console.warn('[windtunnel] shader build failed', e); return false; }
  R.vao = gl.createVertexArray();
  R.tf = gl.createTransformFeedback();
  glOK = true;
  for (const k in TEX) delete TEX[k];
  loadSprite('smoke-atlas.webp', 'atlas', false);
  loadSprite('motes.webp', 'motes', false);
  loadSprite('wisp.webp', 'wisp', true);
  build(null);
  return true;
}

// Particle pool: two float buffers ping-ponged through transform feedback.
function pool(n, lifeMin, lifeMax, dormant) {
  const data = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    const life = lifeMin + (lifeMax - lifeMin) * Math.random();
    data.set(dormant ? [-1e4, -1e4, 1e3, Math.random()] : [Math.random() * dims.W, Math.random() * dims.H, Math.random() * life, Math.random()], i * 4);
  }
  const mk = () => {
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_COPY);
    const upd = gl.createVertexArray(); gl.bindVertexArray(upd);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 16, 0);
    const drw = gl.createVertexArray(); gl.bindVertexArray(drw);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 4, gl.FLOAT, false, 16, 0); gl.vertexAttribDivisor(0, 1);
    gl.bindVertexArray(null);
    return { buf, upd, drw };
  };
  return { n, draw: n, lifeMin, lifeMax, dormant, a: mk(), b: mk(), free() { [this.a, this.b].forEach(x => { gl.deleteBuffer(x.buf); gl.deleteVertexArray(x.upd); gl.deleteVertexArray(x.drw); }); } };
}

function build(prev) {
  const d = sizes();
  const old = prev && S;
  dims = d;
  canvas.width = d.cw; canvas.height = d.ch;
  const N = {
    mask: target(d.dw, d.dh, fmt.rgba), vel: pair(d.sw, d.sh, fmt.rg), jfa: pair(d.dw, d.dh, fmt.rg), glyph: glyphTexture(d.dw, d.dh), p: pair(d.sw, d.sh, fmt.r),
    div: target(d.sw, d.sh, fmt.r), curl: target(d.sw, d.sh, fmt.r), uv: pair(d.sw, d.sh, fmt.rgba),
    dye: pair(d.dw, d.dh, fmt.rg), phi1: target(d.dw, d.dh, fmt.rg),
    glow: target(Math.max(8, d.dw >> 2), Math.max(8, d.dh >> 2), fmt.rg),
  };
  // resample the old state into the new textures so a resize doesn't pop
  if (old && old.vel) {
    gl.disable(gl.BLEND);
    R.copy.use().t('uSrc', old.vel.read.tex); draw(N.vel.read);
    R.copy.use().t('uSrc', old.dye.read.tex); draw(N.dye.read);
    R.copy.use().t('uSrc', old.uv.read.tex); draw(N.uv.read);
    ['mask', 'div', 'curl', 'phi1', 'glow', 'glyph'].forEach(k => old[k].free());
    ['vel', 'p', 'uv', 'dye', 'jfa'].forEach(k => old[k].free());
    N.parts = old.parts; N.puffs = old.puffs; N.gusts = old.gusts;
  } else {
    R.fill.use().f('uVal', freeStream(), 0, 0, 1); draw(N.vel.read);
    S = N; prefill();
    N.parts = pool(T.parts, 2.5, 8, false);
    N.puffs = pool(T.puffs, 5, 10, false);
    N.gusts = pool(Math.round(T.puffs * 1.5), 1.6, 3.2, true);
  }
  S = N;
  S.iters = T.iters;
  if (!gctx) {
    gctx = document.createElement('canvas').getContext('2d');
    tctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  }
  gctx.canvas.width = d.dw; gctx.canvas.height = d.dh;
  glyphs.dirty = true;
  S.uvT = [0, 0.5];
}

function prefill() {
  R.prefill.use().f('uView', dims.W, dims.H).f('uScrollPrev', st.scroll).f('uDecayPx', decayPx());
  rakeUniforms(R.prefill);
  draw(S.dye.read);
}

// ---------- flow parameters ----------
function carRect() { const c = st.car; return c ? [c.x, c.y, c.w, c.h] : [0, 0, 0, 0]; }
function freeStream() {
  const base = Math.max(1.1, Math.min(2.6, dims.W / 600));
  return base * (1 + st.speed / 115) * (1 + 1.6 * st.gust);
}
function decayPx() { return 0.75 / dims.W; }
function rakeUniforms(P) {
  const spacing = Math.max(11, Math.min(18, dims.H / 52));
  P.f('uSpacing', spacing).f('uSigma', Math.max(0.55, dims.dyePx * 0.42)).f('uTexelPx', dims.dyePx)
    .f('uTime', st.time).f('uEmit', 1.0 + 0.5 * st.gust);
}

// ---------- simulation step ----------
function step(dt, dy) {
  const d = dims, W = d.W, H = d.H;
  const U = freeStream(), dtS = dt * VU, shift = dy / H;
  const tx = [1 / d.sw, 1 / d.sh];
  gl.disable(gl.BLEND);
  gl.bindVertexArray(R.vao);

  // obstacles
  packRects();
  updateGlyphs();
  const car = st.car, p = st.ptr, ptrOn = FINE && performance.now() - p.t < 2500;
  R.mask.use().f('uView', W, H).v4('uR', st.shapes).v4('uP', st.props).i('uN', st.n)
    .t('uGlyph', S.glyph.tex).t('uJfa', S.jfa.read.tex).f('uGlyphOn', 1).f('uPxCss', d.dyePx)
    .f('uCar', ...(car ? [car.x, car.y, car.w, car.h] : [0, 0, 0, 0])).f('uCarV', st.carV[0] / VU, -st.carV[1] / VU).f('uCarOn', car ? (st.carMask ? 2 : 1) : 0)
    .f('uPtr', p.x, p.y, 30, ptrOn ? 1 : 0).f('uPtrV', p.vx / VU, -p.vy / VU);
  draw(S.mask);

  // velocity
  R.advVel.use().t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).f('uInvView', 1 / W, 1 / H).f('uFree', U, 0)
    .f('uDtS', dtS).f('uDt', dt).f('uShift', shift).f('uRelax', 0.18).f('uInlet', 0.01 + 0.03 * st.gust).f('uTime', st.time).f('uCell', d.cell);
  draw(S.vel.write); S.vel.swap();
  R.curl.use().t('uVel', S.vel.read.tex).f('uTx', ...tx); draw(S.curl);
  const eps = (4 + st.speed * 0.03 + 22 * st.gust) * (4 / d.cell);
  const tip = st.tip;
  R.vort.use().t('uVel', S.vel.read.tex).t('uCurl', S.curl.tex).t('uMask', S.mask.tex).f('uTx', ...tx).f('uEps', eps).f('uDt', dt)
    .f('uView', W, H).f('uTip', tip ? tip.x + tip.s * 0.08 : -1e4, tip ? tip.y + tip.s * 0.06 : -1e4)
    .f('uTipR', tip ? Math.max(10, tip.s * 0.16) : 1).f('uTipK', tip ? U * (5 + 4 * st.gust) * (0.75 + 0.25 * Math.sin(st.time * 7.3)) : 0);
  draw(S.vel.write); S.vel.swap();

  // pressure solve
  R.div.use().t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).f('uTx', ...tx).f('uFree', U, 0); draw(S.div);
  for (let i = 0; i < S.iters; i++) {
    R.pres.use().t('uP', S.p.read.tex).t('uDiv', S.div.tex).t('uMask', S.mask.tex).f('uTx', ...tx);
    draw(S.p.write); S.p.swap();
  }
  R.grad.use().t('uP', S.p.read.tex).t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).f('uTx', ...tx);
  draw(S.vel.write); S.vel.swap();

  // dye
  const dyeCommon = P => { P.t('uDye', S.dye.read.tex).t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).f('uView', W, H).f('uInvView', 1 / W, 1 / H)
    .f('uDtS', dtS).f('uShift', shift).f('uScrollPrev', st.scrollPrev).f('uDecayPx', decayPx()); rakeUniforms(P); return P; };
  dyeCommon(R.dyeA.use()); draw(S.phi1);
  dyeCommon(R.dyeB.use()).t('uPhi1', S.phi1.tex).f('uDyeRes', d.dw, d.dh).f('uDecay', Math.exp(-dt * U * VU * decayPx()))
    .f('uNozzle', 10).f('uScrollNow', st.scroll).f('uCarRect', ...carRect()).f('uDiffuse', 0.012 + 0.05 * st.gust);
  draw(S.dye.write); S.dye.swap();
  R.glow.use().t('uDye', S.dye.read.tex).f('uTx', 1 / d.dw, 1 / d.dh); draw(S.glow);

  // advected wisp coordinates: each phase resets once per cycle, half a cycle apart
  const period = 3.2;
  const ph = (st.time / period) % 1, prevPh = ((st.time - dt) / period) % 1;
  const reset0 = ph < prevPh, reset1 = ((ph + 0.5) % 1) < ((prevPh + 0.5) % 1);
  if (reset0) S.off0 = [Math.random() * 7, Math.random() * 7];
  if (reset1) S.off1 = [Math.random() * 7, Math.random() * 7];
  const o0 = S.off0 || [0, 0], o1 = S.off1 || [3.3, 1.7];
  R.advUV.use().t('uUV', S.uv.read.tex).t('uVel', S.vel.read.tex).f('uInvView', 1 / W, 1 / H).f('uView', W, H).f('uDtS', dtS).f('uShift', shift)
    .f('uReset0', reset0 || !S.uvInit ? 1 : 0).f('uReset1', reset1 || !S.uvInit ? 1 : 0).f('uTile', 420).f('uOff', o0[0], o0[1], o1[0], o1[1]);
  draw(S.uv.write); S.uv.swap(); S.uvInit = true;
  S.phase = ph;

  // particles (transform feedback, no CPU per-particle work)
  gl.enable(gl.RASTERIZER_DISCARD);
  const upd = (pl, extra) => {
    const src = pl.a, dst = pl.b;
    R.pupd.use().t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).f('uView', W, H).f('uDtS', dtS).f('uDt', dt).f('uDy', dy)
      .f('uLifeMin', pl.lifeMin).f('uLifeMax', pl.lifeMax).f('uDormant', pl.dormant ? 1 : 0).ui('uFrame', st.frame >>> 0)
      .f('uSpawnLeft', extra.left).f('uBurst', extra.burst ? 1 : 0).f('uBurstRect', ...(extra.rect || [0, 0, 0, 0]));
    gl.bindVertexArray(src.upd);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);        // dst must not be bound anywhere else during capture
    gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK, R.tf);
    gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, dst.buf);
    gl.beginTransformFeedback(gl.POINTS);
    gl.drawArrays(gl.POINTS, 0, pl.n);
    gl.endTransformFeedback();
    gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER, 0, null);
    gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK, null);
    pl.a = dst; pl.b = src;
  };
  upd(S.parts, { left: 0.3 });
  upd(S.puffs, { left: 0.55 });
  let rect = null;
  if (st.burst) {
    const c = st.car;
    rect = c ? [c.x + c.w * 0.4, c.y + c.h * 0.1, c.w * 0.9, c.h * 0.8] : [0, H * 0.35, W * 0.25, H * 0.4];
  }
  upd(S.gusts, { left: 0, burst: st.burst, rect });
  st.burst = 0;
  gl.disable(gl.RASTERIZER_DISCARD);
  gl.bindVertexArray(R.vao);
}

// ---------- render ----------
function render() {
  const d = dims, W = d.W, H = d.H;
  const laserY = H * (0.5 + 0.36 * Math.sin(st.time * 0.09)), laserW = H * 0.085;
  gl.bindVertexArray(R.vao);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.disable(gl.BLEND);
  const wisp = TEX.wisp;
  R.render.use().t('uDye', S.dye.read.tex).t('uGlow', S.glow.tex).t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).t('uUV', S.uv.read.tex)
    .t('uWisp', wisp || S.glow.tex)
    .f('uDyeRes', d.dw, d.dh).f('uGlowRes', S.glow.w, S.glow.h).f('uView', W, H).f('uFreeMag', freeStream())
    .f('uTime', st.time).f('uLaserY', laserY).f('uLaserW', laserW).f('uGain', 0.85).f('uDim', 0.08)
    .f('uCarRect', ...carRect()).f('uPhase', S.phase || 0).f('uWispK', 0.5).f('uHasWisp', wisp ? 1 : 0).f('uDebug', api.debug ? 1 : 0);
  draw(null);
  if (api.debug) return;

  gl.enable(gl.BLEND);
  // volumetric puffs (premultiplied over)
  if (TEX.atlas) {
    gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const puff = (pl, alpha, size, grow) => {
      R.puff.use().t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).t('uAtlas', TEX.atlas).f('uView', W, H)
        .f('uAlpha', alpha).f('uDim', 0.08).f('uLifeMin', pl.lifeMin).f('uLifeMax', pl.lifeMax).f('uSize', size).f('uGrow', grow).f('uTime', st.time);
      gl.bindVertexArray(pl.a.drw);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, pl.draw);
    };
    const sc = Math.min(1, W / 1200);
    puff(S.puffs, 0.085, 190 * Math.max(0.55, sc), 1.4);
    puff(S.gusts, 0.36, 150 * Math.max(0.6, sc), 2.2);
  }
  // tracer motes (additive)
  if (TEX.motes) {
    gl.blendFuncSeparate(gl.ONE, gl.ONE, gl.ZERO, gl.ONE);
    R.pdraw.use().t('uVel', S.vel.read.tex).t('uMask', S.mask.tex).t('uMotes', TEX.motes).f('uView', W, H)
      .f('uStreak', 0.022).f('uDpr', d.dpr).f('uLaserY', laserY).f('uLaserW', laserW).f('uAlpha', 0.85).f('uDim', 0.1)
      .f('uLifeMin', S.parts.lifeMin).f('uLifeMax', S.parts.lifeMax).f('uSize', 1);
    gl.bindVertexArray(S.parts.a.drw);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, S.parts.draw);
  }
  gl.bindVertexArray(R.vao);
  gl.disable(gl.BLEND);
}

// ---------- frame loop ----------
let last = 0, lastRender = 0, slow = 0, shown = false;
function readInputs(dt) {
  const target = +window.__speed || 0;
  st.speed += (Math.max(0, Math.min(350, target)) - st.speed) * Math.min(1, dt * 3);
  st.gust *= Math.exp(-dt / 1.4);
  // car silhouette mask (preferred) or its screen bounds
  const cm = window.__carMask;
  st.carMask = cm && cm.canvas && cm.canvas.width > 0 && cm.w > 4 && cm.h > 4 ? cm : null;
  if (!st.carMask) st.tip = null;
  const cb = st.carMask || window.__carBounds;
  if (cb && cb.w > 4 && cb.h > 4) {
    if (st.car && dt > 0) {
      const vx = ((cb.x + cb.w / 2) - (st.car.x + st.car.w / 2)) / dt, vy = ((cb.y + cb.h / 2) - (st.car.y + st.car.h / 2)) / dt;
      st.carV[0] += (Math.max(-3000, Math.min(3000, vx)) - st.carV[0]) * 0.3;
      st.carV[1] += (Math.max(-3000, Math.min(3000, vy)) - st.carV[1]) * 0.3;
    }
    st.car = { x: cb.x, y: cb.y, w: cb.w, h: cb.h };
  } else { st.car = null; st.carV = [0, 0]; }
  st.ptr.vx *= Math.exp(-dt * 6); st.ptr.vy *= Math.exp(-dt * 6);
}
function frame(now) {
  raf = requestAnimationFrame(frame);
  if (now - lastRender < 12) return;          // cap at ~60 Hz on high-refresh screens
  const dt = Math.min(1 / 30, Math.max(0.001, (now - (last || now - 16)) / 1000));
  last = now; lastRender = now;
  const y = scrollY;
  let dy = y - st.scroll;
  st.scrollPrev = st.scroll; st.scroll = y;
  if (Math.abs(dy) > dims.H * 0.9) { dy = 0; st.scrollPrev = y; prefill(); }
  readInputs(dt);
  st.time += dt; st.frame++;
  step(dt, dy);
  const base = Math.max(1.1, Math.min(2.6, dims.W / 600));
  window.__windFlow = { speed: Math.min(1, freeStream() / (base * 4)), gust: Math.min(1, st.gust) };
  render();
  if (!shown) { shown = true; canvas.style.opacity = '1'; }
  // adaptive quality: shed pressure iterations and particle draws if frames run long
  slow = dt > 0.024 ? slow + 1 : Math.max(0, slow - 2);
  if (slow > 90) {
    slow = 0;
    if (S.iters > 10) S.iters -= 4;
    else { S.parts.draw = Math.max(3000, Math.round(S.parts.draw * 0.7)); }
  }
}
function start() { if (!raf && alive && glOK && !frozen && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); } }
function stop() { cancelAnimationFrame(raf); raf = 0; }

// Reduced motion: settle the flow offscreen, show one still frame; refresh after scrolling stops.
let stillTimer = 0;
function still(steps) {
  if (!glOK) return;
  st.scrollPrev = st.scroll = scrollY;
  prefill();
  for (let i = 0; i < steps; i++) { st.time += 1 / 60; st.frame++; step(1 / 60, 0); }
  render();
  canvas.style.opacity = '1';
  window.__windFlow = { speed: 0, gust: 0 };    // frozen: car.js should idle too
}

// ---------- wiring ----------
on(window, 'resize', () => { if (!glOK) return; requestAnimationFrame(() => { if (!glOK) return; const d = sizes(); if (d.W !== dims.W || d.H !== dims.H || d.cw !== canvas.width) { build(true); if (frozen) still(60); } queueMeasure(); }); });
on(window, 'scroll', () => {
  if (frozen && glOK) { canvas.style.opacity = '0'; clearTimeout(stillTimer); stillTimer = setTimeout(() => { measure(); still(70); }, 220); }
}, { passive: true });
on(window, 'pointermove', e => {
  if (!FINE) return;
  const p = st.ptr, t = performance.now(), dt = Math.max(8, t - (p.t || t - 16)) / 1000;
  if (t - p.t < 200) { p.vx += ((e.clientX - p.x) / dt - p.vx) * 0.5; p.vy += ((e.clientY - p.y) / dt - p.vy) * 0.5; }
  p.vx = Math.max(-2500, Math.min(2500, p.vx)); p.vy = Math.max(-2500, Math.min(2500, p.vy));
  p.x = e.clientX; p.y = e.clientY; p.t = t;
}, { passive: true });
on(window, 'race:go', () => { st.gust = 1; st.burst = 1; [700, 1600, 2600].forEach(t => setTimeout(() => alive && measure(), t)); });
// re-measure after scrolling settles and again once reveal animations have finished
let scrollEnd = 0;
on(window, 'scroll', () => { clearTimeout(scrollEnd); scrollEnd = setTimeout(() => { measure(); setTimeout(() => alive && measure(), 900); }, 180); }, { passive: true });
on(window, 'race:reset', () => { st.gust = 0; queueMeasure(); });
function setFrozen(f) {
  f = f || REDUCE;
  if (f === frozen) return;
  frozen = f;
  if (frozen) { stop(); if (glOK) render(); }   // keep the current smoke on screen, just stop it
  else { canvas.style.opacity = '1'; start(); }
}
on(window, 'motion:toggle', e => setFrozen(!!(e.detail && e.detail.paused)));
const mo = new MutationObserver(() => setFrozen(userPaused()));
mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-motion'] });
on(document, 'visibilitychange', () => { document.hidden ? stop() : start(); });
on(canvas, 'webglcontextlost', e => { e.preventDefault(); stop(); glOK = false; });
on(canvas, 'webglcontextrestored', () => { if (initGL()) { frozen ? still(60) : start(); } });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => alive && measure());
const ro = new ResizeObserver(queueMeasure); ro.observe(document.body);
const ival = setInterval(() => { if (!document.hidden) measure(); }, 3000);   // safety net for late layout shifts

const api = {
  debug: false,
  get tier() { return T && T.name; },
  get dims() { return dims; },
  get stats() { return { rects: st.rects.length, glyphs: glyphs.list.length, solid: glyphs.list.filter(g => g.s).length, tip: st.tip }; },
  destroy() {
    alive = false; stop(); clearInterval(ival); ro.disconnect(); mo.disconnect(); disposers.forEach(f => f());
    if (glOK) { try { gl.getExtension('WEBGL_lose_context')?.loseContext(); } catch (e) {} }
    glOK = false; canvas.remove(); if (fb) fb.stop();
    if (window.__windTunnel === api) delete window.__windTunnel;
  },
};
window.__windTunnel = api;

// ---------- Canvas2D fallback: streak tracers bent around obstacle rects ----------
let fb = null;
function fallback2D() {
  if (REDUCE) return null;
  const c2 = makeCanvas(); canvas.remove(); canvas = c2;
  const ctx = canvas.getContext('2d'); if (!ctx) return null;
  const n = innerWidth < 700 ? 140 : 320, P = [];
  let W, H, dpr, id = 0, ly = scrollY, lt = 0;
  const size = () => { dpr = Math.min(devicePixelRatio || 1, 1.5); W = innerWidth; H = innerHeight; canvas.width = W * dpr; canvas.height = H * dpr; };
  size(); on(window, 'resize', size);
  for (let i = 0; i < n; i++) P.push({ x: Math.random() * W, y: Math.random() * H, b: Math.random() < 0.2 });
  const tick = t => {
    id = requestAnimationFrame(tick);
    const dt = Math.min(0.033, (t - (lt || t)) / 1000 || 0.016); lt = t;
    const dy = scrollY - ly; ly = scrollY; packRects();
    const U = 160 * (1 + (+window.__speed || 0) / 115);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = 'destination-out'; ctx.fillStyle = 'rgba(0,0,0,0.12)'; ctx.fillRect(0, 0, W, H);
    if (dy) { ctx.globalCompositeOperation = 'copy'; ctx.drawImage(canvas, 0, -dy * dpr, canvas.width, canvas.height, 0, 0, W, H); }
    ctx.globalCompositeOperation = 'lighter'; ctx.lineWidth = 1;
    for (const p of P) {
      p.y -= dy;
      let vx = U, vy = 0;
      for (let i = 0; i < st.n; i++) {
        if (st.props[i * 4 + 1] > 0.5) continue;
        const x = st.shapes[i * 4], y = st.shapes[i * 4 + 1], w = st.shapes[i * 4 + 2], h = st.shapes[i * 4 + 3];
        const cx = x + w / 2, cy = y + h / 2, qx = Math.abs(p.x - cx) - w / 2, qy = Math.abs(p.y - cy) - h / 2;
        const dd = Math.max(qx, qy);
        if (dd < 50 && dd > -h) { const s = Math.sign(p.y - cy) || 1, k = Math.exp(-Math.max(0, dd) / 22); vy += s * U * 0.9 * k * (qx < 0 ? 1 : Math.exp(-qx / 40)); }
      }
      const x0 = p.x, y0 = p.y;
      p.x += vx * dt; p.y += vy * dt;
      if (p.x > W + 10 || p.y < -20 || p.y > H + 20) { p.x = -Math.random() * 30; p.y = Math.random() * H; continue; }
      ctx.strokeStyle = p.b ? 'rgba(43,123,255,0.5)' : 'rgba(210,222,240,0.22)';
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(p.x, p.y); ctx.stroke();
    }
  };
  canvas.style.opacity = '1';
  id = requestAnimationFrame(tick);
  return { stop() { cancelAnimationFrame(id); } };
}

// ---------- boot ----------
measure();
try {
  if (initGL()) {
    if (frozen) still(90);
    else { for (let i = 0; i < (tierIx ? 30 : 12); i++) { st.time += 1 / 60; step(1 / 60, 0); } start(); }
  } else { glOK = false; fb = fallback2D(); }
} catch (e) {
  console.warn('[windtunnel] disabled', e);
  glOK = false; try { fb = fallback2D(); } catch (e2) {}
}
