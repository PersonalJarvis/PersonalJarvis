"""CUIndicatorController: refcount, capability gating, Escape cancel, hints."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import CUControlEnded, CUControlStarted
from jarvis.cu.indicator import controller as controller_mod
from jarvis.cu.indicator.controller import (
    _ESC_HINTS,
    CUIndicatorController,
    wire_cu_indicator,
)


def _quiet_controller(
    monkeypatch: pytest.MonkeyPatch, bus: EventBus
) -> tuple[CUIndicatorController, list[str]]:
    """A wired controller whose heavy sides (sidecar, hotkey) are recorded
    fakes — the refcount logic runs for real."""
    calls: list[str] = []
    ctl = CUIndicatorController(bus)

    async def fake_activate() -> None:
        calls.append("activate")

    async def fake_deactivate() -> None:
        calls.append("deactivate")

    monkeypatch.setattr(ctl, "_activate", fake_activate)
    monkeypatch.setattr(ctl, "_deactivate", fake_deactivate)
    ctl.wire()
    return ctl, calls


async def test_refcount_two_concurrent_missions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    _ctl, calls = _quiet_controller(monkeypatch, bus)

    await bus.publish(CUControlStarted(mission_id="a"))
    await bus.publish(CUControlStarted(mission_id="b"))
    assert calls == ["activate"], "second overlapping mission must not re-show"

    await bus.publish(CUControlEnded(mission_id="a"))
    assert calls == ["activate"], "border must stay while a sibling still runs"

    await bus.publish(CUControlEnded(mission_id="b"))
    assert calls == ["activate", "deactivate"]


async def test_stray_ended_without_started_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    _ctl, calls = _quiet_controller(monkeypatch, bus)
    await bus.publish(CUControlEnded(mission_id="ghost"))
    assert calls == []


async def test_headless_host_never_spawns_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No display → activate arms nothing visual and never touches Popen."""
    bus = EventBus()
    ctl = CUIndicatorController(bus)
    ctl.wire()

    monkeypatch.setattr(ctl, "_arm_escape", lambda: False)
    monkeypatch.setattr(
        CUIndicatorController,
        "_border_capability",
        staticmethod(lambda: (False, "no display on this host (headless)")),
    )

    def _explode() -> None:  # pragma: no cover — the assertion IS the test
        raise AssertionError("sidecar must not spawn on a headless host")

    monkeypatch.setattr(ctl, "_spawn_sidecar", _explode)
    await bus.publish(CUControlStarted(mission_id="a"))
    await bus.publish(CUControlEnded(mission_id="a"))


async def test_disabled_config_skips_border_but_arms_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    ctl = CUIndicatorController(bus)
    ctl.wire()
    armed: list[bool] = []
    monkeypatch.setattr(
        ctl, "_arm_escape", lambda: armed.append(True) or True
    )
    monkeypatch.setattr(
        controller_mod, "_screen_indicator_enabled", lambda: False
    )
    monkeypatch.setattr(
        ctl,
        "_spawn_sidecar",
        lambda: (_ for _ in ()).throw(AssertionError("no sidecar when off")),
    )
    await bus.publish(CUControlStarted(mission_id="a"))
    assert armed == [True], "Escape is a safety affordance — armed even with the visual off"
    await bus.publish(CUControlEnded(mission_id="a"))


async def test_escape_cancels_missions_without_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_cancel(reason: str = "", *, suppress_new: bool = True) -> bool:
        seen["reason"] = reason
        seen["suppress_new"] = suppress_new
        return True

    import jarvis.harness.computer_use_context as cu_ctx

    monkeypatch.setattr(cu_ctx, "cancel_active_cu", fake_cancel)
    CUIndicatorController._cancel_all_missions()
    assert seen == {"reason": "user_escape", "suppress_new": False}


def test_esc_binding_matches_each_backend_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live 2026-07-16: global-hotkeys (win32) only accepts "escape" — "esc"
    raises and disables the registration; pynput canonicalizes the key to
    "esc" and never matches "escape". The binding must be per-platform."""
    monkeypatch.setattr(controller_mod.sys, "platform", "win32")
    assert controller_mod._esc_binding() == ["escape"]
    monkeypatch.setattr(controller_mod.sys, "platform", "darwin")
    assert controller_mod._esc_binding() == ["esc"]
    monkeypatch.setattr(controller_mod.sys, "platform", "linux")
    assert controller_mod._esc_binding() == ["esc"]


def test_hint_table_covers_every_supported_locale() -> None:
    """Repo language rule: a phrase table carries ALL supported locales and
    resolves through the one resolver."""
    from jarvis.core.turn_language import resolve_output_language

    supported = {
        resolve_output_language(pin, "", "", default="en")
        for pin in ("de", "en", "es")
    }
    assert supported == set(_ESC_HINTS.keys())
    assert all(hint.strip() for hint in _ESC_HINTS.values())


def test_wire_cu_indicator_is_idempotent_per_bus() -> None:
    bus = EventBus()
    first = wire_cu_indicator(bus)
    second = wire_cu_indicator(bus)
    assert first is not None
    assert first is second
    assert wire_cu_indicator(None) is None


async def test_dead_pipe_disables_sidecar_quietly() -> None:
    bus = EventBus()
    ctl = CUIndicatorController(bus)

    class _DeadStdin:
        def write(self, _data: str) -> None:
            raise BrokenPipeError

        def flush(self) -> None:  # pragma: no cover
            raise BrokenPipeError

    class _DeadProc:
        stdin = _DeadStdin()

        @staticmethod
        def poll() -> None:
            return None

    ctl._proc = _DeadProc()  # type: ignore[assignment]
    assert ctl._send("show", hint="x") is False
    assert ctl._proc is None, "a broken pipe must drop the sidecar handle"


async def test_suppress_for_grab_fails_open_without_acks() -> None:
    """A blank that never gets acked must still let the grab run."""
    bus = EventBus()
    ctl = CUIndicatorController(bus)
    monkeypatch_sent: list[str] = []
    ctl._send = lambda cmd, **f: monkeypatch_sent.append(cmd) or True  # type: ignore[method-assign]

    ran = False
    with ctl._suppress_for_grab():
        ran = True
    assert ran is True
    assert monkeypatch_sent == ["blank", "unblank"]
