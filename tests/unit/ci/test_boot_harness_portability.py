"""Cross-platform contracts for the local boot-performance gates."""

from __future__ import annotations

import importlib.util
import json
import os
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
    assert selected.samefile(sys.executable)


def test_fresh_run_paths_do_not_delete_or_share_previous_data(monkeypatch, tmp_path):
    monkeypatch.setattr(measure_boot, "BENCH_DIR", tmp_path / "owned")
    first, first_missions = measure_boot._fresh_run_dirs()
    marker = first / "keep.txt"
    marker.write_text("first run", encoding="utf-8")
    second, second_missions = measure_boot._fresh_run_dirs()
    assert first.parent != second.parent
    assert marker.read_text(encoding="utf-8") == "first run"
    assert all(p.resolve().is_relative_to((tmp_path / "owned").resolve()) for p in (
        first, first_missions, second, second_missions,
    ))


def test_child_environment_pins_both_imports_and_module_data_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "old-checkout"))
    env = measure_boot._bench_env(12345, tmp_path / "data", tmp_path / "missions")
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(REPO_ROOT)
    assert env["JARVIS_DATA_DIR"] == str(tmp_path / "data")
    assert env["JARVIS__MEMORY__DATA_DIR"] == str(tmp_path / "data")


@pytest.mark.parametrize("flag,expected", [(None, "legacy"), ("0", "legacy"), ("1", "fastboot")])
def test_auto_mode_follows_the_actual_launcher_flag(monkeypatch, flag, expected):
    if flag is None:
        monkeypatch.delenv("JARVIS_DESKTOP_FASTBOOT", raising=False)
    else:
        monkeypatch.setenv("JARVIS_DESKTOP_FASTBOOT", flag)
    assert measure_boot._desktop_boot_mode() == expected
    assert measure_boot._desktop_boot_mode("legacy") == "legacy"
    assert measure_boot._desktop_boot_mode("fastboot") == "fastboot"


def test_child_source_mismatch_fails_before_application_spawn(monkeypatch, tmp_path):
    calls = []

    def probe(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "python": sys.executable, "jarvis_file": str(tmp_path / "wrong" / "__init__.py"),
        }))

    monkeypatch.setattr(measure_boot.subprocess, "run", probe)
    with pytest.raises(RuntimeError, match="different checkout"):
        measure_boot._assert_child_source(sys.executable, {})
    assert len(calls) == 1
    assert calls[0][0][0] == sys.executable
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["creationflags"] == measure_boot.NO_WINDOW_CREATIONFLAGS


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
