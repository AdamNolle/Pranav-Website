(() => {
  const CONFIG = { accent: '#2b7bff', startLights: true };

  const root = document.getElementById('page');
  const q = s => Array.from(root.querySelectorAll(s));
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  root.style.setProperty('--acc', CONFIG.accent);

  // Split the hero name into per-letter spans so each can fly in and carry its own chrome offset.
  q('[data-word]').forEach(w => {
    w.textContent = '';
    for (const ch of w.dataset.word) {
      const s = document.createElement('span');
      s.dataset.l = ''; s.textContent = ch;
      w.appendChild(s);
    }
  });

  // Outline "grid slot" of the name, painted from the first frame; the chrome letters fly into it.
  // It is also the page's first large paint, so LCP no longer waits for the start-light intro.
  const ghost = document.createElement('div');
  ghost.className = 'name-ghost';
  ghost.setAttribute('aria-hidden', 'true');
  q('[data-word]').forEach(w => {
    const line = document.createElement('div');
    line.textContent = w.dataset.word;
    ghost.appendChild(line);
  });
  root.querySelector('[data-name]').appendChild(ghost);

  // Specular sweep over the name, compositor-only: a white copy of the name seen through a soft band
  // (the window). The window slides by x and the copy inside it by -x, so the white text stays on the
  // letters while the band travels. Only transforms change per frame, so the name never repaints
  // (repainting its gradient-clipped letters every frame cost up to half a second of GPU raster).
  const glintBox = document.createElement('div'), glintWin = document.createElement('div');
  const glintTxt = ghost.cloneNode(true);
  glintBox.className = 'name-glint'; glintWin.className = 'name-glint-win'; glintTxt.className = 'name-glint-txt';
  glintBox.setAttribute('aria-hidden', 'true');
  glintWin.appendChild(glintTxt); glintBox.appendChild(glintWin);
  root.querySelector('[data-name]').appendChild(glintBox);

  const letters = q('[data-l]');
  const nameEl = root.querySelector('[data-name]');
  const hero = root.querySelector('[data-hero]');
  // Full-width shift-light strip: one LED per ~26px, green then red then blue, filling left to right.
  const redline = document.querySelector('[data-redline]');
  let leds = [];
  function buildRedline() {
    const n = Math.max(16, Math.min(64, Math.round(innerWidth / 26)));
    if (n === leds.length) return;
    redline.textContent = '';
    leds = Array.from({ length: n }, (_, i) => {
      const d = document.createElement('i');
      const f = i / n;
      if (f >= 0.72) d.className = 'b'; else if (f >= 0.4) d.className = 'r';
      redline.appendChild(d);
      return d;
    });
    lastLit = -1;
  }
  const speedEl = root.querySelector('[data-hud="speed"]');
  const gearEl = root.querySelector('[data-hud="gear"]');
  const plates = q('[data-metal]');
  const ptr = { x: 0.5, y: 0.5, cx: -9999, cy: -9999, t: 0 };
  const FINE = matchMedia('(pointer: fine)').matches;
  let lx = 0.5, lt = 0.5, v = 0, boost = 0, hw = 1000, gw = 400, lastGx = NaN, glint = null;
  let lastY = scrollY, lastT = performance.now(), lastKmh = -1, lastLit = -1;
  let timers = [];

  q('.chrome-text').forEach(el => el.setAttribute('data-text', el.textContent));

  // Which chrome surfaces are on screen (so the sweep only touches those).
  let lastLxr = -1, nameOnScreen = true;
  const chromeOnScreen = new Set();
  if ('IntersectionObserver' in window) {
    const cio = new IntersectionObserver(es => es.forEach(e => {
      if (e.target === nameEl) nameOnScreen = e.isIntersecting;
      else e.isIntersecting ? chromeOnScreen.add(e.target) : chromeOnScreen.delete(e.target);
      lastLxr = -1;
    }));
    cio.observe(nameEl);
    q('.chrome-text').forEach(el => cio.observe(el));
  } else q('.chrome-text').forEach(el => chromeOnScreen.add(el));

  function measure() {
    // Fit the longest name line to the hero's content width (capped for very wide screens).
    const cs = getComputedStyle(hero);
    const avail = hero.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    nameEl.style.setProperty('--name-size', '100px');
    const lineW = Math.max(...q('[data-word]').map(c => c.scrollWidth));   // the two name lines only
    const size = Math.max(24, Math.min(innerWidth > 760 ? 168 : 120, avail / lineW * 100 * 0.985));
    root.style.setProperty('--name-size', size.toFixed(1) + 'px');
    nameEl.style.removeProperty('--name-size');
    hw = nameEl.offsetWidth;
    gw = Math.round(hw * 0.64);                           // band is 48% of the name wide, plus room for its tilt
    glintWin.style.width = gw + 'px';
    glintTxt.style.width = hw + 'px';
    lastGx = NaN;
  }

  // Moves each metal plate's highlight toward the pointer (CSS finish, when metal.js isn't drawing).
  // On touch screens the sheen stays put: animating it forced a layout and repainted whole cards every
  // frame, most of all while scrolling. Only plates on screen are touched.
  const platesOnScreen = new Set();
  if ('IntersectionObserver' in window) {
    const pio = new IntersectionObserver(es => es.forEach(e => e.isIntersecting ? platesOnScreen.add(e.target) : platesOnScreen.delete(e.target)));
    plates.forEach(p => pio.observe(p));
  } else plates.forEach(p => platesOnScreen.add(p));
  function updatePlates() {
    const H = innerHeight;
    plates.forEach(p => {
      if (!platesOnScreen.has(p)) return;
      const b = p.getBoundingClientRect();
      if (b.bottom < 0 || b.top > H) return;
      p.style.setProperty('--px', (ptr.cx - b.left) + 'px');
      p.style.setProperty('--py', (ptr.cy - b.top) + 'px');
    });
  }

  // ---------- scroll reveals ----------
  function initReveals() {
    const revs = q('[data-rev]');
    if (reduce || !('IntersectionObserver' in window)) return;
    revs.forEach(el => { el.style.opacity = '0'; });
    const io = new IntersectionObserver(entries => {
      let i = 0;
      entries.forEach(en => {
        if (!en.isIntersecting) return;
        const el = en.target; io.unobserve(el);
        el.style.opacity = '';
        if (motionPaused()) return;
        // transform + opacity only: both run on the compositor. (A blur() filter here re-filtered every
        // revealing card on the GPU each frame, right while the page was scrolling.)
        el.animate([
          { opacity: 0, transform: 'translateX(140px) scaleX(1.25)' },
          { opacity: 1, transform: 'translateX(-6px)', offset: 0.75 },
          { opacity: 1, transform: 'none' }
        ], { duration: 520, delay: (i++) * 70, easing: 'cubic-bezier(.12,.8,.2,1)', fill: 'backwards' });
        boost = Math.max(boost, 60);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    revs.forEach(el => io.observe(el));
  }

  // ---------- start sequence ----------
  const motionPaused = () => document.documentElement.dataset.motion === 'paused';
  const pods = [0, 1, 2, 3, 4].map(i => q(`[data-pod="${i}"]`));
  const lightsLabel = root.querySelector('[data-lights-label]');
  const off = () => pods.flat().forEach(el => { el.style.background = '#2a0a08'; el.style.boxShadow = 'none'; });
  let introLive = false;
  // <html data-intro>: hold (lights waiting for the grid) -> live (intro playing) -> done. car.js and the
  // staged start-up below keep heavy main-thread work out of `live`.
  const setIntro = v => { document.documentElement.dataset.intro = v; if (v === 'done') dispatchEvent(new Event('intro:done')); };
  // Replay Intro plays even with motion paused (it's an explicit request); page load doesn't.
  function race(replay) {
    timers.forEach(clearTimeout); timers = [];
    letters.forEach(l => l.getAnimations().forEach(a => a.cancel()));
    root.style.setProperty('--nameo', '0'); root.style.setProperty('--heroo', '0');
    nameEl.removeAttribute('data-landed');
    off();
    lightsLabel.textContent = 'LIGHTS';
    document.documentElement.dataset.race = 'reset';
    dispatchEvent(new Event('race:reset'));
    if (reduce || (motionPaused() && !replay)) { lightsLabel.textContent = 'LIGHTS OUT'; return land(false); }
    introLive = true;
    setIntro('live');
    if (!CONFIG.startLights) { timers.push(setTimeout(fly, 150)); return; }
    pods.forEach((p, i) => timers.push(setTimeout(() => {
      p.forEach(el => { el.style.background = '#ff2415'; el.style.boxShadow = '0 0 18px 4px rgba(255,36,21,0.6), inset 0 -3px 6px rgba(0,0,0,0.35)'; });
      boost = Math.max(boost, 20 + i * 12);
      dispatchEvent(new CustomEvent('race:light', { detail: i }));
    }, 150 + i * 190)));
    timers.push(setTimeout(() => {
      off(); lightsLabel.textContent = 'LIGHTS OUT';
      fly();
    }, 150 + 4 * 190 + 260 + Math.random() * 200));
  }

  // Name, stripe and meta in their final place; `animate` plays the landing flourish.
  function land(animate) {
    introLive = false;
    setIntro('done');
    root.style.setProperty('--nameo', '1'); root.style.setProperty('--heroo', '1');
    nameEl.setAttribute('data-landed', '');
    if (!animate) return;
    const st = root.querySelector('[data-stripe]');
    st.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 320, easing: 'cubic-bezier(.16,1,.3,1)' });
    q('[data-heroin]').forEach((el, i) => el.animate([
      { opacity: 0, transform: 'translateX(60px)', filter: 'blur(6px)' }, { opacity: 1, transform: 'none', filter: 'blur(0px)' }
    ], { duration: 420, delay: i * 80, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' }));
    glint = performance.now();
  }

  // HIG Motion, "let people cancel motion": a scroll, tap, click or key press during the intro
  // lands it at once instead of making people wait it out.
  function skipIntro() {
    if (!introLive) return;
    timers.forEach(clearTimeout); timers = [];
    letters.forEach(l => l.getAnimations().forEach(a => a.finish()));
    root.querySelector('[data-streaks]').textContent = '';
    if (document.documentElement.dataset.race !== 'go') {
      off(); lightsLabel.textContent = 'LIGHTS OUT';
      document.documentElement.dataset.race = 'go';
      dispatchEvent(new Event('race:go'));
    }
    land(false);
  }

  function fly() {
    const hb = hero.getBoundingClientRect();
    const sc = root.querySelector('[data-streaks]');
    const acc = getComputedStyle(root).getPropertyValue('--acc').trim() || '#2b7bff';
    const step = 40, dur = 480;
    const rects = letters.map(l => l.getBoundingClientRect());
    root.style.setProperty('--nameo', '1');
    boost = 330;
    document.documentElement.dataset.race = 'go';
    dispatchEvent(new Event('race:go'));
    letters.forEach((l, i) => {
      l.animate([
        { transform: 'translateX(115vw) scaleX(3)', filter: 'blur(16px)', opacity: 0 },
        { opacity: 1, offset: 0.1 },
        { transform: 'translateX(-0.08em) scaleX(1.06)', filter: 'blur(1px)', opacity: 1, offset: 0.8 },
        { transform: 'none', filter: 'blur(0px)', opacity: 1 }
      ], { duration: dur, delay: i * step, easing: 'cubic-bezier(.1,.75,.2,1)', fill: 'backwards' });
      const lb = rects[i], s = document.createElement('div');
      s.style.cssText = `position:absolute;left:${lb.left - hb.left}px;right:0;top:${lb.top - hb.top + lb.height * (0.3 + Math.random() * 0.4)}px;height:${i % 3 ? 2 : 4}px;background:linear-gradient(90deg,${i % 2 ? '#ffffff' : acc},transparent 70%);box-shadow:0 0 12px ${acc};transform-origin:0 50%;`;
      sc.appendChild(s);
      const a = s.animate([
        { transform: 'translateX(100%)', opacity: 1 },
        { transform: 'translateX(0)', opacity: 0.9, offset: 0.6 },
        { transform: 'translateX(0) scaleX(0.05)', opacity: 0 }
      ], { duration: dur, delay: i * step, easing: 'cubic-bezier(.1,.75,.2,1)', fill: 'both' });
      a.onfinish = () => s.remove();
    });
    const end = (letters.length - 1) * step + dur;
    timers.push(setTimeout(() => land(true), end - 120));
  }

  // ---------- frame loop: speedo, rev LEDs, chrome glint ----------
  function loop(t) {
    requestAnimationFrame(loop);
    const y = scrollY;
    const dt = Math.max(1, t - lastT);
    const inst = Math.min(20, Math.abs(y - lastY) / dt);
    lastY = y; lastT = t;
    v += (inst - v) * 0.12;
    boost *= 0.982;
    const kmh = Math.min(350, v * 210 + boost);
    const k = Math.round(kmh);
    if (k !== lastKmh) {
      lastKmh = k;
      speedEl.textContent = String(k).padStart(3, '0');
      gearEl.textContent = k < 4 ? 'N' : String(Math.min(8, 1 + Math.floor(k / 44)));
    }
    const lit = Math.round(Math.min(1, kmh / 330) * leds.length);
    if (lit !== lastLit) {
      lastLit = lit;
      leds.forEach((el, i) => el.classList.toggle('on', i < lit));
    }
    window.__speed = kmh;
    let target;
    if (glint) {
      const p = (t - glint) / 850;
      if (p >= 1) glint = null; else { target = -0.25 + p * 1.5; lx = target; }
    }
    const pointing = ptr.t !== 0 && t - ptr.t <= 2600;
    if (target == null) {
      const still = reduce || document.documentElement.dataset.motion === 'paused';
      target = pointing ? ptr.x : (still ? 0.5 : 0.5 + 0.42 * Math.sin(t / 1600));
      target += Math.sin(y / 600) * 0.12;
      lx += (target - lx) * 0.14;
    }
    // Name glint: two transforms, no repaint.
    const gx = Math.round((lx * hw - gw / 2) * 2) / 2;
    if (nameOnScreen && gx !== lastGx) {
      lastGx = gx;
      glintWin.style.transform = `translate3d(${gx}px,0,0)`;
      glintTxt.style.transform = `translate3d(${-gx}px,0,0)`;
    }
    // Section titles keep a paint-based sweep, so it moves only with a real pointer (never on idle or
    // scroll, when a repaint per frame would cost smoothness) and only while it visibly changes.
    lt += ((FINE && pointing ? ptr.x : 0.5) - lt) * 0.14;
    const lxr = Math.max(0, Math.min(1, 1 - lt));
    if (chromeOnScreen.size && Math.abs(lxr - lastLxr) > 0.0015) {
      lastLxr = lxr;
      const v = lxr.toFixed(4);
      chromeOnScreen.forEach(el => el.style.setProperty('--lxr', v));
    }
    // CSS plate highlight only matters when metal.js isn't drawing the plates.
    // The idle drift is slow (period ~10 s), so 20 Hz updates are visually identical and cost a third.
    if (inst > 0 && FINE && ptr.t !== 0 && !document.documentElement.classList.contains('metal-gl')) updatePlates();
  }

  // ---------- wiring ----------
  const rebuild = () => { measure(); buildRedline(); };
  addEventListener('resize', rebuild);
  addEventListener('pointermove', e => {
    ptr.x = e.clientX / innerWidth; ptr.y = e.clientY / innerHeight;
    ptr.cx = e.clientX; ptr.cy = e.clientY; ptr.t = performance.now();
    if (FINE && !document.documentElement.classList.contains('metal-gl')) updatePlates();
  }, { passive: true });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(rebuild);

  q('a[href^="#"]').forEach(a => a.addEventListener('click', e => {
    const href = a.getAttribute('href');
    const el = document.getElementById(href.slice(1));
    if (!el) return;
    e.preventDefault();
    scrollTo({ top: el.getBoundingClientRect().top + scrollY - (href === '#top' ? 0 : 60), behavior: reduce ? 'auto' : 'smooth' });
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true });
    history.replaceState(null, '', href);
  }));
  // ---------- pause motion (WCAG 2.2.2): stops the smoke, turntable and moving lights ----------
  const motionBtn = root.querySelector('[data-motion]');
  const setMotion = paused => {
    document.documentElement.dataset.motion = paused ? 'paused' : 'running';
    motionBtn.setAttribute('aria-pressed', String(paused));
    motionBtn.querySelector('[data-motion-label]').textContent = paused ? 'Play Motion' : 'Pause Motion';
    try { localStorage.setItem('motion', paused ? 'paused' : 'running'); } catch (e) {}
    dispatchEvent(new CustomEvent('motion:toggle', { detail: { paused } }));
  };
  setMotion(document.documentElement.dataset.motion === 'paused');
  motionBtn.addEventListener('click', () => setMotion(motionBtn.getAttribute('aria-pressed') !== 'true'));

  // ---------- telemetry: counts come from the content itself, so they can't drift ----------
  const counts = { stints: q('.stint').length, degrees: q('.degree').length, langs: q('.lang').length, skills: q('.skill').length };
  q('[data-count]').forEach(el => { el.textContent = String(counts[el.dataset.count] ?? el.textContent).padStart(2, '0'); });

  // ---------- radio waveform: bars with a speech-like envelope (static heights, CSS animates) ----------
  q('[data-wave]').forEach(w => {
    const n = innerWidth < 600 ? 36 : 56;
    for (let i = 0; i < n; i++) {
      const b = document.createElement('i'), x = i / (n - 1);
      const env = 0.35 + 0.65 * Math.pow(Math.sin(Math.PI * x), 0.7) * (0.6 + 0.4 * Math.sin(x * 19.3));
      b.style.cssText = `--lv:${Math.max(.18, env).toFixed(2)};--lo:${(0.08 + 0.1 * ((i * 7) % 5) / 5).toFixed(2)};--d:${520 + ((i * 37) % 9) * 60}ms;--dl:${-((i * 113) % 900)}ms`;
      w.appendChild(b);
    }
  });

  // ---------- mobile menu ----------
  const menuBtn = root.querySelector('[data-menu]');
  const menu = document.getElementById(menuBtn.getAttribute('aria-controls'));
  const setMenu = open => {
    menuBtn.setAttribute('aria-expanded', String(open));
    menu.classList.toggle('open', open);
  };
  menuBtn.addEventListener('click', () => setMenu(menuBtn.getAttribute('aria-expanded') !== 'true'));
  menu.addEventListener('click', e => { if (e.target.closest('a')) setMenu(false); });
  addEventListener('keydown', e => {
    if (e.key === 'Escape' && menuBtn.getAttribute('aria-expanded') === 'true') { setMenu(false); menuBtn.focus(); }
  });
  addEventListener('pointerdown', e => {
    if (!e.target.closest('.nav-menu')) setMenu(false);
  });

  // ---------- language shift lights: light up in sequence when revealed ----------
  q('.leds').forEach(box => {
    const level = Number(box.dataset.level);
    for (let i = 0; i < 12; i++) box.appendChild(document.createElement('i'));
    const dots = Array.from(box.children);
    const fill = () => dots.forEach((d, i) => {
      if (i >= level) return;
      if (reduce) d.classList.add('on');
      else setTimeout(() => d.classList.add('on'), 250 + i * 55);
    });
    if (reduce || !('IntersectionObserver' in window)) return fill();
    const io = new IntersectionObserver(([en]) => { if (en.isIntersecting) { io.disconnect(); fill(); } }, { threshold: 0.6 });
    io.observe(box);
  });

  root.querySelector('[data-replay]').addEventListener('click', () => {
    scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    race(true);
  });
  // Any real input cancels the intro. (Not `scroll`: Replay Intro scrolls to the top itself.)
  // The document-level touchstart listener is also what lets iOS Safari apply :active press states.
  addEventListener('wheel', skipIntro, { passive: true });
  addEventListener('pointerdown', skipIntro);
  document.addEventListener('touchstart', skipIntro, { passive: true });
  addEventListener('keydown', e => { if (!['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) skipIntro(); });

  // ---------- staged start-up: the grid forms before the lights go on ----------
  // Each WebGL layer compiles shaders, builds geometry and uploads textures once, which is hundreds of
  // ms of main-thread work on a slow phone. It all happens while the start lights hold (the name's
  // silhouette and the job title are already on screen): car first, then the smoke, one at a time.
  // The intro then plays on a quiet main thread. If the grid isn't ready by GRID_CAP the intro starts
  // anyway and whatever is still loading waits until it has landed. The metal plates (desktop only;
  // phones keep the CSS finish) are below the fold, so they start after the intro.
  const GRID_CAP = 2600;                                   // ms from navigation start
  const phone = matchMedia('(max-width: 760px), (pointer: coarse)').matches;
  const idle = f => ('requestIdleCallback' in window ? requestIdleCallback(f, { timeout: 1500 }) : setTimeout(f, 200));
  const wait = ms => new Promise(r => setTimeout(r, Math.max(0, ms)));
  const once = ev => new Promise(r => addEventListener(ev, r, { once: true }));
  const carReady = once('car:ready'), windReady = once('wind:ready'), introDone = once('intro:done');
  let gridGo;
  const gridStart = new Promise(r => { gridGo = r; });     // settles once the hold ends (lights on, or skipped)
  const load = (src, readyEvent) => import(src).catch(err => {
    console.warn('[pk] optional layer failed:', src, err);
    if (readyEvent) dispatchEvent(new Event(readyEvent));   // a missing layer never holds the lights
  });
  const hint = (rel, href) => {
    const l = document.createElement('link');
    l.rel = rel; l.href = href;
    document.head.appendChild(l);
  };
  requestAnimationFrame(() => setTimeout(() => {
    // After the first paint, so none of this competes with the page's own CSS and fonts. The three.js
    // bundle downloads alongside car.js (not after it), and ahead of the model: car.js starts the model
    // and decoder downloads as soon as it runs, which on a slow connection is still during the hold.
    hint('modulepreload', 'vendor/three-car.min.js');
    hint('modulepreload', 'windtunnel.js');
    load('./car.js', 'car:ready');
    Promise.race([carReady, gridStart])
      .then(() => (document.documentElement.dataset.intro === 'live' ? introDone : null))
      .then(() => idle(() => load('./windtunnel.js', 'wind:ready')));
    if (!phone) introDone.then(() => Promise.race([windReady, wait(4000)])).then(() => idle(() => load('./metal.js')));
  }, 0));

  measure();
  buildRedline();
  initReveals();
  requestAnimationFrame(loop);
  if (reduce || motionPaused()) { race(); gridGo(); }       // static hero: lands at once
  else {
    introLive = true;                                      // a tap, scroll or key during the hold lands it
    setIntro('hold');
    Promise.race([Promise.all([carReady, windReady]), wait(GRID_CAP - performance.now()), introDone])
      .then(() => { if (document.documentElement.dataset.intro === 'hold') race(); gridGo(); });
  }
})();
