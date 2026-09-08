"""Scaffold and check an isolated game-art study; never publish production assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"character", "building", "prop", "terrain"}


def local_file(root: Path, value: str) -> Path:
    """Study paths are portable and cannot escape through traversal or symlinks."""
    if not value or "\\" in value or ":" in value or Path(value).is_absolute():
        raise ValueError(f"expected a study-relative path: {value!r}")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"path escapes the study: {value!r}")
    return path


def read_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("assets"), list):
        raise ValueError("expected art study schema 1 with an assets list")
    seen = set()
    exports = set()
    for asset in data["assets"]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", asset.get("id", "")):
            raise ValueError("asset IDs must be lowercase slugs")
        if asset["id"] in seen or asset.get("kind") not in KINDS:
            raise ValueError("asset IDs must be unique and kinds must be known")
        seen.add(asset["id"])
        for key, suffix in (("source", ".blend"), ("export", ".glb")):
            target = local_file(path.parent, asset.get(key, ""))
            if target.suffix.lower() != suffix:
                raise ValueError(f"{asset['id']}: {key} must be {suffix}")
        export = local_file(path.parent, asset["export"])
        if export in exports:
            raise ValueError("each asset needs its own review export path")
        exports.add(export)
        if not isinstance(asset.get("collection"), str) or not asset["collection"].strip():
            raise ValueError(f"{asset['id']}: name the export collection")
    return data


def init_study(slug: str, studies: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("study ID must be a lowercase slug")
    parent = (studies or ROOT / "art/studies").resolve()
    folder = parent / slug
    # Refuse to overwrite an existing study, including an existing empty directory.
    folder.mkdir(parents=True, exist_ok=False)
    for name in ("source", "exports", "evidence"):
        (folder / name).mkdir()
    manifest = {
        "schema": 1,
        "study": slug,
        "brief": "brief.md",
        "assets": [],
        "runtime_evidence": [],
        "technical_report": "",
        "approval": {"status": "pending", "record": "review.md", "evidence_sha256": ""},
    }
    (folder / "study.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (folder / "brief.md").write_text(
        "# Art study brief\n\n"
        "Record the user intent, visual references and their usage rights.\n\n"
        "## Decisions to establish\n\n"
        "- Audience, mood, shape language, palette and materials.\n"
        "- Camera, pixel treatment and normal viewing distance.\n"
        "- Reference scope: one character, one functional building and connecting ground.\n"
        "- Target devices, viewport and measurable runtime budgets.\n"
        "- Readability, animation, collision and accessibility acceptance criteria.\n\n"
        "No visual direction or engine migration has been approved by this scaffold.\n",
        encoding="utf-8",
    )
    (folder / "review.md").write_text(
        "# Reference review\n\nDecision: pending.\n\n"
        "Separate visual judgement from technical results. Record defects and revisions.\n"
        "After actual user approval, record its date, scope and an English summary here;\n"
        "keep private conversation content and personal identifiers outside the repository.\n"
        "A passing check or an agent's own opinion is not user approval.\n",
        encoding="utf-8",
    )
    return folder / "study.json"


def glb_report(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) < 20:
        raise ValueError("truncated GLB")
    magic, version, length, size, kind = struct.unpack_from("<IIIII", raw)
    if magic != 0x46546C67 or version != 2 or length != len(raw) or kind != 0x4E4F534A:
        raise ValueError("invalid GLB 2 container")
    if size > len(raw) - 20:
        raise ValueError("truncated GLB JSON")
    doc = json.loads(raw[20 : 20 + size])
    if not doc.get("meshes"):
        raise ValueError("reference asset contains no mesh")
    accessors = doc.get("accessors", [])
    for mesh in doc["meshes"]:
        if not mesh.get("primitives"):
            raise ValueError("mesh contains no primitives")
        for primitive in mesh["primitives"]:
            position = primitive.get("attributes", {}).get("POSITION")
            if not isinstance(position, int) or not 0 <= position < len(accessors):
                raise ValueError("mesh is missing a position accessor")
            if accessors[position].get("type") != "VEC3" or accessors[position].get("count", 0) < 3:
                raise ValueError("mesh has no usable position data")
    for entry in doc.get("buffers", []) + doc.get("images", []):
        if "uri" in entry:
            raise ValueError("embed buffers and textures in the review GLB")
    return {
        "bytes": len(raw),
        "meshes": len(doc["meshes"]),
        "materials": len(doc.get("materials", [])),
        "animations": [a.get("name", "") for a in doc.get("animations", [])],
    }


def reference_files(path: Path, data: dict) -> list[str]:
    names = [data.get("brief", "")]
    for asset in data["assets"]:
        names.extend([asset["source"], asset["export"]])
    evidence = data.get("runtime_evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(n, str) for n in evidence):
        raise ValueError("runtime_evidence must list study-relative files")
    names.extend(evidence)
    names.append(data.get("technical_report", ""))
    for name in names:
        if not local_file(path.parent, name).is_file():
            raise ValueError(f"missing review artifact: {name}")
    return sorted(set(names))


def reference_hash(path: Path, data: dict) -> str:
    # Approval itself is excluded; all reviewed inputs and evidence are included.
    manifest = {k: v for k, v in data.items() if k != "approval"}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    for name in reference_files(path, data):
        digest.update(name.encode())
        digest.update(hashlib.sha256(local_file(path.parent, name).read_bytes()).digest())
    return digest.hexdigest()


def check(path: Path, stage: str = "draft") -> dict:
    path = path.resolve()
    data = read_manifest(path)
    if stage == "draft":
        return {"stage": stage, "assets": len(data["assets"]), "ready_to_roll_out": False}
    if not data["assets"] or not data.get("runtime_evidence"):
        raise ValueError("a reference needs real assets and evidence from the target runtime")
    reference_files(path, data)
    reports = {a["id"]: glb_report(local_file(path.parent, a["export"])) for a in data["assets"]}
    fingerprint = reference_hash(path, data)
    if stage == "ready":
        approval = data.get("approval", {})
        record = local_file(path.parent, approval.get("record", ""))
        if approval.get("status") != "approved" or not record.is_file():
            raise ValueError("record the user's reference-scene approval before rollout")
        if not re.search(
            r"^Decision: approved\.?$", record.read_text(encoding="utf-8"), re.M | re.I
        ):
            raise ValueError("approval record must state 'Decision: approved'")
        if approval.get("evidence_sha256") != fingerprint:
            raise ValueError("approval is stale: the reviewed assets or evidence changed")
    return {
        "stage": stage,
        "assets": reports,
        "evidence_sha256": fingerprint,
        "ready_to_roll_out": stage == "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("slug")
    validate = commands.add_parser("check")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--stage", choices=["draft", "reference", "ready"], default="draft")
    args = parser.parse_args()
    try:
        result = (
            str(init_study(args.slug))
            if args.command == "init"
            else check(args.manifest, args.stage)
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"art pipeline: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
