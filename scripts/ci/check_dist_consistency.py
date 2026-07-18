#!/usr/bin/env python3
"""Gate: the shipped frontend bundle must be self-consistent IN GIT.

The bug class (cloud review 2026-07-18): a frontend rebuild rewrites
``dist/index.html`` to point at freshly-hashed chunks while the chunks
themselves exist only on disk, never ``git add``-ed. On the builder's
machine everything works (the files ARE on disk), so nothing looks wrong
locally -- but every clone / fresh install 404s the entry bundle and
boots to a permanently-blank UI behind the splash spinner. Classic AP-23
("works on my machine" is the defect): the repo ships ``dist/`` on
purpose (see the ``!jarvis/ui/web/dist/**`` carve-out in .gitignore), so
a half-committed bundle IS a broken product for everyone else.

The check therefore reads every blob from GIT (HEAD, or the staged index
with ``--staged``), never from the working tree, and walks the chunk
graph: starting at ``dist/index.html``, every hashed asset reference
must resolve to a git-tracked file under ``dist/assets/``, transitively
through the referenced JS/CSS chunks (dynamic imports, font urls).

Exit codes:
  0 -- consistent (or nothing to check: dist not tracked / not touched).
  1 -- CONFIRMED: at least one referenced chunk is not tracked by git.
  3 -- could not check (no git, unreadable blob, ...). The hooks treat
       this as fail-open so tooling noise never wedges a commit; CI is
       the backstop.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import deque

DIST = "jarvis/ui/web/dist"
ENTRY_HTML = f"{DIST}/index.html"
ASSETS_PREFIX = f"{DIST}/assets/"

# A Vite-hashed asset name: <stem>-<8-char base64url hash>.<ext>. Both
# patterns demand a quote/paren/attribute delimiter before the path so a
# random hash-shaped token inside minified code or a shiki grammar can
# never produce a false block (this gate must not become a bottleneck).
_ASSETS_REF = re.compile(
    r"""["'(=]/?assets/([A-Za-z0-9._-]+-[A-Za-z0-9_-]{8}\.[a-z0-9]{2,5})"""
)
_RELATIVE_REF = re.compile(
    r"""["'(]\./([A-Za-z0-9._-]+-[A-Za-z0-9_-]{8}\.[a-z0-9]{2,5})"""
)
# Only these blob types are scanned for onward references.
_SCANNABLE = (".js", ".mjs", ".css", ".html")


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        check=True,
    )
    return out.stdout.decode("utf-8", errors="replace")


def _tracked_dist_files(staged: bool) -> set[str]:
    if staged:
        listing = _git("ls-files", "--cached", "--", DIST)
    else:
        listing = _git("ls-tree", "-r", "--name-only", "HEAD", "--", DIST)
    return {line.strip() for line in listing.splitlines() if line.strip()}


def _read_blob(path: str, staged: bool) -> str:
    rev = f":{path}" if staged else f"HEAD:{path}"
    return _git("show", rev)


def _refs_in(blob: str) -> set[str]:
    names = set(_ASSETS_REF.findall(blob))
    names.update(_RELATIVE_REF.findall(blob))
    return names


def main(argv: list[str]) -> int:
    staged = "--staged" in argv

    try:
        tracked = _tracked_dist_files(staged)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"check_dist_consistency: could not list tracked files ({exc})")
        return 3

    if ENTRY_HTML not in tracked:
        # Nothing shipped -> nothing to keep consistent.
        return 0

    if staged:
        # Only enforce when this commit actually touches dist/ -- an
        # unrelated commit must never be wedged by breakage another
        # session left in HEAD (the pre-push gate still catches that
        # before anything leaves the machine).
        try:
            touched = _git("diff", "--cached", "--name-only", "--", DIST)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dist_consistency: could not read staged diff ({exc})")
            return 3
        if not touched.strip():
            return 0

    missing: dict[str, str] = {}  # missing tracked path -> first referrer
    visited: set[str] = set()
    queue: deque[str] = deque([ENTRY_HTML])

    while queue:
        path = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        try:
            blob = _read_blob(path, staged)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dist_consistency: could not read {path} ({exc})")
            return 3
        for name in sorted(_refs_in(blob)):
            ref = ASSETS_PREFIX + name
            if ref not in tracked:
                missing.setdefault(ref, path)
            elif ref.endswith(_SCANNABLE):
                queue.append(ref)

    if not missing:
        state = "staged index" if staged else "HEAD"
        print(
            "check_dist_consistency: OK - all "
            f"{len(visited)} reachable bundle files tracked ({state})."
        )
        return 0

    print("check_dist_consistency: BROKEN shipped frontend bundle.")
    print(
        "  The entry chunk graph references files git does NOT track -- on a"
    )
    print(
        "  fresh clone these 404 and the UI never mounts (blank spinner):"
    )
    for ref, referrer in sorted(missing.items()):
        print(f"    MISSING {ref}   (referenced by {referrer})")
    print(
        "  Fix: commit the rebuilt bundle as ONE set -- `git add "
        f"{DIST}/index.html {ASSETS_PREFIX}` -- or rebuild it"
    )
    print(
        "  (`npm run build` in jarvis/ui/web/frontend/) and stage everything"
    )
    print("  vite emitted, then retry.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
