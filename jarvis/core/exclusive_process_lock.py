"""Strict non-blocking file lock for coordinating separate processes.

The lock file is permanent and contains one NUL byte.  Ownership belongs to
the open file descriptor: closing it, or process exit, releases the OS lock.
The file is never removed because unlinking a live lock creates two unrelated
lock identities and defeats mutual exclusion.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, NoReturn, Self

LockFailureReason = Literal["busy", "unsafe", "unavailable"]

_MESSAGES: dict[LockFailureReason, str] = {
    "busy": "The protected profile is already in use.",
    "unsafe": "The profile process lock is unsafe.",
    "unavailable": "The profile process lock is unavailable.",
}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ExclusiveProcessLockError(RuntimeError):
    """Stable, path-free failure raised when an exclusive lock cannot be held."""

    def __init__(self, reason: LockFailureReason) -> None:
        self.reason = reason
        super().__init__(_MESSAGES[reason])


def _raise(reason: LockFailureReason, cause: BaseException | None = None) -> NoReturn:
    error = ExclusiveProcessLockError(reason)
    if cause is None:
        raise error
    raise error from cause


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _validate_metadata(metadata: os.stat_result) -> None:
    if _is_link_or_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        _raise("unsafe")
    if getattr(metadata, "st_nlink", 1) != 1:
        _raise("unsafe")
    if metadata.st_size != 1:
        _raise("unsafe")
    if os.name == "posix":
        geteuid = getattr(os, "geteuid", None)
        if not callable(geteuid) or metadata.st_uid != geteuid():
            _raise("unsafe")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            _raise("unsafe")


def _validate_windows_handle_security(handle: int, *, directory: bool) -> None:
    """Require an exact current-user-only Windows owner and DACL."""
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415 - Windows-only, off the import floor
        from ctypes import wintypes  # noqa: PLC0415 - Windows-only

        token_query = 0x0008
        token_user_class = 1
        security_file_object = 1
        owner_security_information = 0x00000001
        dacl_security_information = 0x00000004
        sddl_revision = 1
        error_insufficient_buffer = 122

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        class TokenUser(ctypes.Structure):
            _fields_ = [("User", SidAndAttributes)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_uint,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.c_uint,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.HLOCAL),
        ]
        advapi32.GetSecurityInfo.restype = wintypes.DWORD
        advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        advapi32.EqualSid.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL

        security_information = owner_security_information | dacl_security_information

        def local_free(pointer: Any) -> None:
            if pointer:
                kernel32.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))

        def canonical_sddl(descriptor_pointer: object) -> str:
            rendered = wintypes.LPWSTR()
            rendered_length = wintypes.DWORD()
            if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor_pointer,
                sddl_revision,
                security_information,
                ctypes.byref(rendered),
                ctypes.byref(rendered_length),
            ):
                _raise("unavailable")
            try:
                value = rendered.value
                if not value:
                    _raise("unavailable")
                return value
            finally:
                local_free(rendered)

        def canonical_expected(sddl: str) -> str:
            pointer = ctypes.c_void_p()
            size = wintypes.DWORD()
            if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl,
                sddl_revision,
                ctypes.byref(pointer),
                ctypes.byref(size),
            ):
                _raise("unavailable")
            try:
                return canonical_sddl(pointer)
            finally:
                local_free(pointer)

        token = wintypes.HANDLE()
        descriptor = wintypes.HLOCAL()
        try:
            if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
            ):
                _raise("unavailable")
            required = wintypes.DWORD()
            if advapi32.GetTokenInformation(
                token, token_user_class, None, 0, ctypes.byref(required)
            ):
                _raise("unavailable")
            if ctypes.get_last_error() != error_insufficient_buffer or not required.value:
                _raise("unavailable")
            token_buffer = ctypes.create_string_buffer(required.value)
            if not advapi32.GetTokenInformation(
                token,
                token_user_class,
                token_buffer,
                required.value,
                ctypes.byref(required),
            ):
                _raise("unavailable")
            token_user = ctypes.cast(token_buffer, ctypes.POINTER(TokenUser)).contents
            sid_text = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(token_user.User.Sid, ctypes.byref(sid_text)):
                _raise("unavailable")
            try:
                sid = sid_text.value
                if not sid:
                    _raise("unavailable")
            finally:
                local_free(sid_text)

            owner = ctypes.c_void_p()
            dacl = ctypes.c_void_p()
            result = advapi32.GetSecurityInfo(
                wintypes.HANDLE(handle),
                security_file_object,
                security_information,
                ctypes.byref(owner),
                None,
                ctypes.byref(dacl),
                None,
                ctypes.byref(descriptor),
            )
            if result != 0 or not owner.value or not dacl.value or not descriptor:
                _raise("unavailable")
            if not advapi32.EqualSid(owner, token_user.User.Sid):
                _raise("unsafe")
            actual_sddl = canonical_sddl(descriptor)
            if directory:
                expected = {canonical_expected(f"O:{sid}D:P(A;OICI;FA;;;{sid})")}
            else:
                expected = {
                    canonical_expected(f"O:{sid}D:P(A;;FA;;;{sid})"),
                    canonical_expected(f"O:{sid}D:(A;ID;FA;;;{sid})"),
                }
            if actual_sddl not in expected:
                _raise("unsafe")
        finally:
            if descriptor:
                kernel32.LocalFree(descriptor)
            if token:
                kernel32.CloseHandle(token)
    except ExclusiveProcessLockError:
        raise
    except (ImportError, OSError, ValueError) as exc:
        # Translate platform failure to a stable error.
        _raise("unavailable", exc)


def _validate_windows_file_security(fd: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import msvcrt  # noqa: PLC0415 - Windows-only, off the import floor

        _validate_windows_handle_security(msvcrt.get_osfhandle(fd), directory=False)
    except ExclusiveProcessLockError:
        raise
    except (ImportError, OSError, ValueError) as exc:  # Translate handle failure to a stable error.
        _raise("unavailable", exc)


def _validate_windows_directory_security(path: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415 - Windows-only, off the import floor
        from ctypes import wintypes  # noqa: PLC0415 - Windows-only

        read_control = 0x00020000
        share_read = 0x00000001
        share_write = 0x00000002
        open_existing = 3
        open_reparse = 0x00200000
        backup_semantics = 0x02000000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            os.fspath(path),
            read_control,
            share_read | share_write,
            None,
            open_existing,
            open_reparse | backup_semantics,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            _raise("unavailable")
        try:
            _validate_windows_handle_security(handle, directory=True)
        finally:
            kernel32.CloseHandle(handle)
    except ExclusiveProcessLockError:
        raise
    except (ImportError, OSError, ValueError) as exc:
        # Translate directory failure to a stable error.
        _raise("unavailable", exc)


def _canonical_lock_path(lock_path: Path, protected_directory: Path) -> Path:
    if not lock_path.is_absolute() or not protected_directory.is_absolute():
        _raise("unsafe")
    try:
        parent_metadata = lock_path.parent.lstat()
        if _is_link_or_reparse(parent_metadata) or not stat.S_ISDIR(parent_metadata.st_mode):
            _raise("unsafe")
        if os.name == "posix":
            geteuid = getattr(os, "geteuid", None)
            if (
                not callable(geteuid)
                or parent_metadata.st_uid != geteuid()
                or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            ):
                _raise("unsafe")
        _validate_windows_directory_security(lock_path.parent)
        parent = lock_path.parent.resolve(strict=True)
        if protected_directory.exists() or protected_directory.is_symlink():
            protected_metadata = protected_directory.lstat()
            if _is_link_or_reparse(protected_metadata):
                _raise("unsafe")
            protected = protected_directory.resolve(strict=True)
        else:
            protected_parent = protected_directory.parent.resolve(strict=True)
            protected = protected_parent / protected_directory.name
    except ExclusiveProcessLockError:
        raise
    except OSError as exc:  # Canonicalization failure is reported as unavailable.
        _raise("unavailable", exc)
    candidate = parent / lock_path.name
    try:
        candidate.relative_to(protected)
    except ValueError:  # Failure means the lock is safely outside the protected directory.
        return candidate
    _raise("unsafe")


def _open_lock_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    existed = path.exists() or path.is_symlink()
    if existed:
        try:
            _validate_metadata(path.lstat())
        except OSError as exc:  # Metadata failure is translated to the stable lock error.
            _raise("unavailable", exc)
    else:
        flags |= os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        # A concurrent creator won. Validate and open the file it published.
        try:
            _validate_metadata(path.lstat())
            fd = os.open(
                path,
                os.O_RDWR
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except ExclusiveProcessLockError:
            raise
        except OSError as exc:  # Concurrent-open failure is translated to the stable lock error.
            _raise("unavailable", exc)
        existed = True
    except OSError as exc:  # Open failure is translated without exposing a filesystem path.
        _raise("unsafe" if exc.errno == errno.ELOOP else "unavailable", exc)

    try:
        if not existed:
            os.write(fd, b"\0")
            os.fsync(fd)
        opened = os.fstat(fd)
        _validate_metadata(opened)
        _validate_windows_file_security(fd)
        current = path.lstat()
        _validate_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            _raise("unsafe")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _acquire_fd(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - Windows-only, off import floor

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - POSIX-only, off import floor

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError) as exc:  # Expected contention becomes the busy state.
        _raise("busy", exc)
    except OSError as exc:  # Platform lock errors become stable busy or unavailable states.
        busy_errors = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
        if exc.errno in busy_errors or getattr(exc, "winerror", None) in {32, 33, 36}:
            _raise("busy", exc)
        _raise("unavailable", exc)


def _release_fd(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - Windows-only, off import floor

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - POSIX-only, off import floor

            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class ExclusiveProcessLock:
    """An acquired cross-process lock, released by :meth:`close`."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    @classmethod
    def acquire(
        cls,
        lock_path: str | os.PathLike[str],
        *,
        protected_directory: str | os.PathLike[str],
    ) -> Self:
        """Acquire immediately or raise :class:`ExclusiveProcessLockError`."""
        path = _canonical_lock_path(Path(lock_path), Path(protected_directory))
        fd = _open_lock_file(path)
        try:
            _acquire_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        return cls(fd)

    @property
    def closed(self) -> bool:
        return self._fd < 0

    def fileno(self) -> int:
        """Return the held descriptor for safe POSIX child inheritance."""
        if self._fd < 0:
            raise ValueError("The profile process lock is closed.")
        return self._fd

    def close(self) -> None:
        if self._fd < 0:
            return
        fd, self._fd = self._fd, -1
        _release_fd(fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["ExclusiveProcessLock", "ExclusiveProcessLockError", "LockFailureReason"]
