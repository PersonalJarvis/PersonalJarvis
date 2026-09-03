# components/society/world — the island (M3, in progress)

The 3D world of the Jarvis Agents section. Built in the maintainer's own
session; sibling sessions own `../card/`, `../data.ts` and `scripts/figures/`.

Binding design: `docs/agent-society/world-masterplan-v2.md` (look, kit, market, behaviour) over
`world-art-direction.md` (layout, camera, palette) under `docs/agent-society/MASTERPLAN.md` §4.

| File | Role |
|---|---|
| `islandLayout.ts` | THE model: grid constants, the height field and its biomes, graded roads, village ring, places, trees, boulders, A\* pathfinding. Pure, tested. |
| `worldCamera.ts` | Pitch/yaw/zoom steps and screen↔ground math. Pure, tested. |
| `walkerKinematics.ts` | character-pipeline.md §6 as functions (heading, turning, arrival). Pure, tested. The figure pipeline's F3 step plugs its GLB `<Figure>` in here without changing these. |
| `wander.ts` | Rest-biased idle model (zero tokens). Pure, tested. |
| `terrainGeometry.ts` | Tile map → one vertex-coloured mesh. Pure three, tested. |
| `worldPalette.ts` | The world's own colours (§4.3). Nothing inside the canvas reads a theme token. |
| `cameraStore.ts` · `useWorldControls.ts` · `WorldCameraRig.tsx` | Target/zoom store, DOM navigation, the R3F camera driver. |
| `WorldComposer.tsx` · `worldSettings.ts` | Scaled render + bloom (smooth default) or `RenderPixelatedPass` (grain option / cheap-GPU mode). |
| `SunRig.tsx` · `Clouds.tsx` · `Shadowed.tsx` | The sun with a view-following shadow camera, shadow-casting clouds, the cast/receive marker. |
| `Terrain.tsx` · `Village.tsx` · `Landmarks.tsx` · `Trees.tsx` | The scene: the terrain mesh and the shore-driven water shader, the village with the hub on its podium, the landmarks, every tree, palm, pine and boulder — all primitives, shared through `WorldKit.tsx`. |
| `Walkers.tsx` · `WalkerFigure.tsx` · `walkerRegistry.ts` | Agents on foot: state machine, stand-in figure, minimap pins. |
| `retirement.ts` · `retirementRoute.ts` · `retireStore.ts` · `RetirementScene.tsx` | Deleting an agent, as a scene: the lead walks up, shoots it, and two bearers carry the body to the mine and tip it in. Curves and route are pure and tested; the scene drives the existing walkers through a mutable pose map and commits the `DELETE` only when the body lands. |
| `PlaceLabels.tsx` · `WorldHud.tsx` · `Minimap.tsx` · `world.css` | DOM over the canvas in the world's type. |
| `WorldStage.tsx` | The composition; mounted by `views/JarvisAgentsView.tsx` behind the World / Ledger switch. |

Strings live in the lazy `society` locale chunk (`src/i18n/locales/society/`).

## Retiring an agent

Deleting a row is a ceremony on the island, not a figure blinking out. It is
started from the model card's options rail (`../card/AgentCardOverlay.tsx`),
which archives the row FIRST — so a refusal is an error the person sees — and
only then hands the island a `commit` that refreshes the roster once the body
is in the mine. The roster's refetch interval pauses for the duration, or the
figure being carried would vanish mid-scene.

Two cuts keep it to about twenty seconds on a 500 m island: the lead walks on
`MARCH_APPROACH_M` from its target rather than crossing the map, and the
stretcher crew jumps forward along its own route once it has set off, with the
camera flying after it. Both are film cuts along a route the figures were
already walking — never a teleport somewhere else.

If the ceremony cannot run — reduced motion, no WebGL, the viewer is on the
ledger, an unreachable body — the agent is retired anyway. Pressing Retire must
never depend on a working canvas; `retireStore.ts` holds the watchdogs that
guarantee it.
