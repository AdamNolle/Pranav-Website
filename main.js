// Team PK: event-driven UI. No permanent animation loop, no layout reads per pointer event.
(() => {
  const root = document.documentElement;
  const $ = s => document.querySelector(s);
  const all = s => [...document.querySelectorAll(s)];
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(hover: hover) and (pointer: fine)');
  const hero = $('[data-hero]');
  const name = $('[data-name]');
  const words = all('[data-word]');
  const motionButton = $('button[data-motion]');
  let userPaused = root.dataset.motion === 'paused';
  const isPaused = () => media.matches || userPaused;
  const emit = (event, detail) => dispatchEvent(new CustomEvent(event, { detail }));

  // Keep a readable HTML heading even if scripts, models, or graphics are unavailable.
  function fitName() {
    const style = getComputedStyle(hero);
    const available = hero.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    name.style.fontSize = '100px';
    const width = Math.max(...words.map(w => w.scrollWidth));
    name.style.fontSize = `${Math.min(130, available / width * 99)}px`;
  }
  fitName();
  document.fonts?.ready.then(fitName);
  new ResizeObserver(fitName).observe(hero);

  // Start lights are brief and skippable. Content is never gated by WebGL readiness.
  let introTimers = [], introActive = false;
  const pods = all('[data-pod]');
  function finishIntro() {
    introTimers.forEach(clearTimeout); introTimers = [];
    words.forEach(w => w.getAnimations().forEach(a => a.cancel()));
    pods.forEach(p => p.classList.remove('on'));
    introActive = false;
    root.dataset.race = 'go'; root.dataset.intro = 'done';
    $('[data-lights-label]').textContent = 'LIGHTS OUT';
    emit('race:go'); emit('intro:done');
  }
  function race() {
    introTimers.forEach(clearTimeout); introTimers = [];
    if (isPaused()) return finishIntro();
    root.dataset.race = ''; root.dataset.intro = 'live';
    introActive = true; emit('race:reset');
    pods.forEach(p => p.classList.remove('on'));
    for (let i = 0; i < 5; i++) introTimers.push(setTimeout(() => {
      all(`[data-pod="${i}"]`).forEach(p => p.classList.add('on'));
      emit('race:light', i);
    }, 90 + i * 150));
    introTimers.push(setTimeout(() => {
      finishIntro();
      words.forEach((w, i) => w.animate([
        { transform: 'translateX(28px)', opacity: .4 }, { transform: 'none', opacity: 1 }
      ], { duration: 500, delay: i * 60, easing: 'cubic-bezier(.2,.8,.2,1)' }));
    }, 1000));
  }
  const skipIntro = () => { if (introActive) finishIntro(); };
  addEventListener('wheel', skipIntro, { passive: true });
  addEventListener('pointerdown', skipIntro, { passive: true });
  addEventListener('keydown', skipIntro);
  $('[data-replay]').addEventListener('click', () => {
    scrollTo({ top: 0, behavior: 'instant' }); race();
  });

  function setMotion(requestedPause, persist = true) {
    if (persist) userPaused = requestedPause;
    const paused = userPaused || media.matches;
    root.dataset.motion = paused ? 'paused' : 'running';
    motionButton.setAttribute('aria-pressed', String(paused));
    $('[data-motion-label]').textContent = paused ? 'Play motion' : 'Pause motion';
    if (persist) try { localStorage.setItem('motion', userPaused ? 'paused' : 'running'); } catch {}
    if (paused) skipIntro();
    resetHolo();
    emit('motion:toggle', { paused });
  }
  motionButton.addEventListener('click', () => setMotion(!isPaused()));
  media.addEventListener('change', () => {
    setMotion(userPaused, false);
    motionButton.disabled = media.matches;
  });

  // Telemetry: green -> yellow -> red as scroll velocity approaches the rev limiter.
  const leds = Array.from({ length: 48 }, (_, i) => {
    const el = document.createElement('i');
    if (i >= 32) el.className = 'r'; else if (i >= 16) el.className = 'y';
    $('[data-redline]').appendChild(el); return el;
  });
  const hud = $('.hud'), speed = $('[data-hud="speed"]'), gear = $('[data-hud="gear"]');
  const digitHost = $('[data-hud-digits]');
  const segmentShapes = [
    ['a', 'M5 1H19L16 5H8Z'], ['b', 'M21 4L23 7V18L20 20L18 18V8Z'],
    ['c', 'M20 21L23 23V34L20 37L18 34V23Z'], ['d', 'M5 39H19L16 35H8Z'],
    ['e', 'M4 21L6 23V34L4 37L1 34V23Z'], ['f', 'M4 4L6 7V18L4 20L1 18V7Z'],
    ['g', 'M5 20L8 18H16L19 20L16 22H8Z'],
  ];
  const segmentStates = ['abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg'];
  const digitPaths = Array.from({ length: 3 }, () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 40');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const paths = segmentShapes.map(([, shape]) => {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', shape);
      svg.appendChild(path);
      return path;
    });
    digitHost.appendChild(svg);
    return paths;
  });
  let previousDigits = '---';
  function renderSpeed(value) {
    const next = String(value).padStart(3, '0');
    speed.textContent = next;
    for (let i = 0; i < digitPaths.length; i++) {
      if (next[i] === previousDigits[i]) continue;
      const active = segmentStates[Number(next[i])];
      digitPaths[i].forEach((path, index) => path.classList.toggle('on', active.includes(segmentShapes[index][0])));
    }
    previousDigits = next;
  }
  renderSpeed(0);
  digitHost.hidden = false;
  digitHost.parentElement.classList.add('is-segmented');
  let uiFrame = 0, lastY = scrollY, lastScroll = performance.now(), previousFrame = 0;
  let targetSpeed = 0, currentSpeed = 0, lastSpeed = -1, lastLit = -1, pageHeight = 1;
  function pageSize() { pageHeight = Math.max(1, document.documentElement.scrollHeight - innerHeight); }
  new ResizeObserver(pageSize).observe(document.body); pageSize();
  function telemetry(t) {
    uiFrame = 0;
    const dt = Math.min(64, t - (previousFrame || t - 16)); previousFrame = t;
    if (t - lastScroll > 70) targetSpeed *= Math.exp(-dt / 150);
    currentSpeed += (targetSpeed - currentSpeed) * (1 - Math.exp(-dt / 65));
    if (targetSpeed < .1 && currentSpeed < .5) currentSpeed = targetSpeed = 0;
    const k = Math.round(currentSpeed);
    if (k !== lastSpeed) {
      lastSpeed = k; renderSpeed(k);
      gear.textContent = k < 4 ? 'N' : String(Math.min(8, 1 + Math.floor(k / 44)));
      const band = k > 265 ? 'limit' : k > 140 ? 'push' : 'cruise';
      if (hud.dataset.band !== band) {
        hud.dataset.band = band;
        $('[data-hud="mode"]').textContent = band === 'limit' ? 'REV LIMIT' : band === 'push' ? 'PUSH LAP' : 'SCROLL TO ACCELERATE';
      }
      const lit = Math.round(k / 350 * leds.length);
      if (lit !== lastLit) {
        const first = Math.min(Math.max(0, lastLit), lit), end = Math.max(lastLit, lit);
        for (let i = first; i < end; i++) leds[i].classList.toggle('on', i < lit);
        lastLit = lit;
      }
    }
    window.__speed = currentSpeed;
    $('[data-progress]').style.transform = `scaleX(${Math.min(1, Math.max(0, scrollY / pageHeight))})`;
    if (currentSpeed || targetSpeed) uiFrame = requestAnimationFrame(telemetry);
  }
  function wakeTelemetry() { if (!uiFrame && !document.hidden) uiFrame = requestAnimationFrame(telemetry); }
  addEventListener('scroll', () => {
    const now = performance.now();
    targetSpeed = Math.min(350, Math.abs(scrollY - lastY) / Math.max(8, now - lastScroll) * 170);
    lastY = scrollY; lastScroll = now; wakeTelemetry();
  }, { passive: true });
  wakeTelemetry();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(uiFrame); uiFrame = 0; }
    else { lastY = scrollY; lastScroll = performance.now(); currentSpeed = targetSpeed = 0; wakeTelemetry(); }
  });

  // Navigation: native section anchors plus focus transfer and an accurate current-section state.
  const menuButton = $('[data-menu]'), menu = $('#nav-links');
  function setMenu(open) { menuButton.setAttribute('aria-expanded', String(open)); menu.classList.toggle('open', open); }
  menuButton.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
  addEventListener('keydown', e => { if (e.key === 'Escape' && menu.classList.contains('open')) { setMenu(false); menuButton.focus(); } });
  addEventListener('pointerdown', e => { if (!e.target.closest('.nav-menu')) setMenu(false); });
  all('a[href^="#"]').forEach(a => a.addEventListener('click', e => {
    const el = document.getElementById(a.hash.slice(1)); if (!el) return;
    e.preventDefault(); setMenu(false);
    const top = a.hash === '#top' ? 0 : el.getBoundingClientRect().top + scrollY - $('.nav').offsetHeight - 32;
    scrollTo({ top, behavior: isPaused() ? 'instant' : 'smooth' });
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true }); history.replaceState(null, '', a.hash);
  }));
  const navLinks = all('.nav-links a'), sections = all('main>section');
  const active = new Map();
  const sectionObserver = new IntersectionObserver(entries => {
    entries.forEach(e => active.set(e.target.id, e.isIntersecting));
    const id = sections.find(s => active.get(s.id))?.id;
    navLinks.forEach(a => { if (a.hash === `#${id}`) a.setAttribute('aria-current', 'location'); else a.removeAttribute('aria-current'); });
  }, { rootMargin: '-18% 0px -55% 0px', threshold: 0 });
  sections.forEach(s => sectionObserver.observe(s));

  const counts = { stints: all('.stint').length, degrees: all('.degree').length, langs: all('.lang').length, skills: all('.skill').length };
  all('[data-count]').forEach(el => el.textContent = String(counts[el.dataset.count]).padStart(2, '0'));
  all('.leds').forEach(box => { for (let i = 0; i < 12; i++) { const d = document.createElement('i'); if (i < +box.dataset.level) d.className = 'on'; box.appendChild(d); } });

  // Holographic card. Cached geometry, one update per frame, no idle animation or global mouse handler.
  const card = $('[data-holo]');
  let cardRect, holoFrame = 0, mx = .5, my = .35;
  let activeTouchId = null, touchStartX = 0, touchStartY = 0;
  function resetHolo() {
    if (!card) return;
    activeTouchId = null;
    cancelAnimationFrame(holoFrame); holoFrame = 0;
    card.classList.remove('is-tracking');
    ['--rx', '--ry', '--mx', '--my'].forEach(p => card.style.removeProperty(p));
  }
  card.addEventListener('pointerenter', () => { cardRect = card.parentElement.getBoundingClientRect(); });
  card.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'touch' || isPaused()) return;
    activeTouchId = e.pointerId;
    touchStartX = e.clientX; touchStartY = e.clientY;
    cardRect = card.parentElement.getBoundingClientRect();
  }, { passive: true });
  card.addEventListener('pointermove', e => {
    if (isPaused()) return;
    if (e.pointerType === 'touch') {
      if (activeTouchId !== e.pointerId) return;
      const dx = e.clientX - touchStartX, dy = e.clientY - touchStartY;
      if (Math.abs(dx) <= Math.abs(dy)) { if (holoFrame || card.classList.contains('is-tracking')) resetHolo(); return; }
      if (Math.abs(dx) < 5) return;
    } else if (!fine.matches) return;
    cardRect ||= card.parentElement.getBoundingClientRect();
    mx = Math.max(0, Math.min(1, (e.clientX - cardRect.left) / cardRect.width));
    my = Math.max(0, Math.min(1, (e.clientY - cardRect.top) / cardRect.height));
    if (!holoFrame) holoFrame = requestAnimationFrame(() => {
      holoFrame = 0; card.classList.add('is-tracking');
      card.style.setProperty('--rx', `${(0.5 - my) * 12}deg`);
      card.style.setProperty('--ry', `${(mx - 0.5) * 16}deg`);
      card.style.setProperty('--mx', `${mx * 100}%`); card.style.setProperty('--my', `${my * 100}%`);
    });
  }, { passive: true });
  card.addEventListener('pointerup', e => { if (e.pointerType === 'touch' && e.pointerId === activeTouchId) resetHolo(); });
  card.addEventListener('pointerleave', resetHolo); card.addEventListener('pointercancel', resetHolo);
  addEventListener('scroll', () => {
    cardRect = null;
    if (holoFrame || activeTouchId !== null || card.classList.contains('is-tracking')) resetHolo();
  }, { passive: true });
  addEventListener('resize', () => { cardRect = null; resetHolo(); });

  // Progressive 3D enhancement. A lightweight poster covers loading and unsupported GPUs.
  addEventListener('car:ready', () => {
    const ok = $('[data-car]')?.classList.contains('ready');
    if ($('[data-car]')) {
      $('[data-car]').tabIndex = ok ? 0 : -1;
      $('[data-car]').setAttribute('aria-hidden', String(!ok));
    }
    if (ok) $('[data-car-poster]').hidden = true;
  });
  addEventListener('car:unavailable', () => {
    $('[data-car-poster]').hidden = false;
    if ($('[data-car]')) { $('[data-car]').tabIndex = -1; $('[data-car]').setAttribute('aria-hidden', 'true'); }
  });
  const connection = navigator.connection;
  const conserveData = connection?.saveData || /^(slow-2g|2g)$/.test(connection?.effectiveType || '');
  if (conserveData) $('[data-car]').setAttribute('aria-hidden', 'true');
  if (!conserveData) requestAnimationFrame(() => {
    const link = document.createElement('link'); link.rel = 'modulepreload'; link.href = 'vendor/three-car.min.js'; document.head.appendChild(link);
    import('./car.js').catch(err => console.warn('[pk] showing the car poster:', err));
  });

  // The script is at the end of the body. Font metrics are the only remaining layout
  // dependency; below-fold images should not hold the tunnel on slow connections.
  Promise.resolve(document.fonts?.ready).then(() => {
    root.dataset.airflow = 'starting';
    import('./windtunnel.js').then(() => { root.dataset.airflow = 'ready'; }).catch(err => console.warn('[pk] permanent wind fallback:', err));
  });
  root.dataset.effects = 'full';
  setMotion(userPaused, false);
  motionButton.disabled = media.matches;
  // A fresh visit opens on the finished hero. The start lights remain a deliberate replay
  // interaction, rather than firing while fonts, textures and the car are still swapping in.
  finishIntro();
})();
