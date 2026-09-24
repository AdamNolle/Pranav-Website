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
- **Type:** PK Wide (original, `tools/build_font.py`) for display race lettering, Titillium Web for reading, JetBrains Mono for utility labels and tabular numbers.
- **Surfaces:** calm graphite anodised plates with a soft key light, a light chamfer edge on top and a dark one below, and a pointer-driven sheen. Carbon weave is reserved for data panels. Radii: 28px plates, pill controls.
- **Assets:**
  - Pranav's photo: supplied by Pranav, upscaled 4× with Real-ESRGAN, then background removed with BiRefNet-portrait.
  - Car: original procedural Blender model.
  - Logos: official marks, with sources listed in `assets/logos/SOURCES.md`.
- **Signature interaction:** lights out → the car drives in and brakes, the name flies in, and a gust of smoke pushes through the tunnel. Scroll speed drives the HUD and the smoke velocity. Under reduced motion the car is already parked and the smoke is still.
- **Small screens:** nav links collapse, leaving the brand and HUD. The car is framed larger above the name. Stats stack in two columns, then one. The smoke simulation runs at a lower resolution with fewer particles.
- **Platform rules (Apple Human Interface Guidelines):** the site honours the settings people already chose.
  - *Writing:* navigation and kickers use plain labels (About, Experience, Skills, Education, Contact); the F1 voice lives in the headlines. Buttons are title-case verbs.
  - *Controls:* at least 44 × 44 pt, a press state on every control, a visible focus ring. Content that isn't interactive never reacts like a control.
  - *Motion:* optional and cancellable. Any scroll, tap or key lands the intro; Pause Motion and Reduce Motion stop every loop, reveal and hover movement.
  - *Text size:* reading text in `rem` (Dynamic Type on iOS through `-apple-system-body`); display headings capped by column width so no word clips at 200%. Minimum 11 pt on phones.
  - *Contrast:* WCAG AA over the live smoke; `prefers-contrast: more` and `prefers-reduced-transparency` get opaque, high-contrast variants.
  - *Layout:* a scroll edge effect under the nav rather than a hard bar; safe-area margins (`viewport-fit=cover`); dark only, as for immersive media.
