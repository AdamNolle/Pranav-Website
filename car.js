// Team PK studio. One WebGL context, no bloom, mirror scene, live shadow map, or idle GPU work.
// Blender owns the black-and-blue geometry/livery; this module supplies neutral studio reflections.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const hero = document.querySelector('[data-hero]');
const canvas = document.querySelector('[data-car]');
const motion = matchMedia('(prefers-reduced-motion: reduce)');
const phone = matchMedia('(max-width: 760px), (pointer: coarse)').matches;
const fine = matchMedia('(hover: hover) and (pointer: fine)').matches;
const debug = new URLSearchParams(location.search);
const paused = () => motion.matches || document.documentElement.dataset.motion === 'paused';
let cinematic = document.documentElement.dataset.effects === 'full';
let visible = true, loaded = false, lost = false, raf = 0, until = 0, previous = 0, lastDraw = 0;
let view = 'launch', azimuth = 48, elevation = 16, yaw = 0, pointerX = 0, pointerY = 0;
let orbitAzimuth = phone ? 40 : 48, orbitElevation = 16, drag = null;
let width = 1, height = 1, distance = 10, phase = 'parked', driveStart = 0, lights = 0;
let dpr = Math.min(devicePixelRatio || 1, phone ? 1.25 : 1.5);
let slowFrames = 0;
const wake = (duration = 1100) => {
  until = Math.max(until, performance.now() + duration);
  if (!raf && !document.hidden && visible && !lost) raf = requestAnimationFrame(tick);
};
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'default' });
} catch (error) {
  dispatchEvent(new Event('car:unavailable'));
  dispatchEvent(new Event('car:ready'));
  throw error;
}
renderer.setPixelRatio(dpr);
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.toneMappingExposure = 1.18;
renderer.setClearColor(0x080d16, 0);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(26, 1, .1, 100);
const rig = new THREE.Group();
rig.rotation.y = Math.PI;
scene.add(rig);
const target = new THREE.Vector3(0, .45, 0);
scene.add(new THREE.HemisphereLight(0xf0ede6, 0x202b3a, 1.25));

// Bake a small studio into one environment map. Broad boxes describe the bodywork while the
// narrow strips give the clearcoat a readable reflection; neither adds a per-frame light pass.
function studioEnvironment() {
  const studio = new THREE.Scene();
  studio.background = new THREE.Color(0x171d26);
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const ctx = c.getContext('2d');
  const feather = ctx.createRadialGradient(32, 32, 6, 32, 32, 32);
  feather.addColorStop(0, '#fff'); feather.addColorStop(.65, '#fff'); feather.addColorStop(1, '#000');
  ctx.fillStyle = feather; ctx.fillRect(0, 0, 64, 64);
  const softbox = new THREE.CanvasTexture(c); softbox.colorSpace = THREE.SRGBColorSpace;
  function panel(w, h, intensity, color, position, soft = false) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({
      color: new THREE.Color(color).multiplyScalar(intensity), side: THREE.DoubleSide,
      map: soft ? softbox : null
    }));
    mesh.position.set(...position); mesh.lookAt(0, 0, 0); studio.add(mesh);
  }
  panel(20, 9, 3.2, 0xfff1df, [0, 10, 2], true);   // overhead diffusion
  panel(22, 9, 2.7, 0xf0f4ff, [0, 5, 13], true);   // broad key
  panel(20, 8, 1.6, 0xd5e3ff, [0, 4, -12], true);  // cooler fill
  panel(17, .65, 7, 0xffffff, [0, 7, 5]);         // longitudinal strip reflections
  panel(16, .55, 4.5, 0xdbeaff, [0, 7, -5]);
  panel(6, 10, 1.4, 0x3477df, [-11, 4, -4], true); // restrained blue edge light
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromScene(studio, .04, .1, 100, { size: 128 });
  scene.environment = env.texture;
  scene.environmentIntensity = 1.1;
  pmrem.dispose();
  studio.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
  softbox.dispose();
}

// The Cycles AO map makes the tyre contact and underfloor shadow follow every camera angle.
// A low-opacity matte pool gives the black shadow something to fall on without a floor scene.
const poolCanvas = document.createElement('canvas'); poolCanvas.width = poolCanvas.height = 64;
const poolContext = poolCanvas.getContext('2d');
const poolGradient = poolContext.createRadialGradient(32, 32, 7, 32, 32, 32);
poolGradient.addColorStop(0, 'rgba(36,49,68,0.38)');
poolGradient.addColorStop(.6, 'rgba(25,34,49,0.22)');
poolGradient.addColorStop(1, 'rgba(17,25,38,0)');
poolContext.fillStyle = poolGradient; poolContext.fillRect(0, 0, 64, 64);
const poolTexture = new THREE.CanvasTexture(poolCanvas); poolTexture.colorSpace = THREE.SRGBColorSpace;
const floorPool = new THREE.Mesh(new THREE.PlaneGeometry(9, 5), new THREE.MeshBasicMaterial({
  map: poolTexture, transparent: true, depthWrite: false, toneMapped: false
}));
floorPool.rotation.x = -Math.PI / 2; floorPool.position.set(.3, .001, 0); rig.add(floorPool);
new THREE.TextureLoader().load('assets/car/shadow.webp', texture => {
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(7.2, 3.2), new THREE.MeshBasicMaterial({
    color: 0x000000, alphaMap: texture, transparent: true, opacity: .72, depthWrite: false
  }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.set(.3, .003, 0); rig.add(shadow); wake();
});

// Merge stationary parts, but keep each exported wheel hub as a pivot. Flattening the entire
// GLB into one group removed the only way to roll the tyres during the drive-in.
function batch(model) {
  model.updateMatrixWorld(true);
  const wheelNames = new Set(['Wheel_FL', 'Wheel_FR', 'Wheel_RL', 'Wheel_RR']);
  const wheels = new Map(), staticBuckets = new Map(), originals = new Set();
  const combined = new THREE.Group();
  model.traverse(o => {
    if (!wheelNames.has(o.name)) return;
    const pivot = new THREE.Group();
    pivot.name = o.name;
    o.matrixWorld.decompose(pivot.position, pivot.quaternion, pivot.scale);
    pivot.userData.restRotation = pivot.quaternion.clone();
    combined.add(pivot);
    wheels.set(o, { pivot, inverse: o.matrixWorld.clone().invert(), buckets: new Map() });
  });
  const transform = model.matrixWorld.clone();
  model.traverse(o => {
    if (!o.isMesh) return;
    let wheel = null;
    for (let parent = o.parent; parent; parent = parent.parent) {
      if (wheels.has(parent)) { wheel = wheels.get(parent); break; }
    }
    const geometry = o.geometry.clone();
    geometry.applyMatrix4(wheel ? transform.multiplyMatrices(wheel.inverse, o.matrixWorld) : o.matrixWorld);
    const key = `${o.material.uuid}:${Object.keys(geometry.attributes).sort().join(',')}:${!!geometry.index}`;
    const buckets = wheel ? wheel.buckets : staticBuckets;
    if (!buckets.has(key)) buckets.set(key, { material: o.material, geometries: [] });
    buckets.get(key).geometries.push(geometry);
    originals.add(o.geometry);
  });
  function appendBuckets(parent, buckets) {
    for (const { material, geometries } of buckets.values()) {
      const geometry = geometries.length === 1 ? geometries[0] : mergeGeometries(geometries, false);
      if (geometry) {
        parent.add(new THREE.Mesh(geometry, material));
        if (geometries.length > 1) geometries.forEach(g => g.dispose());
      } else { // Preserve geometry on an unusual exporter layout rather than silently dropping it.
        geometries.forEach(g => parent.add(new THREE.Mesh(g, material)));
      }
    }
  }
  appendBuckets(combined, staticBuckets);
  for (const wheel of wheels.values()) appendBuckets(wheel.pivot, wheel.buckets);
  originals.forEach(g => g.dispose());
  combined.userData.wheels = [...wheels.values()].map(wheel => wheel.pivot);
  return combined;
}
const yieldToMain = () => new Promise(resolve => setTimeout(resolve, 0));
const draco = new DRACOLoader().setDecoderPath('vendor/draco/').setWorkerLimit(2);
const loader = new GLTFLoader().setDRACOLoader(draco);
let body, carBox, boundPoints = [], wheels = [], wheelAngle = 0;
const wheelRadius = .36; // The exported 720 mm slicks, in metres.
const axle = new THREE.Vector3(0, 0, 1), rollRotation = rig.quaternion.clone();
function setWheelAngle(angle) {
  wheelAngle = angle;
  rollRotation.setFromAxisAngle(axle, angle);
  for (const wheel of wheels) wheel.quaternion.copy(wheel.userData.restRotation).multiply(rollRotation);
}
const vector = new THREE.Vector3();
async function loadCar() {
  try {
    // Fetch the Draco decoder alongside the model instead of after the GLB transfer.
    draco.preload();
    const gltf = await loader.loadAsync(phone ? 'assets/car_mobile.glb' : 'assets/car.glb');
    await yieldToMain();
    studioEnvironment();
    await yieldToMain();
    gltf.scene.traverse(o => {
      if (!o.isMesh) return;
      const material = o.material;
      if (material.name.startsWith('Paint')) {
        material.envMapIntensity = 1.05;
        // The exported livery keeps its roughness map. Soften only the nearly perfect
        // clearcoat mirror, which otherwise looks like chrome on a painted race car.
        material.clearcoatRoughness = Math.max(.08, material.clearcoatRoughness || 0);
      }
      if (material.name.startsWith('Decal')) { material.polygonOffset = true; material.polygonOffsetFactor = -2; material.polygonOffsetUnits = -2; }
      for (const k in material) if (material[k]?.isTexture) material[k].anisotropy = phone ? 2 : 4;
    });
    body = batch(gltf.scene);
    wheels = body.userData.wheels;
    body.traverse(o => { if (o.isMesh) o.layers.enable(2); });
    carBox = new THREE.Box3().setFromObject(body); // local bounds, before attaching to the rotated rig
    rig.add(body);
    for (const x of [carBox.min.x, carBox.max.x]) for (const y of [carBox.min.y, carBox.max.y]) for (const z of [carBox.min.z, carBox.max.z]) boundPoints.push(new THREE.Vector3(x, y, z));
    await yieldToMain();
    resize(); updateCamera(true);
    if (renderer.getContext().isContextLost()) throw new Error('Car context unavailable during loading');
    await renderer.compileAsync(scene, camera);
    const textures = new Set();
    body.traverse(o => { if (o.material) for (const k in o.material) if (o.material[k]?.isTexture) textures.add(o.material[k]); });
    for (const texture of textures) { renderer.initTexture(texture); await yieldToMain(); }
    // Present a real frame before revealing the canvas and dismissing the fallback poster.
    phase = 'parked'; rig.position.x = 0;
    if (renderer.getContext().isContextLost()) throw new Error('Car context unavailable before first frame');
    renderer.render(scene, camera);
    loaded = true; lost = false; canvas.dataset.state = 'parked'; canvas.classList.add('ready');
    dispatchEvent(new Event('car:ready')); wake(300);
  } catch (error) {
    console.warn('[car] fallback poster retained:', error);
    canvas.classList.remove('ready'); renderer.dispose(); lost = true;
    dispatchEvent(new Event('car:unavailable')); dispatchEvent(new Event('car:ready'));
  } finally { draco.dispose(); }
}

function resize() {
  width = hero.clientWidth; height = hero.clientHeight;
  const small = width <= 760;
  renderer.setPixelRatio(dpr); renderer.setSize(width, height, false);
  camera.aspect = width / height; camera.fov = small ? 30 : 26;
  camera.zoom = small ? 1.10 : 1.25;
  const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const span = 5.9; // enough room for every drag angle, including the full side profile
  const fill = small ? .84 : camera.aspect > 1.6 ? .60 : .68;
  distance = Math.max(span / (fill * 2 * tan * camera.aspect), (view === 'front' ? 3.2 : 2.1) / (2 * tan * .48));
  camera.setViewOffset(width, height, width * (small ? 0 : -.10), height * (small ? -.045 : .12), width, height);
  camera.updateProjectionMatrix(); wake();
}
function updateCamera(immediate = false) {
  const a = view === 'orbit' ? orbitAzimuth : width <= 760 ? 40 : 48;
  const e = view === 'orbit' ? orbitElevation : 16;
  const ease = immediate || paused() ? 1 : .13;
  azimuth += (a - azimuth) * ease; elevation += (e - elevation) * ease;
  const az = THREE.MathUtils.degToRad(azimuth), el = THREE.MathUtils.degToRad(elevation - pointerY * 2);
  camera.position.set(-Math.sin(az) * Math.cos(el) * distance, target.y + Math.sin(el) * distance, Math.cos(az) * Math.cos(el) * distance);
  camera.lookAt(target);
}
function projectBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  rig.updateMatrixWorld(true);
  for (const p of boundPoints) {
    vector.copy(p).applyMatrix4(rig.matrixWorld).project(camera);
    const x = (vector.x * .5 + .5) * width, y = (-vector.y * .5 + .5) * height - scrollY;
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  const bounds = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  window.__carBounds = bounds;
  if (window.__carMask) Object.assign(window.__carMask, bounds);
  return bounds;
}
// Silhouette only updates when the car moves. The fluid retains its original shape-aware flow,
// without a second car draw + GPU readback on every idle frame.
const maskTarget = new THREE.WebGLRenderTarget(192, 96, { depthBuffer: true });
const maskMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
const maskCanvas = document.createElement('canvas'), maskContext = maskCanvas.getContext('2d');
let maskBusy = false, lastMask = -Infinity;
async function publishMask(b) {
  if (maskBusy || !b || b.w < 2 || b.h < 2) return;
  maskBusy = true;
  const w = 192, h = Math.max(8, Math.min(192, Math.round(w * b.h / b.w)));
  maskTarget.setSize(w, h);
  const offset = { ...camera.view };
  camera.setViewOffset(width, height, offset.offsetX + b.x, offset.offsetY + b.y + scrollY, b.w, b.h);
  camera.layers.set(2); scene.overrideMaterial = maskMaterial;
  renderer.setRenderTarget(maskTarget); renderer.setClearColor(0x000000, 0); renderer.render(scene, camera);
  renderer.setRenderTarget(null); renderer.setClearColor(0x080d16, 0); scene.overrideMaterial = null; camera.layers.set(0);
  camera.setViewOffset(width, height, offset.offsetX, offset.offsetY, width, height);
  try {
    const pixels = new Uint8Array(w * h * 4);
    await renderer.readRenderTargetPixelsAsync(maskTarget, 0, 0, w, h, pixels);
    maskCanvas.width = w; maskCanvas.height = h;
    const image = maskContext.createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const dst = (y * w + x) * 4, src = ((h - 1 - y) * w + x) * 4;
      image.data[dst] = image.data[dst + 1] = image.data[dst + 2] = 255;
      image.data[dst + 3] = pixels[src];
    }
    maskContext.putImageData(image, 0, 0);
    let tip = null;
    findTip: for (let y = 0; y < h; y++) for (let x = w - 1; x > w * .65; x--) {
      if (image.data[(y * w + x) * 4 + 3] > 128) { tip = { u: x / w, v: y / h }; break findTip; }
    }
    if (visible && !lost) window.__carMask = { canvas: maskCanvas, ...(window.__carBounds || b), tip };
  } catch { /* Bounding rectangle is a graceful fallback for older drivers. */ }
  finally { maskBusy = false; }
}
function tick(now) {
  raf = 0;
  if (document.hidden || !visible || lost || !loaded) return;
  const dt = now - (previous || now - 16); previous = now;
  const continuous = false; // Smoke moves continuously; a stationary car does not need to redraw.
  if (now < until || phase === 'driving' || continuous) raf = requestAnimationFrame(tick);
  // 60 Hz is plenty for the subtle turntable, even on a 120/144 Hz screen.
  if (now - lastDraw < 15) return;
  lastDraw = now;
  if (phase === 'driving') {
    const p = paused() ? 1 : Math.min(1, (now - driveStart) / 1100);
    rig.position.x = 15 * Math.pow(1 - p, 3);
    // The rig's half-turn makes decreasing world X equal forward travel along the car's +X.
    // glTF's Y-up export puts the Blender wheel axles on local Z. Distance / radius gives
    // the actual rolling angle, including the easing deceleration and exact parked stop.
    setWheelAngle(-(15 - rig.position.x) / wheelRadius);
    if (p === 1) { phase = 'parked'; canvas.dataset.state = 'parked'; dispatchEvent(new Event('car:settled')); }
  }
  const aimYaw = !paused() && view === 'launch' ? pointerX * .16 : 0;
  yaw += (aimYaw - yaw) * .15;
  rig.rotation.y = Math.PI + yaw;
  scene.environmentRotation.y = yaw * .6;
  updateCamera();
  renderer.render(scene, camera);
  if (cinematic) {
    const bounds = projectBounds();
    if (now - lastMask > 240) { lastMask = now; publishMask(bounds); }
  } else window.__carBounds = null;
  // Adapt down only during sustained activity, never mistake an idle interval for a slow frame.
  if (dt < 100 && dt > 25) slowFrames++; else slowFrames = Math.max(0, slowFrames - 1);
  if (slowFrames > 50 && dpr > 1) { dpr = Math.max(1, dpr - .25); slowFrames = 0; resize(); }
}
new ResizeObserver(resize).observe(hero);
new IntersectionObserver(([entry]) => {
  visible = entry.isIntersecting;
  if (!visible) { cancelAnimationFrame(raf); raf = 0; window.__carBounds = null; window.__carMask = null; }
  else wake();
}).observe(hero);
// Direct manipulation with no view buttons. Vertical touch scrolling stays native (pan-y).
canvas.addEventListener('pointerdown', e => {
  if (!loaded || !e.isPrimary || e.button !== 0) return;
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, az: azimuth, el: elevation, touch: e.pointerType === 'touch' };
  if (!drag.touch) { canvas.setPointerCapture(e.pointerId); canvas.classList.add('dragging'); }
});
canvas.addEventListener('pointermove', e => {
  if (!drag || e.pointerId !== drag.id) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (drag.touch && !canvas.hasPointerCapture(e.pointerId)) {
    if (Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 7) { drag = null; return; }
    if (Math.abs(dx) < 7) return;
    canvas.setPointerCapture(e.pointerId); canvas.classList.add('dragging');
  }
  view = 'orbit';
  // A finger has less travel than a mouse. One near-edge-to-edge phone drag can turn the
  // car through a full revolution, while shorter drags still allow precise inspection.
  const degreesPerPixel = drag.touch ? 360 / Math.max(240, width - 70) : .32;
  orbitAzimuth = drag.az - dx * degreesPerPixel;
  orbitElevation = Math.max(5, Math.min(36, drag.el + (drag.touch ? 0 : dy * .12)));
  wake();
});
function endDrag() { drag = null; canvas.classList.remove('dragging'); }
canvas.addEventListener('pointerup', endDrag); canvas.addEventListener('pointercancel', endDrag); canvas.addEventListener('lostpointercapture', endDrag);
canvas.addEventListener('keydown', e => {
  if (!loaded || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(e.key)) return;
  e.preventDefault(); view = 'orbit';
  if (e.key === 'Home') { orbitAzimuth = width <= 760 ? 40 : 48; orbitElevation = 16; }
  else if (e.key === 'ArrowLeft') orbitAzimuth -= 8;
  else if (e.key === 'ArrowRight') orbitAzimuth += 8;
  else orbitElevation = Math.max(5, Math.min(36, orbitElevation + (e.key === 'ArrowUp' ? 3 : -3)));
  wake();
});
addEventListener('race:reset', () => { if (!loaded || paused()) return; phase = 'waiting'; canvas.dataset.state = 'waiting'; rig.position.x = 15; setWheelAngle(0); lights = 0; wake(2000); });
addEventListener('race:light', e => { lights = e.detail + 1; wake(); });
addEventListener('race:go', () => {
  if (!loaded || phase !== 'waiting') return;
  if (paused()) { phase = 'parked'; rig.position.x = 0; } else { phase = 'driving'; driveStart = performance.now(); }
  canvas.dataset.state = phase;
  if (phase === 'parked') dispatchEvent(new Event('car:settled'));
  wake(1600);
});
addEventListener('motion:toggle', () => { pointerX = pointerY = 0; if (paused()) { phase = 'parked'; rig.position.x = 0; canvas.dataset.state = phase; dispatchEvent(new Event('car:settled')); } wake(200); });
motion.addEventListener('change', () => wake(200));
addEventListener('effects:toggle', e => { cinematic = !!e.detail.enabled; wake(); });
addEventListener('scroll', () => { if (cinematic && loaded && visible) projectBounds(); }, { passive: true });
document.addEventListener('visibilitychange', () => { if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else wake(); });
canvas.addEventListener('webglcontextlost', e => {
  e.preventDefault(); lost = true; cancelAnimationFrame(raf); raf = 0;
  canvas.classList.remove('ready'); window.__carBounds = null; window.__carMask = null; dispatchEvent(new Event('car:unavailable'));
});
canvas.addEventListener('webglcontextrestored', () => {
  if (!loaded) return; // Loading may still complete; never expose a canvas with no real frame.
  try {
    resize(); updateCamera(true); renderer.render(scene, camera);
    lost = false; canvas.classList.add('ready'); dispatchEvent(new Event('car:ready')); wake();
  } catch (error) { console.warn('[car] poster retained after context restore:', error); }
});
if (debug.has('gpu')) window.__car = { renderer, scene, camera, wake,
  get wheelAngle() { return wheelAngle; }, get orbitAzimuth() { return orbitAzimuth; } };
window.__carMask = null;
resize();
await loadCar();
