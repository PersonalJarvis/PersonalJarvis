"""UI-tree ``VisionSource`` factory (Wave 2.4, AD-6 + AD-7 + AD-10).

``make_ui_tree_source()`` is the single seam that selects the per-OS UI-element
accessibility-tree source, replacing the six hardcoded ``UIATreeSource()``
literals across the click/read/wait tools, the vision engine, and the
screenshot-only loop. It mirrors the Wave-1 ``terminal.backend.make_pty_backend``
factory shape:

* ``win32``  -> ``UIATreeSource()`` — **unchanged** Windows path (AD-7: the
  battle-tested UIA source is grandfathered, never rewritten).
* ``darwin`` -> ``AXTreeSource()`` — macOS Accessibility tree (Wave 2.1).
* ``linux``  -> ``AtspiTreeSource()`` when ``capabilities.has_ax_tree`` is true,
  else ``NullUITreeSource`` — the AD-6 graceful fallback whose ``observe``
  returns an empty ``Observation`` (``source="screenshot_only"``, no nodes) and
  logs once. Every consumer already treats "no nodes" as "no labels -> pixel
  path", so the null source self-gates back to the pixel-click loop.

Never raises (AD-6): an unknown platform or a missing capability degrades to the
null source, never a crash. The ``AXTreeSource`` / ``AtspiTreeSource`` imports
are module-scope-safe because both lazy-import their native libs inside
``observe`` (HN-7) — importing this factory pulls in no pyobjc / pyatspi.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Literal
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation
from jarvis.platform import detect_platform
from jarvis.platform.capabilities import detect_capabilities

logger = logging.getLogger(__name__)

# Logged once when the null source is constructed on a host with no UI-tree
# capability, so the degrade is visible (AD-13: never silently empty).
_NULL_SOURCE_MSG = (
    "No UI-element accessibility tree available on this host — named UI clicks "
    "fall back to pixel clicks. On Linux install python3-pyatspi + "
    "gir1.2-atspi-2.0 and run an AT-SPI session to enable them."
)

_warned_null = False


class NullUITreeSource:
    """AD-6 graceful fallback: a ``VisionSource`` that yields no nodes.

    ``observe`` returns an empty ``Observation`` with ``source="screenshot_only"``
    so consumers route to the pixel-click path. ``name``/``kind`` satisfy the
    Protocol. Logs the degrade exactly once across the process.
    """

    name: str = "null-ui-tree"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(self) -> None:
        global _warned_null
        if not _warned_null:
            logger.info(_NULL_SOURCE_MSG)
            _warned_null = True

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")
        empty_hash = hashlib.sha256(b"").hexdigest()
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=empty_hash,
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={"nodes_before": 0, "nodes_after": 0, "depth_used": 0},
        )

    async def close(self) -> None:
        return None


def make_ui_tree_source():
    """Select the UI-element accessibility-tree source for this host (AD-6).

    Returns a ``VisionSource``-conformant object. Never raises; an absent
    capability degrades to ``NullUITreeSource``.
    """
    plat = detect_platform()
    if plat == "win32":
        # AD-7: the Windows UIA source is untouched.
        from jarvis.vision.uia_tree import UIATreeSource  # noqa: PLC0415

        return UIATreeSource()
    if plat == "darwin":
        from jarvis.vision.ax_tree import AXTreeSource  # noqa: PLC0415

        return AXTreeSource()
    # Linux (and any POSIX fallback): AT-SPI when the capability is present.
    if detect_capabilities().has_ax_tree:
        from jarvis.vision.atspi_tree import AtspiTreeSource  # noqa: PLC0415

        return AtspiTreeSource()
    return NullUITreeSource()


__all__ = ["make_ui_tree_source", "NullUITreeSource"]
