"""The drift guard never proposes a dotted environment override and never
repairs a desired empty string that is already absent.

2026-08-25: ``JARVIS__BRAIN.PROVIDERS.OLLAMA__MODEL`` was "repaired" every five
minutes. Python nests ``JARVIS__`` overrides on ``__`` only, so the dotted
variable was inert, and setting a User-scope variable to ``""`` deletes it, so
the next run found it absent again. These tests run the real script with
``-DryRun -EnvironmentTarget Process`` on a temporary repo root (the machine's
User-scope registry is never touched) and are skipped where no PowerShell
exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "jarvis-config-drift-guard.ps1"
_SHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    _SHELL is None, reason="PowerShell is not installed; the drift guard is a Windows daemon"
)

_TOML = """\
[brain]
primary = "openrouter"

[brain.providers.ollama]
model = ""
tool_model = "qwen3.5:4b"

[tts]
provider = "grok-voice"
vertex_project = ""
"""

_SOLL = {  # i18n-allow
    "_comment": "test baseline",
    "brain": {"primary": "openrouter"},
    "brain.providers.ollama": {"model": "", "tool_model": "qwen3.5:4b"},
    "tts": {"provider": "grok-voice", "vertex_project": ""},
}


def _run(repo: Path, env: dict[str, str]) -> str:
    cmd = [
        str(_SHELL),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_SCRIPT),
        "-RepoRoot",
        str(repo),
        "-EnvironmentTarget",
        "Process",
        "-DryRun",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = repo / "logs" / "config-drift-guard.log"
    return proc.stdout + log.read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "jarvis.toml").write_text(_TOML, encoding="utf-8")
    (tmp_path / "scripts" / "config-soll.json").write_text(  # i18n-allow
        json.dumps(_SOLL, indent=2), encoding="utf-8"  # i18n-allow
    )
    return tmp_path


def _clean_env() -> dict[str, str]:
    # The child only sees Process scope, so a JARVIS__ variable inherited from
    # the developer's shell must not leak into the assertions.
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS__")}
    # The top-level scalar pins keep their (legitimate) environment layer.
    env["JARVIS__BRAIN__PRIMARY"] = "openrouter"
    env["JARVIS__TTS__PROVIDER"] = "grok-voice"
    return env


def test_dry_run_proposes_no_dotted_env_and_no_repair_for_desired_empty(repo: Path) -> None:
    out = _run(repo, _clean_env())
    assert "JARVIS__BRAIN.PROVIDERS" not in out
    assert "JARVIS__TTS__VERTEX_PROJECT" not in out
    assert "TOML is in sync" in out
    assert "Detected" not in out
    assert "repair(s) applied" not in out


def test_dry_run_removes_an_existing_dotted_variable_and_keeps_the_toml_guard(
    repo: Path,
) -> None:
    env = _clean_env()
    env["JARVIS__BRAIN.PROVIDERS.OLLAMA__MODEL"] = "qwen3.5:4b"
    # A TOML value that drifted from the baseline is still caught for a dotted
    # section: that comparison never depended on the environment.
    (repo / "jarvis.toml").write_text(
        _TOML.replace('tool_model = "qwen3.5:4b"', 'tool_model = "other:1b"'),
        encoding="utf-8",
    )
    out = _run(repo, env)
    assert "JARVIS__BRAIN.PROVIDERS.OLLAMA__MODEL (no scalar codec" in out
    assert "Removing 1 unsupported structured environment override(s)" in out
    assert "[brain.providers.ollama] tool_model: actual='other:1b' desired='qwen3.5:4b'" in out
    assert "missing or divergent environment override" not in out
    assert "jarvis.toml was not changed" in out
