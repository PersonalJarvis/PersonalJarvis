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


async def announce_prompt_mode(
    enabled: bool, *, bus: Any, source: str, paused: bool = False
) -> None:
    """Broadcast the switch's current value to every mirror. Never raises.

    Both levels travel together — ``enabled`` is the setting on disk and
    ``paused`` the runtime hold the bar can put on it — so no mirror has to
    ask a second question to know what to draw.
    """
    if bus is None:
        return
    from jarvis.core.events import DictationPromptModeChanged

    try:
        await bus.publish(
            DictationPromptModeChanged(
                enabled=bool(enabled), paused=bool(paused), source=source
            )
        )
    except Exception:  # noqa: BLE001 — a mirror update must never break the flip
        log.exception("DictationPromptModeChanged publish failed")


async def toggle_prompt_mode_pause(*, cfg: Any, bus: Any, source: str) -> bool:
    """Pause or resume Prompt Mode from a surface. Returns the pause now held.

    The bar's sparkle. Deliberately NOT a settings change: the maintainer's
    ask (2026-08-28) is "off for now, and one click brings it back", so the
    value in ``jarvis.toml`` is left exactly as it is and the settings card
    keeps showing the switch as on. Nothing happens at all when the setting
    is off — there is nothing to pause, and a surface that offered it anyway
    would be offering a hold on a feature that is not running.
    """
    from jarvis.dictation.prompt_mode import (
        prompt_mode_configured,
        prompt_mode_paused,
        set_prompt_mode_paused,
    )

    if not prompt_mode_configured(cfg):
        return prompt_mode_paused()
    paused = set_prompt_mode_paused(not prompt_mode_paused())
    log.info(
        "dictation prompt mode %s (source=%s)",
        "PAUSED — the setting stays on" if paused else "resumed",
        source or "unknown",
    )
    await announce_prompt_mode(True, bus=bus, source=source, paused=paused)
    return paused


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
    # Writing the setting clears any runtime hold: a switch the user just
    # turned on must not come back paused from a click they made an hour ago.
    from jarvis.dictation.prompt_mode import set_prompt_mode_paused

    set_prompt_mode_paused(False)
    log.info("dictation prompt mode %s (source=%s)", "ON" if value else "off", source or "unknown")
    await announce_prompt_mode(value, bus=bus, source=source, paused=False)
    return True


__all__ = ["announce_prompt_mode", "apply_prompt_mode", "toggle_prompt_mode_pause"]
