"""Exercise the release shell against fake Git/GitHub commands, without publishing."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


@pytest.mark.parametrize(
    "watch_exit,gate_state,land_exit,success",
    [
        (0, "success", 0, True),
        (1, "failure", 0, False),
        (0, "in_progress", 0, False),
        (0, "success", 1, False),
    ],
)
def test_only_a_green_candidate_can_land_and_publish(
    tmp_path: Path,
    watch_exit: int,
    gate_state: str,
    land_exit: int,
    success: bool,
) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        # System32/bash.exe enters WSL, which does not preserve this test's
        # Windows environment or paths. Use the runner's Git Bash instead.
        git = shutil.which("git")
        roots = (Path(git).parent, Path(git).parent.parent / "bin") if git else ()
        bash = next(
            (str(root / "bash.exe") for root in roots if (root / "bash.exe").is_file()), None
        )
    if bash is None:
        pytest.skip("the workflow shell requires Bash")
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / ".github/workflows/release-cut.yml").read_text("utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["cut"]["steps"]
        if step.get("name") == "Check the version commit before landing and tagging"
    )
    trace = tmp_path / "commands.txt"
    # Functions shadow commands only inside this disposable shell. No real
    # repository, credentials, GitHub API, or release is touched by this test.
    fakes = r"""
git() {
  printf 'git %s\n' "$*" >> "$TRACE_FILE"
  if [ "$1" = rev-parse ]; then printf '%040d\n' 1; fi
  if [ "$1 $2 $3" = 'push origin HEAD:main' ]; then return "$LAND_EXIT"; fi
  return 0
}
gh() {
  printf 'gh %s\n' "$*" >> "$TRACE_FILE"
  case "$1 $2" in
    'workflow run') return 0 ;;
    'run list') printf '123\n' ;;
    'run watch') return "$WATCH_EXIT" ;;
    api\ *) printf '%s\n' "$GATE_STATE" ;;
    *) return 2 ;;
  esac
}
"""
    script = tmp_path / "release-test.sh"
    script.write_text(fakes + step["run"], encoding="utf-8", newline="\n")
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", script.name],
        cwd=tmp_path,
        env={
            **os.environ,
            "V": "1.2.3",
            "GITHUB_RUN_ID": "77",
            "GITHUB_REPOSITORY": "example/project",
            "TRACE_FILE": trace.name,
            "PATH": "",
            "BASH_ENV": "/dev/null",
            "WATCH_EXIT": str(watch_exit),
            "GATE_STATE": gate_state,
            "LAND_EXIT": str(land_exit),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert trace.exists(), result.stdout + result.stderr
    commands = trace.read_text("utf-8").splitlines()
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    candidate = "git push origin HEAD:refs/heads/release-cut/v1.2.3-77"
    watch = "gh run watch 123 --exit-status --interval 30"
    assert commands.index(candidate) < commands.index(watch)
    if watch_exit or gate_state != "success":
        assert "git push origin HEAD:main" not in commands
    if success:
        assert commands.index(watch) < commands.index("git push origin HEAD:main")
        assert commands.index("git push origin HEAD:main") < commands.index(
            "git tag -a v1.2.3 -m v1.2.3"
        )
        assert commands[-1] == "git push origin v1.2.3"
    else:
        assert not any(command.startswith("git tag ") for command in commands)
        assert "git push origin v1.2.3" not in commands
