// Studio / wind-tunnel scene for the hero: Pranav's F1 car on a rolling road under automotive softbox lighting.
// Contracts:
//   listens  race:reset / race:light (detail = index) / race:go      (start sequence from main.js)
//            motion:toggle {detail.paused} + <html data-motion="paused"> (WCAG 2.2.2 pause)
//   reads    window.__windFlow = { speed 0..1, gust 0..1 } (falls back to window.__speed km/h)
//   exposes  window.__carBounds = { x, y, w, h }  CSS px viewport rect of the projected car, or null
//            window.__carMask   = { canvas, x, y, w, h }  ~256 px silhouette (alpha) + its viewport rect, or null
//   adds class `ready` to canvas[data-car]; the canvas stays aria-hidden (decorative).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const hero = document.querySelector('[data-hero]');
const canvas = document.querySelector('[data-car]');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const small = () => innerWidth <= 760;
const BG = 0x040507;
const BLUE = new THREE.Color('#2b7bff');
const MASK_W = 256;
// Contact shadow baked in Blender (blender/build_car.py): plane size + centre in car space.
const SHADOW = { url: 'assets/car/shadow.png', w: 7.2, d: 3.2, x: 0.3 };

let paused = reduce || document.documentElement.dataset.motion === 'paused';

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
} catch (e) {
  canvas.remove();
  throw e;
}
renderer.setPixelRatio(Math.min(devicePixelRatio, small() ? 1.5 : 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(BG);
scene.fog = new THREE.Fog(BG, 12, 40);

// ---------- automotive studio environment, baked to a PMREM ----------
// Big overhead diffusion softbox, two long strips that draw highlight lines along the bodywork,
// a cool blue kicker, a dim floor bounce, everything else near-black (the cove).
function studioEnvironment() {
  const s = new THREE.Scene();
  s.add(new THREE.Mesh(new THREE.SphereGeometry(40, 32, 16), new THREE.MeshBasicMaterial({ color: 0x020203, side: THREE.BackSide })));
  const panel = (w, h, power, color, pos, look = [0, 0, 0]) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(power), side: THREE.DoubleSide }));
    m.position.set(...pos); m.lookAt(...look);
    s.add(m);
  };
  panel(16, 6, 2.2, 0xffffff, [0, 14, 0]);             // overhead diffusion softbox
  panel(24, 0.35, 16, 0xffffff, [0, 11, 4.5]);         // long strips along the car: crisp highlight lines
  panel(24, 0.35, 12, 0xffffff, [0, 11, -4.5]);
  panel(24, 0.25, 6, 0xf2f5ff, [0, 5, 12]);            // low side strip: shoulder line on the sidepods
  panel(2.5, 10, 5, BLUE, [-14, 4, -8]);              // cool blue kicker from behind
  panel(1.2, 8, 2.5, 0xdfe8ff, [14, 3, 6]);            // front fill column
  panel(40, 8, 0.05, 0xffffff, [0, -6, 0]);           // faint floor bounce
  const pm = new THREE.PMREMGenerator(renderer);
  const tex = pm.fromScene(s, 0.02).texture;
  pm.dispose();
  return tex;
}
scene.environment = studioEnvironment();
scene.environmentIntensity = 1.0;

// ---------- lights (physically based, decay 2) ----------
const key = new THREE.SpotLight(0xffffff, 0, 0, 0.5, 0.9, 2);
key.position.set(1.5, 9, 2);
key.castShadow = true;
key.shadow.mapSize.setScalar(small() ? 1024 : 2048);
key.shadow.bias = -0.0002;
key.shadow.normalBias = 0.02;
key.shadow.radius = 5;
key.shadow.camera.near = 3; key.shadow.camera.far = 20;
scene.add(key, key.target);

const rim = new THREE.SpotLight(BLUE, 30, 0, 0.6, 0.9, 2);   // blue kicker, real light
rim.position.set(-7, 2.4, -6);
scene.add(rim, rim.target);
scene.add(new THREE.HemisphereLight(0xc8d2e6, 0x020203, 0.12));

// ---------- seamless infinity cove + glossy reflective floor ----------
const cove = (() => {
  const pts = [];
  for (let i = 0; i <= 16; i++) { const a = (i / 16) * Math.PI / 2; pts.push(new THREE.Vector2(30 + 8 * Math.sin(a), 8 - 8 * Math.cos(a))); }
  pts.push(new THREE.Vector2(38, 30));
  const m = new THREE.Mesh(new THREE.LatheGeometry(pts, 96),
    new THREE.MeshStandardMaterial({ color: 0x07080b, roughness: 0.92, side: THREE.DoubleSide }));
  m.position.y = -0.001;
  return m;
})();
scene.add(cove);

// Floor: a glossy dark lacquer over a mirrored twin of the car. Fresnel drives how much reflection
// shows through (stronger at grazing angles); the twin fades with depth below the floor, which reads
// like the soft falloff of a real polished studio floor.
const floorMat = new THREE.MeshPhysicalMaterial({
  color: 0x050608, roughness: 0.42, metalness: 0, clearcoat: 0.7, clearcoatRoughness: 0.12, transparent: true, opacity: 0.9,
});
floorMat.onBeforeCompile = sh => {
  sh.fragmentShader = sh.fragmentShader.replace('#include <opaque_fragment>', `
    float fres = pow(1.0 - clamp(dot(geometryNormal, geometryViewDir), 0.0, 1.0), 3.0);
    diffuseColor.a = mix(0.88, 0.5, fres);
    #include <opaque_fragment>`);
};
floorMat.customProgramCacheKey = () => 'studio-floor';
const floor = new THREE.Mesh(new THREE.CircleGeometry(30.5, 96), floorMat);
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

// ---------- rolling road (wind-tunnel belt) ----------
const belt = (() => {
  const c = document.createElement('canvas'); c.width = 64; c.height = 512;
  const g = c.getContext('2d');
  g.fillStyle = '#0c0d10'; g.fillRect(0, 0, 64, 512);
  for (let i = 0; i < 1400; i++) {           // fine longitudinal grain of a steel belt
    const v = 18 + Math.random() * 16;
    g.fillStyle = `rgba(${v},${v + 2},${v + 6},0.5)`;
    g.fillRect(Math.random() * 64, Math.random() * 512, 1, 6 + Math.random() * 30);
  }
  g.fillStyle = 'rgba(120,140,170,0.25)';   // transverse seam marks so motion is readable
  g.fillRect(0, 0, 64, 2); g.fillRect(0, 256, 64, 1);
  const map = new THREE.CanvasTexture(c);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 4;
  const a = document.createElement('canvas'); a.width = 64; a.height = 64;      // soft edges
  const ag = a.getContext('2d'), gr = ag.createLinearGradient(0, 0, 64, 0);
  gr.addColorStop(0, '#000'); gr.addColorStop(0.08, '#fff'); gr.addColorStop(0.92, '#fff'); gr.addColorStop(1, '#000');
  ag.fillStyle = gr; ag.fillRect(0, 0, 64, 64);
  const mat = new THREE.MeshStandardMaterial({
    map, alphaMap: new THREE.CanvasTexture(a), transparent: true, opacity: 0.3, roughness: 0.6, metalness: 0.3,
    depthWrite: false,
  });
  // U runs across the belt, V along the car (x). Rotate the texture so grain runs along x.
  map.rotation = Math.PI / 2; map.center.set(0.5, 0.5); map.repeat.set(3, 1);
  const m = new THREE.Mesh(new THREE.PlaneGeometry(8, 2.3), mat);
  m.rotation.x = -Math.PI / 2; m.position.set(SHADOW.x, 0.0015, 0);
  m.receiveShadow = true;
  m.renderOrder = 1;
  return m;
})();

// ---------- camera + post ----------
const camera = new THREE.PerspectiveCamera(26, 1, 0.1, 120);
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.12, 0.35, 0.98);   // only true highlights
composer.addPass(bloom);
composer.addPass(new OutputPass());

// ---------- car rig ----------
const rig = new THREE.Group();        // drives along X and yaws
const body = new THREE.Group();       // whole car (wheels + chassis)
const mirror = new THREE.Group();     // reflected twin
mirror.scale.y = -1;
rig.add(body, mirror, belt);
rig.rotation.y = Math.PI;             // nose toward -X so the car arrives from screen right
scene.add(rig);

new THREE.TextureLoader().load(SHADOW.url, tex => {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(SHADOW.w, SHADOW.d),
    new THREE.MeshBasicMaterial({ color: 0x000000, alphaMap: tex, transparent: true, depthWrite: false, opacity: 0.95 }));
  m.rotation.x = -Math.PI / 2; m.position.set(SHADOW.x, 0.003, 0);
  m.renderOrder = 2;
  rig.add(m);
});

let car = null, chassis = null, wheels = [], twin = null, tails = [], loaded = false;
const samplePts = [];                  // car-space points used for screen bounds

function fadeForMirror(mat) {
  const m = mat.clone();
  m.onBeforeCompile = sh => {
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying float vMirY;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvMirY = (modelMatrix * vec4(transformed, 1.0)).y;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vMirY;')
      .replace('#include <dithering_fragment>', 'gl_FragColor.rgb *= 0.75 * exp(min(vMirY, 0.0) * 3.2);\n#include <dithering_fragment>');
  };
  m.customProgramCacheKey = () => 'mirror-' + mat.uuid;
  return m;
}

// Split the car root into rotating wheels and a sprung chassis (so heave/pitch never lift the tyres).
function rigCar(root) {
  const sprung = new THREE.Group();
  const ws = [];
  [...root.children].forEach(o => { if (o.name.startsWith('Wheel_')) ws.push(o); else sprung.add(o); });
  root.add(sprung);
  return { sprung, ws };
}

const draco = new DRACOLoader().setDecoderPath('https://www.gstatic.com/draco/versioned/decoders/1.5.7/');
new GLTFLoader().setDRACOLoader(draco).load('assets/car.glb', gltf => {
  car = gltf.scene;
  const root = car.getObjectByName('Car') || car;
  car.traverse(o => {
    if (!o.isMesh) return;
    o.castShadow = true;
    o.receiveShadow = true;
    o.layers.enable(2);                          // silhouette pass layer
    const m = o.material;
    if (m.name === 'RainLight') tails.push(m);
    if (m.name.startsWith('Paint')) { m.envMapIntensity = 1.15; m.clearcoatRoughness = 0.03; }
    if (m.name.startsWith('Decal')) {            // sit on the body without z-fighting
      m.polygonOffset = true; m.polygonOffsetFactor = -2; m.polygonOffsetUnits = -2;
      o.castShadow = false;
    }
    if (m.name.startsWith('Titanium') || m.name.startsWith('Metal') || m.name.startsWith('Exhaust')) m.envMapIntensity = 1.3;
  });
  ({ sprung: chassis, ws: wheels } = rigCar(root));
  body.add(car);

  // Mirrored twin: same geometry, depth-faded materials, no shadows, not in the silhouette pass.
  twin = car.clone(true);
  const matCache = new Map();
  twin.traverse(o => {
    if (!o.isMesh) return;
    o.castShadow = false; o.receiveShadow = false;
    o.layers.set(0);
    if (!matCache.has(o.material)) matCache.set(o.material, fadeForMirror(o.material));
    o.material = matCache.get(o.material);
  });
  const troot = twin.getObjectByName('Car') || twin;
  twin.userData.parts = { sprung: troot.children.find(c => c.isGroup && !c.name), ws: troot.children.filter(c => c.name.startsWith('Wheel_')) };
  mirror.add(twin);

  // Sample points for the projected bounds (car space).
  body.updateMatrixWorld(true);
  const inv = new THREE.Matrix4().copy(body.matrixWorld).invert();
  const v = new THREE.Vector3(), mtx = new THREE.Matrix4();
  car.traverse(o => {
    if (!o.isMesh || o.material.name.startsWith('Decal')) return;
    const pos = o.geometry.attributes.position;
    mtx.multiplyMatrices(inv, o.matrixWorld);
    const step = Math.max(1, Math.floor(pos.count / 40));
    for (let i = 0; i < pos.count; i += step) samplePts.push(v.fromBufferAttribute(pos, i).applyMatrix4(mtx).clone());
  });

  loaded = true;
  canvas.classList.add('ready');
  if (paused || state.phase === 'parked') park();
}, undefined, () => { canvas.remove(); window.__carBounds = null; window.__carMask = null; });

// ---------- race staging ----------
const DRIVE_IN = 1300, START_X = 17, KEY_FULL = 110;
const state = { phase: 'waiting', t0: 0, x: START_X, v: 0, lights: 0 };
if (document.documentElement.dataset.race === 'go') state.phase = 'parked';
const ptr = { x: 0.5, y: 0.5, active: false };
let yaw = 0, envYaw = 0, pitch = 0, gustKick = 0, beltOffset = 0, wheelSpin = 0, idleClock = 0;

function park() { state.phase = 'parked'; state.x = 0; state.v = 0; key.intensity = KEY_FULL; }
if (paused || state.phase === 'parked') park();

addEventListener('race:reset', () => {
  if (paused) return park();
  state.phase = 'waiting'; state.x = START_X; state.v = 0; state.lights = 0;
});
addEventListener('race:light', e => { state.lights = e.detail + 1; });
addEventListener('race:go', () => {
  if (paused) return park();
  state.phase = 'driving'; state.t0 = performance.now();
  gustKick = 1;
});
addEventListener('motion:toggle', e => {
  paused = reduce || !!(e.detail && e.detail.paused);
  if (paused && state.phase !== 'parked') park();
});
addEventListener('pointermove', e => {
  ptr.x = e.clientX / innerWidth; ptr.y = e.clientY / innerHeight; ptr.active = true;
}, { passive: true });

// ---------- framing: car sits above the name, bigger and centred on phones ----------
const target = new THREE.Vector3(0, 0.5, 0);
const view = { w: 1, h: 1, ox: 0, oy: 0 };
function frame() {
  const w = hero.clientWidth, h = hero.clientHeight;
  renderer.setPixelRatio(Math.min(devicePixelRatio, small() ? 1.5 : 2));
  renderer.setSize(w, h, false);
  composer.setSize(w, h);
  bloom.resolution.set(w, h);
  const aspect = w / h;
  camera.aspect = aspect;
  const tanH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const fill = small() ? 0.86 : aspect > 1.6 ? 0.56 : 0.72;   // share of width the car should span
  const dW = 5.6 / (fill * 2 * tanH * aspect);
  const dH = 2.1 / ((small() ? 0.36 : 0.5) * 2 * tanH);
  frame.dist = Math.max(dW, dH);
  scene.fog.near = frame.dist + 3; scene.fog.far = frame.dist + 30;
  const cx = small() ? 0.5 : 0.6, cy = small() ? 0.36 : 0.4;
  view.w = w; view.h = h; view.ox = w * (0.5 - cx); view.oy = h * (0.5 - cy);
  camera.setViewOffset(w, h, view.ox, view.oy, w, h);
  camera.updateProjectionMatrix();
}
new ResizeObserver(frame).observe(hero);
frame();

// ---------- screen-space bounds + silhouette mask ----------
const tmpV = new THREE.Vector3();
function projectBounds() {
  const r = canvas.getBoundingClientRect();
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const m = body.matrixWorld;
  for (const p of samplePts) {
    tmpV.copy(p).applyMatrix4(m).project(camera);
    if (tmpV.z > 1) continue;
    const sx = (tmpV.x * 0.5 + 0.5) * r.width, sy = (-tmpV.y * 0.5 + 0.5) * r.height;
    if (sx < x0) x0 = sx; if (sx > x1) x1 = sx; if (sy < y0) y0 = sy; if (sy > y1) y1 = sy;
  }
  if (!(x1 > x0 && y1 > y0)) return null;
  return { x: r.left + x0, y: r.top + y0, w: x1 - x0, h: y1 - y0, cx: x0, cy: y0 };
}

const maskMat = new THREE.MeshBasicMaterial({ color: 0xffffff, fog: false });
const maskRT = new THREE.WebGLRenderTarget(MASK_W, 128, { depthBuffer: true });
const maskCanvas = document.createElement('canvas');
const maskCtx = maskCanvas.getContext('2d');
let maskBuf = new Uint8Array(0), maskFrame = 0, lastMaskKey = '';
const clearColor = new THREE.Color();

function renderMask(b) {
  const mh = Math.max(8, Math.min(512, Math.round(MASK_W * b.h / b.w)));
  if (maskRT.height !== mh) maskRT.setSize(MASK_W, mh);
  if (maskCanvas.width !== MASK_W || maskCanvas.height !== mh) { maskCanvas.width = MASK_W; maskCanvas.height = mh; }
  // crop the camera to the car's rect inside the full (offset) view
  camera.setViewOffset(view.w, view.h, view.ox + b.cx, view.oy + b.cy, b.w, b.h);
  camera.updateProjectionMatrix();
  camera.layers.set(2);
  const bg = scene.background, fog = scene.fog;
  scene.background = null; scene.fog = null; scene.overrideMaterial = maskMat;
  renderer.getClearColor(clearColor); const ca = renderer.getClearAlpha();
  renderer.setClearColor(0x000000, 0);
  const su = renderer.shadowMap.autoUpdate; renderer.shadowMap.autoUpdate = false;
  renderer.setRenderTarget(maskRT);
  renderer.clear();
  renderer.render(scene, camera);
  renderer.shadowMap.autoUpdate = su;
  renderer.setRenderTarget(null);
  renderer.setClearColor(clearColor, ca);
  scene.background = bg; scene.fog = fog; scene.overrideMaterial = null;
  camera.layers.set(0);
  camera.setViewOffset(view.w, view.h, view.ox, view.oy, view.w, view.h);
  camera.updateProjectionMatrix();

  const n = MASK_W * mh * 4;
  if (maskBuf.length !== n) maskBuf = new Uint8Array(n);
  renderer.readRenderTargetPixels(maskRT, 0, 0, MASK_W, mh, maskBuf);
  const img = maskCtx.createImageData(MASK_W, mh), d = img.data;
  for (let y = 0; y < mh; y++) {                     // GL rows are bottom-up
    const src = (mh - 1 - y) * MASK_W * 4, dst = y * MASK_W * 4;
    for (let x = 0; x < MASK_W * 4; x += 4) {
      d[dst + x] = d[dst + x + 1] = d[dst + x + 2] = 255;
      d[dst + x + 3] = maskBuf[src + x];
    }
  }
  maskCtx.putImageData(img, 0, 0);
  window.__carMask = { canvas: maskCanvas, x: b.x, y: b.y, w: b.w, h: b.h };
}

// ---------- wind input ----------
function windFlow() {
  const f = window.__windFlow;
  if (f && typeof f.speed === 'number') return { speed: THREE.MathUtils.clamp(f.speed, 0, 1), gust: THREE.MathUtils.clamp(+f.gust || 0, 0, 1) };
  return { speed: THREE.MathUtils.clamp((+window.__speed || 0) / 320, 0, 1), gust: 0 };
}

// ---------- loop ----------
let visible = true, last = performance.now();
new IntersectionObserver(([e]) => {
  visible = e.isIntersecting;
  if (!visible) { window.__carBounds = null; window.__carMask = null; }
}).observe(hero);
window.__carBounds = null;
window.__carMask = null;

function tick(now) {
  requestAnimationFrame(tick);
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  if (!visible || !loaded) { window.__carBounds = null; window.__carMask = null; return; }
  if (!paused) idleClock += dt;

  // Drive-in: hard deceleration from off-screen, nose dives under braking.
  let decel = 0;
  if (state.phase === 'driving') {
    const p = Math.min(1, (now - state.t0) / DRIVE_IN);
    const e = 1 - Math.pow(1 - p, 3.2);
    const x = START_X * (1 - e);
    const v = (state.x - x) / Math.max(dt, 1e-3);
    decel = Math.max(0, state.v - v) / Math.max(dt, 1e-3);
    state.x = x; state.v = v;
    if (p >= 1) { state.phase = 'parked'; state.v = 0; }
  }
  rig.position.x = state.x;
  rig.visible = state.phase !== 'waiting';

  // Wind tunnel: rolling road + wheels at belt speed, aero squat, vibration, gusts.
  const flow = paused ? { speed: 0, gust: 0 } : windFlow();
  const beltV = flow.speed * 22;                               // display m/s
  beltOffset = (beltOffset + (paused ? 0 : beltV * dt / 8)) % 1;
  belt.material.map.offset.y = -beltOffset * 3;
  belt.material.opacity = 0.22 + 0.3 * flow.speed;
  wheelSpin -= paused ? 0 : Math.min(45, (state.v + beltV) / 0.36) * dt;
  wheels.forEach(w => { w.rotation.z = wheelSpin; });

  gustKick = paused ? 0 : gustKick * Math.exp(-dt / 0.5);
  const shakeAmp = paused ? 0 : 0.0007 * flow.speed * flow.speed + 0.004 * gustKick + 0.0015 * flow.gust;
  const t = idleClock;
  const shake = shakeAmp * (Math.sin(t * 97) * 0.6 + Math.sin(t * 151 + 1.3) * 0.4);
  const brake = Math.min(0.02, decel * 0.0005);
  pitch += (brake + 0.004 * flow.speed * flow.speed - pitch) * 0.2;       // nose-down under braking/downforce
  if (chassis) {
    chassis.rotation.x = shake * 2.5;                                          // roll jitter
    chassis.rotation.z = -pitch + shake;                                       // pitch (nose is +x)
    chassis.position.y = -0.008 * flow.speed * flow.speed - pitch * 0.4 + shake;
  }
  // mirror the twin
  if (twin) {
    const tp = twin.userData.parts;
    if (tp.sprung && chassis) { tp.sprung.position.copy(chassis.position); tp.sprung.rotation.copy(chassis.rotation); }
    tp.ws.forEach(w => { w.rotation.z = wheelSpin; });
  }

  // Studio lights ramp with each start light, then full at lights out.
  const keyTarget = state.phase === 'waiting' ? 8 + state.lights * 18 : KEY_FULL;
  key.intensity += (keyTarget - key.intensity) * 0.08;
  rim.intensity = 26 + (state.phase === 'waiting' ? state.lights * 6 : 6) + (paused ? 0 : Math.sin(now / 900) * 4);
  const tailOn = paused || state.phase === 'driving' || Math.floor(now / 250) % 2;
  tails.forEach(m => { m.emissiveIntensity = tailOn ? 0.9 : 0.2; });

  // Pointer + scroll steer the reflections and a gentle turntable (autonomous drift stops when paused).
  const scroll = Math.min(1, scrollY / Math.max(1, hero.offsetHeight));
  const px = ptr.active ? ptr.x - 0.5 : (paused ? 0 : Math.sin(t / 3.2) * 0.35);
  const py = ptr.active ? ptr.y - 0.5 : 0;
  yaw += (px * 0.5 + scroll * 0.9 - yaw) * 0.05;
  envYaw += (px * 1.8 + (paused ? 0 : t / 14) + scroll * 1.5 - envYaw) * 0.06;
  rig.rotation.y = Math.PI + yaw;
  scene.environmentRotation.y = envYaw;
  key.position.set(1.5 + px * 6, 9, 2 + py * 3);
  key.target.position.set(state.x, 0, 0);
  rim.target.position.set(state.x, 0.4, 0);

  const d = frame.dist;
  const az = THREE.MathUtils.degToRad(small() ? 35 : 48) + scroll * 0.25;
  const el = THREE.MathUtils.degToRad(12 + scroll * 14 - py * 4);
  camera.position.set(-Math.sin(az) * Math.cos(el) * d, target.y + Math.sin(el) * d, Math.cos(az) * Math.cos(el) * d);
  camera.lookAt(target);

  composer.render();

  // Screen-space contracts for the wind-tunnel smoke layer.
  rig.updateMatrixWorld(true);
  const b = rig.visible ? projectBounds() : null;
  window.__carBounds = b ? { x: b.x, y: b.y, w: b.w, h: b.h } : null;
  if (!b) { window.__carMask = null; return; }
  const keyStr = `${Math.round(b.x)},${Math.round(b.y)},${Math.round(b.w)},${Math.round(b.h)}`;
  if (++maskFrame % 3 === 0 && (keyStr !== lastMaskKey || !window.__carMask || state.phase === 'driving')) {
    lastMaskKey = keyStr;
    renderMask(b);
  } else if (window.__carMask) {
    Object.assign(window.__carMask, { x: b.x, y: b.y, w: b.w, h: b.h });
  }
}
requestAnimationFrame(tick);
