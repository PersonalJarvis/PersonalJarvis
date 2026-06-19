"""wait_for_ui_state-Tool: pollt bis ein gewuenschter UI-State erreicht ist.

Beispiele:
  - ``wait_for_ui_state(title_contains='Notepad', timeout_s=5)`` wartet bis
    ein Fenster mit "Notepad" im Titel sichtbar ist.
  - ``wait_for_ui_state(text_contains='OK')`` wartet bis irgendwo im UIA-
    Tree ein Element mit Text "OK" erscheint (z.B. ein Dialog-Button).

Polling-Intervall: 250ms. Default-Timeout: 5s. Hartes Maximum: 60s.

Risk-Tier: ``safe`` — reine Beobachtung, kein State-Change.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


_POLL_INTERVAL_S = 0.25
_DEFAULT_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 60.0


class WaitForUIStateTool:
    name: str = "wait_for_ui_state"
    risk_tier: str = "safe"
    description: str = (
        "Wartet bis ein UI-Match auftritt — entweder ein Fenster-Titel-"
        "Substring (title_contains) oder ein Text irgendwo im UIA-Tree "
        "(text_contains). Liefert nach Erfolg den gefundenen Titel zurueck."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title_contains": {
                "type": "string",
                "description": "Substring, der im Fenstertitel auftauchen muss",
            },
            "text_contains": {
                "type": "string",
                "description": "Substring, der irgendwo im UIA-Tree erscheinen muss",
            },
            "timeout_s": {
                "type": "number",
                "default": _DEFAULT_TIMEOUT_S,
                "description": f"Maximale Wartezeit (max {_MAX_TIMEOUT_S}s)",
            },
        },
        "required": [],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        title_needle = (args.get("title_contains") or "").strip().lower()
        text_needle = (args.get("text_contains") or "").strip().lower()
        if not title_needle and not text_needle:
            return ToolResult(
                success=False, output=None,
                error="Mindestens eines von 'title_contains' oder 'text_contains' angeben",
            )

        timeout = min(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), _MAX_TIMEOUT_S)
        timeout = max(timeout, 0.1)

        try:
            from jarvis.vision.tree_factory import make_ui_tree_source
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"UI-tree source unavailable: {exc}",
            )

        source = self._vision_source or make_ui_tree_source()
        deadline = time.monotonic() + timeout
        last_observed_title = ""

        while True:
            try:
                obs = await source.observe()
                last_observed_title = obs.window_title
            except Exception as exc:  # noqa: BLE001
                # UIA kann transient failen (z.B. waehrend Window-Animationen).
                # Wir loggen still und versuchen es im naechsten Tick weiter.
                obs = None
                if time.monotonic() >= deadline:
                    return ToolResult(
                        success=False, output=None,
                        error=f"UIA-Polling failed durchgehend: {exc}",
                    )

            if obs is not None:
                title_lower = (obs.window_title or "").lower()
                title_match = (not title_needle) or (title_needle in title_lower)
                text_match = True
                if text_needle:
                    text_match = any(
                        text_needle in (n.text or "").lower() for n in obs.nodes
                    )
                if title_match and text_match:
                    return ToolResult(
                        success=True,
                        output={
                            "window_title": obs.window_title,
                            "matched_title": bool(title_needle),
                            "matched_text": bool(text_needle),
                            "elapsed_s": round(timeout - (deadline - time.monotonic()), 2),
                        },
                    )

            if time.monotonic() >= deadline:
                return ToolResult(
                    success=False, output=None,
                    error=(
                        f"Timeout nach {timeout}s. "
                        f"Letzter Fenster-Titel: {last_observed_title!r}. "
                        f"Suchte: title~{title_needle!r}, text~{text_needle!r}"
                    ),
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
