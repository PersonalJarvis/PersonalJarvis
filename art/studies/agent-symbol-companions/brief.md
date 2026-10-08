# Agent profile companions

Use the seven existing, accepted flat profile shapes as small companions that
follow the independently configurable agent character. Profile and companion
share shape, colour and eyes. Preserve character recipes, imported bodies and
wardrobe choices. Gigi keeps the existing graphite-and-gold first-party identity.

The bodies are real solids whose front view matches the SVG silhouette
(2026-10): circle = sphere, squircle = rounded cube, pill = capsule,
triangle = square pyramid, hexagon = six-sided crystal, cloud = fused puffs,
drop = teardrop of revolution (`scripts/art/companion_bodies.py`). Plain PBR
materials, two eyes on the curved surface and no human facial features. The
flat profile symbol draws the same volume as light: a key light from the upper
left, a gloss spot, and the lit facets of the pyramid and the crystal. Blender is the editable
mesh source. No image model, external texture, provider request or task/audio
authority is introduced. The runtime target is the current Three.js map.

Runtime size is fixed at 0.50 metres, with a standard 1 metre following distance. Each pet
uses the existing canvas and a cached model; only its materials and transform
belong to the instance. Follow actual route breadcrumbs around corners. Freeze
on stale/hidden presentation and retain existing context recovery. Reduced
motion removes the small walking bob, while deliberate following remains usable.

Rebuild authoring input with `node scripts/art/capture_companion_shapes.mjs`,
then `python scripts/art/sample_companion_outlines.py`. Run
`scripts/art/build_agent_companions.py` inside background Blender; it also
writes `source/body_depths.json`, the body surface depth under each accessory
slot. Then run `python scripts/art/companion_accessories.py` and
`scripts/art/build_companion_accessories.py` (Blender) so the catalog and the
clothing follow the bodies. The manifest
exporter can regenerate the GLB from `source/companions.blend`; its named export
collection is `Collection`. The Gigi master uses `GigiExport`.

The seven-symbol GLB targets less than 1 MB, no external dependencies, and at
most five visible meshes per instance. Check normal and close map views, the
editor, small profile sizes, both application themes and path turns. Record
measured evidence rather than asserting performance from file size.

This is one matching companion family. It does not approve redesigns of the
map, characters, buildings or vehicles. Further unrelated art-family rollout
retains the project's reference-review gate.

## Accessories (2026-10)

Wearables extend the same family without changing a body: one item per slot
(head, face, mouth, neck, outfit, back, held). `scripts/art/companion_accessories.py`
authors every item as small primitives around a per-shape slot anchor and
writes the shared catalog; `scripts/art/build_companion_accessories.py` builds
`source/accessories.blend` and `exports/accessories.glb` from it. Clothing is a
patch cut from the body surface itself and floated a few millimetres above it;
a hood is the grown body above the neckline, opened toward the face. Free
parts worn on the body (outfit, neck, face) ship per shape as
`acc_<id>__<shape>__fit`, bent onto that surface; head, mouth and back items
rest at the measured slot depth. The GLB loads only when a
companion wears something.
