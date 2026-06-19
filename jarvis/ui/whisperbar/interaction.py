"""Click/drag classification, placement, and position persistence for the bar.

The pure helpers (``is_drag``, ``classify_release``, ``default_bottom_center``,
``clamp_to_screen``) mirror the orb's proven movement-threshold model
(overlay.py:1604): a press that never moves past the threshold is a CLICK
(→ start a voice session); a press that moves past it is a DRAG (→ reposition
+ persist). No duration gate is needed — moving the pointer arms a drag.

Persistence uses a dedicated top-level ``[whisperbar]`` TOML section (absolute
x/y) so it never clobbers the orb's ``[overlay.mascot]`` pin, and serialises
through ``config_writer._WRITE_LOCK`` so it cannot race other config writes
(AP-7). The orb's own writer predates that lock; ours is stricter.
"""
from __future__ import annotations

from pathlib import Path


# --------------------------------------------------------------------------- #
# Pure geometry helpers (no I/O)                                              #
# --------------------------------------------------------------------------- #
def is_drag(dx: int, dy: int, threshold: int) -> bool:
    """Manhattan-distance drag test (matches DRAG_THRESHOLD_PX = 16)."""
    return (abs(dx) + abs(dy)) >= threshold


def classify_release(*, moved: bool) -> str:
    return "drag" if moved else "click"


# NOTE: there is intentionally no coarse `click_action(mode)` helper. A bar
# click is resolved ONLY through `resolve_click`, which gates the destructive
# hang-up on the close-X hit-box. A "any active click = hangup" shortcut was
# exactly the silent-hangup bug (2026-06-19) and must not be re-introduced.

# Minimum tap radius (px) around the close-X glyph, so the hit-box stays
# fingertip-tappable even when the pill is tiny. The effective radius also
# scales with the pill width (``_CLOSE_X_HIT_FRAC``) so it tracks the glyph the
# renderer actually draws (``renderer._draw_close_x`` at ``cx - 0.42*pw``).
_CLOSE_X_HIT_PX: float = 10.0
_CLOSE_X_HIT_FRAC: float = 0.14
_CLOSE_X_CENTRE_FRAC: float = 0.42  # X centre offset from pill centre (mirror of renderer)


def resolve_click(
    x: float,
    width: int,
    mode: str,
    *,
    hovered: bool = False,
    pill_w: float | None = None,
) -> str:
    """Resolve a click on the bar into an action by its horizontal zone + state.

    Returns one of ``"hangup"`` / ``"dictate"`` / ``"talk"`` / ``"none"``.

    The RIGHT zone is the square (toggle endpoint-free dictation — non-
    destructive, so it keeps a generous zone). When IDLE, a click anywhere
    starts a normal session.

    The hang-up X is different: it ENDS the session, so its hit-box is
    deliberately narrow and must match what the user can see. The renderer draws
    the close-X ONLY while the bar is ``hovered`` (and as a small glyph at
    ``cx - 0.42*pw``), so a hang-up fires ONLY when (a) the controls are shown
    (``hovered``) AND (b) the click lands on the X glyph itself. A low-intent
    click on the active bar's body — where no X is visible — returns ``"none"``
    instead of silently hanging up. This closes the "Jarvis hangs up by itself"
    trap (live bug 2026-06-19): the old code hung up on ANY click in the left
    40% of the bar, decoupled from the visible affordance.
    """
    frac = x / max(1, width)
    active = mode in ("listen", "think", "speak")
    if frac >= 0.60:            # right zone → the square (non-destructive)
        return "dictate"
    if not active:             # idle middle/left → start a normal session
        return "talk"
    # Active session: the ONLY destructive bar action is the close-X hang-up,
    # which must be a deliberate click ON the visible X glyph.
    if hovered:
        # In production `pill_w` is always the active pill width (ACTIVE_W); the
        # caller only passes None for idle mode, which returns above before this
        # branch. The `width` fallback is just a sane default for direct callers.
        pw = float(pill_w) if pill_w is not None else float(width)
        x_glyph = width / 2.0 - _CLOSE_X_CENTRE_FRAC * pw  # mirror renderer._draw_close_x
        hit = max(_CLOSE_X_HIT_PX, _CLOSE_X_HIT_FRAC * pw)
        if abs(x - x_glyph) <= hit:
            return "hangup"
    return "none"              # active body / no visible X → nothing


def default_bottom_center(
    *, screen_w: int, screen_h: int, bar_w: int, bar_h: int, margin: int
) -> tuple[int, int]:
    """Default anchor: horizontally centered, just above the taskbar."""
    x = (screen_w - bar_w) // 2
    y = screen_h - bar_h - margin
    return x, y


def clamp_to_screen(
    x: int, y: int, *, screen_w: int, screen_h: int, bar_w: int, bar_h: int, margin: int
) -> tuple[int, int]:
    """Keep the bar fully on screen (used when loading a persisted position)."""
    max_x = max(margin, screen_w - bar_w - margin)
    max_y = max(margin, screen_h - bar_h - margin)
    cx = min(max(x, margin), max_x)
    cy = min(max(y, margin), max_y)
    return cx, cy


# --------------------------------------------------------------------------- #
# Position persistence ([whisperbar] section, absolute x/y)                   #
# --------------------------------------------------------------------------- #
def load_whisperbar_position(path: str | Path) -> tuple[int, int] | None:
    """Read [whisperbar] pos_x/pos_y. Returns None if absent/invalid."""
    import tomllib

    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    section = data.get("whisperbar")
    if not isinstance(section, dict):
        return None
    x, y = section.get("pos_x"), section.get("pos_y")
    if isinstance(x, int) and isinstance(y, int):
        return x, y
    return None


def save_whisperbar_position(path: str | Path, x: int, y: int) -> None:
    """Atomically persist [whisperbar] pos_x/pos_y, comment- and BOM-safe.

    Reuses ``config_writer._WRITE_LOCK`` so the write serialises with every
    other jarvis.toml mutation (AP-7). No-op if the config file is missing.
    """
    import os

    import tomlkit

    # Reuse the canonical config-write mutex so this serialises with every
    # other jarvis.toml writer (AP-7). The UTF-8 BOM is a local constant — no
    # need to import config_writer's private name for a one-character string.
    from jarvis.core.config_writer import _WRITE_LOCK

    bom = "﻿"  # UTF-8 BOM as text
    p = Path(path)
    if not p.exists():
        return
    with _WRITE_LOCK:
        raw_bytes = p.read_bytes()
        had_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
        doc = tomlkit.parse(raw_bytes.decode("utf-8-sig"))
        section = doc.get("whisperbar")
        if section is None:
            section = tomlkit.table()
            doc["whisperbar"] = section
        section["pos_x"] = int(x)
        section["pos_y"] = int(y)
        out = tomlkit.dumps(doc)
        if had_bom:
            out = bom + out
        # Path-based temp + os.replace: the context manager guarantees the
        # file handle is closed, so no descriptor can leak (unlike mkstemp).
        tmp = p.with_suffix(p.suffix + ".whisperbar.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(out)
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
