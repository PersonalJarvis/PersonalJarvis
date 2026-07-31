from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.core import exclusive_process_lock as process_lock
from jarvis.core.exclusive_process_lock import (
    ExclusiveProcessLock,
    ExclusiveProcessLockError,
)
from jarvis.core.private_directory import ensure_owner_only_directory


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    protected = tmp_path / "profile"
    protected.mkdir()
    coordination = tmp_path / "coordination"
    ensure_owner_only_directory(coordination, create=True)
    return coordination / "profile-process.lock", protected


def test_lock_releases_on_close_and_keeps_strict_file(tmp_path: Path) -> None:
    path, protected = _paths(tmp_path)

    first = ExclusiveProcessLock.acquire(path, protected_directory=protected)
    assert path.stat().st_size == 1
    assert not first.closed
    first.close()
    assert path.read_bytes() == b"\0"
    assert first.closed

    with ExclusiveProcessLock.acquire(path, protected_directory=protected) as second:
        assert not second.closed
    assert second.closed


def test_lock_rejects_location_inside_protected_directory(tmp_path: Path) -> None:
    _, protected = _paths(tmp_path)

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(
            protected / "process.lock",
            protected_directory=protected,
        )

    assert caught.value.reason == "unsafe"


def test_lock_accepts_a_protected_directory_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    coordination = tmp_path / "coordination"
    ensure_owner_only_directory(coordination, create=True)
    protected = tmp_path / "future-profile"

    with ExclusiveProcessLock.acquire(
        coordination / "profile-process.lock",
        protected_directory=protected,
    ):
        assert not protected.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
def test_lock_rejects_permissive_windows_dacl(tmp_path: Path) -> None:
    win32api = pytest.importorskip("win32api")
    win32con = pytest.importorskip("win32con")
    win32security = pytest.importorskip("win32security")
    path, protected = _paths(tmp_path)
    ExclusiveProcessLock.acquire(path, protected_directory=protected).close()
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    everyone_sid = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, 0x001F01FF, user_sid)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_READ, everyone_sid)
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(path, protected_directory=protected)

    assert caught.value.reason == "unsafe"
    assert str(caught.value) == "The profile process lock is unsafe."


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent permission contract")
def test_lock_rejects_unsafe_posix_parent(tmp_path: Path) -> None:
    protected = tmp_path / "profile"
    protected.mkdir()
    coordination = tmp_path / "coordination"
    coordination.mkdir(mode=0o755)
    coordination.chmod(0o755)

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(
            coordination / "profile-process.lock",
            protected_directory=protected,
        )

    assert caught.value.reason == "unsafe"


@pytest.mark.parametrize("kind", ["empty", "directory", "multi_link"])
def test_lock_rejects_non_strict_existing_entry(tmp_path: Path, kind: str) -> None:
    path, protected = _paths(tmp_path)
    if kind == "empty":
        path.touch()
    elif kind == "directory":
        path.mkdir()
    else:
        original = tmp_path / "original"
        original.write_bytes(b"\0")
        try:
            os.link(original, path)
        except OSError:
            pytest.skip("hard links are unavailable")

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(path, protected_directory=protected)

    assert caught.value.reason == "unsafe"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_lock_rejects_unsafe_posix_mode_without_repair(tmp_path: Path) -> None:
    path, protected = _paths(tmp_path)
    path.write_bytes(b"\0")
    path.chmod(0o640)

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(path, protected_directory=protected)

    assert caught.value.reason == "unsafe"
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_lock_rejects_wrong_posix_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, protected = _paths(tmp_path)
    path.write_bytes(b"\0")
    path.chmod(0o600)
    actual_uid = os.geteuid()
    monkeypatch.setattr(process_lock.os, "geteuid", lambda: actual_uid + 1)

    with pytest.raises(ExclusiveProcessLockError) as caught:
        ExclusiveProcessLock.acquire(path, protected_directory=protected)

    assert caught.value.reason == "unsafe"


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited flock contract")
def test_inherited_descriptor_holds_lock_after_parent_close(tmp_path: Path) -> None:
    path, protected = _paths(tmp_path)
    parent_lock = ExclusiveProcessLock.acquire(path, protected_directory=protected)
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", "import time; time.sleep(0.5)"],
        pass_fds=(parent_lock.fileno(),),
    )
    try:
        parent_lock.close()
        with pytest.raises(ExclusiveProcessLockError) as caught:
            ExclusiveProcessLock.acquire(path, protected_directory=protected)
        assert caught.value.reason == "busy"
    finally:
        child.wait(timeout=5)

    ExclusiveProcessLock.acquire(path, protected_directory=protected).close()
