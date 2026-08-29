#!/usr/bin/env python3
"""Every CLI in the seed catalog must be installable by pressing Install.

The CLI section renders an Install button from ``install_methods``, and
``cli_routes._install_methods_of`` invents a pseudo-method called ``manual``
whenever an entry declares no package manager at all — its "command" being the
project's documentation URL. The button then opens a web page instead of
installing anything, which reads as a broken button rather than as a CLI that
has to be installed by hand. That is exactly how the ``gam`` entry behaved:
Install sent the user to a GitHub wiki.

This gate fails on any seed entry that would fall into that path, and on a
``recommended`` that names a method the entry does not actually declare (which
would preselect a dead radio button in the install dialog).

A custom CLI a user registers themselves may still be manual-only — there is
nobody to fix it for them. The shipped catalog may not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "jarvis" / "clis" / "catalog" / "seed_catalog.json"

# Mirrors InstallMethods.available_methods() in jarvis/clis/spec.py. Kept as a
# plain list so the gate runs without importing the app.
METHOD_FIELDS: tuple[tuple[str, str], ...] = (
    ("winget", "winget_id"),
    ("scoop", "scoop_package"),
    ("npm", "npm_package"),
    ("pip", "pip_package"),
    ("cargo", "cargo_package"),
    ("script", "script_url"),
)


def main() -> int:
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read {CATALOG.relative_to(REPO)}: {exc}")
        return 1

    failures: list[str] = []
    for entry in catalog.get("entries", []):
        name = entry.get("name", "<unnamed>")
        install = entry.get("install") or {}
        declared = [method for method, field in METHOD_FIELDS if install.get(field)]

        if not declared:
            failures.append(
                f"  {name}: declares no install method — Install would open "
                f"{install.get('manual_url') or 'nothing'} instead of installing."
            )
            continue

        recommended = install.get("recommended")
        if recommended and recommended not in declared:
            failures.append(
                f"  {name}: recommended={recommended!r} is not among the declared "
                f"methods {declared} — the install dialog would preselect a dead option."
            )

    if failures:
        print("FAIL: seed catalog entries that cannot be installed from the UI:")
        print("\n".join(failures))
        print(
            "\nGive the entry a real install method (winget_id / scoop_package / "
            "npm_package / pip_package / cargo_package / script_url). A "
            "documentation link is not an install method."
        )
        return 1

    total = len(catalog.get("entries", []))
    print(f"OK: all {total} seed CLIs declare a runnable install method.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
