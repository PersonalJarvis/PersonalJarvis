"""WebGL-context gate.

A browser hands a page a small, fixed number of live WebGL contexts —
Chromium's cap is 16 — and it enforces the cap by silently taking the OLDEST
context away when a new one is asked for. Nothing in three.js gives one back:
``WebGLRenderer.dispose()`` frees buffers and textures, not the context. Only
``WEBGL_lose_context.loseContext()`` (three's ``forceContextLoss()``) does.

So a component that mounts a WebGL renderer and never releases it spends one
of those sixteen slots per mount, for the lifetime of the page. On 2026-08-21
fourteen trips into the Wiki section and back left FIFTEEN live contexts behind
one canvas, and the browser killed the longest-running scene on the page — the
deck's memory map — leaving Chromium's own broken-canvas placeholder there: a
white rectangle with a small sad face. Nothing in the app noticed, because
without a ``webglcontextlost`` handler that calls ``preventDefault()`` the
browser never offers the context back either.

This gate enforces the invariant: **every frontend module that mounts a WebGL
renderer must also give the context back and survive losing it.** In practice
that means using ``hooks/useWebglSurface.ts``, which does both; a module that
calls ``WEBGL_lose_context`` itself (the capability probe in
``lib/graphDimension.ts``) also satisfies it.

Static analysis only — it never runs a bundler, so it is cheap and
dependency-free. Run from CI and covered by
``tests/unit/ui/test_webgl_contexts_released.py``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src"

#: An import of the 3D graph renderer, a hand-rolled three.js renderer, or a
#: raw WebGL context. Type-only imports are excluded at the call site: a module
#: that merely names `NodeObject` never owns a context.
_MOUNTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("react-force-graph-3d", re.compile(r"^\s*import\s+(?!type\b)[^\n]*react-force-graph-3d", re.M)),
    ("new WebGLRenderer", re.compile(r"\bnew\s+(?:THREE\.)?WebGLRenderer\b")),
    ("@react-three/fiber", re.compile(r"^\s*import\s+(?!type\b)[^\n]*@react-three/fiber", re.M)),
    ("getContext(\"webgl\")", re.compile(r"""getContext\(\s*['"]webgl2?['"]""")),
)

#: Proof that the module hands the context back. `useWebglSurface` is the
#: shared way; the other three are what it (or an equivalent) calls.
_RELEASES: tuple[str, ...] = (
    "useWebglSurface",
    "releaseWebglContext",
    "WEBGL_lose_context",
    "forceContextLoss",
)

#: Files whose WebGL use is a test double or a fixture, never a live scene.
_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")


def _is_test(path: Path) -> bool:
    return path.name.endswith(_TEST_SUFFIXES) or "__tests__" in path.parts


def offenders(root: Path | None = None) -> list[tuple[str, str]]:
    """Return (repo-relative path, what it mounts) for every unreleased scene."""
    base = root or _FRONTEND
    found: list[tuple[str, str]] = []
    for path in sorted(base.rglob("*.ts*")):
        if _is_test(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        mounted = next((label for label, rx in _MOUNTS if rx.search(text)), None)
        if not mounted:
            continue
        if any(token in text for token in _RELEASES):
            continue
        found.append((_label(path), mounted))
    return found


def _label(path: Path) -> str:
    """Repo-relative where possible; the tests scan a tmp tree of their own."""
    try:
        return path.relative_to(_REPO).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    bad = offenders()
    if not bad:
        print("WebGL-context gate: every 3D surface releases its context.")
        return 0
    print("WebGL contexts that are taken and never given back:\n", file=sys.stderr)
    for path, mounted in bad:
        print(f"  {path}  (mounts: {mounted})", file=sys.stderr)
    print(
        "\nUse `useWebglSurface(hostRef)` from @/hooks/useWebglSurface and put its\n"
        "`generation` on the renderer as a `key`. It releases the context on\n"
        "unmount and rebuilds the scene when the browser takes one away — without\n"
        "it the sixteenth mount kills the oldest scene on the page and the browser\n"
        "paints a white canvas with a sad face over it (AP-32).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
