"""Portable Blender authoring metadata, including fixed-size path-field tails.

Follows the Outpost source sanitizer contract without importing its evolving
geometry builder. No Blender import is needed to validate saved source bytes.
"""

from __future__ import annotations

import re


def source_path_records(bpy_module):
    records = []
    for image in bpy_module.data.images:
        if not image.filepath:
            continue
        if not image.packed_files:
            raise ValueError("file-backed authoring images must be packed")
        records.append(("image", image.filepath))
        records.extend(("image", packed.filepath) for packed in image.packed_files)
    for screen in bpy_module.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params is not None:
                    records.append(("directory", space.params.directory))
                    records.append(("filename", space.params.filename))
    records.extend(("render", scene.render.filepath) for scene in bpy_module.data.scenes)
    return records


def validate_source_path_records(records):
    for field, value in records:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        portable = value.replace("\\", "/") if value.startswith("//") else value
        if field == "image":
            valid = bool(re.fullmatch(r"//materials/[a-z0-9]+(?:-[a-z0-9]+)*\.png", portable))
        elif field == "directory":
            valid = value == "//"
        elif field == "filename":
            valid = value == ""
        elif field == "render":
            valid = portable == "//renders/"
        else:
            raise ValueError("unknown source path field class")
        if not valid:
            raise ValueError(f"nonportable source metadata in {field}")
    return {"validated_fields": len(records)}


def sanitize_source_metadata(bpy_module):
    def replace(owner, field, value):
        # Assigning a short string alone leaves previous path bytes after NUL.
        maximum = owner.bl_rna.properties[field].length_max
        neutral = "_" * (maximum - 1)
        setattr(owner, field, neutral.encode("ascii") if isinstance(value, bytes) else neutral)
        setattr(owner, field, value)

    for image in bpy_module.data.images:
        if not image.filepath:
            continue
        if not image.packed_files:
            raise ValueError("cannot sanitize an unpacked source image")
        portable = "//materials/" + image.filepath.replace("\\", "/").rsplit("/", 1)[-1]
        validate_source_path_records([("image", portable)])
        replace(image, "filepath", portable)
        for packed in image.packed_files:
            replace(packed, "filepath", portable)
    for screen in bpy_module.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "FILE_BROWSER" and space.params is not None:
                    replace(space.params, "directory", b"//")
                    replace(space.params, "filename", "")
    for scene in bpy_module.data.scenes:
        replace(scene.render, "filepath", "//renders/")
    return validate_source_path_records(source_path_records(bpy_module))


def validate_saved_source(raw: bytes):
    """Fail closed for compressed sources: never scan compressed bytes as plaintext."""
    if not raw.startswith(b"BLENDER"):
        raise ValueError("source must be saved uncompressed for full-content privacy validation")
    patterns = (
        rb"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]",
        rb"/(?:Users|home)/[A-Za-z0-9_.-]+/",
    )
    if any(re.search(pattern, raw, re.IGNORECASE) for pattern in patterns):
        raise ValueError("saved source contains local profile metadata")
    return {"format": "uncompressed Blender", "validated_bytes": len(raw)}
