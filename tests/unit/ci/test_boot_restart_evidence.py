"""The budget guard can consume real restart evidence without creating an app."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ci import check_boot_budget as guard
from scripts.ci.boot_evidence import SOURCE_FILES, read_restart_evidence


@pytest.fixture
def capture(tmp_path):
    source = tmp_path / "checkout"
    hashes = {}
    for relative in SOURCE_FILES:
        file = source / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# test source\n", encoding="utf-8")
        hashes[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    data = {
        "schema_version": 1,
        "measurement": "controlled-desktop-restart",
        "run_id": "dde692d0-d142-4b2e-9d81-ae438b2b864e",
        "pid": 123,
        "launch_mode": "legacy",
        "clock": "parent-perf-counter-from-spawn",
        "capture": "owned-process-stdout",
        "source_root": str(source),
        "jarvis_file": str(source / "jarvis" / "__init__.py"),
        "python": sys.executable,
        "source_sha256": hashes,
        "started_at": "2026-10-01T12:00:00+00:00",
        "finished_at": "2026-10-01T12:00:30+00:00",
        "voice_enabled": True,
        "observations": [
            {"name": "shell_html_served", "elapsed_ms": 1000, "http_status": 200,
             "html_verified": True},
            {"name": "app_interactive", "elapsed_ms": 4000,
             "stdout_line": "APP_INTERACTIVE_MS=3000.0"},
            {"name": "local_voice_usable", "elapsed_ms": 3500,
             "stdout_line": "VOICE_USABLE_MS=2500.0"},
        ],
    }
    return source, tmp_path / "evidence.json", data


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_evidence_uses_parent_elapsed_not_child_clock(capture):
    root, path, data = capture
    _write(path, data)
    summary, voice = read_restart_evidence(path, root)
    assert voice is True
    assert summary["median_app_interactive_wall_ms"] == 4000
    assert summary["median_voice_usable_wall_ms"] == 3500


@pytest.mark.parametrize("mutation", ["source", "missing", "nan", "wrong-clock", "duplicate"])
def test_incomplete_stale_or_ambiguous_evidence_is_rejected(capture, mutation):
    root, path, data = capture
    if mutation == "source":
        (root / SOURCE_FILES[0]).write_text("changed\n", encoding="utf-8")
    elif mutation == "missing":
        data["observations"].pop()
    elif mutation == "nan":
        data["observations"][0]["elapsed_ms"] = float("nan")
    elif mutation == "wrong-clock":
        data["clock"] = "child-BOOT_READY-clock"
    else:
        data["observations"].append(data["observations"][0])
    _write(path, data)
    with pytest.raises(ValueError):
        read_restart_evidence(path, root)


def test_evidence_guard_never_spawns_or_probes_audio_and_still_enforces_budget(
    capture, monkeypatch
):
    root, path, data = capture
    monkeypatch.setattr(guard, "REPO_ROOT", root)

    def forbidden(*args, **kwargs):
        raise AssertionError("evidence mode must be read-only")

    monkeypatch.setattr(guard.subprocess, "run", forbidden)
    monkeypatch.setattr(guard, "_audio_capable", forbidden)
    monkeypatch.setenv("JARVIS_BOOT_BUDGET_WINDOW_MS", "8000")
    monkeypatch.setenv("JARVIS_BOOT_BUDGET_INTERACTIVE_MS", "20000")
    monkeypatch.setenv("JARVIS_BOOT_BUDGET_VOICE_MS", "20000")
    _write(path, data)
    assert guard.main(["--evidence", str(path)]) == 0
    data["observations"][2]["elapsed_ms"] = 25000
    _write(path, data)
    assert guard.main(["--evidence", str(path)]) == 1


def test_no_audio_still_requires_app_interactive(capture):
    root, path, data = capture
    data["voice_enabled"] = False
    data["voice_skip_reason"] = "no-audio-device"
    data["observations"] = data["observations"][:1]
    _write(path, data)
    with pytest.raises(ValueError, match="app_interactive"):
        read_restart_evidence(path, root)


def test_ordinary_guard_pins_interpreter_mode_and_fresh_output(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        output = Path(args[args.index("--output") + 1])
        _write(output, {"median_wall_ms": 1, "median_app_interactive_wall_ms": 2})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(guard, "_audio_capable", lambda: False)
    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.setattr(guard.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    assert guard.main([]) == 0
    args, kwargs = calls[0]
    assert args[0] == args[args.index("--python") + 1] == sys.executable
    assert args[args.index("--mode") + 1] == "auto"
    assert "--interactive" in args
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["creationflags"] == guard.NO_WINDOW_CREATIONFLAGS
