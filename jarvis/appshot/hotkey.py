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
SCOPE_KEYS: dict[str, str] = {"window": "hotkey", "region": "region_hotkey"}
#: Scope → the binding name inside the shared ``HotkeyTrigger``.
_BINDINGS: dict[str, str] = {"window": "appshot", "region": "appshot_region"}


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


def configured_hotkeys(block: Any) -> dict[str, str]:
    """Scope → normalized shortcut from an ``[appshot]`` config block."""
    return {
        scope: normalize_hotkey(str(getattr(block, key, "") or ""))
        for scope, key in SCOPE_KEYS.items()
    }


class AppshotShortcut:
    """Owns whichever listeners the configured shortcuts need."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watchers: list[Any] = []
        self._trigger_task: asyncio.Task[None] | None = None
        self._busy = False
        not_started = ShortcutStatus(hotkey="", armed=False, detail="Not started yet.")
        self._statuses: dict[str, ShortcutStatus] = dict.fromkeys(SCOPE_KEYS, not_started)
        self._subscribed = False
        # The settings route and the ConfigReloaded subscriber can both reload
        # for one write; unserialized, the loser's listener would be orphaned.
        self._reload_lock = asyncio.Lock()

    @property
    def status(self) -> ShortcutStatus:
        """The front-window shortcut."""
        return self._statuses["window"]

    def status_for(self, scope: str) -> ShortcutStatus:
        return self._statuses[scope]

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
            for scope, hotkey in hotkeys.items():
                if not hotkey:
                    self._statuses[scope] = ShortcutStatus("", False, "No shortcut set.")
                elif not owns:
                    self._statuses[scope] = ShortcutStatus(
                        hotkey,
                        False,
                        "The main app owns global shortcuts; this instance does not arm them.",
                    )
                elif scope == "region" and hotkey == hotkeys["window"]:
                    self._statuses[scope] = ShortcutStatus(
                        hotkey, False, "This is already the shortcut for the front window."
                    )
                elif is_gesture(hotkey):
                    self._statuses[scope] = await self._arm_gesture(scope, hotkey)
                else:
                    self._statuses[scope] = self._check_combo(hotkey)
                    if self._statuses[scope].armed:
                        combos[scope] = hotkey
            if combos:
                self._trigger_task = asyncio.get_running_loop().create_task(
                    self._run_combos(combos), name="appshot-hotkey"
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
        watchers, self._watchers = self._watchers, []
        for watcher in watchers:
            await asyncio.to_thread(watcher.stop)
        task, self._trigger_task = self._trigger_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
        from jarvis.platform.probes import has_hotkey  # noqa: PLC0415
        from jarvis.trigger.hotkey import validate_hotkey  # noqa: PLC0415

        verdict = validate_hotkey(hotkey)
        if not getattr(verdict, "ok", True):
            return ShortcutStatus(
                hotkey=hotkey, armed=False, detail=str(getattr(verdict, "reason", "") or "")
            )
        if not has_hotkey():
            return ShortcutStatus(
                hotkey=hotkey,
                armed=False,
                detail="Global shortcuts are not available on this desktop.",
            )
        return ShortcutStatus(hotkey=hotkey, armed=True)

    async def _run_combos(self, combos: dict[str, str]) -> None:
        from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

        scopes = {_BINDINGS[scope]: scope for scope in combos}
        try:
            trigger = HotkeyTrigger({_BINDINGS[scope]: [combo] for scope, combo in combos.items()})
            async with trigger:
                async for name in trigger.events():
                    scope = scopes.get(name)
                    if scope is not None:
                        self._fire(scope)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - voice and chat keep working without it
            log.warning("appshot: shortcut listener stopped", exc_info=True)

    def _fire_threadsafe(self, scope: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fire, scope)

    def _fire(self, scope: str = "window") -> None:
        if self._busy:
            return
        self._busy = True
        task = asyncio.get_running_loop().create_task(self._take(scope), name="appshot-take")
        task.add_done_callback(lambda _t: setattr(self, "_busy", False))

    async def _take(self, scope: str) -> None:
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
            if any(key.startswith("appshot.") for key in event.changed_keys):
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
    "start_appshot_shortcut",
]
