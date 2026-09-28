#!/usr/bin/env python3
"""Classify a change set into CI lanes (the orchestrator's ``detect`` job).

Reads newline-separated changed paths (repo-relative, forward slashes) from
stdin or ``--files-from``, decides which CI lanes the change can affect, and
writes ``lane=true|false`` lines to ``$GITHUB_OUTPUT`` (and stdout).

Contract — FAIL OPEN, never closed. A lane may run without need; it must
never be skipped when the change could break it:

* ``--full`` (push to main, nightly, release, manual dispatch) or an EMPTY
  change set turns every lane on. An empty set means the diff could not be
  computed, never "nothing changed".
* A change under ``.github/`` or ``scripts/ci/`` turns every lane on: it
  rewires the pipeline itself.
* ``python`` is a DENYLIST: it is off only when every path is provably
  outside what the test suite reads. Tests read docs/, the frontend source,
  install/ and assets/, so those keep it on.

Outputs beyond the lanes:

* ``full``           — every lane forced on (the caller also widens matrices).
* ``changed_count``  — number of changed paths (0 = unknown).

Usage::

    git diff --name-only BASE HEAD | python scripts/ci/classify_changes.py
    python scripts/ci/classify_changes.py --full
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path

# Trees the Python suite never reads (verified by grepping tests/ for each).
# Everything NOT listed keeps the python lane on — unknown means relevant.
_PY_IRRELEVANT_PREFIXES = (
    "wiki-video/",
    "homebrew-tap/",
    "scoop-bucket/",
    "board-backend/",
    "qa/",
    "skillbook/",
    "keyproxy/",
    ".github/ISSUE_TEMPLATE/",
)
_PY_IRRELEVANT_FILES = {
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
}

_PIPELINE_PREFIXES = (".github/workflows/", ".github/actions/", "scripts/ci/")

_FRONTEND_PREFIXES = ("jarvis/ui/web/frontend/",)
_FRONTEND_FILES = {"scripts/ci/check_frontend_bundle_budget.py"}

_DEPS_FILES = {
    "pyproject.toml",
    "requirements.in",
    "requirements.txt",
    "uv.lock",
}

_REALTIME_PREFIXES = (
    "jarvis/realtime/",
    "jarvis/plugins/realtime/",
    "jarvis/audio/",
    "jarvis/speech/",
    "jarvis/live/",
    "jarvis/core/",
    "tests/contract/test_realtime",
    "tests/contract/test_gpt_live",
)
_REALTIME_FILES = {"scripts/realtime_reliability_gate.py"} | _DEPS_FILES

_CLI_PREFIXES = ("jarvis/cli_ctl/", "jarvis/commands/", "tests/unit/cli_ctl/")

_DRAGDROP_FILES = {
    "jarvis/overlay/drop_target.py",
    "jarvis/overlay/drop_bridge.py",
    "scripts/crossplatform/verify_drop_target.py",
}

_BROWSER_PREFIXES = (
    "jarvis/society/browser/",
    "tests/contract/test_browser",
    "tests/unit/society/test_browser",
)
_BROWSER_FILES = {
    "jarvis/ui/web/society_browser_routes.py",
    "jarvis/ui/web/mcp_server_routes.py",
    "jarvis/agent_chat/runner_cli.py",
    "jarvis/brain/tool_gateway.py",
    "jarvis/society/approvals.py",
}

# Native macOS surface: permissions, bundle, autostart, Computer-Use. The old
# standalone workflow also matched all of tests/** and jarvis/ui/**, which put
# three 45-minute macOS jobs behind nearly every PR; the lane keeps the
# platform code and the exact test files that job runs.
_MACOS_PREFIXES = (
    "install/",
    "jarvis/admin/",
    "jarvis/audio/",
    "jarvis/autostart/",
    "jarvis/cu/",
    "jarvis/platform/",
    "jarvis/setup/",
    "jarvis/trigger/",
    "jarvis/vision/",
    "jarvis/ui/desktop",
    "tests/unit/platform/",
    "tests/unit/autostart/",
    "tests/unit/cu/",
    "tests/unit/setup/",
    "tests/unit/vision/",
)
_MACOS_FILES = {
    "pyproject.toml",
    "uv.lock",
    ".githooks/pre-push",
    "scripts/measure_boot.py",
    "scripts/measure_desktop_boot.py",
}

# Installer smoke: the raw install path a downloader runs.
_INSTALLER_PREFIXES = ("install/",)
_INSTALLER_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "jarvis/__main__.py",
    "jarvis/setup/prefetch.py",
    "jarvis/setup/model_report.py",
    "jarvis/speech/wake_model_fetch.py",
    "tests/unit/install/test_install_sh_stage1.py",
    ".github/workflows/sign-installer.yml",
}

LANES = (
    "python",
    "frontend",
    "deps",
    "realtime",
    "cli",
    "dragdrop",
    "browser",
    "macos_desktop",
    "installer",
)


def _normalize(paths: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in paths:
        path = raw.strip().replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if path:
            out.append(path)
    return out


def _any(paths: list[str], prefixes: tuple[str, ...] = (), files: set[str] | None = None) -> bool:
    files = files or set()
    return any(p.startswith(prefixes) or p in files for p in paths)


def _python_relevant(path: str) -> bool:
    return not (path.startswith(_PY_IRRELEVANT_PREFIXES) or path in _PY_IRRELEVANT_FILES)


def classify(paths: Iterable[str], *, full: bool = False) -> dict[str, bool]:
    """Return ``{lane: bool}`` plus ``full`` for a change set."""
    changed = _normalize(paths)
    force = full or not changed or _any(changed, _PIPELINE_PREFIXES)
    if force:
        result = {lane: True for lane in LANES}
        result["full"] = True
        return result
    return {
        "python": any(_python_relevant(p) for p in changed),
        "frontend": _any(changed, _FRONTEND_PREFIXES, _FRONTEND_FILES),
        "deps": _any(changed, (), _DEPS_FILES),
        "realtime": _any(changed, _REALTIME_PREFIXES, _REALTIME_FILES),
        "cli": _any(changed, _CLI_PREFIXES)
        or any(p.startswith("jarvis/ui/web/") and p.endswith("_routes.py") for p in changed),
        "dragdrop": _any(changed, (), _DRAGDROP_FILES),
        "browser": _any(changed, _BROWSER_PREFIXES, _BROWSER_FILES),
        "macos_desktop": _any(changed, _MACOS_PREFIXES, _MACOS_FILES),
        "installer": _any(changed, _INSTALLER_PREFIXES, _INSTALLER_FILES),
        "full": False,
    }


def _emit(result: dict[str, bool], changed_count: int) -> list[str]:
    lines = [f"{key}={'true' if value else 'false'}" for key, value in result.items()]
    lines.append(f"changed_count={changed_count}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true", help="Force every lane on.")
    parser.add_argument("--files-from", type=Path, help="Read paths from this file, not stdin.")
    args = parser.parse_args(argv)

    if args.full:
        paths: list[str] = []
    elif args.files_from:
        paths = args.files_from.read_text(encoding="utf-8").splitlines()
    else:
        paths = [] if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read().splitlines()
    changed = _normalize(paths)
    result = classify(changed, full=args.full)
    lines = _emit(result, len(changed))

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
