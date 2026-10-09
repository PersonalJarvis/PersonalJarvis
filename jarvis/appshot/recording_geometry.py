"""Logical-pixel placement for the persistent recording frame and controls."""

from __future__ import annotations

Rect = tuple[int, int, int, int]


def selected_rect(screen: Rect, fractions: tuple[float, ...]) -> Rect:
    left, top, width, height = screen
    x, y, w, h = fractions
    x0, y0 = round(x * width), round(y * height)
    x1, y1 = round((x + w) * width), round((y + h) * height)
    return left + x0, top + y0, max(1, x1 - x0), max(1, y1 - y0)


def toolbar_rect(selection: Rect, available: Rect, size: tuple[int, int]) -> Rect:
    """Below the selection, above it if needed, and inside the work area for full screen."""
    x, y, w, h = selection
    left, top, aw, ah = available
    bw, bh = min(size[0], aw), min(size[1], ah)
    bx = max(left, min(round(x + (w - bw) / 2), left + aw - bw))
    by = y + h + 10
    if by + bh > top + ah:
        by = y - bh - 10
    if by < top:
        by = top + ah - bh - 10
    by = max(top, min(by, top + ah - bh))
    return bx, by, bw, bh


def elapsed_label(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
