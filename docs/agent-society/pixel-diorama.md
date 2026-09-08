# Pixel diorama — rendering and movement contract

Accepted direction: 2026-09-07. This document supersedes the smooth-rendering,
free-orbit and cinematic movement decisions in the older world master plans.
The society remains a projection of agent state; this change adds no dispatch,
credential, model-provider or backend configuration behaviour.

## Visual language

The world is an orthographic pixel diorama with four designed headings (45,
135, 225 and 315 degrees), a fixed 50-degree pitch and five existing zoom stops.
The default output pixel is two screen pixels. Legacy camera links snap to the
nearest heading and fixed pitch. Legacy smooth-look preferences migrate to the
pixel setting; existing saved agent recipes retain contract version 1 and IDs.

Materials use the existing palette, baked four-value shading and a shared toon
ramp. The world renders geometry once into a nearest-filtered target, with a
small depth silhouette treatment and an output colour-space conversion. It does
not need a second normal-buffer geometry pass. Building and figure contact
shadows share one instanced batch. Static architectural blocks share two draws;
each residential building and each kit building uses merged palette geometry.

The Plugin Workshop has three removable-module silhouettes, a central entrance
and a recognisable plug sign. Each other functional building has its own roof
silhouette and front, side and back details. Doors, figures and paths remain
legible before small decorative details are added. Quiet ground colour clusters
replace per-tile noise. Nametags appear on hover, selection or active work.

## Owned asset pipeline

`world-manifest.json` is the design contract for building footprints, collision
polygons, door/stand anchors and navigation parameters. `asset-inventory.json`
records measured bounds, geometry counts and SHA-256 for the complete owned set:
26 bodies, 55 accessories, two animation libraries and twelve kit buildings.
Imported user models are outside that set and are never rewritten.

Run inside Blender 5:

```text
blender -b --python scripts/world/rebuild_diorama.py
python scripts/ci/check_world_kit.py
python scripts/figures/audit_fit.py --json
```

Blender MCP is the interactive modelling/inspection surface. The same saved
builders run without a GUI or MCP in the reproducible build. Every export is
restricted to the active scene, preventing objects selected in another Blender
scene from leaking into an asset. The figure gate accepts and validates RGB/RGBA
vertex shading without changing palette cells, skeleton names or recipe IDs.
The building gate checks metadata parity, ground contact, body-height geometry
inside collision footprints, palette colours and per-asset budgets. It rejects
stale inventories and undeclared kit files.

## Motion ownership

One `MotionWorld` advances all bodies at 30 Hz, with at most five catch-up steps.
The navigation grid is 0.5 m; collision queries use 32 m sectors. A broad route
on the terrain graph guides the fine search, widening when necessary. Expensive
searches run in one scene-owned browser worker; stale results cannot replace a
newer target or layout. Worker and pending requests are disposed on scene exit.

Every actual step sweeps a disc against building polygons, static obstacles and
other bodies. Agent scale and fitted accessories contribute to clearance. Target
slots are reserved independently of current positions. Failed routes remain
unarrived and retry; blocked movement waits and replans. A reversal turns before
translation. Animation speed follows actual travel, including stopping the walk
clip while yielding. Talking overlays animation without changing motion state.

Road-ramp vertices and foot placement use the same piecewise-triangular surface.
Non-road height discontinuities above 0.25 m are rejected. Birth and departure
use normal movement; a blocked departure completes the requested retirement in
place after its presentation timeout, without teleporting through a wall.
Building rotations reject overlapping bodies, buildings and entrances instead
of clearing blocked target cells. Old unsafe stored orientations are discarded.

## Verification

The isolated `world-lab.html` development entry renders 30 labelled sample
agents, never creates real agents and is not a production entrypoint. Start Vite
on port 5190, then run:

```text
python scripts/world/verify_diorama.py --full
```

The lab records all four headings and five zoom stops, real WebGL draw counts,
triangles, frame-time p95, body overlap checks and browser errors. The target at
1600×1000 with 30 agents is at most 300 calls, 400,000 submitted triangles and
33 ms p95. Reference hardware and actual measurements belong in the local
evidence file; the target is not a claim about every GPU.

Automated checks cover thin-wall tunnelling, corner clearance, reserved slots,
head-on motion, frame-rate independence, a stalled render frame, actual routes
to every functional building, legacy camera links, safe rotation, figure/accessory
contracts and WebGL StrictMode replay/context recovery. The previous smoothing
bug is retained as a regression case. The existing provider-independent WebGL
capability probe and ledger fallback apply equally on Windows, macOS and Linux.
