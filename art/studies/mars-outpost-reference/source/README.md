# Communications Outpost authored source

Status: first authored reference; runtime integration and visual review pending.
The study remains `approval.status: pending` and cannot pass the reference gate
without actual runtime evidence. It has not entered the production catalog.

## Rebuild and export

Run from the repository root with Blender 5.0.1 available in the authoring
environment. The command name below denotes its installed executable; Blender
is not a game-server or end-user dependency.

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/build_outpost_reference.py
blender --background --factory-startup --disable-autoexec --python scripts/art/build_outpost_reference.py -- --validate-source
blender --background --factory-startup --disable-autoexec --python scripts/art/export_study.py -- --manifest art/studies/mars-outpost-reference/study.json --asset communications-outpost-reference
python scripts/art/build_outpost_reference.py -- --validate-export art/studies/mars-outpost-reference/exports/communications-outpost.glb
python scripts/art_pipeline.py check art/studies/mars-outpost-reference/study.json
```

The recipe reads only the canonical `jarvis/society/mars/definition.json` and
the study manifest. It does not read private concept PNGs, old world assets,
third-party textures or a local profile path. All geometry, materials and
wayfinding text in this source are newly authored by the recipe for this
project. No third-party model or copied concept-image logo is embedded.
Private reference redistribution remains unauthorized.

`communications-outpost.blend` retains 1,215 individually named editable source
objects in architectural collections. They are hidden in the default view;
unhide `Editable architecture` and hide `Export` to edit them without drawing
both copies. The `Export` collection contains evaluated geometry batched by
material, plus collider/anchor empties. Changing source objects requires
rebatching through the authoring workflow before exporting; the existing
manifest exporter exports the named `Export` collection only.

The surface maps in `materials/` are newly authored procedural normal and roughness maps, retained with the source and embedded in the GLB. Metre-scaled UVs and material-aware vertex tinting carry painted-alloy, mineral, dust and metal variation into the runtime. This adds delivery size; it is not a measured load-time or fidelity result.

The procedural seed and shared placements are deterministic. Blender source
metadata and exporter versions may change bytes; compare geometry contracts
and actual hashes rather than promising byte-identical `.blend`/GLB output.
Rebuilding refuses an already approved study so a new unreviewed artifact
cannot silently overwrite approved evidence.

## Coordinate and integration contract

- The exported origin is the Outpost plateau top. Runtime units are metres,
  Y-up, X-east and negative Z-north. Place the GLB at `[320, 58, 50]` once.
  Source conversion is `(x,y,z) -> (x,-z,y)` into Blender; `export_yup=True`
  returns the runtime convention.
- The canonical operations building is centred at `[-26, 0, 14]`, with a
  20 by 14 m footprint. Its south entrance is at `[-26, 0, 21]`; jambs leave
  2.4 m clear width and the lintel 3 m clear height. Door panels are visibly
  parked open. The desk is north of the clear console anchor.
- The bridge deck top interpolates the exact local graph endpoints
  `[-195, -10, -10]` to `[-64, 0, 0]`, 10 m wide. Remove duplicate coarse
  Outpost/route-01 visual geometry when integrating this study asset.
- `geometry-contract.json` contains the canonical definition hash, layout
  version, placements, colliders and anchors. GLB empties repeat each record
  in `extras.mars_contract` as JSON; `extras.mars_kind` distinguishes
  `collider` and `anchor`. The JSON coordinates remain **asset-local runtime
  coordinates**, regardless of the empty's transform. Do not apply the
  empty translation to those JSON coordinates a second time.
- Collider records describe box solids, a graded bridge deck, and a plateau
  footprint/top/bottom. They are integration data, not self-executing physics.
  Runtime collision/navigation owners must consume or deliberately map them.
  The plateau footprint is a reference boundary, not a navigation mesh.
- `communications-console`, `console-display`, `operations-door`, navigation
  endpoints and reserved branding slots are stable anchors. Blank screens
  convey no invented task status. Backend authority supplies real state.
- Dishes, radomes, mast and support interiors remain exterior-only in this
  asset. The operations room is the usable interior. Roof/maintenance access
  is unadvertised and unresolved. No character, rover, rig or animation is
  included until the respective direction and control decisions resolve.

## Measured export and remaining checks

Second reference export: 20 meshes/material batches, 211,968 triangles, 14,571,224 bytes;
target was fewer than 300,000 triangles. These are asset counts, not an FPS or
quality result. Inspect the current `export-report.json` for the exact hash,
bounds and material extensions. The GLB embeds its buffer and requires no
external resource URI; it uses transmission, IOR and emissive-strength glTF
material extensions. Actual loader behavior still needs runtime verification.

The generic exporter verified a structurally valid GLB with position data and
embedded resources. Geometry tests check closed outward plateau/dish shells,
coordinate handedness, graded bridge endpoints and the real door opening.
Neither proves traversal, legibility, lighting, near-view fidelity, collision
alignment, performance, or user visual acceptance in the actual application.
No offline beauty render has been recorded as runtime evidence.

## Portable editable-source metadata

Image paths become study-relative before packing. The authoring save also
sanitizes each packed image's separate filepath snapshot and clears the saved
file-browser directory and render-output default. These serialized fields can
retain an authoring machine's profile path even when `Image.filepath` appears
relative. Fixed-size path arrays are fully overwritten through Blender's RNA
properties before assigning their neutral relative values.

`--validate-source` reopens the actual saved source with its workspace data and
checks 38 portable path fields without logging their rejected values. Windows
native separators after Blender's `//` prefix are normalized for validation;
absolute and network-share paths remain rejected. All 18 authored material
images remain packed and the editable geometry remains intact.

The current `.blend` is saved uncompressed so byte inspection can verify the
serialized content directly. `public-source-report.json` records the field
classes, marker-absence checks and successful reopen. The sanitized source
reproduces the same GLB hash and numeric material data; sanitization changes
neither the model nor the runtime appearance.

Numeric material validation reads actual accessor buffers with their strides and offsets. It rejects nonfinite or out-of-range linear colors, mismatched counts, invalid embedded-buffer bounds and invalid normal directions. The source also reads back assigned UVs and colors before saving. Blender custom-data layer handles are reacquired after all layers are allocated; retaining a UV handle across color-layer allocation was reproduced to overwrite color channels with UV coordinates.
