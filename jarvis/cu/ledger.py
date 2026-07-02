"""Idempotency ledger — the ONE principle behind "never act twice blindly".

The legacy engine grew a zoo of special-case guards (toggle-thrash points,
last-typed-text, per-app launch counters, stall nudges) that interacted in
surprising ways. CU v2 replaces them with a single rule:

    An action that already executed against a visually identical screen is
    refused deterministically.

"Visually identical" is keyed by the frame's content hash — if the screen
changed since the first execution, the same action is legitimate again
(navigating a list clicks different rows on *different* frames; retyping a
search on a *changed* page is fine). Clicking twice on the same unchanged
frame, typing the same URL again into an unchanged address bar, or launching
the same app while the screen never moved is exactly the double-action bug
class and is blocked regardless of what the model asks for.

``wait`` is exempt (waiting twice is harmless and sometimes right).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Two click points within this many screen units count as the same target
#: (model re-aim jitter), mirroring the legacy refine tolerance.
CLICK_SAME_TOLERANCE = 12


def _norm_text(text: str) -> str:
    """Case/whitespace-insensitive text key for type/name comparisons."""
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def action_key(action: dict[str, Any]) -> str | None:
    """Stable identity key for one validated action, or ``None`` when the
    action kind is exempt from deduplication (``wait``/``done``/``fail``)."""
    kind = action.get("action")
    if kind in (None, "wait", "done", "fail"):
        return None
    if kind == "click_element":
        return f"click_element@{_norm_text(str(action.get('name', '')))}"
    if kind == "type":
        return f"type@{_norm_text(str(action.get('text', '')))}"
    if kind == "key":
        keys = "+".join(_norm_text(str(k)) for k in action.get("keys", []))
        return f"key@{keys}"
    if kind == "scroll":
        return f"scroll@{action.get('direction')}"
    if kind in ("open_app", "switch_window"):
        return f"{kind}@{_norm_text(str(action.get('name', '')))}"
    if kind == "drag":
        return (
            f"drag@{int(action.get('x', 0))},{int(action.get('y', 0))}->"
            f"{int(action.get('x2', 0))},{int(action.get('y2', 0))}"
        )
    if kind == "click":
        # Clicks are matched with tolerance in is_duplicate(); the key only
        # carries button identity.
        return f"click@{action.get('button', 'left')}:{bool(action.get('double'))}"
    return f"{kind}@?"


@dataclass
class ActionLedger:
    """Mission-scoped record of executed actions, keyed by frame hash."""

    _entries: list[tuple[str, str]] = field(default_factory=list)
    _clicks: list[tuple[str, int, int, str]] = field(default_factory=list)

    def is_duplicate(
        self,
        action: dict[str, Any],
        frame_sha: str,
        *,
        resolved_xy: tuple[int, int] | None = None,
    ) -> bool:
        """True when this action already ran against a visually identical
        frame — the caller must refuse it and tell the model why."""
        key = action_key(action)
        if key is None:
            return False
        if action.get("action") == "click" and resolved_xy is not None:
            x, y = resolved_xy
            return any(
                k == key and sha == frame_sha
                and abs(px - x) <= CLICK_SAME_TOLERANCE
                and abs(py - y) <= CLICK_SAME_TOLERANCE
                for (k, px, py, sha) in self._clicks
            )
        return (key, frame_sha) in self._entries

    def record(
        self,
        action: dict[str, Any],
        frame_sha: str,
        *,
        resolved_xy: tuple[int, int] | None = None,
    ) -> None:
        """Record one EXECUTED action (call only after real dispatch)."""
        key = action_key(action)
        if key is None:
            return
        if action.get("action") == "click" and resolved_xy is not None:
            self._clicks.append((key, resolved_xy[0], resolved_xy[1], frame_sha))
            return
        self._entries.append((key, frame_sha))
