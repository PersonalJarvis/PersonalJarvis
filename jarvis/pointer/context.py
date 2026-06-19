"""Compose the AI-Pointer context for one turn (AI Pointer step 5).

``resolve_pointer_context()`` chains cursor position -> element-at-point ->
optional ROI crop into a single :class:`PointerContext`. The crop is added
*only* when the element is unlabeled (a raster graphic) — the accessibility
element is the primary signal; the crop is the scoped fallback.

The async wrapper enforces a hard timeout (AP-9: off the voice hot path; the
native UIA tree query can be slow on huge Chrome/VSCode trees, so the caller
never blocks the turn longer than ``timeout_s``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from jarvis.core.protocols import ImageBlock
from jarvis.vision.pointer_types import PointerElement

log = logging.getLogger(__name__)

CropFn = Callable[[int, int], ImageBlock | None]

# Closed reason vocabulary (not a bare str) — BUG-008 drift guard.
PointerReason = Literal["", "no_cursor", "no_element", "timeout", "error"]

# 110 px half-side (220 px square): big enough for a multimodal model to read a
# WORD in a terminal/editor plus a little context, still tight enough to stay a
# "pointer" (verified: 128 px clipped a word, 320 px read cleanly). 2026-06-02.
DEFAULT_CROP_RADIUS = 110
# Hard wall-clock ceiling for the off-hot-path resolve. ElementFromPoint is a
# single OS hit-test (fast), so 120 ms is a generous ceiling that keeps the
# deictic turn well inside the SLO even on a busy box (AP-9).
DEFAULT_TIMEOUT_S = 0.12
# Max chars of the app/window grounding fields injected into the prompt — bounds
# the worst-case leak of a long path/title into the cloud LLM call.
_MAX_LABEL = 120
_MAX_CONTEXT = 80


@dataclass(frozen=True, slots=True)
class PointerContext:
    """Resolved "what is under the cursor" for a single turn.

    ``available`` gates everything: when ``False`` (no cursor, no element,
    timeout) :meth:`render` returns ``""`` and nothing is injected into the turn.
    """

    available: bool
    x: int = 0
    y: int = 0
    element: PointerElement | None = None
    crop: ImageBlock | None = None
    reason: PointerReason = ""

    def render(self) -> str:
        """A compact grounding block for the turn prompt (empty when unavailable).

        Grounding fields are length-bounded before injection into the cloud LLM
        prompt so a pathological path/title under the cursor cannot leak verbatim.
        This is intended exposure (the user deliberately asked about the element),
        consistent with the existing permanent-vision feature, but bounded.
        """
        if not self.available:
            return ""
        el = self.element
        lines = ["[AI Pointer] The user is pointing the mouse cursor at this on-screen element:"]
        if el is not None and el.is_labeled:
            role = el.role or "element"
            label = (el.name or el.value)[:_MAX_LABEL]
            lines.append(f'- a {role} labeled "{label}".')
            if el.value and el.value != el.name:
                lines.append(f'- its value/text: "{el.value[:200]}".')
        elif el is not None:
            role = el.role or "element"
            lines.append(
                f"- a {role} with no accessible text label; a cropped screenshot "
                "of that region is attached - describe what is actually shown there."
            )
        else:
            lines.append(
                "- the accessibility tree returned nothing; a cropped screenshot of "
                "the cursor region is attached - describe what is actually shown there."
            )
        if el is not None and (el.app_name or el.window_title):
            where = (el.app_name or el.window_title)[:_MAX_CONTEXT]
            lines.append(f"- in: {where}.")
        if self.crop is not None:
            lines.append(
                "- A tight cropped screenshot of the exact cursor region IS "
                "ATTACHED to this message — that image is how you SEE here. Answer "
                "\"Was siehst du hier?\" / \"what do you see here\" directly from it. "
                "Do NOT say you lack a tool or cannot see, and do NOT call the "
                "screenshot tool (it would capture the whole screen, not the "
                "cursor). If the user asks what a word/text says (\"lies das\", "
                "\"was steht da\", \"welches Wort ist das\"), read the text at the "
                "CENTRE of the crop — the accessibility label above may only name "
                "the container (a terminal/editor pane), not the word at the cursor."
            )
        lines.append(
            "This element (and the attached cursor crop, if any) is the ONLY thing "
            "the user is pointing at — describe THAT, not any other part of the "
            "screen, and do not guess a different on-screen element. Answer now, "
            "directly, in one short spoken sentence — do not say you will look or "
            "check first. If their question is unrelated to this element, ignore "
            "this block entirely."
        )
        return "\n".join(lines)


def _default_crop(x: int, y: int, radius: int) -> ImageBlock | None:
    """Capture a tight JPEG crop around ``(x, y)`` as an ``ImageBlock``.

    Defensive: returns ``None`` when Pillow/mss are absent (cloud-first base) or
    the grab fails — the caller then falls back to the element text alone.
    """
    try:
        from jarvis.vision.screenshot import capture_region, region_bbox_around

        data = capture_region(region_bbox_around(x, y, radius))
        return ImageBlock(mime="image/jpeg", data_b64=base64.b64encode(data).decode("ascii"))
    except Exception:
        log.debug("AI Pointer crop failed at (%s, %s)", x, y, exc_info=True)
        return None


def resolve_pointer_context(
    *,
    cursor_backend=None,
    resolver=None,
    crop_fn: CropFn | None = None,
    crop_radius: int = DEFAULT_CROP_RADIUS,
) -> PointerContext:
    """Resolve the element under the cursor into a :class:`PointerContext`.

    All collaborators are injectable for tests; the defaults wire the real
    cross-platform backends. Never raises.
    """
    if cursor_backend is None:
        from jarvis.platform.mouse import make_cursor_backend

        cursor_backend = make_cursor_backend()
    pos = cursor_backend.position()
    if pos is None:
        return PointerContext(available=False, reason="no_cursor")
    x, y = int(pos[0]), int(pos[1])

    if resolver is None:
        from jarvis.vision.element_at_point import make_pointer_resolver

        resolver = make_pointer_resolver()
    element = resolver.at(x, y)

    # Always capture the crop on a pointer turn (labeled AND unlabeled): for text
    # under the cursor (a terminal word, a code token) the a11y name is only the
    # pane/container, never the word — so the crop is the real answer source for
    # "lies das" / "was steht da" / "welches Wort ist das".
    crop: ImageBlock | None = None
    fn = crop_fn if crop_fn is not None else (lambda cx, cy: _default_crop(cx, cy, crop_radius))
    try:
        crop = fn(x, y)
    except Exception:
        log.debug("AI Pointer crop_fn raised", exc_info=True)
        crop = None

    if element is None and crop is None:
        return PointerContext(available=False, x=x, y=y, reason="no_element")
    return PointerContext(available=True, x=x, y=y, element=element, crop=crop)


async def resolve_pointer_context_async(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    **kwargs,
) -> PointerContext:
    """Run :func:`resolve_pointer_context` in a worker thread with a hard timeout.

    On timeout returns an unavailable context (``reason="timeout"``) so the turn
    proceeds without pointer context rather than blocking (AP-9 / AD-OE6).
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(lambda: resolve_pointer_context(**kwargs)),
            timeout=timeout_s,
        )
    except TimeoutError:
        log.debug("AI Pointer context resolution timed out after %ss", timeout_s)
        return PointerContext(available=False, reason="timeout")
    except Exception:
        log.debug("AI Pointer context resolution failed", exc_info=True)
        return PointerContext(available=False, reason="error")


__all__ = [
    "PointerContext",
    "resolve_pointer_context",
    "resolve_pointer_context_async",
    "DEFAULT_CROP_RADIUS",
    "DEFAULT_TIMEOUT_S",
]
