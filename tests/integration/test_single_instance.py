"""Integration-Tests fuer den Single-Instance-Lock der Desktop-App.

Fokus: Lock-Akquise, Re-Entry-Verweigerung, Stale-PID-Uebernahme. Das
eigentliche ``webview.start()`` ist nicht headless-testbar und wird hier
bewusst nicht ausgeloest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jarvis.ui import desktop_app


@pytest.fixture
def lock_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Isolierte Lock- und Meta-Pfade pro Test, vermeidet Interferenz mit
    einer echten Jarvis-Installation auf derselben Maschine."""
    return tmp_path / "jarvis.lock", tmp_path / ".jarvis-running"


def test_first_acquire_succeeds(lock_paths: tuple[Path, Path]) -> None:
    lock_p, meta_p = lock_paths
    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert lock.is_locked
    finally:
        lock.release()


def test_second_acquire_raises_when_first_alive(
    lock_paths: tuple[Path, Path],
) -> None:
    lock_p, meta_p = lock_paths

    first = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        # Meta-Sidecar mit unserer eigenen PID (garantiert lebendig).
        meta_p.write_text(
            json.dumps({"pid": os.getpid(), "port": 47821, "started_at": 0.0}),
            encoding="utf-8",
        )
        with pytest.raises(desktop_app.SingleInstanceError):
            desktop_app.acquire_single_instance_lock(
                lock_path=lock_p, meta_path=meta_p
            )
    finally:
        first.release()


def test_release_frees_lock(lock_paths: tuple[Path, Path]) -> None:
    lock_p, meta_p = lock_paths

    first = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    first.release()

    second = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert second.is_locked
    finally:
        second.release()


def _find_dead_pid() -> int:
    """Liefert einen PID der mit sehr hoher Wahrscheinlichkeit nicht lebt.

    Strategie: probier 999999 abwaerts — auf modernen Windows/Linux sind
    PIDs typischerweise < 100000. psutil.pid_exists ist O(1) pro Call.
    """
    import psutil  # type: ignore[import-not-found]

    for candidate in (999983, 999979, 999961, 999959):
        if not psutil.pid_exists(candidate):
            return candidate
    pytest.skip("Kein toter PID-Kandidat gefunden — System zu voll.")
    return 0  # unreachable


def test_stale_lock_gets_taken_over(lock_paths: tuple[Path, Path]) -> None:
    """Wenn das Sidecar einen toten PID nennt, darf die zweite Acquire
    den Lock uebernehmen (statt SingleInstanceError zu werfen)."""
    lock_p, meta_p = lock_paths

    dead_pid = _find_dead_pid()

    # Stale-Zustand simulieren: Meta-Sidecar schreiben, *ohne* dass der
    # FileLock tatsaechlich gehalten wird — Prozess-Crash-Szenario. Da
    # filelock POSIX-/Windows-OS-Locks nutzt, ist das Lock nach Prozess-
    # Exit automatisch frei; das Sidecar bleibt aber liegen.
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(
        json.dumps({"pid": dead_pid, "port": 47821, "started_at": 0.0}),
        encoding="utf-8",
    )
    # Lock-File existiert evtl. auch noch; das ist ok — acquire kuemmert sich.
    lock_p.touch()

    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        # Kritisch: _keine_ SingleInstanceError trotz existierendem Sidecar.
        assert lock.is_locked
    finally:
        lock.release()


def test_stale_lock_with_contention_cleans_sidecar(
    lock_paths: tuple[Path, Path],
) -> None:
    """Harter Stale-Pfad: OS-Lock wird von einem Subprocess gehalten, der
    sich schnell beendet. Sidecar nennt einen unabhaengigen toten PID
    (nicht der Subprocess-PID, sonst waere es kein Stale-Fall nach
    Definition). acquire muss nach Subprocess-Exit das Lock kriegen und
    den Sidecar beseitigen.
    """
    import subprocess
    import sys as _sys

    lock_p, meta_p = lock_paths
    dead_pid = _find_dead_pid()

    # Subprocess der das Lock kurz haelt (250ms) dann beendet.
    holder_code = (
        "from filelock import FileLock;"
        "import time;"
        f"l = FileLock(r'{lock_p}');"
        "l.acquire();"
        "print('LOCKED', flush=True);"
        "time.sleep(0.25);"
        "l.release();"
    )
    proc = subprocess.Popen(
        [_sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Warte bis der Holder das Lock wirklich haelt.
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert b"LOCKED" in line, f"Holder-Subprocess gab kein LOCKED aus: {line!r}"

    # Sidecar mit *anderem* toten PID (nicht proc.pid!) — sonst wuerde
    # _pid_alive(proc.pid) True zurueckgeben solange der Subprocess laeuft,
    # und wir waeren im "Jarvis laeuft bereits"-Pfad statt Stale-Pfad.
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta_p.write_text(
        json.dumps({"pid": dead_pid, "port": 47821, "started_at": 0.0}),
        encoding="utf-8",
    )

    # Jetzt acquire: erster Versuch (timeout=0) scheitert -> Sidecar gelesen
    # -> PID tot -> Sidecar loeschen -> retry (timeout=2s) -> klappt nachdem
    # der Subprocess freigibt.
    lock = desktop_app.acquire_single_instance_lock(
        lock_path=lock_p, meta_path=meta_p
    )
    try:
        assert lock.is_locked
        assert not meta_p.exists(), (
            "Sidecar mit totem PID muss im Stale-Pfad entfernt werden."
        )
    finally:
        lock.release()
        proc.wait(timeout=3)
