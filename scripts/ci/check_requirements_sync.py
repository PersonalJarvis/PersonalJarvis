#!/usr/bin/env python3
"""Fail-closed guard: requirements.in must mirror pyproject [project].dependencies.

This is the guard for the bug class that shipped a broken public installer:
``requirements.in`` (the source for the hash-pinned ``requirements.txt``
lockfile) drifted from ``pyproject.toml [project].dependencies``. It listed
extras-only, torch-pulling packages (``silero-vad``, ``sentence-transformers``,
``pvporcupine`` …) that pyproject deliberately keeps in the ``[local-voice]`` /
``[desktop]`` extras per the cloud-first doctrine (CLAUDE.md §3). ``pip-compile``
then baked the multi-GB, GPU-specific CUDA-13 wheel stack (``nvidia-*``) into the
committed lockfile, and the Windows/macOS installer forced every downloader
through it with ``pip install --require-hashes -r requirements.txt`` — which is
unresolvable on a plain-PyPI machine (``nvidia-cufile==1.15.1.6`` does not exist
on PyPI). "Works on my machine" is the defect (AP-23).

``requirements.in`` is the base runtime set ONLY. Every line in it must appear,
byte-for-byte on name + extras + version specifier, in
``pyproject.toml [project].dependencies`` and vice-versa. Extras
(``[project.optional-dependencies]``) are intentionally NOT mirrored here — they
are installed separately and never enter the base lockfile.

Exit 0 when in lockstep; exit 1 with a diff otherwise.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - degrade gracefully when packaging is absent
    # Fail-open when the guard genuinely cannot run (e.g. a deps-less venv).
    # Layer-3 CI installs `packaging` explicitly, so it is the backstop.
    print("SKIP: `packaging` not importable; requirements-sync guard cannot run here.")
    sys.exit(0)

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
REQUIREMENTS_IN = REPO_ROOT / "requirements.in"


def _canonical(spec: str) -> tuple[str, frozenset[str], str]:
    """Normalize a requirement to (name, extras, specifier) for comparison."""
    req = Requirement(spec)
    name = req.name.lower().replace("_", "-")
    extras = frozenset(e.lower() for e in req.extras)
    # Sort specifier components so ">=1,<2" and "<2,>=1" compare equal.
    specifier = ",".join(sorted(str(s) for s in req.specifier))
    return (name, extras, specifier)


def _parse_pyproject() -> dict[tuple[str, frozenset[str], str], str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    return {_canonical(d): d for d in deps}


def _parse_requirements_in() -> dict[tuple[str, frozenset[str], str], str]:
    out: dict[tuple[str, frozenset[str], str], str] = {}
    for raw in REQUIREMENTS_IN.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        out[_canonical(line)] = line
    return out


def main() -> int:
    pyproject = _parse_pyproject()
    req_in = _parse_requirements_in()

    only_in_pyproject = sorted(pyproject.keys() - req_in.keys())
    only_in_reqin = sorted(req_in.keys() - pyproject.keys())

    if not only_in_pyproject and not only_in_reqin:
        print("OK: requirements.in is in lockstep with pyproject.toml [project].dependencies")
        return 0

    print("FAIL: requirements.in has drifted from pyproject.toml [project].dependencies.")
    print("      They must mirror each other exactly (name + extras + version).")
    print("      After fixing, regenerate the lockfile:")
    print("        pip-compile --allow-unsafe --generate-hashes "
          "--output-file=requirements.txt requirements.in")
    print()
    if only_in_pyproject:
        print("  In pyproject.toml but MISSING from requirements.in:")
        for key in only_in_pyproject:
            print(f"    + {pyproject[key]}")
    if only_in_reqin:
        print("  In requirements.in but NOT a pyproject base dependency")
        print("  (is it an extras-only package that belongs in "
              "[project.optional-dependencies]?):")
        for key in only_in_reqin:
            print(f"    - {req_in[key]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
