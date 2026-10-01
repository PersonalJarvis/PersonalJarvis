#!/usr/bin/env python3
"""Run every static repository gate in one process and report them together.

The gates used to be ~25 separate CI steps spread over five jobs, each paying
for its own runner, checkout and Python set-up — and the first red step hid
every later one. Here they run back to back (each takes seconds), all of them
report, and the exit code is non-zero if any failed. Agents run the SAME
command locally before landing work::

    python scripts/ci/run_gates.py                  # everything that applies
    python scripts/ci/run_gates.py --base origin/main --pr
    python scripts/ci/run_gates.py --only mirrors,privacy

``--pr`` adds the gates that judge a diff against its merge base (no new
German, unrelated history). ``--base`` feeds the diff-scoped privacy scan.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


@dataclass(frozen=True)
class Gate:
    name: str
    commands: tuple[tuple[str, ...], ...]
    pr_only: bool = False
    needs_base: bool = False
    needs_docker: bool = False


def _gates(base: str) -> list[Gate]:
    ci = "scripts/ci/"
    return [
        Gate("private-keys", ((PY, ci + "check_no_private_keys.py"),)),
        Gate("dist-consistency", ((PY, ci + "check_dist_consistency.py"),)),
        Gate("brand-logos", ((PY, ci + "check_brand_logos.py"),)),
        Gate(
            "mirrors",
            (
                (PY, ci + "check_agents_md.py"),
                (PY, ci + "sync_agents_dir.py", "--check"),
                (PY, ci + "sync_codex_agents.py", "--check"),
            ),
        ),
        Gate("cli-coverage", ((PY, ci + "check_cli_coverage.py"),)),
        Gate("danger-metadata", ((PY, ci + "check_danger_metadata.py"),)),
        Gate("cli-install-methods", ((PY, ci + "check_cli_install_methods.py"),)),
        Gate("dead-config-switches", ((PY, ci + "check_config_switches_wired.py"),)),
        Gate("silent-exceptions", ((PY, ci + "check_silent_exception_handlers.py"),)),
        Gate("async-routes", ((PY, ci + "check_async_routes.py"),)),
        Gate("webgl-contexts", ((PY, ci + "check_webgl_contexts_released.py"),)),
        Gate("plugin-auth-contract", ((PY, ci + "check_plugin_auth_contract.py"),)),
        Gate(
            "reference-docs",
            (
                (PY, ci + "gen_cli_reference.py", "--check"),
                (PY, ci + "gen_commands_reference.py", "--check"),
            ),
        ),
        Gate("docs-privacy", ((PY, ci + "docs_privacy_scan.py"),)),
        Gate("public-docs", ((PY, ci + "check_public_docs.py"),)),
        Gate("shell-bash32", ((PY, ci + "check_shell_bash32.py", "--require"),), needs_docker=True),
        Gate("privacy", ((PY, ci + "privacy_scan_ci.py", "--base", base),), needs_base=True),
        Gate("no-new-german", ((PY, ci + "check_no_new_german.py", base),), pr_only=True),
        Gate(
            "history",
            (("git", "merge-base", base, "HEAD"),),
            pr_only=True,
            needs_base=True,
        ),
    ]


def _run(cmd: tuple[str, ...]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(  # noqa: S603
        list(cmd),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="", help="Diff base (sha or ref).")
    parser.add_argument("--pr", action="store_true", help="Also run pull-request gates.")
    parser.add_argument("--only", default="", help="Comma-separated gate names.")
    parser.add_argument("--skip", default="", help="Comma-separated gate names.")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    only = {x for x in args.only.split(",") if x}
    skip = {x for x in args.skip.split(",") if x}
    has_docker = shutil.which("docker") is not None
    results: list[tuple[str, str, float]] = []
    failed = False
    for gate in _gates(args.base):
        if (only and gate.name not in only) or gate.name in skip:
            continue
        reason = ""
        if gate.pr_only and not args.pr:
            reason = "pull-request gate"
        elif gate.needs_base and not args.base:
            reason = "no --base given"
        elif gate.name == "no-new-german" and not args.base:
            reason = "no --base given"
        elif gate.needs_docker and not has_docker:
            reason = "docker unavailable"
        if reason:
            results.append((gate.name, f"skip ({reason})", 0.0))
            continue
        started = time.monotonic()
        status = "pass"
        for cmd in gate.commands:
            code, output = _run(cmd)
            if code != 0:
                status = f"FAIL (exit {code})"
                failed = True
                print(f"::group::{gate.name} FAILED: {' '.join(cmd)}")
                print(output[-6000:])
                print("::endgroup::")
                print(f"::error title=Gate {gate.name} failed::{' '.join(cmd[1:])}")
                break
        results.append((gate.name, status, time.monotonic() - started))

    lines = ["| gate | result | seconds |", "|---|---|---|"]
    lines += [f"| {name} | {status} | {secs:.1f} |" for name, status, secs in results]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## Static gates\n\n" + "\n".join(lines) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
