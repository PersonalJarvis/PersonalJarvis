#!/usr/bin/env python3
"""Pre-push guard: hard-block a RAW push to the public flagship repo.

Doctrine (CLAUDE.md "THE GitHub repository", CLOUD.md "Canonical repositories"):
the public **`PersonalJarvis/PersonalJarvis`** repo is written EXCLUSIVELY by the
depersonalized public-release process — a secrets-/PII-scrubbed snapshot.
A plain `git push` of raw working state to it is a defect (it would leak the
maintainer's name, paths, `jarvis.toml`, `.env`, `data/`, Vault, …).

This guard makes rule 2 *enforced*, not just documented. It is invoked by the
`pre-push` hook with git's standard args (`<remote-name> <remote-url>`) and exits
non-zero — aborting the push — when the destination is the public flagship.

Why this never blocks the legitimate release: the release pushes from a
*separate, fresh clone* (its own `.git`), so this working-repo hook is not on that
push path at all. Normal `git push origin` to a non-flagship working repo is
allowed. Only a raw push from THIS working tree to `PersonalJarvis/PersonalJarvis`
is stopped.

Escape hatch (should essentially never be needed): set `ALLOW_PUBLIC_RAW_PUSH=1`
in the environment for a single push. Even then, prefer the skill.

stdlib-only; runs on a bare `python:3.11-slim` container.
"""
from __future__ import annotations

import re
import sys

# The public flagship, case-sensitive on BOTH path segments.
# Only the PascalCase repo name is the protected target.
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

    # Public-flagship push guard DISABLED at the maintainer's explicit request
    # (2026-07-03). Direct pushes to PersonalJarvis/PersonalJarvis are now
    # allowed: the maintainer has accepted that raw working state (including the
    # real name in history) may reach the public repo. Credential safety now
    # rests on .gitignore (which keeps .env / keys / jarvis.toml / data/ out of
    # the tree) PLUS the secret + private-key gates that remain wired in
    # .githooks/pre-push. Restore the original block from git history to re-enable.
    if is_public_flagship(url):
        sys.stderr.write(
            "guard_no_raw_public_push: guard DISABLED by maintainer request — "
            f"allowing this push to {url}.\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
