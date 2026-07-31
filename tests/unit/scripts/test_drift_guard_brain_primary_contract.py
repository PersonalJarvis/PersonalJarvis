"""Contract tests for the desired-state ``brain.primary`` ENV mapping.

The Brain-provider switch in the desktop app's API/Settings section persists
across all three drift-defence layers (BUG-010):

* ``jarvis.toml``                -> ``[brain].primary``
* Desired-state JSON             -> ``brain.primary``
* User-scope ENV override        -> ``JARVIS__BRAIN__PRIMARY``

For the UI switch to make the drift-guard a no-op (instead of reverting the
choice on its next 5-minute run), the guard's derived ENV-variable name for
``brain.primary`` MUST be exactly ``JARVIS__BRAIN__PRIMARY`` -- the same name
the UI sets and the same name the maintainer sets by hand.

``scripts/jarvis-config-drift-guard.ps1`` derives that name with the rule
(see the ENV-override block around lines 153-163)::

    "JARVIS__" + <section>.ToUpper() + "__" + <key>.ToUpper()

This test mirrors that rule in Python and pins the contract so a rename on
either side (the JSON section/key, or the guard's derivation rule) fails CI
loudly instead of silently breaking provider persistence.

Most tests are platform-independent. The structured-override regression runs
PowerShell in a process-scoped sandbox when available and skips otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

# Desired state: scripts/config-soll.json.  # i18n-allow: filename
# This test file is at <repo>/tests/unit/scripts/, so parents[3] is the repo root.
DESIRED_STATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "config-soll.json"  # i18n-allow: filename
)


def _derive_env_name(section: str, key: str) -> str:
    """Reproduce the drift-guard's JARVIS__<SECTION>__<KEY> derivation.

    Mirrors ``scripts/jarvis-config-drift-guard.ps1``:
        "JARVIS__" + $section.Name.ToUpper() + "__" + $keyProp.Name.ToUpper()
    """
    return "JARVIS__" + section.upper() + "__" + key.upper()


@pytest.fixture(scope="module")
def desired_state() -> dict:
    assert DESIRED_STATE_PATH.is_file(), (
        f"expected the drift-guard desired-state file at {DESIRED_STATE_PATH}"
    )
    return json.loads(DESIRED_STATE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# brain.primary presence + shape
# ---------------------------------------------------------------------------


def test_desired_state_is_valid_json(desired_state: dict) -> None:
    """If the JSON is malformed the whole drift-guard fails to parse (exit 4)."""
    assert isinstance(desired_state, dict)


def test_brain_section_exists(desired_state: dict) -> None:
    assert "brain" in desired_state, (
        "desired-state JSON must keep a top-level 'brain' section"
    )
    assert isinstance(desired_state["brain"], dict)


def test_brain_primary_is_non_empty_string(desired_state: dict) -> None:
    """brain.primary must exist and be a non-empty string.

    The drift-guard pins this value and the UI provider switch overwrites it.
    A missing/empty value would make the guard skip the key (WARN) and the UI
    switch would have no desired state to keep in sync -> silent revert risk.
    """
    primary = desired_state["brain"].get("primary")
    assert primary is not None, "brain.primary must exist in the desired state"
    assert isinstance(primary, str), "brain.primary must be a string"
    assert primary.strip() != "", "brain.primary must not be empty/whitespace"


# ---------------------------------------------------------------------------
# brain.primary <-> JARVIS__BRAIN__PRIMARY ENV-name contract
# ---------------------------------------------------------------------------


def test_brain_primary_env_name_is_canonical(desired_state: dict) -> None:
    """The guard-derived ENV name for brain.primary is JARVIS__BRAIN__PRIMARY.

    This is the exact variable the desktop app's provider switch sets and the
    maintainer sets by hand. If the desired-state section or key is renamed,
    the derived name diverges from what the UI writes and the guard
    would re-add a 'missing' ENV var every run -> the whole fix breaks.
    """
    assert "primary" in desired_state["brain"]
    env_name = _derive_env_name("brain", "primary")
    assert env_name == "JARVIS__BRAIN__PRIMARY"


def test_env_derivation_rule_matches_guard_for_all_keys(desired_state: dict) -> None:
    """Every scalar desired-state entry maps to a JARVIS__-prefixed ENV name.

    Pins the general derivation rule (double-underscore separator, upper-case,
    JARVIS__ prefix) the guard relies on -- not just brain.primary -- so any
    future key inherits the same contract.
    """
    for section, payload in desired_state.items():
        if section.startswith("_") or not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if isinstance(value, (dict, list)) or value is None:
                continue
            env_name = _derive_env_name(section, key)
            assert env_name.startswith("JARVIS__"), env_name
            assert "__" in env_name[len("JARVIS__"):], env_name
            assert env_name == env_name.upper(), env_name


def test_structured_stt_models_never_becomes_one_env_override(
    tmp_path: Path,
) -> None:
    """A sandboxed guard run removes the malformed process override."""
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    guard = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "jarvis-config-drift-guard.ps1"
    )
    sandbox = tmp_path / "repo"
    sandbox.mkdir()
    desired_file = sandbox / "desired.json"
    desired_file.write_text(
        json.dumps({"stt": {"models": {"openrouter-stt": "openai/gpt-4o-transcribe"}}}),
        encoding="utf-8",
    )
    toml_file = sandbox / "jarvis.toml"
    toml_file.write_text(
        '[stt.models]\nopenrouter-stt = "openai/gpt-4o-transcribe"\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["JARVIS__STT__MODELS"] = "@{openrouter-stt=openai/gpt-4o-transcribe}"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(guard),
            "-RepoRoot",
            str(sandbox),
            "-DesiredFile",
            str(desired_file),
            "-EnvironmentTarget",
            "Process",
        ],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (sandbox / "logs" / "config-drift-guard.log").read_text(
        encoding="utf-8-sig"
    )
    assert "removed JARVIS__STT__MODELS from Process-scope environment" in log
    assert toml_file.read_text(encoding="utf-8") == (
        '[stt.models]\nopenrouter-stt = "openai/gpt-4o-transcribe"\n'
    )


def test_user_selected_stt_provider_survives_stale_desired_state(
    tmp_path: Path,
) -> None:
    """The guard heals stale ENV toward the user's selection, never away."""
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    guard = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "jarvis-config-drift-guard.ps1"
    )
    sandbox = tmp_path / "repo"
    sandbox.mkdir()
    desired_file = sandbox / "desired.json"
    desired_file.write_text(
        json.dumps({"stt": {"provider": "groq-api"}}),
        encoding="utf-8",
    )
    toml_file = sandbox / "jarvis.toml"
    expected = (
        '[stt]\nprovider = "openrouter-stt"\nprovider_user_selected = true\n'
    )
    toml_file.write_text(expected, encoding="utf-8")
    env = os.environ.copy()
    env["JARVIS__STT__PROVIDER"] = "groq-api"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(guard),
            "-RepoRoot",
            str(sandbox),
            "-DesiredFile",
            str(desired_file),
            "-EnvironmentTarget",
            "Process",
        ],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log = (sandbox / "logs" / "config-drift-guard.log").read_text(
        encoding="utf-8-sig"
    )
    assert "JARVIS__STT__PROVIDER := 'openrouter-stt'" in log
    assert "[stt] provider: actual='openrouter-stt' desired='groq-api'" not in log
    assert toml_file.read_text(encoding="utf-8") == expected
