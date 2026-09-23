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
- **Type:** Anybody italic/wide for display (race lettering), Barlow for reading, JetBrains Mono for utility labels and tabular numbers.
- **Surfaces:** anodised brushed-aluminium plates with a real directional brush texture, a light chamfer edge on top and a dark one below, and a pointer-driven specular highlight. Carbon weave is reserved for data panels. Radii: 28px plates, pill controls.
- **Assets:**
  - Pranav's photo: supplied by Pranav, upscaled 4× with Real-ESRGAN, then background removed with BiRefNet-portrait.
  - Car: original procedural Blender model.
  - Logos: official marks, with sources listed in `assets/logos/SOURCES.md`.
- **Signature interaction:** lights out → the car drives in and brakes, the name flies in, and a gust of smoke pushes through the tunnel. Scroll speed drives the HUD and the smoke velocity. Under reduced motion the car is already parked and the smoke is still.
- **Small screens:** nav links collapse, leaving the brand and HUD. The car is framed larger above the name. Stats stack in two columns, then one. The smoke simulation runs at a lower resolution with fewer particles.
