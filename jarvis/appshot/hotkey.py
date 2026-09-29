"""The global appshot shortcut: armed after boot, re-armed on a settings change.

``[appshot].hotkey`` is either ``alt+alt`` (both Alt keys, watched by
:mod:`jarvis.appshot.gesture`), any combo in the shared hotkey syntax (armed
through the regular per-OS :class:`~jarvis.trigger.hotkey.HotkeyTrigger`), or
empty (off). Only the instance that owns ambient duties arms it — a dev app
beside the live one would otherwise take every appshot twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

BOTH_ALT = "alt+alt"


@dataclass(frozen=True, slots=True)
class ShortcutStatus:
    hotkey: str
    armed: bool
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"hotkey": self.hotkey, "armed": self.armed, "detail": self.detail}


def normalize_hotkey(value: str) -> str:
    """Canonical spelling: lower case, no spaces, both-Alt aliases folded."""
    combo = "+".join(part.strip().lower() for part in str(value or "").split("+") if part.strip())
    if combo in {"alt+alt", "left_alt+right_alt", "right_alt+left_alt", "alt+altgr", "altgr+alt"}:
        return BOTH_ALT
    return combo


class AppshotShortcut:
    """Owns whichever listener the configured shortcut needs."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watcher: Any | None = None
        self._trigger_task: asyncio.Task[None] | None = None
        self._busy = False
        self._status = ShortcutStatus(hotkey="", armed=False, detail="Not started yet.")
        self._subscribed = False

    @property
    def status(self) -> ShortcutStatus:
        return self._status

    async def start(self) -> ShortcutStatus:
        self._loop = asyncio.get_running_loop()
        self._subscribe_reload()
        return await self.reload()

    async def reload(self) -> ShortcutStatus:
        """Re-read ``[appshot].hotkey`` and re-arm. Never raises."""
        await self.stop()
        try:
            from jarvis.core.config import load_config  # noqa: PLC0415
            from jarvis.core.instance import current_instance  # noqa: PLC0415

            config = await asyncio.to_thread(load_config)
            hotkey = normalize_hotkey(config.appshot.hotkey)
            if not hotkey:
                self._status = ShortcutStatus(hotkey="", armed=False, detail="No shortcut set.")
                return self._status
            if not current_instance().owns_ambient_duties:
                self._status = ShortcutStatus(
                    hotkey=hotkey,
                    armed=False,
                    detail="The main app owns global shortcuts; this instance does not arm them.",
                )
                return self._status
            if hotkey == BOTH_ALT:
                self._status = await self._arm_both_alt()
            else:
                self._status = self._arm_combo(hotkey)
        except Exception as exc:  # noqa: BLE001 - a bad shortcut must not break boot
            log.warning("appshot: shortcut could not be armed", exc_info=True)
            self._status = ShortcutStatus(
                hotkey="",
                armed=False,
                detail=f"The shortcut failed to start ({type(exc).__name__}).",
            )
        log.info(
            "appshot: shortcut %s (%s)",
            self._status.hotkey or "off",
            "armed" if self._status.armed else self._status.detail,
        )
        return self._status

    async def stop(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            await asyncio.to_thread(watcher.stop)
        task, self._trigger_task = self._trigger_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _arm_both_alt(self) -> ShortcutStatus:
        from jarvis.appshot.gesture import BothAltWatcher, make_probe  # noqa: PLC0415

        probe, reason = await asyncio.to_thread(make_probe)
        if probe is None:
            return ShortcutStatus(hotkey=BOTH_ALT, armed=False, detail=reason)
        watcher = BothAltWatcher(self._fire_threadsafe, probe=probe)
        watcher.start()
        self._watcher = watcher
        return ShortcutStatus(hotkey=BOTH_ALT, armed=True)

    def _arm_combo(self, hotkey: str) -> ShortcutStatus:
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
        self._trigger_task = asyncio.get_running_loop().create_task(
            self._run_combo(hotkey), name="appshot-hotkey"
        )
        return ShortcutStatus(hotkey=hotkey, armed=True)

    async def _run_combo(self, hotkey: str) -> None:
        from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

        try:
            trigger = HotkeyTrigger({"appshot": [hotkey]})
            async with trigger:
                async for name in trigger.events():
                    if name == "appshot":
                        self._fire()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - voice and chat keep working without it
            log.warning("appshot: shortcut listener stopped", exc_info=True)

    def _fire_threadsafe(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fire)

    def _fire(self) -> None:
        if self._busy:
            return
        self._busy = True
        task = asyncio.get_running_loop().create_task(self._take(), name="appshot-take")
        task.add_done_callback(lambda _t: setattr(self, "_busy", False))

    async def _take(self) -> None:
        from jarvis.appshot.service import take_appshot  # noqa: PLC0415

        result = await take_appshot(trigger="hotkey", bus=self._bus)
        if not result.ok:
            log.info("appshot: shortcut press refused — %s", result.message)
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
    "AppshotShortcut",
    "ShortcutStatus",
    "get_shortcut",
    "normalize_hotkey",
    "start_appshot_shortcut",
]
