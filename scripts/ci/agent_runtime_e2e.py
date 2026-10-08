#!/usr/bin/env python3
"""Install a real Hermes / OpenClaw the way the desktop app does, then drive it.

Run by the ``agent-runtimes-e2e`` CI job and the weekly canary, one runtime
per call, on Linux, Windows and macOS:

1. The process runs with a desktop app's PATH (POSIX ``/usr/bin:/bin`` plus
   this Python; Windows the default system folders plus this Python), so a
   setup that only works from a developer's shell fails here.
2. ``manager.wait_ready`` installs and prepares the runtime through the app's
   own setup job: the pinned release (``runtime-versions.json``), its private
   Node.js for OpenClaw, Hermes' one-time prepare. No npm, no manual installer.
3. The spikes run two turns per model-gateway protocol against scripted
   providers (``--replay``) and the fake-model driver check; turn 1 must stay
   inside its budget.
4. ``pytest tests/unit/agent_runtimes`` runs on the same machine.
5. Nothing persistent may change: the Windows user PATH gains no
   ``agent_runtimes`` entry, and OpenClaw edits no POSIX shell profile.

``--expect-setup-failure TEXT`` is the bare-container leg: setup must fail
with a reason containing ``TEXT`` (``python:3.11-slim`` has no curl).

    python scripts/ci/agent_runtime_e2e.py hermes
    python scripts/ci/agent_runtime_e2e.py openclaw --allow-untested   # canary
    python scripts/ci/agent_runtime_e2e.py hermes --expect-setup-failure "Setup needs curl"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIMES = ("hermes", "openclaw")

#: Turn-1 budget after the one-time prepare, in seconds.
TURN_BUDGET_S = {"hermes": 30.0, "openclaw": 90.0}
_TURN = re.compile(r"turn 1 took ([0-9.]+) s")
_PROFILES = (".profile", ".bashrc", ".zshrc")
#: Runtimes whose official installer may write a POSIX shell profile.
_PROFILE_EDITS_ALLOWED = frozenset({"hermes"})


def desktop_path() -> str:
    """The PATH a desktop app starts with, plus the interpreter running it."""
    python_dir = str(Path(sys.executable).resolve().parent)
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        parts = [
            rf"{root}\System32",
            root,
            rf"{root}\System32\Wbem",
            rf"{root}\System32\WindowsPowerShell\v1.0",
        ]
    else:
        parts = ["/usr/bin", "/bin"]
    return os.pathsep.join([*parts, python_dir])


def _user_path_entries() -> list[str]:
    """The Windows user PATH (``HKCU\\Environment``); empty elsewhere."""
    if os.name != "nt":
        return []
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, "Path")
    except OSError:  # no user PATH at all: nothing to compare
        return []
    return [entry for entry in str(value).split(";") if entry]


def _profiles() -> dict[str, str]:
    if os.name == "nt":
        return {}
    home = Path.home()
    return {
        name: (home / name).read_text(encoding="utf-8", errors="replace")
        if (home / name).exists()
        else ""
        for name in _PROFILES
    }


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    print("$", " ".join(argv), flush=True)
    proc = subprocess.run(  # noqa: S603 — this interpreter and repo scripts
        argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = proc.stdout + proc.stderr
    print(output, flush=True)
    return proc.returncode, output


def _setup(runtime: str, timeout_s: float) -> tuple[object, object, str]:
    import asyncio

    from jarvis.agent_runtimes import manager

    status = asyncio.run(manager.wait_ready(runtime, timeout_s=timeout_s))
    job = manager.job(runtime)
    print("status:", status.to_dict(), flush=True)
    print("job:", job.to_dict() if job is not None else None, flush=True)
    return status, job, manager.failure_reason(runtime)


def _keep_setup_log(runtime: str) -> None:
    """Copy the setup log where the job uploads diagnostics from."""
    from jarvis.agent_runtimes import base

    log = base.runtimes_root() / f"{runtime}-setup.log"
    if log.exists():
        target = REPO_ROOT / "eval-results" / f"{runtime}-setup.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(log, target)
        print("setup log kept:", target, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runtime", choices=RUNTIMES)
    parser.add_argument("--timeout", type=float, default=2400.0, help="Setup wait, seconds.")
    parser.add_argument(
        "--allow-untested", action="store_true",
        help="Accept a release newer than the pin (the canary installs upstream latest).",
    )
    parser.add_argument("--expect-setup-failure", default="", metavar="TEXT")
    args = parser.parse_args(argv)

    os.environ["PATH"] = desktop_path()
    sys.path.insert(0, str(REPO_ROOT))
    env = dict(os.environ)
    print("PATH:", env["PATH"], flush=True)
    user_path, profiles = _user_path_entries(), _profiles()
    problems: list[str] = []

    status, job, reason = _setup(args.runtime, args.timeout)
    if args.expect_setup_failure:
        if getattr(status, "ready", False) or args.expect_setup_failure not in reason:
            problems.append(
                f"setup should fail with {args.expect_setup_failure!r}; "
                f"ready={getattr(status, 'ready', None)} reason={reason!r}"
            )
        _keep_setup_log(args.runtime)
        return _verdict(problems)

    ready = bool(getattr(status, "ready", False))
    untested = bool(getattr(status, "untested", False))
    job_ok = job is None or getattr(job, "state", "") == "done"
    if not ready or not job_ok or (untested and not args.allow_untested):
        problems.append(f"setup: ready={ready} untested={untested} job_ok={job_ok} {reason}")
        _keep_setup_log(args.runtime)
        return _verdict(problems)

    spikes = [
        [sys.executable, "scripts/spikes/agent_runtimes_gateway_e2e.py", args.runtime,
         "openai-codex", "--replay"],
        [sys.executable, "scripts/spikes/agent_runtimes_gateway_e2e.py", args.runtime,
         "ollama", "--replay"],
        [sys.executable, "scripts/spikes/agent_runtimes_e2e.py", args.runtime],
    ]
    budget = TURN_BUDGET_S[args.runtime]
    for spike in spikes:
        code, output = _run(spike, env)
        if code != 0:
            problems.append(f"{' '.join(spike[1:])} exited {code}")
        for took in (float(value) for value in _TURN.findall(output)):
            if took >= budget:
                name = " ".join(spike[1:])
                problems.append(f"{name}: turn 1 took {took:.1f} s >= {budget:.0f} s")

    code, _ = _run([sys.executable, "-m", "pytest", "tests/unit/agent_runtimes", "-q",
                    "-p", "no:cacheprovider"], env)
    if code != 0:
        problems.append(f"pytest tests/unit/agent_runtimes exited {code}")

    gained = [e for e in _user_path_entries() if e not in user_path and "agent_runtimes" in e]
    if gained:
        problems.append(f"the Windows user PATH gained {gained}")
    for name, before in profiles.items():
        after = _profiles().get(name, "")
        if after != before:
            print(f"~/{name} changed during {args.runtime} setup", flush=True)
            if args.runtime not in _PROFILE_EDITS_ALLOWED:
                problems.append(f"{args.runtime} changed ~/{name}")
    if problems:
        _keep_setup_log(args.runtime)
    return _verdict(problems)


def _verdict(problems: list[str]) -> int:
    for problem in problems:
        print("FAIL:", problem, flush=True)
    print("RESULT:", "FAIL" if problems else "PASS", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
