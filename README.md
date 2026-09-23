# Pranav Kondapaneni: portfolio

An F1-themed personal site: Pranav's blue-and-black car in a GPU-rendered studio, with wind-tunnel smoke flowing across the whole page and brushed-metal cards lit in real time. It's plain HTML/CSS/JS with no build step.

## Run locally

```
python3 tools/serve.py        # http://127.0.0.1:8765/
```

(`python3 -m http.server` also works, but it drops the parallel texture requests.)

## How it's built

| Part | Source |
| --- | --- |
| Page, layout, accessibility | `index.html`, `styles.css`, `main.js` |
| 3D car in the hero (three.js) | `car.js` ← `assets/car.glb`, built by `blender/build_car.py` |
| Wind-tunnel smoke + tracer particles (WebGL2 stable fluids) | `windtunnel.js` ← sprites from `blender/build_smoke.py` |
| Brushed-metal cards (WebGL, anisotropic GGX) | `metal.js` ← textures from `blender/build_metal.py` |
| Design contract | `DESIGN.md` |

Rebuild an asset with Blender 5.2, for example:

```
blender --background --python blender/build_car.py -- --render 3q side
```

## Formula1 fonts

Download the Formula1 font ZIP, then run:

```
pip install fonttools brotli
python3 tools/install_f1_fonts.py path/to/fonts.zip
```

This writes `assets/fonts/*.woff2` and `assets/fonts/f1.css`, and the site switches to them. Until then it uses Anybody / Barlow / JetBrains Mono. The Formula1 typefaces belong to Formula One Licensing B.V., so check the licence before publishing with them.

## Accessibility

The site targets WCAG 2.2 AA:
- semantic landmarks and headings, plus a skip link
- a keyboard-operable menu
- visible focus and touch targets of at least 44px
- decorative canvases marked `aria-hidden`
- support for `prefers-reduced-motion` and forced colours
- a persistent **Pause motion** control

## Credits

The logos are the trademarks of their owners and identify Pranav's employer and schools; sources are in `assets/logos/SOURCES.md`. Photo © Pranav Kondapaneni.
