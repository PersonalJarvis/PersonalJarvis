# Gigi usage and migration audit

Status: reference implementation in progress; no visual-family rollout approved.
The source inventory is captured separately in `usage-paths.txt`.

| Surface | Source and generated uses | Migration boundary |
| --- | --- | --- |
| 3D spirit recipe | `scripts/figures/gigi_builder.py`, `sources/gigi-mascot.json`, figure `contract.json`, `build_figures.py`, frontend `figures/catalog.json`, `figureRegistry.ts`, `assembleFigure.ts`, `assets/society/figures/spirit-gigi.glb` | Preserve existing `spirit/gigi` recipe IDs and user selections. Companion uses an independent asset, not the lead entity. Legacy presentation replacement needs the visual gate. |
| Society lead | `jarvis/society/runtime.py` lead avatar and frontend `society/data.ts` default spirit recipe | Do not delete or recreate lead, missions or saved configuration. Worker presentation is RUB-81; Gigi presence is separate. |
| Deck 3D | `components/deck/room/GigiFigure.tsx` | Replace SVG extrusion only after the new reference passes review; preserve existing press callback and resource lifecycle. |
| Animated 2D mascot | `MascotGigi.tsx`, `JarvisOrb.tsx`, `DeckOrb.tsx`, `JarvisDock.tsx`, onboarding, home greeting, overlays | Current pixel details and monochrome material must be migrated to the new simplified shape; keep all functional handlers. |
| App mark | `GigiMark.tsx`, `src/assets/jarvis-mark.png`, sidebar, top bar, composer, share card | Preserve imported fingerprinted URL; do not replace with a fixed cached public path. |
| Native icons | `scripts/make_gigi_app_icon.py`, `assets/icons/jarvis-gigi-256.png`, public PNG/ICO, `jarvis/assets/__init__.py`, `jarvis/ui/overlay_styles.py` | New icon generator must replace old geometry sources as well as outputs. Check actual 16/24/32/48 sizes and native monochrome variants. |
| Splash and favicon | frontend `index.html`, `public/jarvis-gigi.ico`, public PNG and built `dist` | Rebuild from approved sources; stage only this work's generated bundle in coordinated landing. |
| Style rules | `docs/BRAND.md`, frontend `index.css`, overlay styles | Gigi-specific black/white mandate conflicts with new direction. Update that exception during approved rollout; retain neutral UI and independent wordmark rules. |
| Website | `personaljarvisweb/src/components/mark/gigi.ts`, `TurningMark.tsx` | Read nested instructions before editing. Replacement delivery does not prove external publication. |
| Marketing/history | `wiki-video/public/gigi.svg`, `wiki-video/src/components/Ghost.tsx`, `assets/brand/social-preview.html`, `video/public/jarvis-gigi.png`, `videos/agent-mode-launch/assets/gigi.png` | Determine active template vs completed production. Preserve historical recordings/screenshots; supply reviewed replacements for active templates. |
| Regression checks | `tests/test_app_icon.py`, mascot tests, figure registry tests, onboarding/deck/sidebar tests | Update shape assertions with approved source; retain behavior/identity checks. |

## Initial findings

The existing spirit model is explicitly a pixel-ghost extrusion; its source
marks are white. The native icon generator deliberately derives from the same
old mascot geometry. Replacing only a PNG or GLB would be reverted by the next
asset build. The app mark already uses Vite fingerprinting correctly.

The new Mars runtime currently has a capsule player and unfinished Outpost
reference. Actual final-worker, rover and station docking acceptance cannot be
claimed from that foundation. Gigi work continues independently while those
world contracts are completed by their owner.
