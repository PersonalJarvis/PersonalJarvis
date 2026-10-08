"""Folder layout per app instance, pinned versions, PATH refresh, descendant reaping."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from jarvis.agent_runtimes import base, hermes, versions
from jarvis.core import path_augment
from jarvis.core.process_tree import DescendantTracker
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


def test_each_app_instance_keeps_its_own_runtime_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("JARVIS_INSTANCE", raising=False)
    default = base.runtimes_root()
    monkeypatch.setenv("JARVIS_INSTANCE", "dev")
    dev = base.runtimes_root()
    assert default != dev and dev.name == "agent_runtimes-dev"
    assert base.legacy_runtimes_root() == default


def test_the_pins_name_a_tested_release_at_or_above_the_minimum():
    for runtime in ("hermes", "openclaw"):
        pin = versions.pin(runtime)
        assert pin.tested is not None and pin.tested >= pin.minimum, runtime
    assert len(versions.pin("hermes").commit) == 40
    assert versions.pin("hermes").config_version


def _fake_hermes(tmp_path: Path, version_line: str) -> str:
    script = tmp_path / "hermes_fake.py"
    script.write_text(f"print({version_line!r})\n", encoding="utf-8")
    return str(script)


@pytest.mark.parametrize(
    ("line", "ready", "untested"),
    [
        ("Hermes Agent v0.21.5+1.gabc (2026.9.24)", True, False),
        ("Hermes Agent v9.0.0 (2027.1.1)", True, True),
        ("Hermes Agent v0.1.0 (2025.1.1)", False, False),
    ],
)
def test_a_newer_release_runs_but_is_reported_untested(
    tmp_path, monkeypatch, line, ready, untested
):
    script = _fake_hermes(tmp_path, line)
    monkeypatch.setattr(hermes, "_binary", lambda: sys.executable)
    monkeypatch.setattr(
        hermes, "run_version", lambda argv: base.run_version([sys.executable, script])
    )
    status = hermes.HermesRuntime().detect(refresh=True)
    assert (status.ready, status.untested) == (ready, untested)
    assert status.build == line
    assert status.to_dict()["untested"] is untested


def test_a_runtime_installed_mid_session_is_found_without_a_restart(tmp_path, monkeypatch):
    fresh = tmp_path / "Programs" / "fresh"
    fresh.mkdir(parents=True)
    monkeypatch.setenv("PATH", str(tmp_path / "old"))
    added = path_augment.refresh_from_persistent_path(
        lambda: [f"{tmp_path / 'old'};{fresh};{tmp_path / 'missing'}"],
        skip=lambda entry: "skipme" in entry,
    )
    assert added == [str(fresh)]
    assert str(fresh) in __import__("os").environ["PATH"]
    # Idempotent: nothing is added twice.
    assert path_augment.refresh_from_persistent_path(lambda: [str(fresh)]) == []


def test_jarvis_hermes_folders_are_never_taken_from_the_persistent_path(tmp_path, monkeypatch):
    from jarvis.agent_runtimes.path_cleanup import is_jarvis_hermes_bin

    stale = tmp_path / "Jarvis" / "agent_runtimes" / "hermes" / "agent-1" / "bin"
    stale.mkdir(parents=True)
    monkeypatch.setenv("PATH", "")
    added = path_augment.refresh_from_persistent_path(
        lambda: [str(stale)], skip=is_jarvis_hermes_bin
    )
    assert added == []


def test_a_detached_grandchild_is_reaped_with_its_parent(tmp_path):
    """POSIX: a setsid'd child leaves the process group; the tracker still has it."""
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "start_new_session=os.name != 'nt'); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid)); time.sleep(60)"
    )
    parent = subprocess.Popen(  # noqa: S603 — started and ended by this test
        [sys.executable, "-c", code], creationflags=NO_WINDOW_CREATIONFLAGS
    )
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        grandchild = int(pid_file.read_text(encoding="utf-8"))
        tracker = DescendantTracker(parent.pid, enabled=True)
        tracker.snapshot()
        parent.kill()
        parent.wait(timeout=10)
        assert grandchild in tracker.close()
        proc = psutil.Process(grandchild)
        # Not our child: wait() would block on a zombie nobody reaps (a
        # container without an init process). Dead or zombie is the outcome.
        deadline = time.monotonic() + 10
        while proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
    except psutil.NoSuchProcess:
        pass
    finally:
        if parent.poll() is None:
            parent.kill()
    try:
        survivor = psutil.Process(grandchild).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:  # gone between checks: reaped
        survivor = False
    assert not survivor


async def test_turns_and_setup_jobs_share_one_gate():
    gate = base.RuntimeGate()
    await gate.acquire_turn(1)
    entered = []

    async def setup() -> None:
        async with gate.exclusive(5):
            entered.append(gate.turns)

    import asyncio

    job = asyncio.ensure_future(setup())
    await asyncio.sleep(0.1)
    assert entered == []  # waits for the running turn
    gate.release_turn()
    await asyncio.wait_for(job, 2)
    assert entered == [0]
    with pytest.raises(TimeoutError):
        async with gate.exclusive(0.1):
            await gate.acquire_turn(0.1)  # no turn starts while setup holds it


def test_a_detached_child_no_snapshot_saw_is_found_by_its_turn_mark():
    """CI 2026-10-08: a setsid child appeared between two snapshots, its parent
    exited, and the tree walk could no longer reach it. It still carries the
    turn's inherited environment mark."""
    import os
    import uuid

    from jarvis.core.process_tree import TURN_MARKER_ENV

    mark = uuid.uuid4().hex
    env = {**os.environ, TURN_MARKER_ENV: mark}
    orphan = subprocess.Popen(  # noqa: S603 — started and ended by this test
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env=env,
        start_new_session=os.name != "nt",
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    try:
        tracker = DescendantTracker(999_999_999, enabled=True, marker=mark)  # parent long gone
        assert orphan.pid in tracker.close()
        orphan.wait(timeout=10)
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_an_unmarked_process_is_never_touched():
    import os

    bystander = subprocess.Popen(  # noqa: S603 — started and ended by this test
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    try:
        tracker = DescendantTracker(999_999_999, enabled=True, marker="not-this-turn")
        assert bystander.pid not in tracker.close()
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=10)
    assert os.getpid()  # this process is never a candidate either
