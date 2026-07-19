"""Cross-platform contracts for the local boot-performance gates."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "measure_boot_portability", REPO_ROOT / "scripts" / "measure_boot.py"
)
assert _SPEC is not None and _SPEC.loader is not None
measure_boot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measure_boot)
_GUARD_SPEC = importlib.util.spec_from_file_location(
    "check_boot_budget_portability",
    REPO_ROOT / "scripts" / "ci" / "check_boot_budget.py",
)
assert _GUARD_SPEC is not None and _GUARD_SPEC.loader is not None
check_boot_budget = importlib.util.module_from_spec(_GUARD_SPEC)
_GUARD_SPEC.loader.exec_module(check_boot_budget)


def test_default_benchmark_interpreter_exists_on_this_host() -> None:
    selected = Path(measure_boot.DEFAULT_PYTHON)

    assert selected.is_file()
    if sys.platform != "win32":
        assert selected.samefile(sys.executable)


def test_pre_push_prefers_the_repository_venv() -> None:
    hook = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")

    posix = hook.index('PY=".venv/bin/python3"')
    windows = hook.index('PY=".venv/Scripts/python.exe"')
    path_fallback = hook.index("command -v python3")
    assert posix < path_fallback
    assert windows < path_fallback


@pytest.mark.parametrize(
    ("granted", "expected"),
    ((False, False), (True, True)),
)
def test_macos_voice_budget_requires_an_existing_microphone_grant(
    monkeypatch: pytest.MonkeyPatch,
    granted: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(check_boot_budget.sys, "platform", "darwin")
    monkeypatch.setattr(
        check_boot_budget,
        "_macos_microphone_granted",
        lambda: granted,
    )
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(query_devices=lambda: [{"max_input_channels": 1}]),
    )

    assert check_boot_budget._audio_capable() is expected
