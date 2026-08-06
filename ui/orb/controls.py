"""The voice orb's control row — the desktop twin of the in-app bubble's buttons.

``components/agentic/VoiceBubble.tsx`` puts four equal circles under the orb:
attach a file, start/end the conversation, put the voice away, mute the
assistant's voice. On the desktop those capabilities existed only as invisible
gestures (double-double-click, right-click, drop-on-orb), which is the same as
not existing. This module draws that row and routes its clicks, so the orb ON
the desktop offers what the orb IN the window offers.

Two constraints shape the implementation, and neither is cosmetic:

* **Hard circular edges.** The overlay windows key out one exact colour
  (magenta) to become transparent. A pixel that is a *blend* of button and key
  colour survives as a pink fringe, so every disc is composed through a BINARY
  mask — the antialiasing lives strictly inside the disc, where the glyph is.
  Same rule as ``ui.orb.voice_orb``.
* **Supersampled glyphs.** A 4 px stroke drawn directly at 28 px aliases into a
  staircase. Glyphs are drawn at 4x on their own layer and downscaled with
  LANCZOS, exactly as ``jarvis.ui.jarvisbar.renderer`` does for its mic.

The palette is the app's dark theme (``frontend/src/index.css``) resolved
against the opaque background the app composites onto, because Tk has no
per-pixel alpha here: what the web calls ``bg-background/85`` is one flat
colour on the desktop.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

# --- Palette (the app's dark theme, flattened) ------------------------------
#: Resting disc: ``bg-background/85`` over the desktop reads as a near-black
#: pebble; solid here because a layered Tk window has no partial alpha.
BTN_BG = (19, 19, 19)
#: ``border-border/50``, opened up a little. In the app a ``shadow-lg`` does
#: the separating; a colour-keyed window cannot cast one (a soft edge blends
#: with the key and survives as a pink fringe), so the rim has to carry that
#: job alone — measured against a black terminal, where the app's own value
#: disappeared completely.
BTN_BORDER = (52, 52, 52)
#: ``text-muted-foreground``.
BTN_ICON = (143, 143, 143)
#: Hover: ``hover:bg-secondary hover:text-foreground``.
BTN_BG_HOVER = (26, 26, 26)
BTN_ICON_HOVER = (244, 244, 245)
#: Engaged (a live conversation on the mic button): ``bg-primary/15``,
#: ``border-primary/50``, ``text-primary``.
BTN_BG_ON = (47, 41, 10)
BTN_BORDER_ON = (133, 112, 10)
BTN_ICON_ON = (255, 214, 10)
#: Muted speaker: ``bg-destructive/10``, ``border-destructive/40``.
BTN_BG_OFF = (33, 16, 16)
BTN_BORDER_OFF = (102, 33, 33)
BTN_ICON_OFF = (239, 68, 68)
#: Unavailable action (``disabled:opacity-40``) — dimmed, never hidden: a
#: control that vanishes teaches the user nothing about why.
BTN_ICON_DISABLED = (74, 74, 74)

#: One disc, in pixels. Matches the in-app row's ``h-8 w-8``.
BUTTON_SIZE = 28
#: Gap between discs (``gap-2``).
BUTTON_GAP = 8
#: Breathing room around the row, so a disc never touches the window edge.
ROW_PADDING = 4
#: Vertical distance from the orb's bottom edge to the row.
ROW_GAP_FROM_ORB = 6
#: Supersampling factor for every glyph.
_SS = 4

#: The row's actions, left to right — the in-app order (attach, mic, close,
#: speaker). Kept as a tuple so the hit-test, the renderer and the caller's
#: dispatch can never disagree about which disc is which.
ACTIONS: tuple[str, ...] = ("attach", "mic", "close", "speaker")


def row_size(count: int = len(ACTIONS)) -> tuple[int, int]:
    """Pixel size of the window holding ``count`` discs."""
    width = count * BUTTON_SIZE + max(0, count - 1) * BUTTON_GAP + 2 * ROW_PADDING
    return width, BUTTON_SIZE + 2 * ROW_PADDING


def button_centers(count: int = len(ACTIONS)) -> list[float]:
    """Horizontal centre of each disc inside the row window."""
    step = BUTTON_SIZE + BUTTON_GAP
    first = ROW_PADDING + BUTTON_SIZE / 2.0
    return [first + index * step for index in range(count)]


def hit_test(x: float, y: float, count: int = len(ACTIONS)) -> str | None:
    """Which action a click at ``(x, y)`` lands on, or ``None`` between discs.

    The gaps deliberately resolve to nothing. Every one of these buttons does
    something the user would rather not undo by a near-miss — hanging up is the
    obvious one — so a click has to land ON a disc, the same rule the Jarvis
    Bar applies to its close-X (``jarvis.ui.jarvisbar.interaction``).
    """
    radius = BUTTON_SIZE / 2.0
    cy = ROW_PADDING + radius
    for index, cx in enumerate(button_centers(count)):
        if math.hypot(x - cx, y - cy) <= radius:
            return ACTIONS[index] if index < len(ACTIONS) else None
    return None


@dataclass(frozen=True)
class ControlState:
    """Everything the row needs to know to paint itself truthfully."""

    #: A conversation is running (``listen`` / ``think`` / ``speak``).
    active: bool = False
    #: The assistant's voice is muted for this session.
    speaker_muted: bool = False
    #: Which disc the pointer is over, if any.
    hovered: str | None = None
    #: Attaching needs somewhere to attach TO; without it the disc is dimmed.
    can_attach: bool = True


# --- Glyphs -----------------------------------------------------------------
# Each takes the supersampled draw context, the disc centre and radius in
# SUPERSAMPLED coordinates, the stroke colour and width. They draw line art
# only: a filled glyph at this size reads as a blob.


def _draw_paperclip(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    """A paper clip, drawn upright; the caller tilts it.

    Two nested loops: an outer capsule (down the left, round the bottom, up the
    right, closed across the top) and an inner one that stops short — which is
    exactly the wire path of a real clip, and the only version of this shape
    that survives being 17 px wide.
    """
    outer = r * 0.40
    inner = r * 0.17
    top = cy - r * 0.86
    bottom = cy + r * 0.86
    inner_bottom = cy + r * 0.50
    # Outer loop.
    d.line([(cx - outer, top + outer), (cx - outer, bottom - outer)], fill=color, width=w)
    d.arc(
        [cx - outer, bottom - 2 * outer, cx + outer, bottom],
        start=0,
        end=180,
        fill=color,
        width=w,
    )
    d.line([(cx + outer, top + outer), (cx + outer, bottom - outer)], fill=color, width=w)
    d.arc(
        [cx - outer, top, cx + outer, top + 2 * outer],
        start=180,
        end=360,
        fill=color,
        width=w,
    )
    # Inner loop — shorter at the bottom, open at the top, where the wire ends.
    d.line(
        [(cx - inner, top + outer * 1.6), (cx - inner, inner_bottom - inner)],
        fill=color,
        width=w,
    )
    d.arc(
        [cx - inner, inner_bottom - 2 * inner, cx + inner, inner_bottom],
        start=0,
        end=180,
        fill=color,
        width=w,
    )
    d.line(
        [(cx + inner, top + outer * 0.4), (cx + inner, inner_bottom - inner)],
        fill=color,
        width=w,
    )


def _draw_mic(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    color,
    w: int,
    *,
    slashed: bool,
) -> None:
    """Outline microphone: capsule head, cradle bow, stand. Mirrors the bar's."""
    head_w = r * 0.30
    d.rounded_rectangle(
        [cx - head_w, cy - r * 0.72, cx + head_w, cy + r * 0.06],
        radius=head_w,
        outline=color,
        width=w,
    )
    bow = r * 0.55
    d.arc(
        [cx - bow, cy - r * 0.40, cx + bow, cy + r * 0.48],
        start=15,
        end=165,
        fill=color,
        width=w,
    )
    d.line([(cx, cy + r * 0.44), (cx, cy + r * 0.74)], fill=color, width=w)
    foot = r * 0.30
    d.line([(cx - foot, cy + r * 0.74), (cx + foot, cy + r * 0.74)], fill=color, width=w)
    if slashed:
        s = r * 0.72
        d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def _draw_close(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    s = r * 0.56
    d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=color, width=w)
    d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def _draw_speaker(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    color,
    w: int,
    *,
    muted: bool,
) -> None:
    """Speaker cone plus either two sound arcs or the mute cross."""
    left = cx - r * 0.62
    throat = cx - r * 0.18
    body = r * 0.26
    mouth = r * 0.52
    d.polygon(
        [
            (left, cy - body),
            (throat, cy - body),
            (throat + r * 0.34, cy - mouth),
            (throat + r * 0.34, cy + mouth),
            (throat, cy + body),
            (left, cy + body),
        ],
        outline=color,
        width=w,
    )
    if muted:
        s = r * 0.26
        mx = cx + r * 0.50
        d.line([(mx - s, cy - s), (mx + s, cy + s)], fill=color, width=w)
        d.line([(mx - s, cy + s), (mx + s, cy - s)], fill=color, width=w)
        return
    for scale in (0.32, 0.60):
        span = r * scale
        d.arc(
            [cx + r * 0.24 - span, cy - span, cx + r * 0.24 + span, cy + span],
            start=-55,
            end=55,
            fill=color,
            width=w,
        )


_Rgb = tuple[int, int, int]


def _disc_colors(action: str, state: ControlState) -> tuple[_Rgb, _Rgb, _Rgb]:
    """(fill, border, icon) for one disc in the given state."""
    hovered = state.hovered == action
    if action == "mic" and state.active:
        return BTN_BG_ON, BTN_BORDER_ON, BTN_ICON_ON
    if action == "speaker" and state.speaker_muted:
        return BTN_BG_OFF, BTN_BORDER_OFF, BTN_ICON_OFF
    if action == "attach" and not state.can_attach:
        return BTN_BG, BTN_BORDER, BTN_ICON_DISABLED
    if hovered:
        icon = BTN_ICON_OFF if action == "close" else BTN_ICON_HOVER
        return BTN_BG_HOVER, BTN_BORDER, icon
    return BTN_BG, BTN_BORDER, BTN_ICON


def _binary_disc_mask(diameter: int) -> Image.Image:
    """Aliased disc mask. Binary on purpose — see the module docstring."""
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
    return mask


_MASK_CACHE: dict[int, Image.Image] = {}


def _disc_mask(diameter: int) -> Image.Image:
    mask = _MASK_CACHE.get(diameter)
    if mask is None:
        mask = _binary_disc_mask(diameter)
        _MASK_CACHE[diameter] = mask
    return mask


#: How far the clip leans. A paper clip drawn bolt upright reads as a safety
#: pin; the tilt is what makes it recognisable at this size.
_CLIP_TILT_DEG = 35.0


def _render_disc(action: str, state: ControlState) -> Image.Image:
    """One disc as an opaque square; the caller applies the circular mask."""
    fill, border, icon = _disc_colors(action, state)
    size = BUTTON_SIZE * _SS
    layer = Image.new("RGB", (size, size), fill)
    d = ImageDraw.Draw(layer)
    stroke = max(1, round(1.0 * _SS))
    inset = stroke
    d.ellipse(
        [inset, inset, size - 1 - inset, size - 1 - inset],
        outline=border,
        width=stroke,
    )
    centre = size / 2.0
    radius = size * 0.30
    glyph_w = max(1, round(1.35 * _SS))

    # The glyph goes on its own transparent layer so it can be rotated before
    # it meets the disc. Drawing straight onto the disc would make a tilted
    # glyph impossible without tilting the disc with it.
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph)
    colour = (*icon, 255)
    if action == "attach":
        _draw_paperclip(gd, centre, centre, radius, colour, glyph_w)
        glyph = glyph.rotate(
            _CLIP_TILT_DEG, resample=Image.Resampling.BICUBIC, center=(centre, centre)
        )
    elif action == "mic":
        # The in-app bubble shows a live mic while a conversation runs and a
        # slashed one at rest, because the button STARTS the conversation. The
        # desktop row says the same thing with the same glyph.
        _draw_mic(gd, centre, centre, radius, colour, glyph_w, slashed=not state.active)
    elif action == "close":
        _draw_close(gd, centre, centre, radius, colour, glyph_w)
    else:
        _draw_speaker(gd, centre, centre, radius, colour, glyph_w, muted=state.speaker_muted)
    layer.paste(glyph, (0, 0), glyph)
    return layer.resize((BUTTON_SIZE, BUTTON_SIZE), Image.Resampling.LANCZOS)


def render_row(
    state: ControlState,
    *,
    color_key: tuple[int, int, int] = (255, 0, 255),
    actions: Sequence[str] = ACTIONS,
) -> Image.Image:
    """The whole row as a colour-keyed RGB frame, ready for a layered window."""
    width, height = row_size(len(actions))
    frame = Image.new("RGB", (width, height), color_key)
    mask = _disc_mask(BUTTON_SIZE)
    top = ROW_PADDING
    for action, cx in zip(actions, button_centers(len(actions)), strict=False):
        disc = _render_disc(action, state)
        frame.paste(disc, (int(round(cx - BUTTON_SIZE / 2.0)), top), mask)
    return frame


def toggle_speaker_mute() -> bool | None:
    """Mute / unmute the assistant's voice for this session. Returns the new state.

    Session-only by design, exactly like the in-app speaker button: a mute the
    user forgot about must not survive a restart and leave Jarvis mysteriously
    silent tomorrow. Nothing is written to ``jarvis.toml``.

    Returns ``None`` when there is no live pipeline to talk to (a headless or
    still-booting host), so the caller can leave the icon alone instead of
    lying about a mute that never happened.
    """
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline
    except Exception:  # noqa: BLE001 — importable everywhere in practice
        return None
    pipeline = get_speech_pipeline()
    if pipeline is None:
        return None
    setter = getattr(pipeline, "set_tts_volume", None)
    if not callable(setter):
        return None
    current = _current_tts_volume(pipeline)
    if current > 0.0:
        _remember_volume(pipeline, current)
        setter(0.0)
        return True
    setter(_remembered_volume(pipeline))
    return False


#: Where the pre-mute volume is parked, keyed per pipeline instance so a
#: restarted pipeline cannot inherit a stale value.
_PRE_MUTE_VOLUME: dict[int, float] = {}


def _current_tts_volume(pipeline: object) -> float:
    getter = getattr(pipeline, "get_tts_volume", None)
    if callable(getter):
        try:
            return max(0.0, min(1.0, float(getter())))
        except Exception:  # noqa: BLE001 — a refusing getter is not a failure
            logging.getLogger("jarvis.orb").debug(
                "pipeline.get_tts_volume raised; probing the attribute instead",
                exc_info=True,
            )
    for attribute in ("_tts_volume", "tts_volume"):
        value = getattr(pipeline, attribute, None)
        if isinstance(value, int | float):
            return max(0.0, min(1.0, float(value)))
    # Unknown volume — treat as audible, same assumption the in-app button
    # makes when its GET fails. The first click then simply mutes.
    return 1.0


def _remember_volume(pipeline: object, volume: float) -> None:
    _PRE_MUTE_VOLUME[id(pipeline)] = volume


def _remembered_volume(pipeline: object) -> float:
    return _PRE_MUTE_VOLUME.get(id(pipeline), 1.0)


def speaker_is_muted() -> bool:
    """Best-effort read of "the assistant's voice is currently silenced"."""
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline
    except Exception:  # noqa: BLE001
        return False
    pipeline = get_speech_pipeline()
    if pipeline is None:
        return False
    return _current_tts_volume(pipeline) <= 0.0


def pick_and_dispatch_files(parent: object, on_error: Callable[[str], None] | None = None) -> int:
    """Open a file chooser and hand the picks to the conversation.

    Same destination as dropping a file onto the orb — ``drop_bridge`` — so the
    macOS companion process needs no new IPC of its own: its drop forwarding
    already carries this to the parent where the brain lives.

    Returns the number of files handed over (0 when the user cancelled).
    """
    # Same guard as ``OrbOverlay.start``: a modal file chooser opened by a test
    # run blocks the whole suite until somebody notices and closes it by hand.
    # Dead code in the live app — pytest is never imported there.
    if "pytest" in sys.modules and not os.environ.get("JARVIS_GUI_TESTS"):
        return 0
    try:
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 — no Tk, no dialog
        if on_error is not None:
            on_error(f"file dialog unavailable: {exc}")
        return 0
    try:
        picked = filedialog.askopenfilenames(parent=parent, title="Attach to the conversation")
    except Exception as exc:  # noqa: BLE001 — a cancelled/broken dialog is not fatal
        if on_error is not None:
            on_error(f"file dialog failed: {exc}")
        return 0
    paths = [str(path) for path in (picked or ()) if str(path).strip()]
    if not paths:
        return 0
    try:
        from jarvis.overlay.drop_bridge import dispatch_drop

        dispatch_drop(paths, "")
    except Exception as exc:  # noqa: BLE001
        if on_error is not None:
            on_error(f"attach dispatch failed: {exc}")
        return 0
    return len(paths)
