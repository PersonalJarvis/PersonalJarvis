"""Cross-platform owner-only directory creation and verification.

The helper is intentionally strict because callers use these directories for
credential-bearing private profiles.  Creation never repairs an existing
directory: a path that already exists must independently satisfy the same
owner-only contract.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import NoReturn

_PRIVATE_DIRECTORY_ERROR = (
    "The private directory is unavailable or does not meet owner-only "
    "security requirements."
)

# Win32 security constants.  The native modules themselves stay function-local
# so importing this module remains safe on macOS and headless Linux.
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_READ_CONTROL = 0x00020000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_ERROR_ALREADY_EXISTS = 183


def _fail() -> NoReturn:
    raise RuntimeError(_PRIVATE_DIRECTORY_ERROR)


def _ensure_posix_owner_only_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=0o700, parents=False)
        except FileExistsError:
            # An existing path is accepted only after the independent lstat
            # checks below; this must never become an implicit permission repair.
            pass
        except OSError:  # Platform security setup failure is translated by the fail-closed helper.
            _fail()

    try:
        metadata = path.lstat()
    except OSError:  # Metadata failure is translated by the fail-closed helper.
        _fail()

    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail()
    get_effective_uid = getattr(os, "geteuid", None)
    if get_effective_uid is None or metadata.st_uid != get_effective_uid():
        _fail()
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        _fail()


def _ensure_windows_owner_only_directory(path: Path, *, create: bool) -> None:
    # Lazy imports are mandatory: Linux/macOS base installs do not expose the
    # Win32 loader, while this module is imported by cross-platform code.
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = (
            ("Sid", wintypes.LPVOID),
            ("Attributes", wintypes.DWORD),
        )

    class _TokenUser(ctypes.Structure):
        _fields_ = (("User", _SidAndAttributes),)

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        )

    class _FileTime(ctypes.Structure):
        _fields_ = (
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        )

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    ]
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD

    def close_handle(handle: int) -> None:
        if handle and not kernel32.CloseHandle(handle):
            _fail()

    def local_free(pointer: object) -> None:
        if pointer:
            kernel32.LocalFree(ctypes.cast(pointer, wintypes.LPVOID))

    def current_user_sid_string() -> str:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            _TOKEN_QUERY,
            ctypes.byref(token),
        ):
            _fail()
        try:
            required = wintypes.DWORD()
            advapi32.GetTokenInformation(
                token,
                _TOKEN_USER,
                None,
                0,
                ctypes.byref(required),
            )
            if required.value == 0:
                _fail()
            buffer = ctypes.create_string_buffer(required.value)
            if not advapi32.GetTokenInformation(
                token,
                _TOKEN_USER,
                buffer,
                required.value,
                ctypes.byref(required),
            ):
                _fail()
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            sid_text = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(
                token_user.User.Sid,
                ctypes.byref(sid_text),
            ):
                _fail()
            try:
                value = sid_text.value
                if not value:
                    _fail()
                return value
            finally:
                local_free(sid_text)
        finally:
            close_handle(token.value)

    def descriptor_from_sddl(sddl: str) -> wintypes.LPVOID:
        descriptor = wintypes.LPVOID()
        descriptor_size = wintypes.DWORD()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(descriptor_size),
        ):
            _fail()
        return descriptor

    security_information = (
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
    )

    def canonical_sddl(descriptor: wintypes.LPVOID) -> str:
        rendered = wintypes.LPWSTR()
        rendered_length = wintypes.DWORD()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            _SDDL_REVISION_1,
            security_information,
            ctypes.byref(rendered),
            ctypes.byref(rendered_length),
        ):
            _fail()
        try:
            value = rendered.value
            if not value:
                _fail()
            return value
        finally:
            local_free(rendered)

    sid = current_user_sid_string()
    expected_descriptor = descriptor_from_sddl(
        f"O:{sid}D:P(A;OICI;FA;;;{sid})"
    )
    try:
        expected_sddl = canonical_sddl(expected_descriptor)
        if create and not kernel32.CreateDirectoryW(
            os.fspath(path),
            ctypes.byref(
                _SecurityAttributes(
                    ctypes.sizeof(_SecurityAttributes),
                    expected_descriptor,
                    False,
                )
            ),
        ):
            if ctypes.get_last_error() != _ERROR_ALREADY_EXISTS:
                _fail()
    finally:
        local_free(expected_descriptor)

    invalid_handle = ctypes.c_void_p(-1).value
    handle = kernel32.CreateFileW(
        os.fspath(path),
        _READ_CONTROL,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if not handle or handle == invalid_handle:
        _fail()
    try:
        file_information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(file_information),
        ):
            _fail()
        attributes = file_information.dwFileAttributes
        if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
            _fail()
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _fail()

        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        actual_descriptor = wintypes.LPVOID()
        result = advapi32.GetSecurityInfo(
            handle,
            _SE_FILE_OBJECT,
            security_information,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(actual_descriptor),
        )
        if result != 0 or not owner or not dacl or not actual_descriptor:
            _fail()
        try:
            if canonical_sddl(actual_descriptor) != expected_sddl:
                _fail()
        finally:
            local_free(actual_descriptor)
    finally:
        close_handle(handle)


def ensure_owner_only_directory(path: Path, *, create: bool) -> None:
    """Create or validate one private directory without following links.

    A newly created POSIX directory has mode ``0700``.  On Windows the directory
    is created with a protected DACL containing exactly one inheritable full-
    access ACE for the current process user.  Existing paths are validated but
    never silently repaired.
    """
    target = Path(path)
    if os.name == "nt":
        _ensure_windows_owner_only_directory(target, create=create)
        return
    _ensure_posix_owner_only_directory(target, create=create)


__all__ = ["ensure_owner_only_directory"]
