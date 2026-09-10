# Individual asset review

Open `index.html` for the image gallery with city/legacy filters. Every entry in
`asset-inventory.csv` and every exported asset in `study.json` has its own Blender
render. Contact sheets provide an overview; PNGs remain available individually.

The legacy inventory is reference documentation of existing assets, not a claim
that all legacy figures and accessories have been redesigned. The two
animation-only GLBs have no mesh; their PNGs visualize their skeletons and are
explicitly labeled as such in `coverage.json` and the gallery.

## Reproduction

Run the authoring script in a fresh background Blender process, with embedded
scripts disabled. It saves `source/city-reference.blend` and never touches an
open Blender GUI scene.

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/build_city_reference.py
```

Export each asset ID through the existing manifest exporter. For example:

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/export_study.py -- --manifest art/studies/city-realism-study/study.json --asset train
```

Then regenerate the individual PNGs and assemble the review index:

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/render_city_asset_gallery.py -- --group all --force
python scripts/art/compose_city_asset_gallery.py
```

`coverage.json` records the original GLB path and SHA-256, rendered PNG path and
SHA-256, Blender version, geometry counts, and the visualization kind. Render
failure leaves an explicit failure entry and returns an error. The gallery
assembler rejects missing entries. Newly measured bounds use runtime X/Y/Z and
also retain Blender X/Y/Z dimensions; these coordinate systems differ.

## Runtime integration contract

- One runtime unit equals one metre. Building fronts face runtime -Z. Entrances
  are at `(0, 0, -19)`; ground-floor walking surfaces are at local Y=0.
- The Terminal contains 30 standing workstations: X `[-12,-6,0,6,12]`, Z
  `[-9,-5,-1,3,7,11]`. `Workstation_00` through `Workstation_29` are explicit
  feet anchors. Keyboards lie 0.45 m farther along +Z. Chairs are parked beside
  the desks to keep their approach routes clear. `TerminalScreen` is an
  independent material; `ScreenText` and `Cyan` contain the visible code lines.
- Other functional halls have 30 `Interaction_00` through `Interaction_29`
  anchors with the same X columns and Z `[-8,-3,2,7,12,16]`.
- Train local +X follows the track and local +Z is the platform side. Cabin
  floor is Y=0.08; the lowest wheel point is Y=-1.12. The two named door parents
  `Door_Platform_Left` and `Door_Platform_Right` slide respectively -1.25/+1.25
  metres along local X. Their child geometry is retained independently.
- `Arm_L`/`Arm_R` and `Forearm_L`/`Forearm_R` are the reference citizen's local
  shoulder/elbow pivots. Negative X rotation raises the arms toward local +Z
  for typing. Existing walking clips control the legs independently.
- Static surfaces are batched by material. Blender collection assets remain
  independent GLBs; no single combined city mesh is exported.

Technical rendering does not establish user visual approval, final artistic
quality, animation fit for every legacy character, or integrated-GPU frame rate.
These are separate runtime checks. The city kit remains in the isolated study.
