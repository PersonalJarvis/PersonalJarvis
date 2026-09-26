"""Validate native release assets and write the signed manifest input."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ASSETS = (
    "PersonalJarvis-Setup-x64.exe",
    "PersonalJarvis-macOS-arm64.dmg",
    "PersonalJarvis-macOS-x64.dmg",
    "PersonalJarvis-Linux-x86_64.AppImage",
    "personal-jarvis-src.tar.gz",
)
TARGETS = ("windows-x64", "macos-arm64", "macos-x64", "linux-x86_64")
TARGET_ASSETS = dict(zip(TARGETS, ASSETS[:4], strict=True))


def _version_tuple(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", tag)
    if match is None:
        raise ValueError(f"invalid release tag: {tag!r}")
    return tuple(int(part) for part in match.groups())


def prepare(directory: Path, tag: str, commit: str) -> None:
    """Fail if any required asset/proof is absent or incomplete."""
    candidate_version = _version_tuple(tag)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full lowercase Git SHA")
    lines = [f"# release: {tag}\n"]
    asset_hashes: dict[str, str] = {}
    for name in ASSETS:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty release asset: {name}")
        with path.open("rb") as asset:
            digest = hashlib.file_digest(asset, "sha256").hexdigest()
        asset_hashes[name] = digest
        lines.append(f"{digest}  {name}\n")

    proofs: dict[str, dict[str, object]] = {}
    for target in TARGETS:
        path = directory / f"proof-{target}.json"
        if not path.is_file():
            raise ValueError(f"missing native smoke proof: {target}")
        proof = json.loads(path.read_text(encoding="utf-8"))
        if proof.get("target") != target or proof.get("tag") != tag:
            raise ValueError(f"wrong native smoke proof identity: {target}")
        if (
            proof.get("commit") != commit
            or proof.get("asset_sha256") != asset_hashes[TARGET_ASSETS[target]]
        ):
            raise ValueError(f"native proof is not bound to commit and artifact: {target}")
        for flag in (
            "installed",
            "upgraded",
            "rollback_tested",
            "settings_preserved",
            "database_preserved",
            "credential_preserved",
        ):
            if proof.get(flag) is not True:
                raise ValueError(f"native proof lacks {flag}=true: {target}")
        previous_tag = proof.get("previous_tag")
        if not isinstance(previous_tag, str):
            raise ValueError(f"native proof lacks a prior version: {target}")
        try:
            previous_version = _version_tuple(previous_tag)
        except ValueError as exc:
            raise ValueError(f"native proof lacks a prior version: {target}") from exc
        if previous_version >= candidate_version:
            raise ValueError(f"native proof prior version is not older: {target}")
        if target == "windows-x64" and proof.get("signed") is not True:
            raise ValueError("Windows installer signature is unverified")
        if target.startswith("macos-") and proof.get("notarized") is not True:
            raise ValueError(f"macOS notarization is unverified: {target}")
        proofs[target] = proof

    qualification = {
        "schema": 1,
        "tag": tag,
        "commit": commit,
        "asset_sha256": asset_hashes,
        "targets": proofs,
    }
    qualification_path = directory / "release-qualification.json"
    qualification_path.write_text(
        json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    qualification_hash = hashlib.sha256(qualification_path.read_bytes()).hexdigest()
    lines.append(f"{qualification_hash}  release-qualification.json\n")
    (directory / "installers-SHA256SUMS.txt").write_bytes("".join(lines).encode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    prepare(args.directory, args.tag, args.commit)


if __name__ == "__main__":
    main()
