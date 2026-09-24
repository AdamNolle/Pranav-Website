# Design contract

- **Purpose:** in five seconds a recruiter knows who Pranav is (software engineer at J.B. Hunt, CS at Georgia Tech) and can reach LinkedIn.
- **Art direction:** a night session in an F1 wind tunnel: Pranav's blue-and-black car under studio softboxes, with smoke streaming across the whole site. The page reads like a team's timing and telemetry sheet.
- **Reference roles** (from `my-skills/design-ui`):
  - *Nick Ho Motorsports* supplies the layout logic: a visible measuring grid, compact tabular stats at the edges, and a single race-sized headline.
  - *Lando Norris* supplies the identity motif, built from the subject's own equipment (car, livery, helmet) with one accent carrying through.
  - *Cartier / Immersive Garden* supply the material lighting: one coherent studio light model for the car, the metal plates and the chrome type.
  - Original to this site: the start-light sequence, the speed-driven HUD and the wind-tunnel flow field.
- **Palette roles:**

  | Role | Value |
  | --- | --- |
  | Canvas | `#050608` |
  | Raised surface | `#0c0f14` |
  | Text | `#eef2f7` |
  | Secondary text | `#98a2b3` |
  | Accent / action | electric blue `#2b7bff` |
  | Deep blue | `#0b3a9e` |
  | Glow | `#7fb0ff` |
  | Success | `#3ddc97` |

  The red start lights and rain light stay red because they are functional signals, not palette.
- **Type:** PK Wide (original, `tools/build_font.py`) carries display headings, Titillium Web carries reading text, and JetBrains Mono carries utility labels.
- **Surfaces:** calm graphite anodised plates with a soft key light, a light chamfer edge on top and a dark one below, and a pointer-driven sheen. Carbon weave is reserved for data panels. Radii: 28px plates, pill controls.
- **Assets:**
  - Pranav's photo: supplied by Pranav, upscaled 4× with Real-ESRGAN, then background removed with BiRefNet-portrait.
  - Car: original procedural Blender model.
  - Logos: official marks, with sources listed in `assets/logos/SOURCES.md`. Sponsors are substantially larger on the car. `assets/logos/pk-monogram.svg` is an original, forward-leaning PK identity, shared by the navigation and livery.
  - Portrait card: Pranav's original supplied photograph with a CSS holographic treatment. The operator dropped the generated suit-and-helmet concept entirely; do not reintroduce it.
- **Signature interaction:** keep the large five paired start lights, rolling-wheel car arrival, full-page wind-tunnel flow and speed-driven HUD. The start-light sequence runs on request, after the hero is already usable. Mouse movement deflects the airflow on desktop. The wind tunnel is always present: no off switch, including on mobile. The shift strip and language bars progress green, yellow, red; the paired start lights remain red. The brand and livery remain black and electric blue.
- **Scope boundary (operator clarification):** refine the existing design, do not replace its identity or layout concept. No mint/white livery. Improve logo scale, personal identity, materials, typography, spacing and performance.
- **Additional interaction:** direct drag-to-rotate car, arrow keys and Home, active-section navigation, reading progress, and a pointer-tilting holographic driver card. No view buttons or PK–07/status/hint bar. Touch gestures preserve vertical scrolling; reduced motion removes autonomous movement and card tilt.
- **Lighting:** broad lateral reflections and soft material transitions keep the car readable. The page canvas stays near-black to match the deployed site; blue and white flow supplies the atmosphere without a warm page wash.
- **Airflow contract:** start after font metrics settle, independently of below-fold images and the 3D car. Show clear air on each load, then fill softly from the left once; scroll and resize do not restart the fill. Use a neutral fallback gradient with no baked text or car silhouette. The flow follows the car silhouette when it becomes available and bends around a moving fine pointer; touch does not inject an obstacle. Pure scroll motion reprojects the field without adding physical car velocity. Use fixed-step pressure-projected 2D flow, midpoint-advection tracers and solid silhouette exclusion. Light the smoke without ray-cast shadows from the cursor, text, or car. This is a stylized 2D approximation, not engineering CFD.
- **Contact:** keep “Box, box.” and a clear LinkedIn action in an open layout. No Team Radio box, waveform, channel readout or recording indicator.
- **Performance target:** aim for 60 FPS through demand-rendered car frames, geometry batching, bounded DPR, lower fluid tiers, adaptive simulation quality, no metallic-card WebGL layer, and a static photographic portrait asset. Never claim universal 60 FPS; distinguish local browser emulation from physical-device evidence.
- **Small screens:** nav links collapse, leaving the brand and centered segmented HUD. The car is framed larger above the name and turns through 360° with a full-width horizontal touch; vertical gestures scroll. The holographic card tilts on horizontal touch. Stats stack in two columns, then one. The smoke simulation runs at a lower resolution and solver rate while retaining dense tracers.
- **Platform rules (Apple Human Interface Guidelines):** the site honours the settings people already chose.
  - *Writing:* navigation and kickers use plain labels (About, Experience, Skills, Education, Contact); the F1 voice lives in the headlines. Buttons are title-case verbs.
  - *Controls:* at least 44 × 44 pt, a press state on every control, a visible focus ring. Content that isn't interactive never reacts like a control.
  - *Motion:* optional and cancellable. Any scroll, tap or key lands the intro; Pause Motion and Reduce Motion stop every loop, reveal and hover movement.
  - *Text size:* reading text in `rem` (Dynamic Type on iOS through `-apple-system-body`); display headings capped by column width so no word clips at 200%. Minimum 11 pt on phones.
  - *Contrast:* WCAG AA over the live smoke; `prefers-contrast: more` and `prefers-reduced-transparency` get opaque, high-contrast variants.
  - *Layout:* a scroll edge effect under the nav rather than a hard bar; safe-area margins (`viewport-fit=cover`); dark only, as for immersive media.
