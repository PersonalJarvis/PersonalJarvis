#!/usr/bin/env python3
"""Check repository-specific CI safety rules that workflow syntax lint cannot infer."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# This generator rejects SHA refs. Its signed builder identity is independently
# checked by the installer verifier; no other mutable action reference is allowed.
TAG_EXCEPTIONS = {
    "slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0"
}


def audit(workflows: dict[str, dict]) -> list[str]:
    findings = []
    for filename, workflow in workflows.items():
        if "permissions" not in workflow:
            findings.append(f"{filename}: declare default token permissions")
        for name, job in workflow.get("jobs", {}).items():
            for step in [job, *job.get("steps", [])]:
                uses = step.get("uses", "")
                if uses and not uses.startswith("./") and uses not in TAG_EXCEPTIONS:
                    if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", uses):
                        findings.append(f"{filename}/{name}: unpinned action {uses}")
    ci = workflows["ci.yml"]["jobs"]
    if set(ci) - {"gate"} != set(ci["gate"].get("needs", [])):
        findings.append("ci.yml: CI gate must depend on every job")
    gate_run = "\n".join(step.get("run", "") for step in ci["gate"]["steps"])
    if "--pipeline" not in gate_run:
        findings.append("ci.yml: CI gate must enforce expected lanes")
    release = workflows["release.yml"]["jobs"]["publish"]
    if "github.ref_type == 'tag'" not in release.get("if", "") or "admit" not in release["needs"]:
        findings.append("release.yml: PyPI publication requires explicit tag admission")
    signing = workflows["sign-installer.yml"]
    if signing["permissions"] != {"contents": "read"}:
        findings.append("sign-installer.yml: default permissions must be read-only")
    if "provenance" not in signing["jobs"]["release"]["needs"]:
        findings.append("sign-installer.yml: release upload must wait for provenance")
    for filename in ("desktop-installers.yml", "sign-installer.yml"):
        steps = workflows[filename]["jobs"]["release"]["steps"]
        if any(step.get("uses", "").startswith("softprops/action-gh-release@") for step in steps):
            findings.append(f"{filename}: producers must not change release visibility")
        commands = "\n".join(step.get("run", "") for step in steps)
        if "release_assets.py upload" not in commands or "--clobber" in commands:
            findings.append(f"{filename}: use the verified no-replacement uploader")
    provenance = signing["jobs"]["provenance"]["with"]
    if provenance.get("upload-assets") is not False or "draft-release" in provenance:
        findings.append("sign-installer.yml: provenance must only produce a workflow artifact")
    return findings


def main() -> int:
    workflows = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }
    findings = audit(workflows)
    for finding in findings:
        print(f"::error::{finding}")
    print(f"workflow-policy: {len(workflows)} workflows, {len(findings)} findings")
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
