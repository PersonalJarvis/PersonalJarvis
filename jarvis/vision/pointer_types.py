"""``PointerElement`` — the semantic on-screen element under the cursor.

Produced by the per-OS point resolver (``jarvis.vision.element_at_point``) from
a native accessibility point query (UIA ``ElementFromPoint`` / AX
``AXUIElementCopyElementAtPosition`` / AT-SPI ``getAccessibleAtPoint``).
``role`` reuses the existing UIA role vocabulary (``UIANode.role``) — no new
wire-format enum is introduced (see docs/plans/ai-pointer/DESIGN.md section 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Source of the resolved element, for honest grounding in the prompt. A closed
# vocabulary (not a bare ``str``) so a new value cannot drift silently across the
# modules that read it (the BUG-008 multi-layer-enum class; the single source of
# truth is this alias). Escalate to the five-layer pattern only if it ever
# crosses a wire format (DB/HTTP/TS).
PointerSource = Literal["ax_tree", "crop_fallback", "none"]


@dataclass(frozen=True, slots=True)
class PointerElement:
    """An accessibility element resolved at a screen point.

    ``bounds`` is ``(x, y, width, height)`` in physical screen pixels, matching
    ``UIANode.bounds`` and the DPI-aware coordinate space used by the screenshot
    source. Populated by the Windows resolver; best-effort ``(0, 0, 0, 0)`` on
    the macOS/Linux resolvers for now (not yet consumed downstream).
    """

    name: str = ""
    role: str = ""
    value: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    app_name: str = ""
    window_title: str = ""
    source: PointerSource = "ax_tree"

    @property
    def is_labeled(self) -> bool:
        """True when the element carries an accessible name or value.

        When ``False`` the element is opaque to the accessibility tree (a raster
        graphic, a custom-drawn canvas) and the context resolver augments it with
        a tight region crop.
        """
        return bool(self.name.strip() or self.value.strip())
