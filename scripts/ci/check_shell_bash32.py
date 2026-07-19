"""Parse every tracked shell script with bash 3.2 - the version macOS ships.

Why this gate exists
--------------------
macOS still ships GNU bash 3.2.57 (2007) as `/bin/bash`; Linux and Git Bash
ship bash 4/5. A script can therefore be perfectly valid on every developer
machine and CI runner we own, yet fail to PARSE on a Mac - which means not one
line of it runs.

That is not hypothetical. `install/uninstall.sh` shipped in v1.1.0 and v1.1.1
with a `case` arm inside a `$( )` command substitution:

    pids=$(ps -axo pid=,comm= | while read -r pid comm; do
        case "$comm" in "$root"/*) printf '%s ' "$pid" ;; esac
    done)

The bash 3.2 parser reads the `)` that closes the case PATTERN as the end of
the command substitution, so the whole file dies with

    uninstall.sh: line 57: syntax error near unexpected token `;;'

Every Mac on v1.1.0+ had a completely dead uninstaller. The optional leading
`(` on a case pattern - `case "$x" in ("$root"/*) ... ;; esac` - is POSIX and
fixes it on every shell we target.

No existing workflow parsed a single shell script against bash 3.2, so nothing
could have caught it. This gate does, and it is cheap: a parse check, never an
execution.

Engine selection (portable by design, per CLAUDE.md section 3)
-------------------------------------------------------------
1. A local `/bin/bash` that IS 3.2 - the real thing, on a Mac. No Docker.
2. Docker image `bash:3.2` - on Linux/Windows dev boxes and CI.
3. Neither available: report and skip, so a contributor without Docker is not
   blocked. CI passes `--require` to turn that skip into a hard failure.

Usage:
    python scripts/ci/check_shell_bash32.py            # local, skips if no engine
    python scripts/ci/check_shell_bash32.py --require  # CI: no engine == failure
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASH32_IMAGE = "bash:3.2"


def _run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw
    )


def tracked_shell_scripts() -> list[str]:
    """Repo-relative POSIX paths of every git-tracked *.sh file."""
    out = _run(["git", "-C", str(REPO_ROOT), "ls-files", "*.sh"])
    if out.returncode != 0:
        print(f"ERROR: could not list tracked files: {out.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return sorted(
        line.strip().replace("\\", "/") for line in out.stdout.splitlines() if line.strip()
    )


def local_bash32() -> str | None:
    """Path to a local bash that is 3.2 (i.e. we are on a Mac), else None."""
    for candidate in ("/bin/bash", shutil.which("bash") or ""):
        if not candidate or not Path(candidate).exists():
            continue
        probe = _run([candidate, "--version"])
        if probe.returncode == 0 and "version 3.2" in probe.stdout:
            return candidate
    return None


def docker_available() -> bool:
    return shutil.which("docker") is not None and _run(["docker", "info"]).returncode == 0


def parse_with_local(bash: str, scripts: list[str]) -> dict[str, str]:
    failures: dict[str, str] = {}
    for rel in scripts:
        got = _run([bash, "-n", str(REPO_ROOT / rel)])
        if got.returncode != 0:
            failures[rel] = got.stderr.strip()
    return failures


def parse_with_docker(scripts: list[str]) -> dict[str, str]:
    """One container for all scripts; `bash -n` parses, never executes."""
    # Marker-delimited output so a per-file verdict survives the shared stderr.
    script = "\n".join(
        f'printf "@@ {rel}\\n"; bash -n "/w/{rel}" 2>&1 || printf "@@FAIL {rel}\\n"'
        for rel in scripts
    )
    mount = f"{REPO_ROOT.as_posix()}:/w:ro"
    got = _run(["docker", "run", "--rm", "-v", mount, BASH32_IMAGE, "sh", "-c", script])
    if got.returncode != 0 and not got.stdout:
        print(f"ERROR: bash 3.2 container failed to start: {got.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)

    failures: dict[str, str] = {}
    current, buffer = None, []
    for line in got.stdout.splitlines():
        if line.startswith("@@FAIL "):
            failures[line[7:].strip()] = "\n".join(buffer).strip()
        elif line.startswith("@@ "):
            current, buffer = line[3:].strip(), []
        elif current:
            buffer.append(line)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--require",
        action="store_true",
        help="Fail when no bash 3.2 engine is available (CI) instead of skipping.",
    )
    args = ap.parse_args()

    scripts = tracked_shell_scripts()
    if not scripts:
        print("No tracked *.sh files - nothing to check.")
        return 0

    if (bash := local_bash32()) is not None:
        print(f"Engine: local {bash} (bash 3.2 - native macOS)")
        failures = parse_with_local(bash, scripts)
    elif docker_available():
        print(f"Engine: docker {BASH32_IMAGE}")
        failures = parse_with_docker(scripts)
    else:
        message = (
            "No bash 3.2 available (need a Mac's /bin/bash or Docker for "
            f"{BASH32_IMAGE}). macOS parse-compatibility was NOT verified."
        )
        if args.require:
            print(f"FAIL: {message}", file=sys.stderr)
            return 1
        print(f"SKIP: {message}")
        return 0

    for rel in scripts:
        print(f"  {'FAIL' if rel in failures else 'ok  '}  {rel}")

    if failures:
        print(
            f"\nFAIL: {len(failures)} shell script(s) do not PARSE under bash 3.2, "
            "so they run on Linux and Git Bash but are dead on macOS.\n",
            file=sys.stderr,
        )
        for rel, err in failures.items():
            print(f"--- {rel} ---\n{err}\n", file=sys.stderr)
        print(
            "Common cause: a `case` arm inside a $( ) command substitution. bash 3.2 "
            "reads the pattern's `)` as the end of the substitution. Add the optional "
            'leading parenthesis - `case "$x" in (pattern) ... ;; esac` - which is '
            "POSIX and parses on every shell we target.",
            file=sys.stderr,
        )
        return 1

    print(f"\nOK: all {len(scripts)} tracked shell script(s) parse under bash 3.2 (macOS).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
