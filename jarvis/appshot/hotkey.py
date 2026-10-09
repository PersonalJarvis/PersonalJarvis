"""The global appshot shortcuts: armed after boot, re-armed on a settings change.

Two shortcuts, one per scope:

* ``[appshot].hotkey`` takes the front window;
* ``[appshot].region_hotkey`` first lets the user drag out an area.

Each is either a both-keys gesture — ``alt+alt``, ``shift+shift`` or
``ctrl+ctrl``, watched by :mod:`jarvis.appshot.gesture` — any combo in the
shared hotkey syntax (armed through the regular per-OS
:class:`~jarvis.trigger.hotkey.HotkeyTrigger`), or empty (off). Only the
instance that owns ambient duties arms them — a dev app beside the live one
would otherwise take every appshot twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

BOTH_ALT = "alt+alt"
BOTH_SHIFT = "shift+shift"
BOTH_CTRL = "ctrl+ctrl"
#: Spellings of the two-sided gestures that fold onto their canonical form.
_GESTURE_ALIASES: dict[str, str] = {
    "alt+alt": BOTH_ALT,
    "left_alt+right_alt": BOTH_ALT,
    "right_alt+left_alt": BOTH_ALT,
    "alt+altgr": BOTH_ALT,
    "altgr+alt": BOTH_ALT,
    "alt+right_alt": BOTH_ALT,
    "right_alt+alt": BOTH_ALT,
    "shift+shift": BOTH_SHIFT,
    "left_shift+right_shift": BOTH_SHIFT,
    "right_shift+left_shift": BOTH_SHIFT,
    "ctrl+ctrl": BOTH_CTRL,
    "left_ctrl+right_ctrl": BOTH_CTRL,
    "right_ctrl+left_ctrl": BOTH_CTRL,
    "ctrl+right_ctrl": BOTH_CTRL,
    "right_ctrl+ctrl": BOTH_CTRL,
}

#: Scope → the ``[appshot]`` key holding its shortcut.
SCOPE_KEYS: dict[str, str] = {
    "window": "hotkey", "region": "region_hotkey", "recording": "recording_hotkey",
}
#: Scope → the binding name inside the shared ``HotkeyTrigger``.
_BINDINGS: dict[str, str] = {
    "window": "appshot", "region": "appshot_region", "recording": "appshot_recording",
}


@dataclass(frozen=True, slots=True)
class ShortcutStatus:
    hotkey: str
    armed: bool
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"hotkey": self.hotkey, "armed": self.armed, "detail": self.detail}


#: Status text for the both-Option gesture on a Mac. Its permission need is
#: UNVERIFIED (see ``jarvis.appshot.gesture``), so the hint names no permission:
#: sending someone to grant one it may not need is worse than saying nothing.
BOTH_OPTION_MAC_NOTE = (
    "If the both-Option shortcut does nothing on this Mac, pick a key "
    "combination instead."
)


def _both_alt_note() -> str:
    """The macOS-only hint that goes with an armed both-Option gesture."""
    from jarvis.platform import detect_platform  # noqa: PLC0415

    return BOTH_OPTION_MAC_NOTE if detect_platform() == "darwin" else ""


def normalize_hotkey(value: str) -> str:
    """Canonical spelling: lower case, no spaces, both-keys aliases folded."""
    combo = "+".join(part.strip().lower() for part in str(value or "").split("+") if part.strip())
    return _GESTURE_ALIASES.get(combo, combo)


def is_gesture(hotkey: str) -> bool:
    """A both-keys gesture, not a combo for the shared hotkey backends."""
    from jarvis.appshot.gesture import GESTURES  # noqa: PLC0415

    return hotkey in GESTURES


def shortcuts_conflict(left: str, right: str) -> bool:
    """Whether two appshot shortcuts would fire on the same press.

    A both-keys gesture is not the single token ``alt``: both Alts must not
    block every other Alt shortcut. Ordinary chords collide when one key set
    is the other, or sits inside it — ``ctrl+b`` also fires while
    ``ctrl+shift+b`` is held, so the area picker and the recorder would start
    together.
    """
    if not left or not right:
        return False
    if is_gesture(left) or is_gesture(right):
        return left == right
    from jarvis.trigger.hotkey import combos_collide  # noqa: PLC0415

    return combos_collide(left, right)


def _pynput_present() -> bool:
    """The Windows appshot listener is pynput, not the polled hotkey package."""
    import importlib.util  # noqa: PLC0415

    return importlib.util.find_spec("pynput") is not None


def configured_hotkeys(block: Any) -> dict[str, str]:
    """Scope → normalized shortcut from an ``[appshot]`` config block."""
    return {
        scope: normalize_hotkey(str(getattr(block, key, "") or ""))
        for scope, key in SCOPE_KEYS.items()
    }


async def request_saved_shortcut_access(
    previous: dict[str, str], current: dict[str, str], *, was_enabled: bool, enabled: bool,
) -> None:
    """Only saving or enabling a tap shortcut may ask for Input Monitoring."""
    from jarvis.core.instance import current_instance
    from jarvis.platform import detect_platform

    if not enabled or detect_platform() != "darwin" or not current_instance().owns_ambient_duties:
        return
    changed = any(
        combo and not is_gesture(combo) and (not was_enabled or previous[scope] != combo)
        for scope, combo in current.items()
    )
    if not changed:
        return
    from jarvis.platform.permission_service import get_permission_service
    from jarvis.platform.probes import display_present, has_hotkey

    if display_present() and has_hotkey():
        await get_permission_service().ensure_async(
            "input_monitoring", feature="global_shortcuts", interactive=True, wait_s=0.0,
        )


class AppshotShortcut:
    """Owns whichever listeners the configured shortcuts need."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watchers: list[Any] = []
        self._trigger_task: asyncio.Task[None] | None = None
        self._trigger: Any | None = None
        self._combo_scopes: set[str] = set()
        self._warm_task: asyncio.Task[None] | None = None
        self._busy = False
        self._recording_busy = False
        # One press that arrives while a toggle is still running. Dropping it
        # made the next press start a second recording instead of stopping.
        self._pending: dict[str, str] = {}
        not_started = ShortcutStatus(hotkey="", armed=False, detail="Not started yet.")
        self._statuses: dict[str, ShortcutStatus] = dict.fromkeys(SCOPE_KEYS, not_started)
        self._subscribed = False
        # The settings route and the ConfigReloaded subscriber can both reload
        # for one write; unserialized, the loser's listener would be orphaned.
        self._reload_lock = asyncio.Lock()

    @property
    def status(self) -> ShortcutStatus:
        """The front-window shortcut."""
        return self.status_for("window")

    def status_for(self, scope: str) -> ShortcutStatus:
        status = self._statuses[scope]
        trigger = self._trigger
        if scope not in self._combo_scopes or trigger is None:
            return status
        if trigger.armed:
            return ShortcutStatus(status.hotkey, True)
        detail = (
            "The shortcut is waiting for Input Monitoring access."
            if trigger.needs_input_monitoring
            else "The global shortcut listener is not running."
        )
        return ShortcutStatus(status.hotkey, False, detail)

    async def start(self) -> ShortcutStatus:
        self._loop = asyncio.get_running_loop()
        self._subscribe_reload()
        return await self.reload()

    async def reload(self) -> ShortcutStatus:
        """Re-read both shortcuts and re-arm. Never raises."""
        async with self._reload_lock:
            return await self._reload()

    async def _reload(self) -> ShortcutStatus:
        await self.stop()
        try:
            from jarvis.core.config import load_config  # noqa: PLC0415
            from jarvis.core.instance import current_instance  # noqa: PLC0415

            config = await asyncio.to_thread(load_config)
            hotkeys = configured_hotkeys(config.appshot)
            owns = current_instance().owns_ambient_duties
            combos: dict[str, str] = {}
            claimed: list[str] = []
            for scope, hotkey in hotkeys.items():
                if not hotkey:
                    self._statuses[scope] = ShortcutStatus("", False, "No shortcut set.")
                elif not config.screen_context.enabled:
                    self._statuses[scope] = ShortcutStatus(
                        hotkey, False, "Appshots are switched off.",
                    )
                elif not owns:
                    self._statuses[scope] = ShortcutStatus(
                        hotkey,
                        False,
                        "The main app owns global shortcuts; this instance does not arm them.",
                    )
                elif any(shortcuts_conflict(hotkey, previous) for previous in claimed):
                    self._statuses[scope] = ShortcutStatus(
                        hotkey, False, "This shortcut is already used by another AppShot action."
                    )
                elif is_gesture(hotkey):
                    self._statuses[scope] = await self._arm_gesture(scope, hotkey)
                else:
                    self._statuses[scope] = self._check_combo(hotkey)
                    if self._statuses[scope].armed:
                        combos[scope] = hotkey
                        self._statuses[scope] = ShortcutStatus(
                            hotkey, False, "The global shortcut listener is starting.",
                        )
                if hotkey:
                    claimed.append(hotkey)
            if combos:
                self._combo_scopes = set(combos)
                self._trigger_task = asyncio.get_running_loop().create_task(
                    self._run_combos(combos), name="appshot-hotkey"
                )
            if owns and bool(getattr(getattr(config, "screen_context", None), "enabled", False)):
                self._warm_task = asyncio.create_task(
                    self._warm_surfaces(bool(getattr(config.appshot, "effect", True))),
                    name="appshot-surfaces-warm",
                )
            from jarvis.appshot.recording import warm_recording_service

            await warm_recording_service(
                owns and bool(getattr(getattr(config, "screen_context", None), "enabled", False))
            )
        except Exception as exc:  # noqa: BLE001 - a bad shortcut must not break boot
            log.warning("appshot: shortcut could not be armed", exc_info=True)
            await self.stop()  # never leave half of a failed arming running
            failed = ShortcutStatus(
                hotkey="",
                armed=False,
                detail=f"The shortcut failed to start ({type(exc).__name__}).",
            )
            self._statuses = dict.fromkeys(SCOPE_KEYS, failed)
        for scope, status in self._statuses.items():
            log.info(
                "appshot: %s shortcut %s (%s)",
                scope,
                status.hotkey or "off",
                "armed" if status.armed else status.detail,
            )
        return self.status

    async def stop(self) -> None:
        from jarvis.appshot.recording import warm_recording_service

        await warm_recording_service(False)
        watchers, self._watchers = self._watchers, []
        for watcher in watchers:
            await asyncio.to_thread(watcher.stop)
        task, self._trigger_task = self._trigger_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._trigger = None
        self._combo_scopes.clear()
        self._statuses = {
            scope: ShortcutStatus(status.hotkey, False, "The shortcut is stopped.")
            for scope, status in self._statuses.items()
        }
        warm, self._warm_task = self._warm_task, None
        if warm is not None:
            warm.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warm
        from jarvis.appshot.picker_host import close_picker_host
        from jarvis.cu.indicator.controller import get_indicator_controller

        await close_picker_host()
        controller = get_indicator_controller()
        if controller is not None:
            await controller.warm_for_appshots(False)

    async def _warm_surfaces(self, effect: bool) -> None:
        from jarvis.appshot.picker_host import get_picker_host
        from jarvis.cu.indicator.controller import wire_cu_indicator

        try:
            await get_picker_host().prewarm()
        except Exception:
            log.warning("appshot: picker preparation failed; next press will retry", exc_info=True)
        if effect:
            try:
                controller = wire_cu_indicator(self._bus)
                if controller is not None:
                    await controller.warm_for_appshots(True)
            except Exception:
                log.warning(
                    "appshot: shutter preparation failed; next capture will retry", exc_info=True
                )

    async def _arm_gesture(self, scope: str, hotkey: str) -> ShortcutStatus:
        from jarvis.appshot.gesture import (  # noqa: PLC0415
            GESTURES,
            BothKeysWatcher,
            make_probe,
            together_window,
        )

        family = GESTURES[hotkey]
        probe, reason = await asyncio.to_thread(make_probe, family)
        if probe is None:
            return ShortcutStatus(hotkey=hotkey, armed=False, detail=reason)
        watcher = BothKeysWatcher(
            lambda: self._fire_threadsafe(scope),
            probe=probe,
            together_s=together_window(family),
        )
        watcher.start()
        self._watchers.append(watcher)
        detail = _both_alt_note() if hotkey == BOTH_ALT else ""
        return ShortcutStatus(hotkey=hotkey, armed=True, detail=detail)

    @staticmethod
    def _check_combo(hotkey: str) -> ShortcutStatus:
        from jarvis.platform import detect_platform  # noqa: PLC0415
        from jarvis.platform.probes import (  # noqa: PLC0415
            has_hotkey,
            hotkey_unavailable_reason,
        )
        from jarvis.trigger.hotkey import validate_hotkey  # noqa: PLC0415

        verdict = validate_hotkey(hotkey)
        if not getattr(verdict, "ok", True):
            return ShortcutStatus(
                hotkey=hotkey, armed=False, detail=str(getattr(verdict, "reason", "") or "")
            )
        # Voice hotkeys poll ``global_hotkeys`` on Windows. AppShot chords use
        # pynput, so a machine with only that package can still arm them.
        ready = has_hotkey() or (detect_platform() == "win32" and _pynput_present())
        if not ready:
            return ShortcutStatus(
                hotkey=hotkey,
                armed=False,
                detail=hotkey_unavailable_reason(),
            )
        return ShortcutStatus(hotkey=hotkey, armed=True)

    async def _run_combos(self, combos: dict[str, str]) -> None:
        from jarvis.platform import detect_platform

        if detect_platform() == "win32":
            await self._run_key_events(combos)
            return
        from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

        scopes = {_BINDINGS[scope]: scope for scope in combos}
        try:
            trigger = HotkeyTrigger({_BINDINGS[scope]: [combo] for scope, combo in combos.items()})
            async with trigger:
                self._trigger = trigger
                async for name in trigger.events():
                    scope = scopes.get(name)
                    if scope is not None:
                        self._fire(scope)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - voice and chat keep working without it
            log.warning("appshot: shortcut listener stopped", exc_info=True)
        finally:
            self._trigger = None
            for scope, hotkey in combos.items():
                self._statuses[scope] = ShortcutStatus(
                    hotkey, False, "The global shortcut listener is not running.",
                )

    async def _run_key_events(self, combos: dict[str, str]) -> None:
        from jarvis.appshot.key_events import AppshotKeyEvents
        from jarvis.trigger.backends.global_hotkeys import _normalize_combo

        listener = AppshotKeyEvents()
        rows = [
            [_normalize_combo(combo), None, lambda scope=scope: self._fire_threadsafe(scope)]
            for scope, combo in combos.items()
        ]
        try:
            listener.register(rows)
            starting = asyncio.create_task(asyncio.to_thread(listener.start))
            try:
                await asyncio.shield(starting)
            except asyncio.CancelledError:
                # Finish acquiring the listener before teardown, even if a
                # setting changes while its native hook is being created.
                await starting
                raise
            if not listener.ready:
                for scope, combo in combos.items():
                    self._statuses[scope] = ShortcutStatus(
                        combo, False, "The keyboard event listener could not start."
                    )
                return
            for scope, combo in combos.items():
                self._statuses[scope] = ShortcutStatus(combo, True)
            await asyncio.Future()  # Cancellation owns the listener's complete lifecycle.
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("appshot: keyboard event listener failed")
            for scope, combo in combos.items():
                self._statuses[scope] = ShortcutStatus(
                    combo, False, "The keyboard event listener stopped."
                )
        finally:
            await asyncio.to_thread(listener.stop)
            listener.unregister()
            for scope, combo in combos.items():
                if self._statuses[scope].armed:
                    self._statuses[scope] = ShortcutStatus(
                        combo, False, "The keyboard event listener stopped."
                    )

    def _fire_threadsafe(self, scope: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fire, scope)

    def _fire(self, scope: str = "window") -> None:
        busy_field = "_recording_busy" if scope == "recording" else "_busy"
        if getattr(self, busy_field):
            self._pending[busy_field] = scope
            return
        self._begin_take(scope, busy_field)

    def _begin_take(self, scope: str, busy_field: str) -> None:
        setattr(self, busy_field, True)

        def _finished(_task: asyncio.Task[None]) -> None:
            setattr(self, busy_field, False)
            pending = self._pending.pop(busy_field, None)
            if pending is not None:
                self._begin_take(pending, busy_field)

        task = asyncio.get_running_loop().create_task(self._take(scope), name="appshot-take")
        task.add_done_callback(_finished)

    async def _take(self, scope: str) -> None:
        if scope == "recording":
            from jarvis.appshot.recording import get_recording_service

            try:
                await get_recording_service().toggle()
            except ValueError as exc:
                await self._publish_refusal(str(exc))
            return
        from jarvis.appshot.service import take_appshot  # noqa: PLC0415

        result = await take_appshot(
            trigger="hotkey",
            bus=self._bus,
            scope="region" if scope == "region" else "window",
        )
        if result.ok:
            return
        log.info("appshot: shortcut press refused — %s", result.message)
        if result.reason_code != "cancelled":  # Esc on the picker needs no toast
            await self._publish_refusal(result.message)

    async def _publish_refusal(self, message: str) -> None:
        try:
            from jarvis.core.events import AppshotTaken  # noqa: PLC0415

            await self._bus.publish(
                AppshotTaken(source_layer="appshot", trigger="hotkey", delivered_to="refused",
                             target_label=message[:200])
            )
        except Exception:  # noqa: BLE001 - the log line above already records it
            log.debug("appshot: refusal receipt failed", exc_info=True)

    def _subscribe_reload(self) -> None:
        if self._subscribed or not hasattr(self._bus, "subscribe"):
            return
        from jarvis.core.events import ConfigReloaded  # noqa: PLC0415

        async def _on_reload(event: ConfigReloaded) -> None:
            if any(key.startswith(("appshot.", "screen_context.")) for key in event.changed_keys):
                await self.reload()

        self._bus.subscribe(ConfigReloaded, _on_reload)
        self._subscribed = True


_shortcut: AppshotShortcut | None = None


def get_shortcut() -> AppshotShortcut | None:
    return _shortcut


async def start_appshot_shortcut(bus: Any) -> AppshotShortcut:
    """Boot hook (scheduled after the app is ready, never on the boot path)."""
    global _shortcut
    if _shortcut is None:
        _shortcut = AppshotShortcut(bus)
        await _shortcut.start()
    return _shortcut


async def stop_appshot_shortcut() -> None:
    """Release the native helpers before the backend event loop closes."""
    global _shortcut
    shortcut, _shortcut = _shortcut, None
    if shortcut is not None:
        await shortcut.stop()
    else:
        from jarvis.appshot.picker_host import close_picker_host

        await close_picker_host()
    from jarvis.cu.indicator.controller import get_indicator_controller

    controller = get_indicator_controller()
    if controller is not None:
        await controller.close()
    from jarvis.appshot.recording import close_recording_service

    await close_recording_service()


__all__ = [
    "BOTH_ALT",
    "BOTH_CTRL",
    "BOTH_SHIFT",
    "SCOPE_KEYS",
    "AppshotShortcut",
    "ShortcutStatus",
    "configured_hotkeys",
    "is_gesture",
    "get_shortcut",
    "normalize_hotkey",
    "shortcuts_conflict",
    "start_appshot_shortcut",
]
