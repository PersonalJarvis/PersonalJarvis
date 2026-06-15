#!/usr/bin/env python3
"""Pre-push guard: hard-block a RAW push to the public flagship repo.

Doctrine (CLAUDE.md "THE GitHub repository", CLOUD.md "Canonical repositories"):
the public **`PersonalJarvis/PersonalJarvis`** repo is written EXCLUSIVELY by the
`ship-public-release` skill — a depersonalized, secrets-/PII-scrubbed snapshot.
A plain `git push` of raw working state to it is a defect (it would leak the
maintainer's name, paths, `jarvis.toml`, `.env`, `data/`, Vault, …).

This guard makes rule 2 *enforced*, not just documented. It is invoked by the
`pre-push` hook with git's standard args (`<remote-name> <remote-url>`) and exits
non-zero — aborting the push — when the destination is the public flagship.

Why this never blocks the legitimate release: `ship-public-release` pushes from a
*separate, fresh clone* (its own `.git`), so this working-repo hook is not on that
push path at all. Normal `git push origin` (the private `personal-jarvis` backstage)
is allowed. Only a raw push from THIS working tree to `PersonalJarvis/PersonalJarvis`
is stopped.

Escape hatch (should essentially never be needed): set `ALLOW_PUBLIC_RAW_PUSH=1`
in the environment for a single push. Even then, prefer the skill.

stdlib-only; runs on a bare `python:3.11-slim` container.
"""
from __future__ import annotations

import os
import re
import sys

# The public flagship, case-sensitive on BOTH path segments. The private backstage
# is `PersonalJarvis/personal-jarvis` (lower-case repo name) — only the PascalCase
# repo name is the protected target.
PUBLIC_OWNER = "PersonalJarvis"
PUBLIC_REPO = "PersonalJarvis"


def _owner_repo(url: str) -> tuple[str, str] | None:
    """Extract ``(owner, repo)`` from an https or ssh GitHub remote URL.

    Returns ``None`` if the URL is not a github.com remote we recognise.
    """
    u = url.strip()
    # git@github.com:Owner/Repo(.git)  |  ssh://git@github.com/Owner/Repo(.git)
    # https://github.com/Owner/Repo(.git)  |  https://x-token@github.com/Owner/Repo
    m = re.search(r"github\.com[/:]+([^/]+)/([^/]+?)(?:\.git)?/?$", u)
    if not m:
        return None
    return m.group(1), m.group(2)


def is_public_flagship(url: str) -> bool:
    """True iff ``url`` points at the case-exact public flagship repo."""
    parsed = _owner_repo(url)
    if parsed is None:
        return False
    owner, repo = parsed
    # Case-SENSITIVE: `PersonalJarvis/PersonalJarvis` is public; the lower-case
    # `…/personal-jarvis` backstage must not trip the guard.
    return owner == PUBLIC_OWNER and repo == PUBLIC_REPO


def main(argv: list[str]) -> int:
    # git invokes pre-push as: pre-push <remote-name> <remote-url>
    # We accept either order / a single arg defensively.
    candidates = [a for a in argv[1:] if a]
    url = ""
    for a in candidates:
        if "github.com" in a:
            url = a
            break
    if not url and candidates:
        url = candidates[-1]

    if not is_public_flagship(url):
        return 0  # not the flagship → allow (origin pushes, anything else)

    if os.environ.get("ALLOW_PUBLIC_RAW_PUSH") == "1":
        sys.stderr.write(
            "guard_no_raw_public_push: ALLOW_PUBLIC_RAW_PUSH=1 set - allowing a raw "
            "push to PersonalJarvis/PersonalJarvis. This is almost never correct; "
            "the depersonalized release goes through the ship-public-release skill.\n"
        )
        return 0

    sys.stderr.write(
        "\n"
        "==================================================================\n"
        " PUSH BLOCKED -- raw push to the PUBLIC flagship repo is forbidden.\n"
        "==================================================================\n"
        f"  target : PersonalJarvis/PersonalJarvis  ({url})\n"
        "\n"
        "  That repo only ever receives a DEPERSONALIZED release snapshot —\n"
        "  never raw working state (it would leak name/paths/secrets/PII).\n"
        "\n"
        "  To publish the maintainer's work, use the skill instead:\n"
        "      ship-public-release   (builds a scrubbed snapshot, scans it,\n"
        "                             shows a diff, pushes only on approval)\n"
        "\n"
        "  Day-to-day dev pushes go to:  git push origin  (the private repo).\n"
        "  See CLAUDE.md \"THE GitHub repository\" + CLOUD.md \"Canonical repositories\".\n"
        "==================================================================\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
