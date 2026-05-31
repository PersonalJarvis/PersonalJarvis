"""Elevator seam (Wave 3, sub-task 3.4).

Asserts the factory selects the right per-OS elevator, ``is_available`` reflects
the host, and — the AD-6 invariant — ``NullElevator.ensure_elevated_helper``
returns a typed refusal ``ElevationResult`` and **never raises**. No test here
triggers a real elevation prompt (those are deferred to Wave 4 / AD-3 and any
that would are marked ``skip_ci``).
"""
from __future__ import annotations

import sys

import pytest

from jarvis.admin import elevator as elevator_mod
from jarvis.admin.elevator import (
    Elevator,
    ElevationResult,
    MacAuthElevator,
    NullElevator,
    PolkitElevator,
    SudoElevator,
    UacElevator,
    make_elevator,
)
from tests.fakes.fake_elevator import FakeElevator


def test_make_elevator_returns_elevator():
    assert isinstance(make_elevator(), Elevator)


def test_fake_elevator_satisfies_protocol():
    assert isinstance(FakeElevator(), Elevator)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only factory leg")
def test_factory_selects_uac_on_windows():
    assert isinstance(make_elevator(), UacElevator)


def test_factory_returns_null_when_no_elevation_capability(monkeypatch):
    """``not has_elevation`` -> NullElevator, regardless of OS."""
    from jarvis.platform.capabilities import Capabilities

    fake_caps = Capabilities(
        platform="linux", has_hotkey=False, has_ax_tree=False,
        has_overlay=False, has_pty=False, has_elevation=False,
        display_present=False, is_wayland=False, ax_permission_granted=None,
    )
    monkeypatch.setattr(elevator_mod, "detect_capabilities", lambda: fake_caps)
    assert isinstance(make_elevator(), NullElevator)


def test_factory_selects_polkit_when_pkexec_present(monkeypatch):
    from jarvis.platform.capabilities import Capabilities

    caps = Capabilities(
        platform="linux", has_hotkey=False, has_ax_tree=False,
        has_overlay=False, has_pty=False, has_elevation=True,
        display_present=False, is_wayland=False, ax_permission_granted=None,
    )
    monkeypatch.setattr(elevator_mod, "detect_capabilities", lambda: caps)
    monkeypatch.setattr(elevator_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(
        elevator_mod.shutil, "which",
        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
    )
    assert isinstance(make_elevator(), PolkitElevator)


def test_factory_falls_back_to_sudo_when_no_pkexec(monkeypatch):
    from jarvis.platform.capabilities import Capabilities

    caps = Capabilities(
        platform="linux", has_hotkey=False, has_ax_tree=False,
        has_overlay=False, has_pty=False, has_elevation=True,
        display_present=False, is_wayland=False, ax_permission_granted=None,
    )
    monkeypatch.setattr(elevator_mod, "detect_capabilities", lambda: caps)
    monkeypatch.setattr(elevator_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(
        elevator_mod.shutil, "which",
        lambda name: "/usr/bin/sudo" if name == "sudo" else None,
    )
    assert isinstance(make_elevator(), SudoElevator)


def test_factory_returns_null_on_linux_with_neither(monkeypatch):
    from jarvis.platform.capabilities import Capabilities

    caps = Capabilities(
        platform="linux", has_hotkey=False, has_ax_tree=False,
        has_overlay=False, has_pty=False, has_elevation=True,
        display_present=False, is_wayland=False, ax_permission_granted=None,
    )
    monkeypatch.setattr(elevator_mod, "detect_capabilities", lambda: caps)
    monkeypatch.setattr(elevator_mod, "detect_platform", lambda: "linux")
    monkeypatch.setattr(elevator_mod.shutil, "which", lambda _name: None)
    assert isinstance(make_elevator(), NullElevator)


def test_factory_selects_macauth_on_darwin(monkeypatch):
    from jarvis.platform.capabilities import Capabilities

    caps = Capabilities(
        platform="darwin", has_hotkey=False, has_ax_tree=False,
        has_overlay=False, has_pty=False, has_elevation=True,
        display_present=True, is_wayland=False, ax_permission_granted=None,
    )
    monkeypatch.setattr(elevator_mod, "detect_capabilities", lambda: caps)
    monkeypatch.setattr(elevator_mod, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(
        elevator_mod.shutil, "which",
        lambda name: "/usr/bin/osascript" if name == "osascript" else None,
    )
    assert isinstance(make_elevator(), MacAuthElevator)


# ----------------------------------------------------------------------
# is_available probes
# ----------------------------------------------------------------------


def test_polkit_is_available_reflects_which(monkeypatch):
    monkeypatch.setattr(
        elevator_mod.shutil, "which",
        lambda name: "/usr/bin/pkexec" if name == "pkexec" else None,
    )
    assert PolkitElevator().is_available() is True
    monkeypatch.setattr(elevator_mod.shutil, "which", lambda _name: None)
    assert PolkitElevator().is_available() is False


def test_sudo_is_available_reflects_which(monkeypatch):
    monkeypatch.setattr(
        elevator_mod.shutil, "which",
        lambda name: "/usr/bin/sudo" if name == "sudo" else None,
    )
    assert SudoElevator().is_available() is True


def test_null_is_never_available():
    assert NullElevator().is_available() is False


# ----------------------------------------------------------------------
# NullElevator refusal — the AD-6 invariant
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_elevator_refuses_without_raising():
    """AD-6: a refusal is a typed result, never an exception."""
    elev = NullElevator()
    result = await elev.ensure_elevated_helper("/run/user/1000/jarvis-admin.sock")
    assert isinstance(result, ElevationResult)
    assert result.ok is False
    assert result.error_code == "no_elevation"
    assert result.message and "no elevation mechanism available" in result.message


@pytest.mark.asyncio
async def test_null_elevator_result_carries_transport_addr():
    addr = r"\\.\pipe\jarvis-admin-S-1-5-18"
    result = await NullElevator().ensure_elevated_helper(addr)
    assert result.transport_addr == addr


@pytest.mark.asyncio
async def test_unavailable_subprocess_elevator_refuses(monkeypatch):
    """A POSIX elevator whose tool is missing refuses, never spawns/raises."""
    monkeypatch.setattr(elevator_mod.shutil, "which", lambda _name: None)
    result = await PolkitElevator().ensure_elevated_helper("/tmp/x.sock")
    assert result.ok is False
    assert result.error_code == "elevation_unavailable"


@pytest.mark.asyncio
async def test_fake_elevator_records_calls():
    elev = FakeElevator(available=True)
    res = await elev.ensure_elevated_helper("/tmp/a.sock")
    assert res.ok is True
    assert elev.calls == ["/tmp/a.sock"]
