# Local validation — 2026-09-24

`npm test`, `git diff --check`, and JavaScript syntax checks passed. The browser suite used headless Brave through Playwright on macOS.

## Frame samples

The suite sampled `requestAnimationFrame` for 2.5 seconds at rest and while scrolling. Desktop and phone emulation both held 60 FPS with no frame interval over 33 ms. The parked car drew zero frames. The scroll-stop probe moved the car 480 px in viewport space and measured zero false physical car velocity in the airflow.

| Full-suite profile | Tracers | Fluid steps | Display frames at rest / while scrolling |
| --- | ---: | ---: | ---: |
| Desktop 1440×1000 | 42,000 | 60 Hz | 60 / 60 FPS |
| Phone emulation 390×844, DPR 2 | 20,000 | 30 Hz | 60 / 60 FPS |

The adaptive tier can step down as needed; this desktop pass selected its 42,000 tracer high tier. Save Data and 2G select 4,000 tracers and retain the responsive car poster. The glow composite uses one filtered texture read in place of four, and the uniform light no longer needs a texture fetch.

With a 2 Mbps / 150 ms connection and 4× CPU throttle, a 390 px DPR 2 pass measured poster LCP at 0.88 s, airflow start at 1.09 s, wind ready at 1.74 s, and interactive car ready at 7.90 s. The page `load` event was at 3.43 s, so below-fold images did not delay airflow. The Draco decoder and car GLB began transfer together at 2.49 s. Only one 33 KB mobile poster and the 2.7 KB original PK Wide font were transferred for the hero. The tunnel starts clear and fills from the left in about 2.6 s after its first frame. These emulated timings vary with host load and do not guarantee performance on physical phones.

## Automated coverage

- Layout at 320–2560 px, landscape, 200% text, and no JavaScript.
- Mouse, touch, and keyboard car rotation, including a full 360° phone drag and native vertical touch scrolling.
- Manual start lights, rolling wheels that stop when parked, centered mobile speed gauge, mobile card touch tilt, navigation, and portrait card mouse tilt.
- Mouse-driven wind disturbance, a visible cursor opening in the smoke mask, and fade, with touch and paused/reduced motion excluded; scroll-stop car-velocity regression.
- Motion pause, reduced motion, saved preference, and visible wind while paused.
- Early wind startup, single responsive poster download, Save Data poster and lean wind tier.
- Failed model and WebGL context loss/restoration fallbacks.
- Image loading and no browser console/page errors.

Screenshots and raw frame samples are in the ignored `tests/artifacts/` directory. The public page uses the original PK Wide display font.
