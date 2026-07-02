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
    # Master switch for the per-action read-back verification suite (type read-back,
    # click_element confirmation, no blind focus->type batching). See
    # ComputerUseConfig.strict_verify. Default ON.
    strict_verify: bool = True
    # Proactive zoom-before-click (DEFAULT OFF since 2026-06-27 — see
    # ComputerUseConfig.zoom_before_click). Internal screenshot crop only.
    zoom_before_click: bool = False
    # UIA snap-missed-click-to-element fallback (DEFAULT OFF since 2026-06-27 —
    # the BUG-CU-UIASNAP wild-snap; see ComputerUseConfig.uia_click_fallback).
    uia_click_fallback: bool = False
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
    # Which monitor CU captures + acts on ([computer_use].monitor): "primary"
    # (default), "foreground", or "all". When "primary", the loop brings the
    # target window onto the main monitor before acting (audit G8c).
    monitor: str = "primary"
    # Which screen is "the main monitor" when monitor="primary"
    # ([computer_use].main_monitor): "primary" | "largest" | explicit id.
    main_monitor: str = "primary"
    # CU v2: coordinate space the model's click coordinates are parsed in
    # ([computer_use].coordinate_space): "auto" | "normalized_1000" |
    # "image_pixels". "auto" resolves per provider (capability first, family
    # metadata second) — see jarvis/cu/conventions.py.
    coordinate_space: str = "auto"
    # CU v2: capture framing ([computer_use].capture_scope): "window" (default)
    # crops every capture to the foreground target window; "monitor" restores
    # the whole-monitor framing. See jarvis/cu/capture.py::select_capture_target.
    capture_scope: str = "window"
    # CU v2: maximize the target window on its own monitor before acting
    # ([computer_use].normalize_window). DEFAULT OFF — the restore/maximize
    # animation visibly "zooms" open windows and rearranges the user's layout
    # uninvited (maintainer complaint 2026-07-02); the window-scoped capture
    # covers the grounding need without touching the window. See
    # jarvis/platform/window_state.py::normalize_foreground_window.
    normalize_window: bool = False


_CONTEXT: ComputerUseContext | None = None

# Active Computer-Use cancel token registry (BUG-CU-HANGUP, 2026-05-28).
# ``ComputerUseHarness.invoke`` registers its CancelScope token here for the
# duration of a mission and removes it in the finally block. The voice hangup
# handler ("auflegen") calls ``cancel_active_cu()`` to stop ONLY the running
# Computer-Use mission(s) -- it must NOT use ``KillSwitch.trip()``, which is
# global and would also kill OpenClaw background missions (the documented
# hangup contract keeps those alive; only their voice readback is muted).
#
# This is a SET, not a single slot (BUG-CU-CONCURRENT-CANCEL, 2026-06-24): CU
# runs as a detached background task, and two missions can overlap (the same
# voice request dispatched twice, or a follow-up before the first finished).
# A single slot only remembered the last registration, so a hangup cancelled
# ONE mission while the other kept clicking the screen for ~22 s after the user
# hung up (live: data/jarvis_desktop.log 20:45:17 + 20:45:28 both active ->
# 20:45:54 hangup -> the sibling ran on to 20:46:16). Every active mission
# registers; a hangup cancels them ALL; each mission removes only its OWN token.
_ACTIVE_CU_TOKENS: set[Any] = set()


def register_active_cu_token(token: Any) -> None:
    """Register the cancel token of a running CU mission so the voice hangup
    path can cancel it CU-scoped.

    Concurrency-safe: multiple overlapping missions may each register, and a
    single ``cancel_active_cu()`` cancels every one. Passing ``None`` CLEARS
    the whole registry -- a global reset used only by tests / teardown; the
    harness never clears this way (it removes its own token via
    ``unregister_active_cu_token`` so a sibling mission stays cancelable).
    """
    if token is None:
        _ACTIVE_CU_TOKENS.clear()
        return
    _ACTIVE_CU_TOKENS.add(token)


def unregister_active_cu_token(token: Any) -> None:
    """Remove a finished mission's token from the active registry.

    Called from ``ComputerUseHarness.invoke``'s finally block. Removes ONLY the
    given token, leaving any concurrently-running sibling mission registered and
    still cancelable. A no-op (never raises) if the token was already removed.
    """
    _ACTIVE_CU_TOKENS.discard(token)


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
    """Cancel EVERY active Computer-Use mission AND open the post-hangup
    suppression window so a mission starting moments later is aborted too.

    Cancels ALL registered tokens (overlapping missions are common — CU runs as
    a detached background task), not just the most recent. Returns True if at
    least one live token was cancelled. Never raises (the hangup path must never
    crash) — a token whose ``cancel`` raises is skipped, the rest still cancel.
    """
    global _CU_SUPPRESS_UNTIL
    try:
        import time  # noqa: PLC0415
        _CU_SUPPRESS_UNTIL = time.monotonic() + _CU_SUPPRESS_GRACE_S
    except Exception:  # noqa: BLE001
        pass
    cancelled_any = False
    for tok in list(_ACTIVE_CU_TOKENS):
        try:
            tok.cancel(reason)
            cancelled_any = True
        except Exception:  # noqa: BLE001 — one bad token must not block the rest
            continue
    return cancelled_any


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
    for tok in list(_ACTIVE_CU_TOKENS):
        try:
            if not bool(tok.is_cancelled()):
                return True
        except Exception:  # noqa: BLE001 — unknown token shape: assume live
            return True
    return False


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
    "strict_verify",
    "zoom_before_click",
    "uia_click_fallback",
    "announce_progress",
    "capture_scope",
    "normalize_window",
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


def subscribe_context_reload(bus: EventBus | None) -> None:
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
            "ComputerUseHarness context not set. "
            "The main app must call `set_computer_use_context(...)` "
            "before the first dispatch.",
        )
    return _CONTEXT
