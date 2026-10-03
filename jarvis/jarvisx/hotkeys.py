"""The Jarvis X global shortcuts: armed after boot, re-armed on a settings change.

Six named shortcuts from ``[jarvisx]`` share ONE :class:`HotkeyTrigger`
(one backend registration, one listener task). Each keeps its own status so
the settings page can show which ones are live and why one is not: empty
(off), invalid, the same keys as another Jarvis X shortcut, no global-hotkey
backend on this desktop, or a second Jarvis instance that does not own the
global shortcuts.

The record shortcuts toggle: pressed while that recording runs, they stop it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: Shortcut name -> ``[jarvisx]`` key. Order is the settings page order.
SHORTCUT_KEYS: dict[str, str] = {
    "region": "hotkey_region",
    "window": "hotkey_window",
    "fullscreen": "hotkey_fullscreen",
    "record_region": "hotkey_record_region",
    "record_fullscreen": "hotkey_record_fullscreen",
    "stop_recording": "hotkey_stop_recording",
}

_EVENT_PREFIX = "jarvisx_"


@dataclass(frozen=True, slots=True)
class ShortcutStatus:
    hotkey: str
    armed: bool
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"hotkey": self.hotkey, "armed": self.armed, "detail": self.detail}


def normalize_hotkey(value: object) -> str:
    """Canonical spelling: lower case, no spaces, ``+``-joined."""
    return "+".join(part.strip().lower() for part in str(value or "").split("+") if part.strip())


def configured_hotkeys(block: Any) -> dict[str, str]:
    return {name: normalize_hotkey(getattr(block, key, "")) for name, key in SHORTCUT_KEYS.items()}


def check_hotkeys(hotkeys: dict[str, str]) -> dict[str, str]:
    """Per-shortcut problem text (``""`` = fine). Empty shortcuts are fine (off)."""
    from jarvis.trigger.hotkey import combos_collide, validate_hotkey  # noqa: PLC0415

    problems = {name: "" for name in hotkeys}
    for name, combo in hotkeys.items():
        if not combo:
            continue
        if combo in {"alt+alt", "left_alt+right_alt", "right_alt+left_alt"}:
            problems[name] = "Both Alt keys together is the appshot gesture; pick another shortcut."
            continue
        verdict = validate_hotkey(combo)
        if not verdict.ok:
            problems[name] = verdict.reason or "This shortcut is not valid."
    names = list(hotkeys)
    for index, name in enumerate(names):
        for other in names[:index]:
            if problems[name] or problems[other]:
                continue
            if combos_collide(hotkeys[name], hotkeys[other]):
                problems[name] = f"Uses the same keys as the {other.replace('_', ' ')} shortcut."
    return problems


def _default_trigger_factory(bindings: dict[str, list[str]]) -> Any:
    from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

    return HotkeyTrigger(bindings)


def _default_has_hotkey() -> bool:
    from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

    return bool(has_hotkey())


def _default_owns_shortcuts() -> bool:
    from jarvis.core.instance import current_instance  # noqa: PLC0415

    return bool(current_instance().owns_ambient_duties)


def _load_config() -> Any:
    from jarvis.core.config import load_config  # noqa: PLC0415

    return load_config()


class JarvisXShortcuts:
    """Owns the listener for all six shortcuts."""

    def __init__(
        self,
        on_press: Callable[[str], Any],
        *,
        bus: Any | None = None,
        config_loader: Callable[[], Any] = _load_config,
        trigger_factory: Callable[[dict[str, list[str]]], Any] = _default_trigger_factory,
        has_hotkey: Callable[[], bool] = _default_has_hotkey,
        owns_shortcuts: Callable[[], bool] = _default_owns_shortcuts,
    ) -> None:
        self._on_press = on_press
        self._bus = bus
        self._config_loader = config_loader
        self._trigger_factory = trigger_factory
        self._has_hotkey = has_hotkey
        self._owns_shortcuts = owns_shortcuts
        self._task: asyncio.Task[None] | None = None
        self._subscribed = False
        self._statuses: dict[str, ShortcutStatus] = {
            name: ShortcutStatus("", False, "Not started yet.") for name in SHORTCUT_KEYS
        }

    @property
    def statuses(self) -> dict[str, ShortcutStatus]:
        return dict(self._statuses)

    async def start(self) -> dict[str, ShortcutStatus]:
        self._subscribe_reload()
        return await self.reload()

    async def reload(self) -> dict[str, ShortcutStatus]:
        """Re-read ``[jarvisx]`` and re-arm. Never raises."""
        await self.stop()
        try:
            config = await asyncio.to_thread(self._config_loader)
            block = config.jarvisx
            hotkeys = configured_hotkeys(block)
            self._statuses = self._plan(bool(block.enabled), hotkeys)
            bindings = {
                f"{_EVENT_PREFIX}{name}": [status.hotkey]
                for name, status in self._statuses.items()
                if status.armed
            }
            if bindings:
                self._task = asyncio.get_running_loop().create_task(
                    self._listen(bindings), name="jarvisx-hotkeys"
                )
        except Exception as exc:  # noqa: BLE001 - a bad shortcut must not break the app
            log.warning("jarvisx: shortcuts could not be armed", exc_info=True)
            detail = f"The shortcuts failed to start ({type(exc).__name__})."
            self._statuses = {name: ShortcutStatus("", False, detail) for name in SHORTCUT_KEYS}
        armed = [f"{n}={s.hotkey}" for n, s in self._statuses.items() if s.armed]
        log.info("jarvisx: shortcuts %s", ", ".join(armed) if armed else "none armed")
        return self.statuses

    def _plan(self, enabled: bool, hotkeys: dict[str, str]) -> dict[str, ShortcutStatus]:
        problems = check_hotkeys(hotkeys)
        owns = self._owns_shortcuts()
        backend = self._has_hotkey()
        plan: dict[str, ShortcutStatus] = {}
        for name, combo in hotkeys.items():
            if not combo:
                plan[name] = ShortcutStatus("", False, "No shortcut set.")
            elif not enabled:
                plan[name] = ShortcutStatus(combo, False, "Jarvis X is switched off.")
            elif problems[name]:
                plan[name] = ShortcutStatus(combo, False, problems[name])
            elif not owns:
                plan[name] = ShortcutStatus(
                    combo,
                    False,
                    "The main app owns global shortcuts; this instance does not arm them.",
                )
            elif not backend:
                plan[name] = ShortcutStatus(
                    combo, False, "Global shortcuts are not available on this desktop."
                )
            else:
                plan[name] = ShortcutStatus(combo, True)
        return plan

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _listen(self, bindings: dict[str, list[str]]) -> None:
        try:
            trigger = self._trigger_factory(bindings)
            async with trigger:
                async for event in trigger.events():
                    if event.startswith(_EVENT_PREFIX):
                        self._fire(event[len(_EVENT_PREFIX) :])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the app keeps working without them
            log.warning("jarvisx: shortcut listener stopped", exc_info=True)

    def _fire(self, name: str) -> None:
        try:
            result = self._on_press(name)
            if asyncio.iscoroutine(result):
                task = asyncio.get_running_loop().create_task(result, name=f"jarvisx-{name}")
                _TASKS.add(task)
                task.add_done_callback(_TASKS.discard)
        except Exception:  # noqa: BLE001 - one press failing must not stop the listener
            log.warning("jarvisx: shortcut %s failed", name, exc_info=True)

    def _subscribe_reload(self) -> None:
        if self._subscribed or self._bus is None or not hasattr(self._bus, "subscribe"):
            return
        from jarvis.core.events import ConfigReloaded  # noqa: PLC0415

        async def _on_reload(event: ConfigReloaded) -> None:
            if any(key.startswith("jarvisx.") for key in event.changed_keys):
                await self.reload()

        self._bus.subscribe(ConfigReloaded, _on_reload)
        self._subscribed = True


_TASKS: set[asyncio.Task[Any]] = set()


async def press(name: str) -> None:
    """What each shortcut does."""
    from jarvis.jarvisx.service import get_service  # noqa: PLC0415

    service = get_service()
    if name in ("region", "window", "fullscreen"):
        result = await service.capture(name)  # type: ignore[arg-type]
    elif name == "record_region":
        result = await service.toggle_recording("region")
    elif name == "record_fullscreen":
        result = await service.toggle_recording("fullscreen")
    elif name == "stop_recording":
        if not service.recording:
            return
        result = await service.stop_recording()
    else:
        return
    if not result.ok:
        log.info("jarvisx: %s shortcut: %s", name, result.message)


_shortcuts: JarvisXShortcuts | None = None


def get_shortcuts() -> JarvisXShortcuts | None:
    return _shortcuts


async def start_jarvisx(bus: Any, app_state: Any | None = None) -> JarvisXShortcuts:
    """Boot hook (scheduled after the app is ready, never on the boot path)."""
    global _shortcuts
    from jarvis.jarvisx.service import get_service  # noqa: PLC0415

    get_service().bind(bus=bus, app_state=app_state)
    if _shortcuts is None:
        _shortcuts = JarvisXShortcuts(press, bus=bus)
        await _shortcuts.start()
    return _shortcuts


__all__ = [
    "SHORTCUT_KEYS",
    "JarvisXShortcuts",
    "ShortcutStatus",
    "check_hotkeys",
    "configured_hotkeys",
    "get_shortcuts",
    "normalize_hotkey",
    "press",
    "start_jarvisx",
]
