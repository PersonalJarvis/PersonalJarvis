# Mars base: source evaluation

Checked 2026-09-10 against author/project sources. Research only: none of these
projects or asset packs has been imported into the application in this step.

| Source | Verified terms and scope | Decision for this base |
| --- | --- | --- |
| [MarsInterloper](https://github.com/ProgramComputer/MarsInterloper) | Three.js exploration prototype with Go backend; [BSD-3-Clause code](https://github.com/ProgramComputer/MarsInterloper/blob/main/LICENSE). README separately credits CC-BY 4.0 Sketchfab assets and NASA/USGS/ESA data. | Study terrain/interaction ideas. Do not fork the whole runtime or assume media shares the code license. |
| [NASA-AMMOS 3DTilesRendererJS](https://github.com/NASA-AMMOS/3DTilesRendererJS) | Apache-2.0 renderer with Three.js/R3F support and a Mars example. [EnvironmentControls API](https://github.com/NASA-AMMOS/3DTilesRendererJS/blob/master/src/three/renderer/API.md) includes terrain-aware camera controls. | Candidate if streaming or camera clearance needs it. Local terrain is simpler initially. It does not provide our agent event or navigation integration. |
| [Kenney Space Kit](https://kenney.nl/assets/space-kit) | Author-declared CC0, 150 model files; [author distribution](https://kenney-assets.itch.io/space-kit). | Later infrastructure/vehicle components. Review the simplified style rather than importing the whole pack. |
| [Quaternius Ultimate Space Kit](https://quaternius.com/packs/ultimatespacekit.html) | Author-declared CC0, 92 models, animated elements, Blend/glTF/FBX/OBJ. | Editable equipment and figure candidates after map proof. Preserve figure recipes and attachment contracts. |
| [Agent Mars paper](https://arxiv.org/abs/2602.13291) and [author repository](https://github.com/ziyangwang007/AgentMars) | Base-operations research; repository inspected with README only, no reusable runtime or software license verified. | Conceptual reference only. Do not depend on unavailable implementation. |

These are useful parts, not a verified drop-in Mars world for Jarvis. Retain Three.js
and existing agent events, establish one authored spatial model, then assess imports.
Keep provenance and exact license alongside each eventual download. The supplied
picture informs composition rather than redistribution.

## Existing work worth retaining

The original 95 assets and saved recipes remain intact. The rejected city study
contains editable Blender source, individual GLBs and inventories. Screens, seating,
vehicle parts, materials and vegetation can be inspected for reuse. The city layout
and station offsets are rejected; they do not set Mars geometry. Retired simulation
code remains recoverable from Git history as a technical reference.

## Spatial consequences

Functions should be identifiable by placement and silhouette: an inhabited biosphere
on a sheltered terrace, computation near command, maintenance beside logistics and
communications on a ridge. Ground circulation comes first. Domes, airlocks, detailed
terrain materials and rover bodywork follow the map; a model kit must not dictate it.
