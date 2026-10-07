#!/usr/bin/env python3
"""The Hermes / OpenClaw agent-runtime lane: which changes reach it, what it runs.

One table for three consumers, so they cannot drift apart:

* ``classify_changes.py`` turns the ``agent_runtimes`` lane on for
  :data:`PREFIXES` / :data:`FILES`;
* ``select_tests.py`` adds :data:`SUITE` to an impact selection that touches
  the lane, so a change to the runtimes also runs the chat and society code
  that drives them (not only the tests that import the changed module);
* the ``agent-runtimes-unit`` / ``agent-runtimes-headless`` CI jobs run
  ``python scripts/ci/agent_runtime_suite.py`` — the same command on every OS.

The real-binary check (``scripts/spikes/agent_runtimes_gateway_e2e.py
--replay``) is a separate job: it needs Hermes or OpenClaw installed.

    python scripts/ci/agent_runtime_suite.py            # run the suite
    python scripts/ci/agent_runtime_suite.py --list     # print the test paths
    python scripts/ci/agent_runtime_suite.py --headless # the slim-container subset
    python scripts/ci/agent_runtime_suite.py --pin hermes   # install pin -> $GITHUB_OUTPUT
    python scripts/ci/agent_runtime_suite.py --bump openclaw --version 2026.9.8

The pin comes from ``jarvis/agent_runtimes/runtime-versions.json``:
``{"hermes": {"tested": "0.21.5", "commit": "<sha>"}, "openclaw": {"tested":
"2026.9.8"}}``. Hermes is pinned by ``commit`` (its installers take
``--commit`` / ``-Commit``; a version string alone cannot select a release),
OpenClaw by ``tested`` (an npm version). A missing file or key means latest:
the lane still runs, it just proves the newest upstream instead of the pin.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "jarvis" / "agent_runtimes" / "runtime-versions.json"
RUNTIMES: tuple[str, ...] = ("hermes", "openclaw")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}")
_COMMIT = re.compile(r"[0-9a-f]{40}")

#: Code whose change can break an agent on Hermes or OpenClaw.
PREFIXES: tuple[str, ...] = (
    "jarvis/agent_runtimes/",
    "tests/unit/agent_runtimes/",
    "scripts/spikes/agent_runtimes_",
)
FILES: frozenset[str] = frozenset(
    {
        "jarvis/agent_chat/runner_acp.py",
        "jarvis/agent_chat/runner_cli.py",
        "jarvis/agent_chat/service.py",
        "jarvis/agent_chat/send_queue.py",
        "jarvis/agent_chat/control.py",
        "jarvis/agent_chat/task_recovery.py",
        "jarvis/agent_chat/catalog.py",
        "jarvis/society/chat_binding.py",
        "jarvis/society/roster.py",
        "jarvis/society/routine_runner.py",
        "jarvis/society/seat_brain.py",
        "jarvis/ui/web/agent_runtime_routes.py",
        "jarvis/ui/web/runtime_gateway_routes.py",
        "jarvis/ui/web/society_routes.py",
        "tests/fakes/fake_acp_agent.py",
        "tests/fakes/fake_runtime_brain.py",
        "scripts/ci/agent_runtime_suite.py",
    }
)

#: Unit and integration tests of the runtimes and of the chat / society code
#: that drives them. Fakes only: no runtime binary, no network, no key.
SUITE: tuple[str, ...] = (
    "tests/unit/agent_runtimes",
    "tests/unit/agent_chat/test_agent_chat_service.py",
    "tests/unit/agent_chat/test_controls.py",
    "tests/unit/agent_chat/test_send_queue.py",
    "tests/unit/agent_chat/test_task_recovery.py",
    "tests/unit/agent_chat/test_turn_host.py",
    "tests/unit/society/test_agent_runtime_field.py",
    "tests/unit/society/test_chat_binding.py",
    "tests/unit/society/test_roster.py",
    "tests/unit/society/test_routines.py",
    "tests/unit/ui/web/test_society_routes.py",
    "tests/contract/test_routine_chats.py",
)

#: What must pass on a bare ``python:3.11-slim`` (no GPU, audio, desktop).
HEADLESS: tuple[str, ...] = ("tests/unit/agent_runtimes",)


def touches(path: str) -> bool:
    """Whether a changed repo-relative path reaches the runtime lane."""
    path = path.strip().replace("\\", "/")
    return path.startswith(PREFIXES) or path in FILES


def _existing(paths: tuple[str, ...]) -> list[str]:
    missing = [path for path in paths if not (REPO_ROOT / path).exists()]
    if missing:
        # A renamed test must be renamed here too; a silent skip would shrink
        # the lane's evidence without anyone noticing.
        raise SystemExit(f"agent runtime suite names missing tests: {', '.join(missing)}")
    return list(paths)


def _manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def pin(runtime: str, path: Path = MANIFEST) -> dict[str, str]:
    """How CI installs ``runtime``: ``version`` (shown), ``npm_spec`` (OpenClaw)
    and ``hermes_commit`` ("" = the installer's default branch, latest)."""
    entry = _manifest(path).get(runtime)
    if isinstance(entry, str):
        entry = {"tested": entry}
    elif not isinstance(entry, dict):
        entry = {}
    version = str(entry.get("tested") or "")
    commit = str(entry.get("commit") or "")
    # Values reach a shell command line: accept only plain versions and SHAs.
    version = version if _VERSION.fullmatch(version) else ""
    commit = commit if _COMMIT.fullmatch(commit) else ""
    return {
        "version": version or "latest",
        "npm_spec": f"openclaw@{version or 'latest'}",
        "hermes_commit": commit,
    }


def bump(runtime: str, version: str, commit: str = "", path: Path = MANIFEST) -> bool:
    """Record ``version`` (and Hermes' ``commit``) as tested; True when it changed."""
    if not _VERSION.fullmatch(version) or (commit and not _COMMIT.fullmatch(commit)):
        raise SystemExit(f"refusing to record an unexpected version {version!r} / {commit!r}")
    data = _manifest(path)
    entry = data.get(runtime) if isinstance(data.get(runtime), dict) else {}
    updated = {**entry, "tested": version, **({"commit": commit} if commit else {})}
    if updated == entry:
        return False
    data[runtime] = updated
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def bump_from(folder: Path, path: Path = MANIFEST, *, expected_reports: int = 3) -> bool:
    """Raise each runtime's pin to what the canary proved on EVERY OS.

    ``folder`` holds one ``report`` JSON per green canary job. A runtime is
    raised only when all ``expected_reports`` OS jobs reported the same
    version and commit; one missing or disagreeing OS keeps the old pin.
    """
    seen: dict[str, list[tuple[str, str]]] = {}
    for file in sorted(folder.rglob("*.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("runtime") in RUNTIMES:
            seen.setdefault(data["runtime"], []).append(
                (str(data.get("version") or ""), str(data.get("commit") or ""))
            )
    changed = False
    for runtime, reports in seen.items():
        if len(reports) != expected_reports or len(set(reports)) != 1:
            print(f"{runtime}: {len(reports)} report(s) {sorted(set(reports))}; pin kept")
            continue
        version, commit = reports[0]
        changed = bump(runtime, version, commit, path) or changed
    return changed


def _hermes_commit() -> str:
    """The commit of the Hermes checkout its official installer made."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    local = os.environ.get("LOCALAPPDATA")
    candidates = [home / "hermes-agent"]
    if local:
        candidates.insert(0, Path(local) / "hermes" / "hermes-agent")
    for checkout in candidates:
        if not (checkout / ".git").exists():
            continue
        found = subprocess.run(  # noqa: S603 — fixed git query
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        if _COMMIT.fullmatch(found):
            return found
    return ""


def report(runtime: str) -> dict[str, str]:
    """What the canary just proved: the installed version (and Hermes' commit)."""
    sys.path.insert(0, str(REPO_ROOT))
    from jarvis.agent_runtimes import driver

    status = driver(runtime).detect(refresh=True)
    if not status.ready or not _VERSION.fullmatch(status.version or ""):
        raise SystemExit(f"{runtime} is not ready to report ({status.problem or 'no version'})")
    commit = _hermes_commit() if runtime == "hermes" else ""
    if runtime == "hermes" and not commit:
        # Without a commit the pin cannot select this release; record nothing.
        raise SystemExit("the Hermes checkout's commit could not be read")
    return {"runtime": runtime, "version": status.version, "commit": commit}


def _emit(values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="Print the test paths and exit.")
    parser.add_argument("--headless", action="store_true", help="Only the slim-container subset.")
    parser.add_argument("--pin", choices=RUNTIMES, help="Print how CI installs this runtime.")
    parser.add_argument("--bump", choices=RUNTIMES, help="Record --version as tested.")
    parser.add_argument("--version", default="", help="With --bump: the version that passed.")
    parser.add_argument("--commit", default="", help="With --bump hermes: its commit.")
    parser.add_argument("--report", choices=RUNTIMES, help="Write the installed version as JSON.")
    parser.add_argument("--out", type=Path, help="With --report: the JSON file to write.")
    parser.add_argument(
        "--bump-from", type=Path, help="Record every report JSON in this folder that agrees."
    )
    args, extra = parser.parse_known_args(argv)
    if args.report:
        found = report(args.report)
        if args.out:
            args.out.write_text(json.dumps(found), encoding="utf-8")
        _emit(found)
        return 0
    if args.bump_from:
        _emit({"changed": "true" if bump_from(args.bump_from) else "false"})
        return 0
    if args.pin:
        _emit(pin(args.pin))
        return 0
    if args.bump:
        _emit({"changed": "true" if bump(args.bump, args.version, args.commit) else "false"})
        return 0
    tests = _existing(HEADLESS if args.headless else SUITE)
    if args.list:
        print("\n".join(tests))
        return 0
    command = [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", *extra]
    # No CREATE_NO_WINDOW: this runs in a CI console, and on Windows that flag
    # detaches pytest from it, so its report would vanish.
    return subprocess.call(command, cwd=REPO_ROOT)  # noqa: S603 — this interpreter


if __name__ == "__main__":
    sys.exit(main())
