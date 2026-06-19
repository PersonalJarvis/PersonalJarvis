"""Per-turn AI-Pointer push decision (AI Pointer step 7).

``resolve_turn_pointer`` is the single entry the brain calls once per turn. It is
the deictic gate plus the off-hot-path resolve, returning a prompt block and an
optional crop image to ride on THIS turn — or ("", None) when the utterance does
not point at the cursor, the feature is disabled, or no element is resolved.

This is the "no context-less garbage" contract in one place: an unrelated turn
("how's the weather?") never reaches the cursor resolver (the gate vetoes it),
and a fired gate that resolves nothing injects nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from jarvis.core.protocols import ImageBlock
from jarvis.pointer.context import (
    DEFAULT_CROP_RADIUS,
    DEFAULT_TIMEOUT_S,
    PointerContext,
    resolve_pointer_context_async,
)
from jarvis.pointer.intent import is_pointing_intent

log = logging.getLogger(__name__)

Gate = Callable[[str], bool]
Resolver = Callable[..., Awaitable[PointerContext]]


async def resolve_turn_pointer(
    user_text: str,
    *,
    enabled: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    crop_radius: int = DEFAULT_CROP_RADIUS,
    gate: Gate | None = None,
    resolver: Resolver | None = None,
) -> tuple[str, ImageBlock | None]:
    """Return ``(prompt_block, crop_image)`` for this turn, or ``("", None)``.

    ``gate`` / ``resolver`` are injectable for tests; the defaults wire the real
    regex gate and the timeout-bounded resolver. Never raises (AD-OE6).
    """
    if not enabled:
        return ("", None)
    g = gate or is_pointing_intent
    try:
        if not g(user_text):
            return ("", None)
        if resolver is not None:
            pc = await resolver(timeout_s=timeout_s)
        else:
            # Headless / Wayland fast-skip: no cursor on this host → don't even
            # dispatch the worker thread (cloud-first €5-VPS: zero overhead).
            from jarvis.platform.capabilities import detect_capabilities

            if not detect_capabilities().has_cursor:
                return ("", None)
            pc = await resolve_pointer_context_async(
                timeout_s=timeout_s, crop_radius=crop_radius
            )
        if not pc.available:
            return ("", None)
        return (pc.render(), pc.crop)
    except Exception:
        log.debug("AI Pointer turn resolve failed", exc_info=True)
        return ("", None)


__all__ = ["resolve_turn_pointer"]
