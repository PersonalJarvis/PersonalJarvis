"""Context registry for the ComputerUseHarness (ADR-0008).

Because harness plugins are instantiated without arguments via `entry_points`,
but the Computer-Use loop needs access to VisionEngine, BrainManager,
ToolExecutor, etc., we maintain a process-wide context singleton.

The app calls `set_computer_use_context(ctx)` once at startup; the harness
retrieves it in `invoke()`. An unset context raises a clear error message
rather than silently degrading.

This is a deliberate pragmatic solution for the plugin architecture — the
rest of the system uses dependency injection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.control import CostMeter, KillSwitch
    from jarvis.core.bus import EventBus
    from jarvis.vision import VisionEngine


@dataclass
class ComputerUseContext:
    """All dependencies of the CU harness in one place.

    `brain_manager` and `tool_executor` are typed as `Any` to avoid importing
    their concrete classes here (cyclic imports from the harness module are a
    risk).
    """
    vision_engine: VisionEngine
    brain_manager: Any                    # jarvis.brain.manager.BrainManager
    tool_executor: Any                    # jarvis.safety.tool_executor.ToolExecutor
    tools: dict[str, Any] | None = None
    bus: EventBus | None = None
    cost_meter: CostMeter | None = None
    kill_switch: KillSwitch | None = None
    step_budget: int = 100  # generous default; see ComputerUseConfig.step_budget
    per_step_timeout_s: float = 30.0
    think_timeout_cap_s: float = 10.0  # L10: tunable model-call ceiling (sec)
    image_max_bytes: int = 300_000  # L7: tunable per-screenshot byte budget
    image_max_dimension: int = 2048  # L7: tunable per-screenshot longest-side px
    settle_scale: float = 1.0  # L8: multiplier on the loop's fixed settle waits
    fast_step_model: str = ""  # L9: cheaper model id for trivial steps ("" = off)
    plan_model_override: str | None = None
    verify_after_each_step: bool = True
    # Proactive zoom-before-click (opt-in, default OFF). See
    # ComputerUseConfig.zoom_before_click. Internal screenshot crop only —
    # nothing renders on screen.
    zoom_before_click: bool = False
    max_replans: int = 2                    # from ADR-0008; configurable
    # Spoken per-step milestones ("Schritt N von M erledigt."). OFF by default
    # (2026-06-10): the counter counts successful ACTIONS, not verified plan
    # steps, so it inflated to "6 von 6 erledigt" on a mission that then kept
    # running and failed — spoken misinformation. Opt back in via
    # ``[computer_use].announce_progress``.
    announce_progress: bool = False
    # Wave 3 (2026-05-29): optional native Gemini computer_use engine
    # (jarvis.harness.native_computer_use.GeminiNativeCU) or None. When set,
    # the loop tries it for the per-step action decision and falls back to the
    # hand-rolled vision+JSON path on any failure. None = hand-rolled only
    # (the default, since [computer_use].prefer_native defaults False).
    native_cu: Any = None


_CONTEXT: ComputerUseContext | None = None

# Active Computer-Use cancel token registry (BUG-CU-HANGUP, 2026-05-28).
# ``ComputerUseHarness.invoke`` registers its CancelScope token here for the
# duration of a mission and clears it in the finally block. The voice hangup
# handler ("auflegen") calls ``cancel_active_cu()`` to stop ONLY the running
# Computer-Use mission -- it must NOT use ``KillSwitch.trip()``, which is
# global and would also kill OpenClaw background missions (the documented
# hangup contract keeps those alive; only their voice readback is muted).
_ACTIVE_CU_TOKEN: Any = None


def register_active_cu_token(token: Any) -> None:
    """Register (or clear, with ``None``) the cancel token of the running CU
    mission so the voice hangup path can cancel it CU-scoped."""
    global _ACTIVE_CU_TOKEN
    _ACTIVE_CU_TOKEN = token


# Post-hangup suppression window (BUG-CU-HANGUP-RACE, 2026-05-28). There is a
# multi-second gap between a voice request being accepted and the CU mission
# actually starting (force-spawn -> gate -> dispatch -> first screenshot+brain).
# If the user says "auflegen" inside that gap, ``cancel_active_cu`` finds NO
# active token yet, so the late-starting mission would run anyway and even
# speak/click after the hangup. ``cancel_active_cu`` therefore ALSO opens a
# short suppression window; ``ComputerUseHarness.invoke`` checks it at startup
# and aborts a mission that is starting just after a hangup.
_CU_SUPPRESS_UNTIL = 0.0
_CU_SUPPRESS_GRACE_S = 8.0


def cancel_active_cu(reason: str = "voice_hangup") -> bool:
    """Cancel the active Computer-Use mission AND open the post-hangup
    suppression window so a mission starting moments later is aborted too.

    Returns True if a live token was cancelled. Never raises (the hangup path
    must never crash)."""
    global _CU_SUPPRESS_UNTIL
    try:
        import time  # noqa: PLC0415
        _CU_SUPPRESS_UNTIL = time.monotonic() + _CU_SUPPRESS_GRACE_S
    except Exception:  # noqa: BLE001
        pass
    tok = _ACTIVE_CU_TOKEN
    if tok is None:
        return False
    try:
        tok.cancel(reason)
        return True
    except Exception:  # noqa: BLE001
        return False


def cu_mission_active() -> bool:
    """True while a Computer-Use mission is running (token registered and not
    cancelled).

    The voice pipeline polls this in its idle-timeout branch and in the
    single-turn hangup decision so the session stays open while the agent is
    still working (live bug 2026-06-10: the idle timeout fired 40 s into a
    running CU mission — the user naturally says nothing while watching the
    agent — closing the session/orb while the mission kept clicking invisibly
    for two more minutes). A CANCELLED token does not count: the hangup that
    cancelled it wants the session closed. Bounded: the harness clears the
    token in its ``finally`` and every mission has a hard deadline, so this
    can never wedge the session open forever. Never raises.
    """
    tok = _ACTIVE_CU_TOKEN
    if tok is None:
        return False
    try:
        return not bool(tok.is_cancelled())
    except Exception:  # noqa: BLE001 — unknown token shape: assume live
        return True


def cu_recently_cancelled() -> bool:
    """True if a voice hangup fired within the suppression window -- a CU
    mission starting now should abort (the user just hung up on it)."""
    try:
        import time  # noqa: PLC0415
        return time.monotonic() < _CU_SUPPRESS_UNTIL
    except Exception:  # noqa: BLE001
        return False


def clear_cu_suppression() -> None:
    """Clear the suppression window (called when a fresh CU mission is
    legitimately allowed to start, e.g. after the window elapses)."""
    global _CU_SUPPRESS_UNTIL
    _CU_SUPPRESS_UNTIL = 0.0


# Hot-reload (2026-05-30): the context is a process-wide singleton built once
# at boot (jarvis/brain/factory.py). Without this hook a Self-Mod write to
# ``computer_use.step_budget`` (voice: "setze Schrittlimit auf N") would only
# take effect after an app restart, contradicting the allowlist's
# ``needs_restart=False`` promise. We subscribe to ``ConfigReloaded`` and
# refresh the hot-reloadable scalar fields IN PLACE on the existing singleton
# (it is a mutable dataclass), reusing all the heavy deps (vision engine, brain
# manager, tool executor). Mirrors the cache-invalidation pattern in
# jarvis/brain/resolver.py. Only scalars that the loop reads per-mission are
# refreshed; the deps themselves are never swapped here.
_RELOADABLE_FIELDS: tuple[str, ...] = (
    "step_budget",
    "per_step_timeout_s",
    "think_timeout_cap_s",
    "image_max_bytes",
    "image_max_dimension",
    "settle_scale",
    "fast_step_model",
    "max_replans",
    "verify_after_each_step",
    "zoom_before_click",
    "announce_progress",
)
_subscribed_bus_id: int | None = None


def _refresh_context_from_config() -> None:
    """Re-read ``[computer_use]`` and update the live singleton's scalar knobs.

    No-op when no context is set yet (boot has not wired it). Never raises:
    a config-read failure must not break the event bus.
    """
    ctx = _CONTEXT
    if ctx is None:
        return
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        cu_cfg = getattr(load_config(), "computer_use", None)
        if cu_cfg is None:
            return
        for name in _RELOADABLE_FIELDS:
            if hasattr(cu_cfg, name):
                setattr(ctx, name, getattr(cu_cfg, name))
    except Exception:  # noqa: BLE001 — never break the bus on a reload
        pass


def subscribe_context_reload(bus: "EventBus | None") -> None:
    """Subscribe the CU context to ``ConfigReloaded`` (idempotent per bus).

    Called once after the context is wired (jarvis/brain/factory.py). On every
    ConfigReloaded event the live singleton's step_budget / timeout / replan
    knobs are refreshed, so a voice-tunable change applies to the next mission
    without an app restart.
    """
    global _subscribed_bus_id
    if bus is None or _subscribed_bus_id == id(bus):
        return
    try:
        from jarvis.core.events import ConfigReloaded  # noqa: PLC0415

        async def _on_reload(event: object) -> None:
            if isinstance(event, ConfigReloaded):
                _refresh_context_from_config()

        bus.subscribe_all(_on_reload)
        _subscribed_bus_id = id(bus)
    except Exception:  # noqa: BLE001 — survive without live reload
        pass


def set_computer_use_context(ctx: ComputerUseContext | None) -> None:
    """Set (or clear with ctx=None) the global CU context."""
    global _CONTEXT
    _CONTEXT = ctx


def get_computer_use_context() -> ComputerUseContext:
    """Return the configured context, or raise a clear error if unset."""
    if _CONTEXT is None:
        raise RuntimeError(
            "ComputerUseHarness-Context nicht gesetzt. "
            "Die Haupt-App muss vor dem ersten Dispatch "
            "`set_computer_use_context(...)` aufrufen.",
        )
    return _CONTEXT
