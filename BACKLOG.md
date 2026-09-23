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
5. **Texture quality everywhere:** no visible tiling, banding, aliasing, blur or stretching on the car, metal cards, smoke sprites, logos or portrait. Check each at 2× DPR.

## Also open
- The metal cards need a calmer, premium look (a subagent redesign was in progress 2026-09-23).
- A car livery and lighting pass was in progress (subagent).
- Measure contrast by eye over the moving smoke and metal (axe can't measure over canvases).
