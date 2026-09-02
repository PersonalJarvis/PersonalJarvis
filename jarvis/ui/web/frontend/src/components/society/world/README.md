# components/society/world — the island (M3, in progress)

The 3D world of the Jarvis Agents section. Built in the maintainer's own
session; sibling sessions own `../card/`, `../data.ts` and `scripts/figures/`.

Binding design: `docs/agent-society/world-masterplan-v2.md` (look, kit, market, behaviour) over
`world-art-direction.md` (layout, camera, palette) under `docs/agent-society/MASTERPLAN.md` §4.

| File | Role |
|---|---|
| `islandLayout.ts` | THE model: grid constants, deterministic terrain, village ring, places, trees, A\* pathfinding. Pure, tested. |
| `worldCamera.ts` | Pitch/yaw/zoom steps and screen↔ground math. Pure, tested. |
| `walkerKinematics.ts` | character-pipeline.md §6 as functions (heading, turning, arrival). Pure, tested. The figure pipeline's F3 step plugs its GLB `<Figure>` in here without changing these. |
| `wander.ts` | Rest-biased idle model (zero tokens). Pure, tested. |
| `terrainGeometry.ts` | Tile map → one vertex-coloured mesh. Pure three, tested. |
| `worldPalette.ts` | The world's own colours (§4.3). Nothing inside the canvas reads a theme token. |
| `cameraStore.ts` · `useWorldControls.ts` · `WorldCameraRig.tsx` | Target/zoom store, DOM navigation, the R3F camera driver. |
| `WorldComposer.tsx` · `worldSettings.ts` | Scaled render + bloom (smooth default) or `RenderPixelatedPass` (grain option / cheap-GPU mode). |
| `SunRig.tsx` · `Clouds.tsx` · `Shadowed.tsx` | The sun with a view-following shadow camera, shadow-casting clouds, the cast/receive marker. |
| `Terrain.tsx` · `Village.tsx` · `Landmarks.tsx` · `Trees.tsx` | The scene, all primitives, shared through `WorldKit.tsx`. |
| `Walkers.tsx` · `WalkerFigure.tsx` · `walkerRegistry.ts` | Agents on foot: state machine, stand-in figure, minimap pins. |
| `PlaceLabels.tsx` · `WorldHud.tsx` · `Minimap.tsx` · `world.css` | DOM over the canvas in the world's type. |
| `WorldStage.tsx` | The composition; mounted by `views/JarvisAgentsView.tsx` behind the World / Ledger switch. |

Strings live in the lazy `society` locale chunk (`src/i18n/locales/society/`).
