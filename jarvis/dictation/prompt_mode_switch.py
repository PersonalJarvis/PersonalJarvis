"""The one writer of the Prompt Mode switch, whichever surface flips it.

``[dictation].prompt_mode`` has three mirrors: the card under Voice, the pill
on the front page, and the sparkle on the native Jarvis bar. Each can flip
it. If each also wrote it, the value on disk, the value the running pipeline
reads and the value each surface shows would drift the moment two of them
disagreed — the multi-writer pattern the voice-mute flag was built to avoid
(one flip owner, one broadcast, every mirror listens).

So: every flip lands here. Disk first, the live config second, and then one
``DictationPromptModeChanged`` on the bus that every mirror redraws from. A
save that fails changes nothing live, for the reason the settings route
gives — a switch that looks on until the next restart is worse than one that
refused.

Pure orchestration; the imports that cost anything are inside the functions
so this module stays free on the boot path (AP-26).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def announce_prompt_mode(enabled: bool, *, bus: Any, source: str) -> None:
    """Broadcast the switch's current value to every mirror. Never raises."""
    if bus is None:
        return
    from jarvis.core.events import DictationPromptModeChanged

    try:
        await bus.publish(DictationPromptModeChanged(enabled=bool(enabled), source=source))
    except Exception:  # noqa: BLE001 — a mirror update must never break the flip
        log.exception("DictationPromptModeChanged publish failed")


async def apply_prompt_mode(
    enabled: bool,
    *,
    dictation_cfg: Any,
    bus: Any,
    source: str,
    persist: bool = True,
) -> bool:
    """Set ``[dictation].prompt_mode`` everywhere it lives, then say so.

    Returns ``True`` when the value is on disk and live. ``False`` means the
    save failed and NOTHING changed — the caller's surface should keep
    showing the old state rather than the one it hoped for.
    """
    value = bool(enabled)
    if persist:
        from jarvis.core import config_writer

        try:
            await asyncio.to_thread(config_writer.set_dictation_setting, "prompt_mode", value)
        except Exception:  # noqa: BLE001 — reported, and the switch stays honest
            log.warning(
                "dictation prompt mode could not be saved (source=%s); the switch stays as it was.",
                source or "unknown",
                exc_info=True,
            )
            return False
    if dictation_cfg is not None:
        try:
            dictation_cfg.prompt_mode = value
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("in-memory dictation.prompt_mode update skipped: %s", exc)
    log.info("dictation prompt mode %s (source=%s)", "ON" if value else "off", source or "unknown")
    await announce_prompt_mode(value, bus=bus, source=source)
    return True


__all__ = ["announce_prompt_mode", "apply_prompt_mode"]
