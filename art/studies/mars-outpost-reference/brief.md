# Mars Communications Outpost reference

Stage: authored Outpost reference awaiting runtime integration and review.
Runtime approval: pending. Scope: RUB-69, RUB-71, RUB-79 and RUB-80.
An editable architecture source and review GLB exist; they are not finished
runtime acceptance or gameplay evidence. See `source/README.md`.

## Intent and reference hierarchy

Build a realistic science-fiction Mars colony for users exploring and operating
their actual Jarvis agents. Establish the new integrated colony foundation
first; finish the Communications Outpost at its eastern canonical location
before any other district. The rest of the colony may be explicitly unfinished
blockouts. Neither rejected world prototype supplies the new art direction.

Use `references/manifest.json`. The proposed operational layout is `ref-layout`,
a complete overview preserving the northern Foundry and the eastern Outpost.
`ref-overview-alt` is a distinct complete interpretation, not a duplicate.
District references refine appearance without moving atlas landmarks.
`ref-outpost` governs the recognizable ensemble: irregular elevated rock,
central mast and blue beacons, two prominent dishes, white radomes, support
buildings, service road, and bridge from the colony. `ref-street` governs detail
at human height: material variation, real glazing/interior depth, paving,
lights, planting, rover construction and restrained printed branding.

Shape language: engineered off-white metal shells, dark structural frames,
purposeful joints and access panels against layered orange-red rock. Use warm
practical lights and sparse blue communications accents. Give weathering a
physical cause; protect entrances, roads and silhouettes from clutter. Use PBR
materials and actual geometry for silhouette detail. No pixel/cartoon treatment,
flat colony texture, camera-facing facade, or effects hiding unfinished meshes.

## Discrepancies and unresolved inputs

- The atlas includes a southern Outpost connection as well as the colony-side
  approach. The hero emphasizes one west bridge. Preserve atlas connectivity;
  resolve its engineered junction in the foundation rather than inventing a
  new district location.
- The street image makes Control Room a dome and depicts Mine on a high ridge;
  the central/industrial references differ. Use the atlas for location and the
  relevant district image for function and silhouette. The street image does
  not redefine the world plan.
- Gigi's front concept is corrected, but the side body and logo-detail tile
  retain a loop emblem. Omit that emblem on every body surface. Headers do not
  reverse the removal instruction. Gigi does not decide all worker identities.
- No separate original mascot, verified vector/high-resolution Jarvis mark,
  Outpost rear/interior views or reference redistribution records were present
  in the inspected pack. Keep production branding unresolved until a genuine
  source or reviewed reconstruction exists. Design unseen geometry coherently
  and identify it as authored interpretation.
- Captions, tiny atlas diagrams and decorative slogans are poster overlays,
  not instructions or required 3D objects. Build useful navigation HUD separately.

## Proposed scale sheet and access

These are operational starting dimensions adopted with the foundation owner,
not measured reference dimensions or user visual approval. The parent-owned
`jarvis/society/mars/definition.json` is the layout authority; this study does not
create a second world registry. Collision, navigation, anchors and visuals must
use that shared definition/version. Detail and access proposals below remain
subject to gameplay checks and runtime review.

| Element | Proposed starting value | Resolution owner |
| --- | --- | --- |
| World units | 1 unit = 1 metre; Y up, X east, negative Z north | Foundation lead |
| Colony extent | Approximately 900 by 850 m | Foundation lead |
| Outpost plateau | Approximately 120 by 95 m, irregular perimeter | Foundation and environment owners |
| Mast | Approximately 62 m above the plateau | Environment owner, runtime composition review |
| Bridge | 7 m carriageway plus separated 1.5 m walks each side | Navigation and rover owners |
| Service entrance | 2.4 m clear width, 3 m clear height | Gameplay and environment owners |
| Worker scale | 1.75 m provisional clearance envelope | Character owner; visual direction pending |
| Rover scale | 4.8 by 2.4 m provisional envelope | Rover owner; travel mode pending |
| Near inspection | Target 0.5-1 m from selected detail | Art review; not an infinite-detail promise |

Foundation alignment: Hub centre `[0, 48, 0]`; Outpost centre `[320, 58, 50]`.
The 62 m mast height is above the Outpost plateau, not absolute elevation.
Foundry, Mine, Workshop, Water Extraction, Memory House, Harbor Gate, Gardens
and Solar Field use the foundation's shared placements and remain blockouts
until the Outpost reference gate permits their visual-family production.

| Outpost area | Access contract |
| --- | --- |
| Bridge and exterior service loop | Required continuous player/agent route; safe separated travel and railings |
| Main service entrance and station room | Required usable door, traversable interior and backend-backed workstation |
| Rover boarding bay | Required safe approach, boarding and collision-safe exit locations |
| Plateau rear and side service routes | Required coherent geometry and declared traversable paths; deliberate edge hazards |
| Mast upper levels and maintenance roof | Access scope unresolved; do not advertise access or assume decorative exclusion |
| Dish mechanisms, radomes and remaining support interiors | Access classification unresolved before final Outpost review |
| Other colony buildings and levels | Register before family rollout; no implied full interiors or silently inaccessible advertised areas |

## Camera, quality and evidence proposals

The study supports overview, continuous horizontal orbit, follow and player
inspection. Fit overview from actual bounds and aspect ratio so the Foundry
and mast stay visible. Save reference-facing, rear, left, right, overhead,
street/entrance and neutral-light presets from one world. View angles are
inspection evidence, not a fixed hero-camera restriction.

D01 economy 1080p/30 FPS and stronger-client 60 FPS are candidate goals only.
Actual named CPU/GPU/RAM/OS/browser/WebView profiles, viewport/render scale,
frame-time percentiles, visible rigs, scene triangles, draw calls, texture/GPU
memory and load size remain unmeasured. Establish per-asset and scene budgets
with the rendering owner before production; do not copy arbitrary limits.
Light/dark controls, terminal focus, reduced motion and critical status/stop
must remain usable independent of 3D rendering.

D05 worker direction and D08 route-driven ride versus direct driving require
resolution. A representative new rigged character and meaningful rover travel
are required in the completed reference. Route animation must not invent task
success or block unrelated backend work. D02 remote hosting, D03 operational
objectives, D04 recovery limits, D09 concurrency and D10 delivery budgets remain
with the integration decision register; this brief adopts none by implication.

## Production and review boundary

Retain separate editable Blender sources and reproducible recipes for the
building ensemble, terrain, representative character and rover. Blender 5.0.1
was found in the authoring environment; exporter/loader compatibility still
needs a real sample. Sources and review GLBs remain in this study. End users
receive packaged runtime assets without Blender or private reference inputs.

Check scale, axes, ground contact, rigs, proxies, embedded resources and LOD
silhouettes. Then verify actual runtime collision/navigation, interior/station
use, movement transitions and measured performance. Record front/rear/sides/
top/near screenshots and motion in intended and neutral light, plus the
build/asset/world fingerprint. No offline render or enhanced screenshot is
runtime evidence.

The user must approve that specific finished Outpost evidence before further
visual-family production. No agent-written record or technical pass provides
consent. Independent backend, tooling and test work may continue meanwhile.
