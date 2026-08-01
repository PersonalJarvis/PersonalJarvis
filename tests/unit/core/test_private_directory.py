"""Owner-only directory security contract."""
from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

import pytest

from jarvis.core import private_directory
from jarvis.core.private_directory import ensure_owner_only_directory

_POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permission test")
_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows ACL test")


def test_module_has_no_module_scope_ctypes_import() -> None:
    source = Path(private_directory.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        (node.module or "").split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    )
    assert "ctypes" not in imported


def test_missing_directory_is_rejected_without_creation(tmp_path: Path) -> None:
    target = tmp_path / "missing-private-directory"

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=False)

    assert not target.exists()


def test_regular_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("not private storage", encoding="utf-8")

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=False)


def test_directory_link_is_rejected(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    link = tmp_path / "linked-directory"
    try:
        link.symlink_to(destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"This host cannot create directory symlinks: {exc}")

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(link, create=False)


def test_error_message_does_not_disclose_the_path(tmp_path: Path) -> None:
    target = tmp_path / "private-path-must-not-appear"

    with pytest.raises(RuntimeError) as raised:
        ensure_owner_only_directory(target, create=False)

    assert str(target) not in str(raised.value)


@_POSIX_ONLY
def test_posix_creation_is_0700_and_revalidates(tmp_path: Path) -> None:
    target = tmp_path / "private"

    ensure_owner_only_directory(target, create=True)
    ensure_owner_only_directory(target, create=False)

    metadata = target.lstat()
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert metadata.st_uid == os.geteuid()


@_POSIX_ONLY
def test_posix_group_or_other_permissions_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "private"
    target.mkdir(mode=0o750)
    target.chmod(0o750)

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=False)


@_POSIX_ONLY
def test_posix_different_owner_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "private"
    target.mkdir(mode=0o700)
    monkeypatch.setattr(
        private_directory.os,
        "geteuid",
        lambda: target.lstat().st_uid + 1,
    )

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=False)


def _windows_modules():
    win32api = pytest.importorskip("win32api")
    win32con = pytest.importorskip("win32con")
    win32security = pytest.importorskip("win32security")
    ntsecuritycon = pytest.importorskip("ntsecuritycon")
    return win32api, win32con, win32security, ntsecuritycon


def _windows_current_user_sid():
    win32api, win32con, win32security, _ntsecuritycon = _windows_modules()
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32con.TOKEN_QUERY,
    )
    try:
        sid, _attributes = win32security.GetTokenInformation(
            token,
            win32security.TokenUser,
        )
        return sid
    finally:
        token.Close()


def _windows_descriptor(path: Path):
    _win32api, _win32con, win32security, _ntsecuritycon = _windows_modules()
    return win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION,
    )


@_WINDOWS_ONLY
def test_windows_creation_has_exact_protected_owner_dacl(tmp_path: Path) -> None:
    _win32api, _win32con, win32security, ntsecuritycon = _windows_modules()
    target = tmp_path / "private"

    ensure_owner_only_directory(target, create=True)
    ensure_owner_only_directory(target, create=False)

    current_sid = _windows_current_user_sid()
    descriptor = _windows_descriptor(target)
    owner = descriptor.GetSecurityDescriptorOwner()
    control, _revision = descriptor.GetSecurityDescriptorControl()
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert win32security.ConvertSidToStringSid(owner) == (
        win32security.ConvertSidToStringSid(current_sid)
    )
    assert control & win32security.SE_DACL_PROTECTED
    assert dacl is not None
    assert dacl.GetAceCount() == 1
    header, access_mask, trustee = dacl.GetAce(0)
    assert header == (
        win32security.ACCESS_ALLOWED_ACE_TYPE,
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
    )
    assert access_mask == ntsecuritycon.FILE_ALL_ACCESS
    assert win32security.ConvertSidToStringSid(trustee) == (
        win32security.ConvertSidToStringSid(current_sid)
    )


@_WINDOWS_ONLY
def test_windows_child_file_inherits_only_the_owner_ace(tmp_path: Path) -> None:
    _win32api, _win32con, win32security, ntsecuritycon = _windows_modules()
    target = tmp_path / "private"
    ensure_owner_only_directory(target, create=True)
    child = target / "credential.json"
    child.write_text("{}", encoding="utf-8")

    descriptor = _windows_descriptor(child)
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl is not None
    assert dacl.GetAceCount() == 1
    header, access_mask, trustee = dacl.GetAce(0)
    assert header == (
        win32security.ACCESS_ALLOWED_ACE_TYPE,
        win32security.INHERITED_ACE,
    )
    assert access_mask == ntsecuritycon.FILE_ALL_ACCESS
    assert win32security.ConvertSidToStringSid(trustee) == (
        win32security.ConvertSidToStringSid(_windows_current_user_sid())
    )


@_WINDOWS_ONLY
def test_windows_extra_ace_is_rejected_without_repair(tmp_path: Path) -> None:
    _win32api, _win32con, win32security, ntsecuritycon = _windows_modules()
    target = tmp_path / "private"
    ensure_owner_only_directory(target, create=True)

    inheritance = (
        win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
    )
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        inheritance,
        ntsecuritycon.FILE_ALL_ACCESS,
        _windows_current_user_sid(),
    )
    acl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        inheritance,
        ntsecuritycon.FILE_GENERIC_READ,
        win32security.CreateWellKnownSid(win32security.WinWorldSid, None),
    )
    win32security.SetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=True)

    dacl = _windows_descriptor(target).GetSecurityDescriptorDacl()
    assert dacl is not None
    assert dacl.GetAceCount() == 2


@_WINDOWS_ONLY
def test_windows_unprotected_dacl_is_rejected(tmp_path: Path) -> None:
    _win32api, _win32con, win32security, _ntsecuritycon = _windows_modules()
    target = tmp_path / "private"
    ensure_owner_only_directory(target, create=True)
    dacl = _windows_descriptor(target).GetSecurityDescriptorDacl()
    assert dacl is not None
    win32security.SetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.UNPROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    with pytest.raises(RuntimeError, match="owner-only security"):
        ensure_owner_only_directory(target, create=False)
