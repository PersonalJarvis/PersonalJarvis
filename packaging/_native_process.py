"""Strict, synchronous process ownership for disposable native installer checks."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import contextmanager

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


class ContainmentError(RuntimeError):
    """Cleanup could not prove that the owned tree stopped; retain its workspace."""


def _windows_job():
    if os.name != "nt":
        return None

    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("working_min", ctypes.c_size_t),
            ("working_max", ctypes.c_size_t),
            ("active_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io_counters", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("user_time", ctypes.c_longlong),
            ("kernel_time", ctypes.c_longlong),
            ("period_user_time", ctypes.c_longlong),
            ("period_kernel_time", ctypes.c_longlong),
            ("page_faults", wintypes.DWORD),
            ("total_processes", wintypes.DWORD),
            ("active_processes", wintypes.DWORD),
            ("terminated_processes", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result

    def checked(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    class Job:
        def __init__(self):
            self.handle = checked(kernel.CreateJobObjectW(None, None))
            try:
                limits = ExtendedLimits()
                # No breakaway flag: a detached installer child must remain owned.
                limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                checked(
                    kernel.SetInformationJobObject(
                        self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                    )
                )
            except BaseException:
                self.close()
                raise

        def assign_and_resume(self, child):
            import psutil  # noqa: PLC0415

            process = checked(kernel.OpenProcess(0x0101, False, child.pid))
            try:
                checked(kernel.AssignProcessToJobObject(self.handle, process))
            finally:
                checked(kernel.CloseHandle(process))
            # Popen closes the primary thread handle. A suspended process cannot
            # create further threads, so its only thread can be resumed safely.
            threads = psutil.Process(child.pid).threads()
            if len(threads) != 1:
                raise RuntimeError("The suspended native process has an unexpected thread count")
            thread = checked(kernel.OpenThread(0x0002, False, threads[0].id))
            try:
                if kernel.ResumeThread(thread) != 1:
                    raise RuntimeError("The native process did not resume from its launch gate")
            finally:
                checked(kernel.CloseHandle(thread))

        def terminate_and_wait(self, deadline):
            checked(kernel.TerminateJobObject(self.handle, 1))
            while True:
                accounting = Accounting()
                checked(
                    kernel.QueryInformationJobObject(
                        self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
                    )
                )
                if not accounting.active_processes:
                    return
                if time.monotonic() >= deadline:
                    raise ContainmentError("The native Windows job did not drain before cleanup")
                # Job handles do not signal ordinary process exit. This bounded
                # shutdown check uses the kernel count, even after launcher exit.
                time.sleep(0.02)

        def close(self):
            if self.handle is not None:
                handle, self.handle = self.handle, None
                checked(kernel.CloseHandle(handle))

    return Job()


def _signal_group(pid: int, sig: int) -> None:
    if os.name != "posix":
        return
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return  # The entire owned group already exited.


def _group_is_running(pid: int) -> bool:
    if os.name != "posix":
        return False
    import psutil  # noqa: PLC0415

    # A reparented zombie cannot execute or retain open files. Its new parent
    # owns waitpid; counting it as live would make container PID 1 a false failure.
    for process in psutil.process_iter(["pid", "status"]):
        try:
            if os.getpgid(process.pid) == pid and process.status() != psutil.STATUS_ZOMBIE:
                return True
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue  # A process can exit between the snapshot and group lookup.
    return False


def _stop_posix(child, deadline):
    _signal_group(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=min(0.5, max(0.0, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        pass  # The whole group is killed below, including stubborn descendants.
    _signal_group(child.pid, signal.SIGKILL)
    child.wait(timeout=max(0.0, deadline - time.monotonic()))
    while _group_is_running(child.pid):
        if time.monotonic() >= deadline:
            raise ContainmentError("The native POSIX process group did not drain before cleanup")
        time.sleep(0.02)


@contextmanager
def contained_process(argv, *, env, cwd=None, stdout=None, stderr=None):
    """Start no executable until containment is established; synchronously reap it."""
    if os.name not in {"nt", "posix"}:
        raise RuntimeError("Native smoke process containment is unavailable on this platform")
    job = _windows_job()
    child = None
    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW_CREATIONFLAGS | (0x00000004 if job is not None else 0),
            start_new_session=os.name == "posix",
        )
        if job is not None:
            job.assign_and_resume(child)
        yield child
    finally:
        try:
            if child is not None:
                deadline = time.monotonic() + 10
                if job is not None:
                    try:
                        job.terminate_and_wait(deadline)
                    finally:
                        # Assignment can fail while the child is still suspended.
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=max(0.0, deadline - time.monotonic()))
                else:
                    _stop_posix(child, deadline)
        except BaseException as exc:
            raise ContainmentError("Could not prove native process tree termination") from exc
        finally:
            try:
                if job is not None:
                    job.close()
            except BaseException as exc:
                raise ContainmentError("Could not release the native process job") from exc
            finally:
                if child is not None:
                    for stream in (child.stdin, child.stdout, child.stderr):
                        if stream is not None:
                            stream.close()
