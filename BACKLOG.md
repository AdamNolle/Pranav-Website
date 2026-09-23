# Backlog

Work top to bottom. After every item: take a headless-Brave screenshot at 1440×900 and 390×844, run the axe check, then commit and push (GitHub Pages redeploys from `main`).

## From Adam, 2026-09-23
1. **Wind tunnel smoothness:** make it less glitchy. Remove flicker and popping in dye and particles, smooth scroll re-projection, add frame-time-independent stepping, and keep a steady 60 fps.
2. **Performance, as fast as possible:**
   - Measure with Performance/Lighthouse: LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1, steady frame times.
   - Lazy-start the smoke and metal canvases and defer the car model until the hero is visible.
   - Use one shared rAF loop, and drop quality tiers adaptively when frame times rise.
   - Compress textures (KTX2/Basis), subset the fonts, and preload only what's critical.
3. **Custom F1 font:**
   - Draw an original typeface in the spirit of Formula1 Display Wide: extended, flat-sided rounds, squared counters, low contrast, upright and italic.
   - Build it as a real font (fontTools/FontForge, or glyphs drawn in code) at `assets/fonts/pk-wide.woff2` with the A–Z, 0–9 and punctuation the site uses.
   - Wire it to `--display`. The official Formula1 fonts are licensed and couldn't be downloaded (bot check); `tools/install_f1_fonts.py` swaps them in if Adam provides the ZIP.
4. **F1 car to near 1:1 with a real car:** keep iterating `blender/build_car.py` against reference photos and dimensions of a real 2024–25 car (Williams FW46 in blue and black is the best reference). Proportions first, then surfacing, then detail. Aim for a beautiful original livery with properly sized logos, and soft-plus-hard studio lighting.
5. **Texture quality everywhere:** (2× DPR pass 2026-09-23: logos, type, metal and portrait are crisp; the car backdrop was beige and is now cool navy. Suspension members now use 28-segment profiles and the GLB stores normals at 11 bits, so the banding is gone.) no visible tiling, banding, aliasing, blur or stretching on the car, metal cards, smoke sprites, logos or portrait. Check each at 2× DPR.

## Done (2026-09-23)
- **Car to 1:1, proportions:** checked against 2024–25 dimensions (length 5.57 m, width 2.02 m, 3.6 m wheelbase, 720 mm tyres, about 0.96 m height; all within regulation).
  - The front wing was 0.57 m tall at the tips against a real 0.33–0.35 m. It's re-proportioned to 0.25 m in the centre and 0.35 m at the tips.
  - Side view: the fin used to stand up to 19 cm proud of the engine cover at the rear, reading as a flat panel. It's now a slim 2024-style fin that tracks the cover (7 cm proud, tapering to 3 cm), with the PK and number moved onto it.
  - Next: PK and number are small at hero scale; consider putting the race number on the nose top and the airbox.
- **Contrast over the live smoke and metal:** measured by `scratchpad/contrast.py`. It hides the text, photographs the real backdrop over 3 frames at every scroll step, and compares each element's colour with the 97th-percentile backdrop.
  - Fixes: darker badge and CTA gradients, light quote marks, opaque club pills, and dark chips under small labels on the smoke.
  - Result: 588 samples, 0 failures; the worst is 5.48:1 on desktop and 5.50:1 on phone.
- **PK Wide refinements:**
  - a proper wedge comma
  - an OpenType kern feature with about 40 pairs (AV/VA/TY/LT, punctuation after diagonals), verified in Brave (AVA 310 → 293 px)
  - italic flag fixed
- **Rear-wing endplates:** J.B. Hunt (0.48 m) and the Georgia Tech wordmark (0.46 m) are sized to the plate, legible at hero scale, and checked in a Cycles close-up.
- **Custom font:** PK Wide, an original extended display face with regular and italic, about 2 KB each (`tools/build_font.py`). It's now the site's display font; the official Formula1 fonts can still be swapped in via `tools/install_f1_fonts.py`.
- **Wind tunnel:** switched to a fixed-timestep solver. Desktop now holds 59–60 fps with p99 frame time 16.8 ms, and the shaking is gone.
- **Performance:**
  - WebGL layers start in stages after first paint, and phones skip the WebGL metal.
  - WebP logos and shadow; metal maps cut to 1K lossy (208 KB total).
  - Mobile page weight is down from 3.1 MB to 1.46 MB, load to about 1.0 s, and LCP from 3.7 s to 2.7 s (throttled 4G at 4× CPU). CLS is 0.006.
- **Metal:** redesigned as calm graphite anodise with a soft key light and one soft sheen; text contrast is at least 7:1.
- **Layout:** the nav now has a solid blurred backing at every width, so nothing collides with it. The hero name is fitted to the screen width. The redline strip spans the full top edge. PNG/ICO favicons and a web manifest are in.

- **Car round 2:**
  - A livery researched from Williams, Alpine and RB: an electric-to-navy sweep on satin black with a silver pinstripe.
  - Realism: fillets, curled wing tips, wheel-cover art and tyre bulge.
  - Lighting: soft softbox plus hard strips, Neutral tone mapping, VSM shadows, and a blurred floor reflection.
  - Phones get a light render path; `car.glb` is 1.08 MB.

## Also open
- LCP is still just over 2.5 s on throttled mobile, because the intro holds the name back. Consider painting the name immediately in a dimmed state.
- Car textures: try KTX2/Basis once an encoder is installed (livery set is WebP at 4K wide).
