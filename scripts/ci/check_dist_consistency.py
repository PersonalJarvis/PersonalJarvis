#!/usr/bin/env python3
"""Gate: the shipped frontend bundle must be self-consistent IN GIT.

Two bug classes, one chunk-graph walk.

**Missing chunks** (cloud review 2026-07-18): a frontend rebuild rewrites
``dist/index.html`` to point at freshly-hashed chunks while the chunks
themselves exist only on disk, never ``git add``-ed. On the builder's
machine everything works (the files ARE on disk), so nothing looks wrong
locally -- but every clone / fresh install 404s the entry bundle and
boots to a permanently-blank UI behind the splash spinner. Classic AP-23
("works on my machine" is the defect): the repo ships ``dist/`` on
purpose (see the ``!jarvis/ui/web/dist/**`` carve-out in .gitignore), so
a half-committed bundle IS a broken product for everyone else.

**Orphaned chunks** (2026-10-06): ``scripts/publish-build.mjs`` keeps a
retired chunk on disk for 24 hours so an already-open window can still
lazy-load it. That grace ledger is untracked, so every fresh clone (the
merge train, every agent worktree) starts the clock again and a
``git add`` of the dist folder committed all retired chunks with the new
bundle. ``dist/assets`` grew to ~5,000 dead files (~210 MB); each copy of
a third-party chunk re-raised the same code-scanning alerts. A hashed file
under ``dist/assets/`` that nothing reachable from ``dist/index.html``
names is therefore refused too.

The check reads every blob from GIT (HEAD, or the staged index with
``--staged``), never from the working tree, and walks the chunk graph:
starting at ``dist/index.html``, every hashed asset reference must
resolve to a git-tracked file under ``dist/assets/``, transitively
through the referenced JS/CSS chunks (dynamic imports, font urls).

Modes:
  (default)          check HEAD: missing AND orphaned chunks fail.
  --staged           check the index, only when the commit touches dist/:
                     missing chunks fail, and so do orphans this commit
                     ADDS (orphans already in HEAD never wedge a commit).
  --staged --prune   untrack every orphan in the index (``git rm
                     --cached``); the files stay on disk for open windows.
  --prune-disk       delete orphans from the working tree's dist/assets.
                     For throwaway clones (merge train) only -- never the
                     live desktop checkout, whose open windows need them.

Exit codes:
  0 -- consistent (or nothing to check: dist not tracked / not touched).
  1 -- CONFIRMED: a referenced chunk is not tracked, or an orphan is.
  3 -- could not check (no git, unreadable blob, ...). The hooks treat
       this as fail-open so tooling noise never wedges a commit; CI is
       the backstop.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

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
# Any hash-shaped file name, whatever precedes it. Used only to decide
# that a file IS referenced (``new URL("x-HASH.wasm", import.meta.url)``
# has no ./ prefix), so a false match keeps a file instead of dropping one.
_ANY_HASHED_NAME = re.compile(r"[A-Za-z0-9._@-]+-[A-Za-z0-9_-]{8}\.[a-z0-9]{2,5}")
# What publish-build.mjs treats as a generated, retirable asset.
_GENERATED_NAME = re.compile(r"-[A-Za-z0-9_-]{8}\.[a-z0-9]+$")
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


def _walk(
    files: set[str], read: Callable[[str], str]
) -> tuple[set[str], dict[str, str]]:
    """Walk the chunk graph from the entry document.

    Returns the reachable files and the strictly referenced files that are
    absent (path -> first referrer). ``read`` raises on an unreadable file.
    """
    missing: dict[str, str] = {}
    visited: set[str] = set()
    queue: deque[str] = deque([ENTRY_HTML])
    while queue:
        path = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        blob = read(path)
        for name in sorted(_refs_in(blob)):
            ref = ASSETS_PREFIX + name
            if ref not in files:
                missing.setdefault(ref, path)
            elif ref.endswith(_SCANNABLE):
                queue.append(ref)
            else:
                visited.add(ref)
        for name in set(_ANY_HASHED_NAME.findall(blob)):
            ref = ASSETS_PREFIX + name
            if ref in files and ref not in visited:
                if ref.endswith(_SCANNABLE):
                    queue.append(ref)
                else:
                    visited.add(ref)
    return visited, missing


def _orphans(files: Iterable[str], reachable: set[str]) -> list[str]:
    """Generated files directly under dist/assets that nothing reaches."""
    out = []
    for path in files:
        if not path.startswith(ASSETS_PREFIX):
            continue
        name = path[len(ASSETS_PREFIX):]
        if "/" in name or not _GENERATED_NAME.search(name):
            continue  # public assets and nested folders are not build chunks
        if path not in reachable:
            out.append(path)
    return sorted(out)


def _report_missing(missing: dict[str, str]) -> None:
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


def _report_orphans(orphans: list[str], staged: bool) -> None:
    print(
        f"check_dist_consistency: {len(orphans)} orphaned bundle file(s) "
        "would be shipped."
    )
    print(
        "  Nothing reachable from dist/index.html loads them; they are chunks"
    )
    print("  of earlier builds the build keeps on disk for open windows:")
    for path in orphans[:20]:
        print(f"    ORPHAN {path}")
    if len(orphans) > 20:
        print(f"    ... and {len(orphans) - 20} more")
    if staged:
        print(
            "  Fix: python scripts/ci/check_dist_consistency.py --staged --prune"
        )
        print("  (untracks them; the files stay on disk), then commit again.")
    else:
        print(
            "  Fix: git rm --cached the files above (or run --staged --prune"
        )
        print("  after staging the bundle) and commit the removal.")


def _prune_disk(root: Path) -> int:
    dist = root / DIST
    if not (dist / "index.html").is_file():
        return 0
    files = {
        p.relative_to(root).as_posix() for p in dist.rglob("*") if p.is_file()
    }

    def read(path: str) -> str:
        return (root / path).read_text(encoding="utf-8", errors="replace")

    try:
        reachable, missing = _walk(files, read)
    except OSError as exc:
        print(f"check_dist_consistency: could not read the bundle ({exc})")
        return 3
    if missing:
        _report_missing(missing)
        return 1
    orphans = _orphans(files, reachable)
    for path in orphans:
        (root / path).unlink()
    print(
        f"check_dist_consistency: removed {len(orphans)} orphaned bundle "
        f"file(s); {len(reachable)} reachable files kept."
    )
    return 0


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    prune = "--prune" in argv

    if "--prune-disk" in argv:
        try:
            root = Path(_git("rev-parse", "--show-toplevel").strip())
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dist_consistency: not in a git checkout ({exc})")
            return 3
        return _prune_disk(root)

    try:
        tracked = _tracked_dist_files(staged)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"check_dist_consistency: could not list tracked files ({exc})")
        return 3

    if ENTRY_HTML not in tracked:
        # Nothing shipped -> nothing to keep consistent.
        return 0

    added: set[str] = set()
    if staged:
        # Only enforce when this commit actually touches dist/ -- an
        # unrelated commit must never be wedged by breakage another
        # session left in HEAD (the pre-push gate still catches that
        # before anything leaves the machine).
        try:
            touched = _git("diff", "--cached", "--name-only", "--", DIST)
            added = set(
                _git(
                    "diff", "--cached", "--name-only", "--diff-filter=A", "--", DIST
                ).split()
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dist_consistency: could not read staged diff ({exc})")
            return 3
        if not touched.strip():
            return 0

    try:
        reachable, missing = _walk(tracked, lambda p: _read_blob(p, staged))
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"check_dist_consistency: could not read the bundle ({exc})")
        return 3

    if missing:
        _report_missing(missing)
        return 1

    orphans = _orphans(tracked, reachable)
    if staged and prune and orphans:
        try:
            for start in range(0, len(orphans), 200):
                _git("rm", "--cached", "--quiet", "--", *orphans[start:start + 200])
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_dist_consistency: could not untrack orphans ({exc})")
            return 3
        print(
            f"check_dist_consistency: untracked {len(orphans)} orphaned bundle "
            "file(s); they stay on disk."
        )
        return 0
    blocking = [p for p in orphans if p in added] if staged else orphans
    if blocking:
        _report_orphans(blocking, staged)
        return 1

    state = "staged index" if staged else "HEAD"
    print(
        "check_dist_consistency: OK - all "
        f"{len(reachable)} reachable bundle files tracked, no orphans ({state})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
