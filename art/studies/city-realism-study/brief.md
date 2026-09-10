# City realism: research and asset migration brief

Date: 2026-09-09. Stage: brief and references. Tier: T1 research artifact only.

Implementation kickoff for 2026-09-10: [work packages, acceptance matrix and start prompt](implementation-plan.md).
The accepted [future city standard](standard.md) now requires fully physical journeys and takes precedence over earlier pacing experiments in this research record. Implementation status is recorded in [the review](evidence/implementation-review.md).

## Updated user direction: a future metropolis

This section supersedes the earlier compact island/European district proposal below. The user explicitly requires a city, no island, new spatial dimensions, strongly modern future architecture, trains and faster agent travel between work locations. The supplied screenshot is the primary visual direction; Cities: Skylines remains supporting research.

Reference: [user-supplied X video](https://x.com/cb_doge/status/2085372286735925741/video/1). The page returned HTTP 403; the video has not been watched. Observations below concern only the supplied screenshot. It depicts broad overhanging roofs, substantial diagonal structural supports, elevated pedestrian connections, layered public space, integrated trees and planting, dark structural surfaces and long warm-white light strips at dusk. Vehicle motion, transport technology and the full city extent cannot be established from the still image.

### Spatial and visual proposal

Design a continuous urban territory with districts, a skyline and multiple circulation levels. Do not carry forward the coastline, central island plateau or old map extent as design constraints. Establish dimensions in metres through building/door/person scale, station spacing and travel-time trials before choosing the final city bounds. Preserve stable functional place identities while allowing entirely new physical locations.

Create a civic/coordination district, a terminal and compute district, a knowledge/archive district and a fabrication/integration district, connected by a rail backbone. These district names and groupings are proposals. Architecture should express large-span transport halls, terraces, elevated walkways and planted public plazas. Use selective linear lighting to describe structures while retaining readable daylight and low-cost rendering profiles.

### Agent travel is part of the redesign

The current `Walkers.tsx` maps semantic checkpoints to destinations and animates A* ground paths. `walkerKinematics.ts` sets purposeful walking to 1.28 metres per second. Increasing that constant alone will not establish believable metropolitan travel.

Introduce a layered route graph: pedestrian links, accessible building entrances, station boarding anchors, rail connections and level-change links. A single ground height at an X/Z position cannot represent both a bridge deck and the route beneath it. Route nodes therefore need explicit elevation/layer identity; retain the ground grid where it remains useful for local walking.

Travel presentation should support walking, boarding, riding and exiting, with run animation for suitable short urgent trips. Select travel by estimated end-to-end time, including the walk to a station and any wait, rather than selecting a train solely by distance. Avoid holding an agent through long simulated commutes while its actual task changes.

Actual task execution must remain independent of travel animation. Shell commands and other tools must not wait for a train or a visual arrival. The phrase "Shell Comments" is provisionally interpreted as shell commands, not a confirmed new task category. Rapid activity changes should update the current intent and coalesce obsolete journeys. While a vehicle is moving, retarget to an appropriate reachable stop; show the true task status immediately. Cancellation, completed tasks, hidden views, reduced motion and unreachable stops need explicit behavior, including an honest shortened transition where appropriate.

This is a proposed behavioral design; no transport code or backend activity contract has been changed. Verify which actual tool events are exposed before promising command-by-command routing. Introducing a shared activity or transport schema would be T3 contract work under the repository rules; the present brief is T1 only.

### Revised first playable reference

Build two station stops and two functional work destinations with one traversable rail connection, a ground route, an elevated walkway and a representative agent. Author one finished station/plaza with the screenshot's structural and lighting language. This is the minimum slice that can demonstrate the new city scale, art direction and useful transport together.

Acceptance: correct boarding and alighting, no foot sliding or train intersections with static geometry, correct navigation above and below the walkway, destination changes during travel, continued execution of real tasks during journeys, and working selection/status displays. Compare walking with total train journey time. Capture the actual runtime at district overview and pedestrian scale, in day and dusk lighting, and report measured performance with device and agent count. Expand districts and asset families only after approval of this specific runtime reference, as required by the art-production standard.

Next action: establish the layered blockout, scale/travel budgets and isolated runtime preview for this two-stop district. The previously proposed standalone workshop street no longer covers the requested reference scope.

## Outcome and scope

The requested transformation is feasible as an authored asset and rendering project in the existing Three.js/WebView renderer. A filter change alone cannot establish the intended look. Use Cities: Skylines II as the working reference for contemporary architecture and natural human proportions. The user named the franchise, not a specific installment; choosing II and a compact European-influenced district is a proposal, not approved art direction.

Research and inventory are complete for the bundled GLB set. No replacement models, Blender reference, runtime redesign, movement proof or performance measurements have been produced. Approval remains pending. Atlas was not exposed as an available browser tool; research used web search, image search and official source pages. Two official reference images were downloaded outside the repository and visually inspected. Other linked images are supplementary references, not independently reviewed runtime evidence.

## Reference board and observations

### R1: Architecture and the street edge

![Official mixed-use street reference](https://images.ctfassets.net/u73tyf0fa8v1/6hxkmLpCK8YFmCE7JdecpV/2475f2f3e512f2de35d227928b9c34b8/4_Mixed_use.png)

[Paradox: Zones & Signature Buildings, 2023-07-10](https://www.paradoxinteractive.com/games/cities-skylines-ii/features/zones-signature-buildings)

Observed: repeated window bays, recessed shop fronts, balconies, muted masonry, warm windows, curbs and street markings establish believable scale. Buildings form a street edge rather than isolated display objects. The figure-to-door relationship matters as much as texture resolution. Transfer this visual logic to original assets and keep entrances readable from the normal map view.

Additional official image: [low-rise office district](https://images.ctfassets.net/u73tyf0fa8v1/7ce0Miss5al15xNO9D7wbg/e0f081ef65da634c185bc6e28f0a20dd/8_LD_offices.png?fm=webp&q=75&w=1920). Use this as the architectural reference for functional service buildings, rather than assuming every hub must become a skyscraper.

### R2: Characters

![Official citizen reference](https://colossalorder.fi/_astro/1-Hanging-around.uwkbz9s-_Z1PNIyW.webp)

[Colossal Order: Citizen Characters, 2023-10-21](https://colossalorder.fi/news/behind-the-scenes-5-citizen-characters/)

Observed: natural limb and head proportions, layered everyday clothing and varied silhouettes; color remains useful for identity. The developer describes modular clothing and body variation with compatible animation. Our proposal is to preserve agent identity through outfit and silhouette, with restrained role accents. Close-up face detail should be justified by the actual viewer size. The referenced character-generation software is not a dependency recommendation.

### R3: Lighting and landscape

[Paradox: Upcoming Visual Updates, 2026-01-29](https://www.paradoxinteractive.com/games/cities-skylines-ii/news/upcoming-visual-updates) and [official mountain-village reference](https://images.ctfassets.net/u73tyf0fa8v1/72ho2UFXwqNuy8u36PS8w9/c20acf1f740b500092636d94b52af1d9/Copy_of_Mountain_new.webp?fm=webp&q=75&w=1920).

The diary discusses sky, day/night visibility, fog and ground treatment. Proposed translation: neutral daylight, soft directional shadows, subtle distance haze, readable night exposure and material separation. Weather simulation and seasons are optional future scope; they are not prerequisites for the style migration. This diary records announced changes, not independent confirmation of the latest shipped game state.

All reference images remain third-party copyrighted material. They are research references, not textures, meshes or approved redistributable game assets. Production assets must be original or have a recorded compatible license. The report links to source images; downloaded reference copies are not committed.

## Art direction proposed for the reference

A compact contemporary district on the existing island: a civic center, service/workshop buildings and a connected waterfront, with natural vegetation outside the built area. Build architectural variety from deliberate rooflines, entrances, facade rhythm and functional equipment. Preserve existing place meanings and agent workflows.

Materials: plaster, brick, concrete, painted metal, asphalt, glass, cloth and foliage. Use physically based roughness and metalness with normal detail where it survives the actual camera distance. Avoid uniformly shiny surfaces. Suggested starting palette, not sampled game values: concrete #B8B5AC, asphalt #454A4C, masonry #8E6252, foliage #60714D and cool glass #708B95. Keep bright accents for selection, activity and identity.

Shape: remove oversized bevels, toy-like volumes and exaggerated heads from the proposed modern family. Fantasy, science-fiction, animals and the ghost mascot remain distinct identities; adapt materials and geometry coherently rather than silently deleting or turning them into ordinary humans. Exact treatment of the cartoon and spirit families remains an art decision for later family review.

Camera: establish material quality at the current orthographic overview first. A perspective inspection mode could provide a closer franchise resemblance, but requires separate control, picking, pan, zoom, minimap and fit verification. An engine replacement is not needed by this brief. Remove pixel styling from the proposed city presentation, including labels; retained viewer preferences need a deliberate migration policy.

## Verified inventory

The complete per-file ledger is [asset-inventory.csv](asset-inventory.csv). It records bytes, meshes, materials, embedded images, skins, animation names, proposed work and status from each GLB header. The catalog independently contains 26 bases and 55 parts. Counts are files, not unique visual designs or placed instances.

| Bundled family | Files | Required treatment |
|---|---:|---|
| Character bases | 26 | Remodel silhouettes, UV/material treatment, rig-compatible skinning |
| Accessories | 55 | Refit to each compatible body family; preserve attachment semantics |
| Buildings | 12 | Authored architecture with function-specific silhouettes and entrances |
| Animation libraries | 2 | Verify/retarget clips for new proportions; not decorative meshes |
| Total | 95 | Every file has a migration ledger row |

The 26 bases comprise 19 bipeds, six quadrupeds and one spirit. These include modern, fantasy, cartoon and science-fiction identities. User-imported assets and external content are outside this bundled-file inventory; preserve their files and saved recipes and verify compatibility during integration.

### Building-by-building proposal

These are proposed visual treatments, not renamed IDs or changed functions.

| Existing building | Proposed architectural expression |
|---|---|
| agent-foundry | Fabrication laboratory with a legible entry and production bay |
| control-room | Operations center with glazing and communication equipment |
| gallery-hall | Contemporary gallery with a clear public entrance |
| memory-house | Archive/library with a distinctive illuminated inner space |
| model-boilerhouse | Computing/utility plant with cooling and service equipment |
| observatory | Research observatory retaining its recognizable dome |
| plugin-docks | Waterfront integration depot with loading bays |
| relay-tower | Telecom tower and compact equipment building |
| signal-office | Communications office with antenna details |
| skill-forge | Workshop/training facility with industrial rooflights |
| terminal-cantina | Neighborhood cafe/workspace with a readable frontage |
| town-hall | Civic administration building with a public forecourt |

### Procedural assets: also in scope

These are not counted in the 95 files and cannot be replaced by swapping that directory alone.

| Source under `jarvis/ui/web/frontend/src/components/society/world/` | Families requiring review/rework |
|---|---|
| Village.tsx | Repeated houses, hub, square, hedges, lamps, festoon lighting |
| Landmarks.tsx | Workshop, harbor/boat details, lighthouse, mine, campfire, greenhouses, solar field |
| Trees.tsx | Broadleaf trees, pines, palms, boulders, reeds; retain instancing |
| Terrain.tsx, terrainGeometry.ts, islandLayout.ts | Ground, shoreline, water, graded paths/roads, plots and terrain contact |
| Clouds.tsx, SunRig.tsx | Sky/cloud appearance and shadow/light setup |
| QuestMonument.tsx, ConversationScene.tsx, RetirementScene.tsx | Scene props, markers and animated interactions |
| WorldHud.tsx, PlaceLabels.tsx, Minimap.tsx, world.css | Pixel typography, markers and map overlays |

This is a source-level family inventory, not a counted census of procedural instances. Before each family rollout, enumerate its submeshes, variants and runtime instances into the ledger. Product logos and unrelated app icons are not map-asset replacements.

## Runtime integration findings

Paths below are relative to the same world directory unless stated otherwise.

1. `KitBuilding.tsx:restyle` converts Standard materials into Toon/Basic materials. Its new Toon construction does not transfer the complete PBR surface. Merely exporting realistic GLBs will therefore lose their intended rendering. Building-card viewers reuse this path and need the same treatment.
2. `MemoryHouse.tsx` has a separate conversion path and special transparency/emission behavior. Named nodes such as `halo`, `core_orb` and `shard_*` drive motion. `AgentFoundry.tsx` also resolves animation nodes by name. Preserve those hooks or deliberately update and test their mappings.
3. `../figures/assembleFigure.ts` uses nearest filtering for palette textures and Lambert/Basic material assembly. `scripts/figures/contract.json` defines a 128px palette sheet, stable bones, slots and clips. PBR clothing detail needs a compatible tint-mask/material approach; simply enlarging that sheet will not author fabric or skin. Preserve palette customization and imported figures.
4. `worldMaterials.ts` and `WorldKit.tsx` supply the primitive world materials. They need the same reference lighting/material treatment as imported assets to avoid two incompatible styles.
5. `WorldStage.tsx` uses an orthographic camera, `flat`, DPR 1 and optional shadows. `worldSettings.ts` defaults to grain 0, internal scale 0.75, bloom off and shadows off. `WorldComposer.tsx` still supports the pixel pass. Thus the shipped default is already smooth, despite remaining pixel/toon art. Evaluate tone mapping and a full-resolution reference explicitly; honor existing performance and appearance choices through a migration decision.
6. `islandLayout.ts` ties visible geography to height and routing. Rework street materials/meshes without breaking entrances, collision or height sampling. New traffic simulation is not implied by adopting road visual language.
7. Keep figure previews, building cards, creation previews and the world consistent. Preserve WebGL context recovery, offscreen sleep, reduced motion and ledger fallback. App overlays must remain usable in light and dark appearance.

Builders requiring follow-up are `scripts/world/build_world_kit.py`, its kit modules, `scripts/figures/build_figures.py` and the humanoid/quadruped/spirit builders. Save editable Blender sources for newly authored art; procedural scripts should generate an approved kit, not substitute for visual review.

## Execution sequence and acceptance

1. Establish baseline screenshots, frame times, draw calls, visible triangles and texture memory in the actual runtime. Record device, GPU, viewport, render scale and agent count. Proposed test viewports: 1366x768 and 1920x1080; proposed agent populations: 1, 25 and 100. Target devices and budgets are not yet agreed or measured. Derive per-family limits from the baseline before modelling; do not copy a desktop game's asset budgets.
2. Build a movement blockout for one worker approaching and leaving the skill-forge entrance. Exercise corners, opposing agents, a blocked destination and ground transitions. Keep collision proxies and interaction anchors distinct from art meshes.
3. Author one finished Blender reference: skill-forge, a worker figure with one accessory, connecting street/sidewalk and a small tree/lamp set. Export inside this study and review through the actual application renderer at overview, normal and close view, with movement and both supported app appearances. No production replacement at this stage.
4. Obtain approval of that specific runtime scene. Only then derive a facade/roof/street/material kit and shared character material conventions. Preserve stable catalog IDs, bones, slots, named animation hooks and saved recipes.
5. Roll out buildings, modern bipeds/accessories, remaining character families, landscape/props and overlays in bounded groups. Apply the approved material standard to all preview surfaces. Every ledger item must become integrated, verified or explicitly deferred; no unrecorded omissions count as completion.
6. Run figure validation and fit audit where applicable, affected render/movement/navigation tests and the frontend build. Record real runtime screenshots/video and performance. Test context loss/recovery, reduced motion, offscreen sleep and no-WebGL fallback. Unavailable platform/device evidence stays unverified.

The reference must demonstrate convincing materials, consistent scale, readable function, non-sliding gait and clean ground/door contact. Functional checks include correct clicks/drawers, equipment fitting through motion, founding and memory interactions, and retirement/conversation props. Artistic review and engineering evidence are separate acceptance records.

Relevant existing commands: `python scripts/ci/check_society_figures.py`, `python scripts/figures/audit_fit.py --json`, `npm run build` from the frontend directory, and `python scripts/art_pipeline.py check art/studies/city-realism-study/study.json --stage reference` once real reference artifacts exist. These production checks have not been run for this research-only deliverable.

## Handoff

Study: `art/studies/city-realism-study/`. Current stage: researched brief with complete bundled GLB ledger and procedural family map, amended by the future-metropolis direction at the top. User direction: a large modern city with no island and faster travel including trains. No specific finished runtime reference approved. Open: measured city dimensions, device budgets, treatment of stylized character families and detailed activity-event mapping. Next action: build the layered two-stop district blockout and isolated preview described above. Earlier island-based proposals are superseded.
