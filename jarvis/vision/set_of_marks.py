"""Set-of-Marks (SoM) rendering for the computer-use planner.

The single biggest reliability win for a vision LLM driving a GUI is to stop
asking it for raw pixel coordinates. Vision transformers are bad at coordinate
regression (research: GPT-4o ~0.8% grounding accuracy raw vs ~39.6% with marks).
Instead we overlay a numbered box on every interactable UI element and have the
model pick an element *number*. The loop then maps that number deterministically
to the element's center, so the click never depends on the model's pixel math.

We already have the elements and their bounding rectangles from the Windows UIA
accessibility tree (``Observation.nodes``), so — unlike OmniParser — we need no
ML detector. We draw the marks ourselves from the UIA bounds.

Coordinate spaces
-----------------
``UIANode.bounds`` are absolute *screen* coordinates (the same space
``SetCursorPos``/click uses), captured by a per-monitor-DPI-aware process so
they are physical pixels. The screenshot is an ``mss`` capture of the foreground
monitor (also physical pixels). To draw a mark we map screen → image:

    image_x = (screen_x - viewport_origin_x) * scale

where ``viewport_origin`` is the captured monitor's top-left and ``scale`` is
``image_width / monitor_width`` (≈ 1.0 for a DPI-aware single-monitor capture,
but computed defensively to survive virtual-desktop captures and odd DPI modes).
Clicking uses ``center_screen`` directly, so even if the *drawing* is slightly
off, the *click* is always exact.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.protocols import UIANode

log = logging.getLogger(__name__)


# Roles worth marking as click targets. Mirrors the UIA pruning whitelist; a
# node with a non-empty automation_id is kept regardless of role (custom
# controls often carry an id but a generic/empty role).
_DEFAULT_INTERACTABLE_ROLES: frozenset[str] = frozenset({
    "Button", "Edit", "ComboBox", "List", "ListItem", "Tab", "TabItem",
    "MenuItem", "CheckBox", "RadioButton", "Hyperlink", "Link", "Text",
    "TreeItem", "Slider", "SplitButton", "Image", "Document", "DataItem",
})

_MAX_MARKS_DEFAULT = 60
#: Long-side cap (px) for the annotated image sent to the vision planner.
#: Smaller image = fewer tiles = lower per-step latency; badges stay legible.
_MAX_IMAGE_LONG_SIDE = 1536


@dataclass(frozen=True, slots=True)
class Mark:
    """One numbered, clickable element."""

    index: int
    role: str
    name: str
    automation_id: str
    bounds_screen: tuple[int, int, int, int]  # x, y, w, h (absolute screen px)
    center_screen: tuple[int, int]            # click target (absolute screen px)


@dataclass(frozen=True, slots=True)
class SetOfMarksResult:
    annotated_path: str | None       # PNG with numbered boxes, or None if undrawable
    marks: tuple[Mark, ...]
    legend_text: str                 # compact textual legend, index ↔ element

    def mark_by_index(self, index: int) -> Mark | None:
        for m in self.marks:
            if m.index == index:
                return m
        return None


def _is_interactable(node: UIANode, roles: frozenset[str]) -> bool:
    x, y, w, h = node.bounds
    if w <= 0 or h <= 0:
        return False
    if not getattr(node, "enabled", True):
        return False
    if node.automation_id:
        return True
    return node.role in roles


def _detect_viewport(img_w: int, img_h: int) -> tuple[tuple[int, int], float]:
    """Best-effort screen→image mapping for the captured screenshot.

    Returns ``((origin_x, origin_y), scale)``. Picks whichever of the
    foreground monitor or the whole virtual desktop best matches the image
    width (a screenshot can be a single-monitor or an all-monitors capture).
    Falls back to ``((0, 0), 1.0)`` on any failure or off Windows.
    """
    if os.name != "nt":
        return (0, 0), 1.0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = (
                ("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG),
            )

        class MONITORINFO(ctypes.Structure):
            _fields_ = (
                ("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
            )

        # Foreground monitor rect (physical px in a DPI-aware process).
        MONITOR_DEFAULTTONEAREST = 2
        hwnd = user32.GetForegroundWindow()
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        fg_origin: tuple[int, int] | None = None
        fg_w = 0
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            fg_origin = (int(r.left), int(r.top))
            fg_w = int(r.right - r.left)

        # Virtual desktop rect.
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        vw = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))

        # Choose the reference whose width is closest to the image width.
        candidates: list[tuple[int, tuple[int, int], int]] = []
        if fg_origin is not None and fg_w > 0:
            candidates.append((abs(img_w - fg_w), fg_origin, fg_w))
        if vw > 0:
            candidates.append((abs(img_w - vw), (vx, vy), vw))
        if not candidates:
            return (0, 0), 1.0
        _, origin, ref_w = min(candidates, key=lambda c: c[0])
        scale = img_w / ref_w if ref_w else 1.0
        if not (0.25 <= scale <= 4.0):
            scale = 1.0
        return origin, scale
    except Exception as exc:  # noqa: BLE001
        log.debug("SoM viewport detection failed, using identity map: %s", exc)
        return (0, 0), 1.0


def _legend_line(mark: Mark) -> str:
    name = (mark.name or "").strip()
    if len(name) > 50:
        name = name[:47] + "..."
    parts = [f"[{mark.index}] {mark.role}"]
    if name:
        parts.append(f"'{name}'")
    if mark.automation_id:
        parts.append(f"(id={mark.automation_id})")
    if not getattr(mark, "_enabled", True):  # pragma: no cover - enabled prefilter
        parts.append("[disabled]")
    return " ".join(parts)


def render_set_of_marks(
    screenshot_path: str | None,
    nodes: Sequence[UIANode],
    *,
    viewport_origin: tuple[int, int] | None = None,
    scale: float | None = None,
    max_marks: int = _MAX_MARKS_DEFAULT,
    interactable_roles: frozenset[str] | None = None,
    output_path: str | None = None,
) -> SetOfMarksResult:
    """Annotate ``screenshot_path`` with numbered boxes over interactable nodes.

    Always returns a usable ``SetOfMarksResult`` with ``marks`` + ``legend_text``
    derived from screen-space bounds, even when no screenshot or PIL is
    available (``annotated_path`` is then ``None`` and the planner falls back to
    the textual legend). ``center_screen`` on each mark is the exact click point.
    """
    roles = interactable_roles or _DEFAULT_INTERACTABLE_ROLES

    # 1) Select + number interactable elements (preserve UIA tree order).
    selected: list[UIANode] = []
    for node in nodes:
        if _is_interactable(node, roles):
            selected.append(node)
        if len(selected) >= max_marks:
            break

    marks: list[Mark] = []
    for i, node in enumerate(selected, start=1):
        x, y, w, h = node.bounds
        marks.append(Mark(
            index=i,
            role=node.role,
            name=node.name,
            automation_id=node.automation_id,
            bounds_screen=(x, y, w, h),
            center_screen=(x + w // 2, y + h // 2),
        ))

    legend_text = "\n".join(_legend_line(m) for m in marks) if marks else "(no interactable UIA elements detected)"

    annotated_path = _draw_marks(
        screenshot_path, marks,
        viewport_origin=viewport_origin, scale=scale, output_path=output_path,
    )

    return SetOfMarksResult(
        annotated_path=annotated_path,
        marks=tuple(marks),
        legend_text=legend_text,
    )


def _draw_marks(
    screenshot_path: str | None,
    marks: Sequence[Mark],
    *,
    viewport_origin: tuple[int, int] | None,
    scale: float | None,
    output_path: str | None,
) -> str | None:
    if not screenshot_path or not marks:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log.debug("Pillow unavailable — SoM image not drawn; legend-only mode")
        return None
    try:
        img = Image.open(screenshot_path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        log.warning("SoM could not open screenshot %s: %s", screenshot_path, exc)
        return None

    img_w, img_h = img.size
    if viewport_origin is None or scale is None:
        det_origin, det_scale = _detect_viewport(img_w, img_h)
        origin = viewport_origin if viewport_origin is not None else det_origin
        sc = scale if scale is not None else det_scale
    else:
        origin, sc = viewport_origin, scale

    draw = ImageDraw.Draw(img)
    font = _load_font(max(12, int(img_h / 60)))
    box_color = (255, 40, 40)
    text_color = (255, 255, 255)

    for m in marks:
        x, y, w, h = m.bounds_screen
        ix = int((x - origin[0]) * sc)
        iy = int((y - origin[1]) * sc)
        iw = int(w * sc)
        ih = int(h * sc)
        # Skip marks whose box does not overlap the captured image.
        if ix + iw < 0 or iy + ih < 0 or ix > img_w or iy > img_h:
            continue
        draw.rectangle([ix, iy, ix + iw, iy + ih], outline=box_color, width=2)
        label = str(m.index)
        tw, th = _text_size(draw, label, font)
        # Badge anchored at the box top-left, nudged inside the image.
        bx = min(max(ix, 0), img_w - tw - 6)
        by = min(max(iy, 0), img_h - th - 4)
        draw.rectangle([bx, by, bx + tw + 6, by + th + 4], fill=box_color)
        draw.text((bx + 3, by + 2), label, fill=text_color, font=font)

    # Downscale before saving to cut vision-model latency: a full 2.5K screenshot
    # is many image tiles. ~1536px on the long side keeps the numbered badges
    # legible while roughly halving the token/latency cost. Click accuracy is
    # unaffected — clicks use center_screen (screen coords), not the image.
    long_side = max(img_w, img_h)
    if long_side > _MAX_IMAGE_LONG_SIDE:
        ratio = _MAX_IMAGE_LONG_SIDE / long_side
        try:
            img = img.resize((max(1, int(img_w * ratio)), max(1, int(img_h * ratio))))
        except Exception:  # noqa: BLE001 — resize failure must not lose the image
            pass

    out = output_path or _derive_output_path(screenshot_path)
    try:
        img.save(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("SoM could not save annotated image to %s: %s", out, exc)
        return None
    return out


def _derive_output_path(screenshot_path: str) -> str:
    base, _ext = os.path.splitext(screenshot_path)
    return base + ".som.png"


def _load_font(size: int):
    from PIL import ImageFont
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw, text: str, font) -> tuple[int, int]:
    # Pillow >=8.0 textbbox; fall back to font.getsize on ancient versions.
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    except Exception:  # noqa: BLE001
        try:
            return font.getsize(text)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return 10 * len(text), 14
