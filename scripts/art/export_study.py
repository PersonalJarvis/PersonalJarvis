"""Export one named collection from a .blend source into its isolated study.

blender --background --factory-startup --disable-autoexec --python
scripts/art/export_study.py -- --manifest art/studies/example/study.json --asset example
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from art_pipeline import glb_report, local_file, read_manifest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--asset", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    manifest = args.manifest.resolve()
    studies = Path(__file__).resolve().parents[2] / "art/studies"
    if not manifest.is_relative_to(studies.resolve()):
        raise SystemExit("review exports must stay in this project's art/studies directory")
    data = read_manifest(manifest)
    asset = next((a for a in data["assets"] if a["id"] == args.asset), None)
    if asset is None:
        raise SystemExit(f"unknown study asset: {args.asset}")
    source = local_file(manifest.parent, asset["source"])
    target = local_file(manifest.parent, asset["export"])
    if not source.is_file():
        raise SystemExit("create and save the authored Blender source first")
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    collection = bpy.data.collections.get(asset["collection"])
    if collection is None:
        raise SystemExit(f"missing export collection: {asset['collection']}")
    selected = [o for o in collection.all_objects if o.name in bpy.context.view_layer.objects]
    if not any(o.type == "MESH" for o in selected):
        raise SystemExit("export collection has no mesh in the active scene")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = selected[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}-{uuid.uuid4().hex}.glb")
    try:
        bpy.ops.export_scene.gltf(
            filepath=str(temporary),
            export_format="GLB",
            use_selection=True,
            use_active_scene=True,
            export_yup=True,
            export_extras=True,
            export_animations=True,
            export_skins=True,
            export_materials="EXPORT",
        )
        report = glb_report(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Exported review asset {asset['id']}: {report}")
    print("Review export only; existing production asset gates still apply before integration.")


if __name__ == "__main__":
    main()
