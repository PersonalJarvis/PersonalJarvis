from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis import codex_login_guard
from jarvis.core.exclusive_process_lock import ExclusiveProcessLock
from jarvis.core.private_directory import ensure_owner_only_directory


def _guardian_path() -> Path:
    return Path(codex_login_guard.__file__).resolve()


def _environment(profile: Path) -> dict[str, str]:
    safe_names = {
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
    environment = {name: value for name, value in os.environ.items() if name in safe_names}
    environment["CODEX_HOME"] = str(profile)
    return environment


def _start_guardian(
    *,
    lock_path: Path,
    acknowledgement: Path,
    release: Path,
    profile: Path,
    log_dir: Path,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            str(_guardian_path()),
            str(lock_path),
            str(acknowledgement),
            str(release),
            str(Path(sys.executable).resolve()),
            hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            str(log_dir),
            json.dumps(_environment(profile), separators=(",", ":")),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_status(path: Path, wanted: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if path.read_text(encoding="ascii") == wanted:
                return
        except OSError:
            pass
        time.sleep(0.02)
    raise AssertionError(f"guardian did not publish {wanted!r}")


def _publish_control(path: Path, status: str) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(status, encoding="ascii")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _coordination_directory(tmp_path: Path) -> Path:
    coordination = tmp_path / "coordination"
    ensure_owner_only_directory(coordination, create=True)
    return coordination


def test_guardian_runs_isolated_launches_fixed_child_and_acknowledges_ready(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    coordination = _coordination_directory(tmp_path)
    acknowledgement = coordination / "ack"
    release = coordination / "release"
    lock_path = coordination / "profile-process.lock"

    process = _start_guardian(
        lock_path=lock_path,
        acknowledgement=acknowledgement,
        release=release,
        profile=profile,
        log_dir=log_dir,
    )
    _wait_for_status(acknowledgement, "waiting")
    _publish_control(release, "acquire")
    _wait_for_status(acknowledgement, "finished")
    assert process.poll() is None
    _publish_control(release, "release")
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 0
    assert stdout == b""
    assert stderr == b""
    assert acknowledgement.read_text(encoding="ascii") == "finished"
    assert not (profile / "profile-process.lock").exists()


def test_guardian_reports_cross_process_contention_without_launching_child(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    coordination = _coordination_directory(tmp_path)
    acknowledgement = coordination / "ack"
    release = coordination / "release"
    lock_path = coordination / "profile-process.lock"

    with ExclusiveProcessLock.acquire(lock_path, protected_directory=profile):
        process = _start_guardian(
            lock_path=lock_path,
            acknowledgement=acknowledgement,
            release=release,
            profile=profile,
            log_dir=log_dir,
        )
        _wait_for_status(acknowledgement, "waiting")
        _publish_control(release, "acquire")
        stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == codex_login_guard.EXIT_BUSY
    assert stdout == b""
    assert stderr == b""
    assert acknowledgement.read_text(encoding="ascii") == "busy"


def test_guardian_rejects_non_allowlisted_environment_without_acknowledging(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    coordination = _coordination_directory(tmp_path)
    acknowledgement = coordination / "ack"
    environment = {
        "CODEX_HOME": str(profile),
        "UNAPPROVED_SECRET": "must-not-be-forwarded",
    }

    result = codex_login_guard.main(
        (
            str(coordination / "profile-process.lock"),
            str(acknowledgement),
            str(coordination / "release"),
            str(Path(sys.executable).resolve()),
            hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            str(log_dir),
            json.dumps(environment),
        )
    )

    assert result == codex_login_guard.EXIT_INVALID
    assert not acknowledgement.exists()


def test_guardian_forwards_graphical_session_handles_to_the_child(
    tmp_path: Path,
) -> None:
    """Codex must be able to open the OAuth page itself on Linux.

    Windows (ShellExecute) and macOS (``open``) always could; the login child
    on Linux could not, because the display handles the guardian itself has
    were stripped from the environment it hands to Codex. The user was left
    copying a device-code URL out of a terminal — a Linux-only degradation of
    the same flow. These are session handles, never credentials.
    """
    profile = tmp_path / "profile"
    profile.mkdir()

    environment = codex_login_guard._strict_environment(
        json.dumps(
            {
                "CODEX_HOME": str(profile),
                "DISPLAY": ":0",
                "WAYLAND_DISPLAY": "wayland-0",
                "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            }
        )
    )

    assert environment["DISPLAY"] == ":0"
    assert environment["WAYLAND_DISPLAY"] == "wayland-0"
    assert environment["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_guardian_still_rejects_unapproved_session_variables(
    tmp_path: Path,
) -> None:
    """Admitting the display handles must not widen the allowlist generally."""
    profile = tmp_path / "profile"
    profile.mkdir()

    with pytest.raises(ValueError, match="not approved"):
        codex_login_guard._strict_environment(
            json.dumps(
                {
                    "CODEX_HOME": str(profile),
                    "DISPLAY": ":0",
                    "XDG_CURRENT_DESKTOP": "GNOME",
                }
            )
        )


def test_guardian_rejects_a_binary_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="verification failed"):
        codex_login_guard._open_verified_binary(
            str(Path(sys.executable).resolve()),
            "0" * 64,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows environment names are caseless")
def test_guardian_normalizes_windows_environment_names_and_rejects_aliases(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()

    parsed = codex_login_guard._strict_environment(
        json.dumps({"Codex_Home": str(profile), "Path": "safe"})
    )
    assert parsed == {"CODEX_HOME": str(profile), "PATH": "safe"}

    with pytest.raises(ValueError, match="duplicate environment"):
        codex_login_guard._strict_environment('{"CODEX_HOME":"safe","Path":"one","PATH":"two"}')
