# Editable master and export recipe

Author with Blender 5.0.1 (the exact build is in `build-report.json`):

```text
blender --background --factory-startup --disable-autoexec --python scripts/art/build_gigi_companion.py
blender --background --factory-startup --disable-autoexec --python scripts/art/export_study.py -- --manifest art/studies/gigi-hover-companion/study.json --asset gigi-companion
```

Run at the repository root. Keep `gigi.blend`, the builder and manifest together.
`GigiExport` owns the model; studio lights/camera stay outside that collection.
The editable eyes include blink animation sources; runtime expression control
uses the named eye/mouth/arm hierarchy. It never runs a walking animation.

The exported material recipe uses standard glTF metallic/roughness and emissive
values, without external textures. Surface detail and additional authored
expressions remain review work. No paid generation service or third-party mesh
is required to rebuild. Imported user concept art is a reference only.

Runtime loader names are sanitized by Three.js; original `Gigi.*` names remain
in `object.userData.name` and are used for expression binding.
