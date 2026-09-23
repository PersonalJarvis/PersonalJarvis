# Agent profile companions

Use the seven existing, accepted flat profile shapes as small companions that
follow the independently configurable agent character. Profile and companion
share shape, colour and eyes. Preserve character recipes, imported bodies and
wardrobe choices. Gigi keeps the existing graphite-and-gold first-party identity.

The bodies retain the SVG silhouettes with a modest rounded extrusion, plain
PBR materials, two eyes and no human facial features. Blender is the editable
mesh source. No image model, external texture, provider request or task/audio
authority is introduced. The runtime target is the current Three.js map.

Runtime size is fixed at 0.50 metres, with a standard 1 metre following distance. Each pet
uses the existing canvas and a cached model; only its materials and transform
belong to the instance. Follow actual route breadcrumbs around corners. Freeze
on stale/hidden presentation and retain existing context recovery. Reduced
motion removes the small walking bob, while deliberate following remains usable.

Rebuild authoring input with `node scripts/art/capture_companion_shapes.mjs`,
then `python scripts/art/sample_companion_outlines.py`. Run
`scripts/art/build_agent_companions.py` inside background Blender. The manifest
exporter can regenerate the GLB from `source/companions.blend`; its named export
collection is `Collection`. The Gigi master uses `GigiExport`.

The seven-symbol GLB targets less than 1 MB, no external dependencies, and at
most five visible meshes per instance. Check normal and close map views, the
editor, small profile sizes, both application themes and path turns. Record
measured evidence rather than asserting performance from file size.

This is one matching companion family. It does not approve redesigns of the
map, characters, buildings or vehicles. Further unrelated art-family rollout
retains the project's reference-review gate.
