"""Exercise release shell boundaries without a repository, credentials or network."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_SHA = "1" * 40
_FAKES = r"""
git() {
  printf 'git %s\n' "$*" >> "$TRACE_FILE"
  case "$1" in
    rev-parse)
      if [ "$2" = HEAD ]; then printf '%s\n' "$FAKE_SHA";
      else printf '%s\n' "$TAG_SHA"; fi ;;
    merge-base) return "$ANCESTOR_EXIT" ;;
    show-ref) return "$TAG_EXISTS" ;;
    checkout) : > checked-out ;;
  esac
  return 0
}
gh() {
  printf 'gh %s\n' "$*" >> "$TRACE_FILE"
  case "$1 $2" in
    'pr create') printf 'https://github.com/example/project/pull/1\n' ;;
    'pr merge') return "$LAND_EXIT" ;;
    'pr view') printf '%s\n' "$FAKE_SHA" ;;
    'workflow run') return 0 ;;
    'run list') printf '123\n' ;;
    'run view') printf '\n' ;;
    'run watch') return "$WATCH_EXIT" ;;
    api\ *) printf '%s\n' "$GATE_STATE" ;;
    *) return 2 ;;
  esac
}
python() {
  printf 'python %s\n' "$*" >> "$TRACE_FILE"
  if [ "$1" = -c ]; then
    if [ -f checked-out ]; then printf '%s\n' "$RESUME_VERSION";
    else printf '%s\n' "$BASE_VERSION"; fi
  else return "$ADMIT_EXIT"; fi
}
"""


def _exercise(
    tmp_path: Path,
    step_names: list[str],
    *,
    workflow_name: str = "release-cut.yml",
    job_id: str = "cut",
    **overrides: str,
):
    bash = shutil.which("bash")
    if os.name == "nt":
        # System32/bash.exe enters WSL instead of the runner's Windows shell.
        git = shutil.which("git")
        roots = (Path(git).parent, Path(git).parent.parent / "bin") if git else ()
        bash = next(
            (str(root / "bash.exe") for root in roots if (root / "bash.exe").is_file()), None
        )
    if bash is None:
        pytest.skip("the workflow shell requires Bash")
    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / ".github/workflows" / workflow_name).read_text("utf-8"))
    steps = {step.get("name"): step for step in workflow["jobs"][job_id]["steps"]}
    trace = tmp_path / "commands.txt"
    script = tmp_path / "release-test.sh"
    script.write_text(
        _FAKES + "\n".join(steps[name]["run"] for name in step_names),
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", script.name],
        cwd=tmp_path,
        env={
            **os.environ,
            "V": "1.2.3",
            "GITHUB_RUN_ID": "77",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_REPOSITORY": "example/project",
            "TRACE_FILE": trace.name,
            "GITHUB_OUTPUT": "outputs.txt",
            "GITHUB_STEP_SUMMARY": "summary.txt",
            "RUNNER_TEMP": ".",
            "PATH": "",
            "BASH_ENV": "/dev/null",
            "TOKEN_IS_BOT": "true",
            "WATCH_EXIT": "0",
            "GATE_STATE": "success",
            "LAND_EXIT": "0",
            "ADMIT_EXIT": "0",
            "ANCESTOR_EXIT": "0",
            "FAKE_SHA": _SHA,
            "RESUME_SHA": _SHA,
            "BASE_VERSION": "1.2.3",
            "RESUME_VERSION": "1.2.3",
            "RELEASE_REF": "refs/tags/v1.2.3",
            "TAG_EXISTS": "1",
            "TAG_SHA": _SHA,
            **overrides,
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    commands = trace.read_text("utf-8").splitlines() if trace.exists() else []
    return result, commands


@pytest.mark.parametrize(
    "ref,strict",
    [
        ("refs/tags/v2.9.0", False),
        ("refs/tags/v2.9.1", True),
        ("refs/tags/v2.10.0", True),
        ("refs/tags/v2.9.0-rc.1", True),
        ("refs/tags/v2.9.00", True),
        ("refs/heads/v2.9.0", True),
        ("refs/heads/main", True),
        ("", True),
    ],
)
def test_plugin_acceptance_exception_is_exactly_one_release(tmp_path, ref, strict):
    result, commands = _exercise(
        tmp_path,
        ["Qualify plugin auth with the v2.9.0 exception"],
        workflow_name="ci.yml",
        job_id="release-qualification",
        RELEASE_REF=ref,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    command = "python scripts/ci/check_plugin_auth_contract.py"
    assert commands == [command + (" --require-e2e-pass" if strict else "")]
    summary = tmp_path / "summary.txt"
    if strict:
        assert not summary.exists()
    else:
        assert "no plugin E2E PASS is asserted" in result.stdout
        assert "Existing audit results remain unchanged" in summary.read_text("utf-8")


@pytest.mark.parametrize("ref", ["refs/tags/v2.9.0", "refs/tags/v2.9.1"])
def test_plugin_exception_never_ignores_a_failing_auth_contract(tmp_path, ref):
    result, _commands = _exercise(
        tmp_path,
        ["Qualify plugin auth with the v2.9.0 exception"],
        workflow_name="ci.yml",
        job_id="release-qualification",
        RELEASE_REF=ref,
        ADMIT_EXIT="1",
    )
    assert result.returncode != 0
    assert not (tmp_path / "summary.txt").exists()


@pytest.mark.parametrize(
    "overrides,success",
    [
        ({}, True),
        ({"WATCH_EXIT": "1"}, False),
        ({"GATE_STATE": "in_progress"}, False),
        ({"LAND_EXIT": "1"}, False),
        ({"ADMIT_EXIT": "1"}, False),
    ],
)
def test_only_a_green_candidate_can_land_and_publish(tmp_path, overrides, success):
    result, commands = _exercise(
        tmp_path,
        [
            "Check the version commit before landing and tagging",
            "Tag the admitted release",
        ],
        **overrides,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    candidate = "git push origin HEAD:refs/heads/release-cut/v1.2.3-77-2"
    watch = "gh run watch 123 --exit-status --interval 30"
    merge = f"gh pr merge release-cut/v1.2.3-77-2 --merge --match-head-commit {_SHA}"
    assert commands.index(candidate) < commands.index(watch)
    assert any("--event pull_request" in command for command in commands)
    assert "git push origin HEAD:main" not in commands
    if overrides.get("WATCH_EXIT") or overrides.get("GATE_STATE"):
        assert merge not in commands
    if success:
        assert commands.index(watch) < commands.index(merge)
        assert commands.index(merge) < commands.index("git tag -a v1.2.3 -m v1.2.3")
        assert commands[-1] == "git push origin v1.2.3"
    else:
        assert not any(command.startswith("git tag ") for command in commands)


@pytest.mark.parametrize(
    "overrides,success",
    [
        ({}, True),
        ({"RESUME_SHA": "refs/heads/unmerged"}, False),
        ({"ANCESTOR_EXIT": "1"}, False),
        ({"RESUME_VERSION": "1.2.2"}, False),
        ({"ADMIT_EXIT": "1"}, False),
        ({"TAG_EXISTS": "0", "TAG_SHA": "2" * 40}, False),
    ],
)
def test_resume_requires_merged_current_version_and_admission(tmp_path, overrides, success):
    result, commands = _exercise(
        tmp_path,
        [
            "Select an already merged version commit",
            "Tag the admitted release",
        ],
        **overrides,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    if overrides.get("ANCESTOR_EXIT") or overrides.get("RESUME_SHA"):
        assert not any(command.startswith("git checkout ") for command in commands)
    if success:
        assert commands[-1] == "git push origin v1.2.3"
        assert (tmp_path / "outputs.txt").read_text("utf-8") == "version=1.2.3\npushed=true\n"
    else:
        assert not any(command.startswith("git tag ") for command in commands)
        assert "git push origin v1.2.3" not in commands
