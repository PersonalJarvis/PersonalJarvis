# Mars base: terrain before architecture

T1: isolated art study and restoration of an existing view; no backend contract change.

## Authorized direction

The future-city prototype was visually rejected. Restore the original world while
developing a compact, interactive Mars base separately. Remove the rejected city
runtime; retain authored models and sources for selective reuse. A city grid, an
island outline and a mandatory ring railway are no longer requirements. Build the
map before producing another asset family.

The supplied reference shows a warm rocky basin with ridges, overlapping levels,
clustered habitats, glazed biospheres and lit connections. Use these spatial
qualities. It is a mood reference, not a licensed texture, scientific reconstruction
or promise of equivalent rendering on integrated graphics. Do not copy branding
or slogans from the reference.

## Map proposal, version 1

- One metre per unit; proposed 650 by 490 m operational envelope with a tighter
  inhabited core and low-detail terrain beyond bounded camera navigation.
- A western inhabited terrace, eastern service terrace and northern ridge. A
  ravine remains open underneath a bridge. Distant ridges establish depth without
  a repeated building field or a planetary-scale world.
- Seven reserved sites: command, compute, habitat, research, workshop,
  communications and logistics. Site elevations range from 2 to 26 m. Labels and
  plain platforms are blockout markers; no new building or character models yet.
- Ground approaches are graded around flat building pads. A bridge uses a separate
  elevation; a switchback reaches communications. `layout.json` is the common
  spatial source for nodes, heights, reserved pads and ground/bridge links.
- Rust soil, dark mineral outcrops and dust bands provide terrain readability.
  Later architecture adds pale metal, graphite structure, glass and warm lighting.
  Vegetation belongs in protected biospheres and enclosed courtyards.
- The normal camera frames the inhabited core at roughly 160–220 m distance;
  street inspection uses 6–35 m. A wider overview is a separate action. Planned
  camera limits include terrain clearance, so the first view does not repeat the
  city prototype's distant view of tiny workspaces.

## Interaction proof

Existing work events, agent IDs and figure recipes remain authoritative. A visual
location never delays a real command. Habitat and utility roles are proposed world
functions, not an implemented life-support simulation.

First runtime proof: one agent travels from Command to Compute, reaches a reserved
workspace, shows real work state and returns. Second: it crosses the ravine and
climbs the switchback without floating, tunnelling or joining different heights.
Doors and airlocks receive interaction anchors when their geometry exists.

Choose transport after measuring journeys. Reserve logistics access for a physically
moving rover or shuttle; inspect the existing train for possible component reuse.
Do not inherit the oversized city ring or shortened travel animations. No teleporting.

## Ordered delivery and acceptance

1. Restore World and Ledger, migrate saved City preferences, remove the city bundle.
2. Develop editable terrain, reserved sites and connected paths. Inspect overhead
   and human-height views. Check reachability, grades, bridge clearance, camera
   bounds and route lengths. Blockout marker geometry is not finished architecture.
3. Verify actual agent movement in an isolated runtime preview. Use the same height
   source for drawing and movement. Record screenshots and motion evidence.
4. Build one small finished Blender reference using selected existing or CC0 parts.
   Review it in the renderer before expanding the asset family.
5. Add remaining functions and transport one tested connection at a time. Add assets
   only where the map needs them, with individual visual inspection.

The original world remains the default through steps 2–4. This study does not claim
production Mars movement, final architecture or asset-family conversion. A terrain
render is not runtime approval. No app restart is needed for view restoration.

## Engineering targets, not measurements

At least 30 FPS at 1366 by 768 with 30 visible agents on documented integrated
graphics. Start with local terrain, vertex colours and one bounded shadow region;
no mandatory remote terrain service. Initial terrain allocation: 100,000 triangles,
3 materials, excluding later architecture. Measure before setting asset budgets.

Retain WebGL fallback, context recovery, light/dark UI, reduced-motion and hidden
view behaviour. See `research.md` for evaluated sources; none is automatically added
as a runtime dependency.
