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
  let lx = 0.5, v = 0, boost = 0, hw = 1000, glint = null;
  let lastY = scrollY, lastT = performance.now(), lastKmh = -1, lastLit = -1;
  let timers = [];

  q('.chrome-text').forEach(el => el.setAttribute('data-text', el.textContent));

  // Which chrome surfaces are on screen (so the sweep only touches those).
  let lastLxr = -1, nameOnScreen = true, lastPlateT = 0;
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
    const lineW = Math.max(...Array.from(nameEl.children, c => c.scrollWidth));
    const size = Math.max(24, Math.min(innerWidth > 760 ? 168 : 120, avail / lineW * 100 * 0.985));
    root.style.setProperty('--name-size', size.toFixed(1) + 'px');
    nameEl.style.removeProperty('--name-size');
    hw = nameEl.offsetWidth;
    nameEl.style.setProperty('--hw', hw + 'px');
    letters.forEach(l => l.style.setProperty('--ox', l.offsetLeft + 'px'));
  }

  // Moves each metal plate's highlight toward the pointer; on touch it drifts on its own.
  // Only plates on screen get their highlight updated (no rect reads for the rest).
  const platesOnScreen = new Set();
  if ('IntersectionObserver' in window) {
    const pio = new IntersectionObserver(es => es.forEach(e => e.isIntersecting ? platesOnScreen.add(e.target) : platesOnScreen.delete(e.target)));
    plates.forEach(p => pio.observe(p));
  } else plates.forEach(p => platesOnScreen.add(p));
  function updatePlates(t) {
    const H = innerHeight, touch = ptr.t === 0;
    plates.forEach((p, i) => {
      if (!platesOnScreen.has(p)) return;
      const b = p.getBoundingClientRect();
      if (b.bottom < 0 || b.top > H) return;
      if (touch) {
        const k = 0.5 + 0.55 * Math.sin((t || 0) / 1700 + i * 0.9 + b.top / 300);
        p.style.setProperty('--px', (k * b.width) + 'px');
        p.style.setProperty('--py', (b.height * 0.2) + 'px');
      } else {
        p.style.setProperty('--px', (ptr.cx - b.left) + 'px');
        p.style.setProperty('--py', (ptr.cy - b.top) + 'px');
      }
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
        el.animate([
          { opacity: 0, transform: 'translateX(140px) scaleX(1.25)', filter: 'blur(10px)' },
          { opacity: 1, transform: 'translateX(-6px)', filter: 'blur(0px)', offset: 0.75 },
          { opacity: 1, transform: 'none', filter: 'blur(0px)' }
        ], { duration: 520, delay: (i++) * 70, easing: 'cubic-bezier(.12,.8,.2,1)', fill: 'backwards' });
        boost = Math.max(boost, 60);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    revs.forEach(el => io.observe(el));
  }

  // ---------- start sequence ----------
  function race() {
    timers.forEach(clearTimeout); timers = [];
    letters.forEach(l => l.getAnimations().forEach(a => a.cancel()));
    root.style.setProperty('--nameo', '0'); root.style.setProperty('--heroo', '0');
    nameEl.removeAttribute('data-landed');
    const pods = [0, 1, 2, 3, 4].map(i => q(`[data-pod="${i}"]`));
    const label = root.querySelector('[data-lights-label]');
    const off = () => pods.flat().forEach(el => { el.style.background = '#2a0a08'; el.style.boxShadow = 'none'; });
    off();
    label.textContent = 'LIGHTS';
    document.documentElement.dataset.race = 'reset';
    dispatchEvent(new Event('race:reset'));
    if (reduce) { root.style.setProperty('--nameo', '1'); root.style.setProperty('--heroo', '1'); nameEl.setAttribute('data-landed', ''); return; }
    if (!CONFIG.startLights) { timers.push(setTimeout(fly, 150)); return; }
    pods.forEach((p, i) => timers.push(setTimeout(() => {
      p.forEach(el => { el.style.background = '#ff2415'; el.style.boxShadow = '0 0 18px 4px rgba(255,36,21,0.6), inset 0 -3px 6px rgba(0,0,0,0.35)'; });
      boost = Math.max(boost, 20 + i * 12);
      dispatchEvent(new CustomEvent('race:light', { detail: i }));
    }, 150 + i * 190)));
    timers.push(setTimeout(() => {
      off(); label.textContent = 'LIGHTS OUT';
      fly();
    }, 150 + 4 * 190 + 260 + Math.random() * 200));
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
    timers.push(setTimeout(() => {
      root.style.setProperty('--heroo', '1');
      nameEl.setAttribute('data-landed', '');
      const st = root.querySelector('[data-stripe]');
      st.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 320, easing: 'cubic-bezier(.16,1,.3,1)' });
      q('[data-heroin]').forEach((el, i) => el.animate([
        { opacity: 0, transform: 'translateX(60px)', filter: 'blur(6px)' }, { opacity: 1, transform: 'none', filter: 'blur(0px)' }
      ], { duration: 420, delay: i * 80, easing: 'cubic-bezier(.16,1,.3,1)', fill: 'backwards' }));
      glint = performance.now();
    }, end - 120));
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
    if (target == null) {
      const idle = ptr.t === 0 || t - ptr.t > 2600;
      const still = reduce || document.documentElement.dataset.motion === 'paused';
      target = idle ? (still ? 0.5 : 0.5 + 0.42 * Math.sin(t / 1600)) : ptr.x;
      target += Math.sin(y / 600) * 0.12;
      lx += (target - lx) * 0.14;
    }
    // Chrome sweep: write only to on-screen chrome elements, and only when the value visibly moves.
    // (Writing to the page root restyled the whole document every frame.)
    const lxr = Math.max(0, Math.min(1, 1 - lx));
    if (Math.abs(lxr - lastLxr) > 0.0015) {
      lastLxr = lxr;
      const v = lxr.toFixed(4);
      if (nameOnScreen) nameEl.style.setProperty('--lxp', (lx * hw).toFixed(1) + 'px');
      chromeOnScreen.forEach(el => el.style.setProperty('--lxr', v));
    }
    // CSS plate highlight only matters when metal.js isn't drawing the plates.
    // The idle drift is slow (period ~10 s), so 20 Hz updates are visually identical and cost a third.
    if (!document.documentElement.classList.contains('metal-gl') &&
        ((ptr.t === 0 && document.documentElement.dataset.motion !== 'paused' && t - lastPlateT > 50) || inst > 0)) {
      lastPlateT = t; updatePlates(t);
    }
  }

  // ---------- wiring ----------
  const rebuild = () => { measure(); buildRedline(); };
  addEventListener('resize', rebuild);
  addEventListener('pointermove', e => {
    ptr.x = e.clientX / innerWidth; ptr.y = e.clientY / innerHeight;
    ptr.cx = e.clientX; ptr.cy = e.clientY; ptr.t = performance.now();
    updatePlates();
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
    motionBtn.querySelector('[data-motion-label]').textContent = paused ? 'Play motion' : 'Pause motion';
    try { localStorage.setItem('motion', paused ? 'paused' : 'running'); } catch (e) {}
    dispatchEvent(new CustomEvent('motion:toggle', { detail: { paused } }));
  };
  setMotion(document.documentElement.dataset.motion === 'paused');
  motionBtn.addEventListener('click', () => setMotion(motionBtn.getAttribute('aria-pressed') !== 'true'));

  // ---------- telemetry: counts come from the content itself, so they can't drift ----------
  const counts = { stints: q('.stint').length, degrees: q('.degree').length, langs: q('.lang').length, skills: q('.skill').length };
  q('[data-count]').forEach(el => { el.textContent = String(counts[el.dataset.count] ?? el.textContent).padStart(2, '0'); });

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
    race();
  });

  // ---------- staged GPU start-up ----------
  // The three WebGL layers each compile shaders and upload textures; starting them together
  // blocks the main thread for over a second on phones. Stage them after first paint, and
  // leave the metal plates to the lightweight CSS finish on phones and touch devices.
  const phone = matchMedia('(max-width: 760px), (pointer: coarse)').matches;
  const idle = f => ('requestIdleCallback' in window ? requestIdleCallback(f, { timeout: 1500 }) : setTimeout(f, 200));
  const load = src => import(src).catch(err => console.warn('[pk] optional layer failed:', src, err));
  requestAnimationFrame(() => setTimeout(() => {
    load('./car.js');
    setTimeout(() => idle(() => load('./windtunnel.js')), phone ? 1400 : 500);
    if (!phone) setTimeout(() => idle(() => load('./metal.js')), 1100);
  }, 0));

  measure();
  buildRedline();
  initReveals();
  requestAnimationFrame(loop);
  race();
})();
