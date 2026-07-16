"""POSIX kill-on-crash containment for mission workers (cross-platform audit C2).

The Windows path reaps a worker's whole process tree via a Job Object
(``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``). On Linux/macOS that was a pure no-op,
so a mission cleanup / cancel / timeout / graceful shutdown left every
claude/codex/node worker — and the grandchildren it spawned — orphaned, which is
fatal on a 1 GB VPS. These tests pin the POSIX equivalent: workers are spawned
into their own session/process-group (``start_new_session=True``) and the job
object signals the whole group SIGTERM then SIGKILL on close.

POSIX syscalls (``os.killpg``/``os.getpgid``, ``SIGKILL``) do not exist on the
Windows test host, so the POSIX job object takes injectable ``getpgid``/``killpg``
callables, and the spawn-contract test monkeypatches ``sys.platform`` + a fake
``create_subprocess_exec`` — the same pattern as ``test_spawn_breakaway_fallback``.

The P-10 gap (a hard SIGKILL of the orchestrator reparents the worker tree to
init because the job object above can only reap via an orderly ``close()``) is
closed on Linux by arming ``PR_SET_PDEATHSIG`` at spawn time in
``create_worker_subprocess`` — see the ``preexec_fn`` tests below.
"""
from __future__ import annotations

import asyncio
import signal
import sys

import pytest

from jarvis.missions.isolation import job_object as jo
from jarvis.missions.workers import process_utils as pu

_SIGTERM = getattr(signal, "SIGTERM", 15)
_SIGKILL = getattr(signal, "SIGKILL", 9)


class _FakeKiller:
    """Records ``(pgid, sig)`` calls; can simulate an already-dead group."""

    def __init__(self, *, dead: set[int] | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._dead = dead or set()

    def getpgid(self, pid: int) -> int:
        # A worker spawned with start_new_session=True is its own group leader,
        # so pgid == pid.
        return pid

    def killpg(self, pgid: int, sig: int) -> None:
        self.calls.append((pgid, sig))
        if pgid in self._dead:
            raise ProcessLookupError


def _make_job(killer: _FakeKiller, name: str = "m") -> object:
    return jo._PosixProcessGroupJobObject(
        name, getpgid=killer.getpgid, killpg=killer.killpg, grace_s=0.0
    )


# --- POSIX job object -------------------------------------------------------


async def test_posix_job_close_signals_term_then_kill_to_each_group() -> None:
    killer = _FakeKiller()
    job = _make_job(killer)
    job.assign(101)
    job.assign(202)
    await job.close()

    assert (101, _SIGTERM) in killer.calls
    assert (202, _SIGTERM) in killer.calls
    assert (101, _SIGKILL) in killer.calls
    assert (202, _SIGKILL) in killer.calls
    # Each group is asked to terminate before it is force-killed.
    assert killer.calls.index((101, _SIGTERM)) < killer.calls.index((101, _SIGKILL))
    assert job.closed


async def test_posix_job_close_is_idempotent() -> None:
    killer = _FakeKiller()
    job = _make_job(killer)
    job.assign(7)
    await job.close()
    n = len(killer.calls)
    await job.close()
    assert len(killer.calls) == n, "second close() must not signal the group again"
    assert job.closed


async def test_posix_job_already_dead_group_is_swallowed() -> None:
    killer = _FakeKiller(dead={5})
    job = _make_job(killer)
    job.assign(5)
    await job.close()  # ProcessLookupError from killpg must not propagate
    assert job.closed


async def test_posix_job_assign_after_close_raises() -> None:
    killer = _FakeKiller()
    job = _make_job(killer)
    await job.close()
    with pytest.raises(RuntimeError):
        job.assign(1)


async def test_posix_job_can_release_a_reaped_short_lived_group() -> None:
    killer = _FakeKiller()
    job = _make_job(killer)
    job.assign(77)
    job.release(77)
    await job.close()
    assert killer.calls == []


async def test_posix_job_async_context_manager_reaps_on_exit() -> None:
    killer = _FakeKiller()
    async with _make_job(killer) as job:
        job.assign(42)
        assert not job.closed
    assert job.closed
    assert (42, _SIGTERM) in killer.calls
    assert (42, _SIGKILL) in killer.calls


def test_posix_job_handle_is_none() -> None:
    job = _make_job(_FakeKiller())
    assert job.handle is None


# --- Factory selection ------------------------------------------------------


def test_factory_selects_posix_impl_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jo.sys, "platform", "linux")
    job = jo.WindowsJobObject("linux-mission")
    assert type(job).__name__ == "_PosixProcessGroupJobObject"


def test_factory_selects_posix_impl_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jo.sys, "platform", "darwin")
    job = jo.WindowsJobObject("mac-mission")
    assert type(job).__name__ == "_PosixProcessGroupJobObject"


# --- Spawn contract: workers form their own killable session ----------------


async def test_worker_spawn_starts_new_session_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake_exec(*_a, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "linux")

    proc = await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert proc is not None
    assert seen.get("start_new_session") is True, (
        "a POSIX worker must be spawned into its own session/process-group so the "
        "job object can killpg the whole tree"
    )


async def test_worker_spawn_no_new_session_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake_exec(*_a, creationflags: int = 0, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "worker_creationflags", lambda: 0x08000000)

    await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert seen.get("start_new_session") in (None, False), (
        "Windows uses the Job Object for containment; start_new_session is a "
        "POSIX-only concept and must not be passed there"
    )


# --- P-10: PR_SET_PDEATHSIG hardening on Linux only --------------------------


async def test_worker_spawn_passes_no_preexec_fn_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only Linux gets the prctl hardening — macOS has no PR_SET_PDEATHSIG."""
    seen: dict[str, object] = {}

    async def _fake_exec(*_a, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "darwin")

    await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert "preexec_fn" not in seen, "macOS has no PR_SET_PDEATHSIG equivalent"


async def test_worker_spawn_passes_no_preexec_fn_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake_exec(*_a, creationflags: int = 0, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "worker_creationflags", lambda: 0x08000000)

    await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert "preexec_fn" not in seen


async def test_worker_spawn_passes_no_preexec_fn_when_prctl_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even on Linux, a missing/unresolvable prctl must degrade silently."""
    seen: dict[str, object] = {}

    async def _fake_exec(*_a, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "linux")
    monkeypatch.setattr(pu, "_resolve_linux_prctl", lambda: None)

    proc = await pu.create_worker_subprocess(["x"], cwd=".", env={})
    assert proc is not None
    assert "preexec_fn" not in seen


async def test_worker_spawn_passes_preexec_fn_on_linux_when_prctl_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux with a resolvable prctl, the spawn gets a PDEATHSIG preexec_fn."""
    seen: dict[str, object] = {}
    prctl_calls: list[tuple[int, int]] = []

    def _fake_prctl(option: int, sig: int) -> int:
        prctl_calls.append((option, sig))
        return 0

    async def _fake_exec(*_a, **kw):  # noqa: ANN002, ANN003
        seen.update(kw)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(pu.sys, "platform", "linux")
    monkeypatch.setattr(pu, "_resolve_linux_prctl", lambda: _fake_prctl)

    await pu.create_worker_subprocess(["x"], cwd=".", env={})

    preexec = seen.get("preexec_fn")
    assert callable(preexec), "Linux spawn with a working prctl must set preexec_fn"
    # Simulate the fork-time call the real subprocess machinery would make.
    preexec()
    assert prctl_calls == [(pu._PR_SET_PDEATHSIG, pu._SIGKILL)]


def test_linux_pdeathsig_preexec_fn_never_raises() -> None:
    """The preexec_fn must swallow prctl failures — it must never abort a spawn."""

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("simulated prctl failure")

    preexec = pu._linux_pdeathsig_preexec_fn(_boom)
    preexec()  # must not raise


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux-only")
def test_resolve_linux_prctl_returns_callable_on_real_linux() -> None:
    """On a real Linux host, libc.prctl must resolve to a callable."""
    prctl = pu._resolve_linux_prctl()
    assert prctl is not None
    assert callable(prctl)


# --- Bootstrap wiring: the mission job factory must reap on POSIX -----------


def test_bootstrap_job_factory_reaps_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mission bootstrap must route its per-mission job through the platform
    factory (real POSIX session/group reaping), not the hard-coded no-op that
    used to leak worker trees on Linux/macOS."""
    import jarvis.missions.init as mi

    monkeypatch.setattr(jo.sys, "platform", "linux")
    job = mi._default_job_factory()
    assert type(job).__name__ == "_PosixProcessGroupJobObject"


# --- Spawn discipline: every worker routes through the containment helper ----
# The behavioural property (start_new_session on POSIX) is pinned above for
# ``create_worker_subprocess`` itself. These pin that each worker ROUTES its
# spawn through that helper instead of calling ``asyncio.create_subprocess_exec``
# directly — a direct spawn skips start_new_session on POSIX, leaving the worker
# in the orchestrator's process group so the killpg reaper could signal the
# orchestrator itself (H3, DEEP-DIVE-AUDIT-2026-06-19). Source-level because
# driving each worker's full async-generator spawn needs a live worktree + job
# object + brain config; the helper's POSIX behaviour is already proven above.


def _worker_source(module) -> str:
    from pathlib import Path

    return Path(module.__file__).read_text(encoding="utf-8")


def test_gemini_worker_routes_spawn_through_containment_helper() -> None:
    from jarvis.missions.workers import gemini_worker

    src = _worker_source(gemini_worker)
    assert "create_worker_subprocess(" in src
    # The trailing "(" distinguishes a real call site from a docstring mention
    # of `asyncio.create_subprocess_exec` (which has no immediate paren).
    assert "asyncio.create_subprocess_exec(" not in src


def test_codex_worker_routes_spawn_through_containment_helper() -> None:
    from jarvis.missions.workers import codex_worker

    src = _worker_source(codex_worker)
    assert "create_worker_subprocess(" in src
    assert "asyncio.create_subprocess_exec(" not in src


def test_bootstrap_job_factory_uses_win32_impl_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.missions.init as mi

    monkeypatch.setattr(jo.sys, "platform", "win32")
    # On a non-Windows host pywin32 is absent, so the factory degrades to the
    # no-op; we only assert it does NOT silently pick the POSIX reaper for win32.
    job = mi._default_job_factory()
    assert type(job).__name__ in {"_Win32JobObjectImpl", "_NoOpJobObject"}
