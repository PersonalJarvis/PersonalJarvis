"""Click/drag classification, placement, and position persistence for the bar.

The pure helpers (``is_drag``, ``classify_release``, ``default_bottom_center``,
``clamp_to_screen``) mirror the orb's proven movement-threshold model
(overlay.py:1604): a press that never moves past the threshold is a CLICK
(→ start a voice session); a press that moves past it is a DRAG (→ reposition
+ persist). No duration gate is needed — moving the pointer arms a drag.

Persistence uses a dedicated top-level ``[jarvisbar]`` TOML section (absolute
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

    Returns one of ``"hangup"`` / ``"mute"`` / ``"talk"`` / ``"none"``.

    The RIGHT zone is the microphone mute toggle (mic muted FOR JARVIS only —
    non-destructive, so it keeps a generous zone). When IDLE, a click anywhere
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
    if frac >= 0.60:            # right zone → the mic mute toggle (non-destructive)
        return "mute"
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
# Multi-monitor placement: relative (free-space) position within a work area  #
# --------------------------------------------------------------------------- #
# A monitor "work area" is ``(left, top, width, height)`` in the platform's
# input units (physical pixels on a per-monitor-DPI-aware Windows thread, Tk
# points on macOS). The bar's position is reasoned about as a RELATIVE spot
# inside that rectangle so it reproduces on a differently-sized monitor: the
# free space (work size minus the bar size) is the basis, so 0.5 is always
# "centred", 1.0 is "flush to the right/bottom edge", regardless of the
# monitor's resolution. Storing a raw pixel offset instead would fall off a
# smaller screen and drift on a larger one — the exact multi-monitor bug this
# model avoids.

WorkArea = tuple[int, int, int, int]


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def relative_within(
    x: int, y: int, *, work: WorkArea, bar_w: int, bar_h: int
) -> tuple[float, float]:
    """Bar top-left ``(x, y)`` as free-space fractions inside a work area.

    Returns ``(rel_x, rel_y)`` each clamped to ``[0, 1]``. Degenerate free
    space (a bar as large as, or larger than, the work area on an axis) yields
    ``0.0`` on that axis so the value stays finite and in range.
    """
    wl, wt, ww, wh = work
    free_w = ww - bar_w
    free_h = wh - bar_h
    rel_x = 0.0 if free_w <= 0 else (x - wl) / free_w
    rel_y = 0.0 if free_h <= 0 else (y - wt) / free_h
    return (_clamp01(rel_x), _clamp01(rel_y))


def project_relative(
    rel_x: float, rel_y: float, *, work: WorkArea, bar_w: int, bar_h: int
) -> tuple[int, int]:
    """Inverse of :func:`relative_within`: fractions → absolute ``(x, y)``.

    Places the bar on ``work`` so its relative spot matches, keeping it fully
    inside (the projection over the clamped free space is inherently in-bounds).
    This is what migrates the bar between monitors of different sizes without
    it drifting off-screen or losing its centred/edge placement.
    """
    wl, wt, ww, wh = work
    free_w = max(0, ww - bar_w)
    free_h = max(0, wh - bar_h)
    x = wl + round(_clamp01(rel_x) * free_w)
    y = wt + round(_clamp01(rel_y) * free_h)
    return (int(x), int(y))


def clamp_to_work_area(
    x: int, y: int, *, work: WorkArea, bar_w: int, bar_h: int, margin: int
) -> tuple[int, int]:
    """Keep the bar fully inside a work area that may have a non-zero origin.

    The generalisation of :func:`clamp_to_screen` to a specific monitor's work
    rectangle (a secondary monitor has a non-zero ``left``/``top``). Used when a
    drag is released so the drop is pinned to the monitor it landed on rather
    than snapped back to the primary monitor (the historical multi-monitor
    drag bug).
    """
    wl, wt, ww, wh = work
    min_x, min_y = wl + margin, wt + margin
    max_x = max(min_x, wl + ww - bar_w - margin)
    max_y = max(min_y, wt + wh - bar_h - margin)
    cx = min(max(int(x), min_x), max_x)
    cy = min(max(int(y), min_y), max_y)
    return cx, cy


# --------------------------------------------------------------------------- #
# Position persistence ([jarvisbar] section, absolute x/y)                   #
# --------------------------------------------------------------------------- #
def load_jarvisbar_position(path: str | Path) -> tuple[int, int] | None:
    """Read [jarvisbar] pos_x/pos_y. Returns None if absent/invalid."""
    section = _load_jarvisbar_section(path)
    if section is None:
        return None
    x, y = section.get("pos_x"), section.get("pos_y")
    if isinstance(x, int) and isinstance(y, int):
        return x, y
    return None


def load_jarvisbar_relative(path: str | Path) -> tuple[float, float] | None:
    """Read [jarvisbar] rel_x/rel_y (the free-space fractions).

    This is the monitor-independent placement (see :func:`relative_within`): it
    survives a monitor being resized, unplugged, or the bar migrating to a
    differently-sized screen, whereas the absolute pos_x/pos_y is only correct
    on the monitor it was captured on. Returns ``None`` when absent/invalid so
    callers fall back to the absolute position (older configs have no rel keys).
    """
    section = _load_jarvisbar_section(path)
    if section is None:
        return None
    rx, ry = section.get("rel_x"), section.get("rel_y")
    if isinstance(rx, (int, float)) and isinstance(ry, (int, float)):
        return (_clamp01(float(rx)), _clamp01(float(ry)))
    return None


def _load_jarvisbar_section(path: str | Path) -> dict | None:
    """Return the parsed ``[jarvisbar]`` table, or ``None`` if absent/invalid."""
    import tomllib

    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    section = data.get("jarvisbar")
    return section if isinstance(section, dict) else None


def save_jarvisbar_position(
    path: str | Path,
    x: int,
    y: int,
    *,
    rel: tuple[float, float] | None = None,
) -> None:
    """Atomically persist [jarvisbar] pos_x/pos_y, comment- and BOM-safe.

    When ``rel`` (the monitor-independent free-space fractions) is supplied it
    is written alongside as ``rel_x``/``rel_y`` — the primary placement truth
    for multi-monitor migration; the absolute pos stays for back-compat and the
    no-monitor-info fallback. Reuses ``config_writer._WRITE_LOCK`` so the write
    serialises with every other jarvis.toml mutation (AP-7). No-op if the config
    file is missing.
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
        section = doc.get("jarvisbar")
        if section is None:
            section = tomlkit.table()
            doc["jarvisbar"] = section
        section["pos_x"] = int(x)
        section["pos_y"] = int(y)
        if rel is not None:
            section["rel_x"] = round(_clamp01(rel[0]), 4)
            section["rel_y"] = round(_clamp01(rel[1]), 4)
        out = tomlkit.dumps(doc)
        if had_bom:
            out = bom + out
        # Path-based temp + os.replace: the context manager guarantees the
        # file handle is closed, so no descriptor can leak (unlike mkstemp).
        tmp = p.with_suffix(p.suffix + ".jarvisbar.tmp")
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
