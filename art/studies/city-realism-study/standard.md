# Future city standard

Superseded on 2026-09-10 by `../mars-base-study/brief.md` after visual rejection.
Retained as history; it does not govern the new map's size or transit geometry.

Accepted direction: 2026-09-10. This supersedes island assumptions and shortened-journey proposals in the earlier research brief and kickoff plan. It is a design/behavior specification, not a record of implementation completion or approval of a rendered reference.

## Shape, material and interface language

Use continuous city districts with broad canopies, diagonal structural members, glazed halls, elevated walkways, planted terraces and selective warm-white light strips. No mandatory coastline or island plateau. Concrete #B8B5AC, graphite #252C32, aluminium #AAB4BC, glass #708B95 and vegetation #60714D establish the base palette. Distinguish surfaces with physically based roughness and metalness; preserve legibility with inexpensive lighting profiles.

The five districts are Central (gold), Terminal (cyan), Knowledge (blue), Workshop (orange), and Communications (turquoise). Match silhouette and symbols to function, rather than relying on color alone. Use ordinary sans-serif labels and restrained selection markers. All controls and overlays follow the app's light/dark tokens. World time is independent of app appearance.

## Space and interaction

One world unit is one metre. Reference district: 600 by 400 m. First city envelope: 1800 by 1200 m. Authored districts and transport corridors are preplanned. Building adjustments use suitable plots on a four-metre grid, with footprint and reachable-entrance validation. Free road/rail construction is outside the initial release. Stable semantic place identities are independent of coordinates.

Camera: continuous perspective zoom, full orbit, street inspection and agent following. Render distant districts with cheaper geometry and load detailed modules by proximity. Physical navigation uses explicit ground, platform and elevated nodes; overlapping X/Z positions do not connect different levels.

## Travel and activity

Journeys are fully physical. Route selection includes access walks, waiting and vehicle travel. Walking, waiting, boarding, riding, exiting and arrival are separate stages. Assign capacity before boarding. A target change on a vehicle changes the onward journey at a reachable stop; it never moves the agent instantaneously. Display actual work state independently. Tool execution never awaits an animation, route or train. Coalesce obsolete intents rather than replaying every historical activity location.

Hidden views stop drawing but retain physical simulation or faithfully catch it up. Reduced motion freezes visual travel and the day cycle while leaving real tasks functional. Normal day cycle: 30 minutes, starting in the morning, with pause and manual time selection. Night remains navigable.

## Assets and compatibility

Blender is the editable source for models and animation. Export modular GLBs through the study manifest. Preserve original or appropriately licensed provenance. Fantasy/cartoon bases develop future roles, while animals and Gigi retain identity. Existing recipes/imports remain supported; material-profile and layout migrations must be explicit and versioned before production replacement.

## Acceptance and rollout

The first reference joins two stations, one train, two functional destinations, an elevated crossing, a representative figure and vegetation. Review in the actual renderer and in motion. A technical pass is not visual approval. Obtain approval of this specific reference before expanding asset families. Track all 95 existing GLBs, procedural families and new assets separately.

Performance target: at least 30 FPS at 1366 by 768 with 30 visible agents on documented integrated graphics. A discrete-GPU observation does not satisfy that target. Record device, viewport, visible population, render settings and measurement method. Required cases include crossing levels, capacity, missed departure, retarget/cancellation, unreachable destinations, equipment fitting, saved adjustments, context recovery and fallback. Unverified acceptance remains pending.
