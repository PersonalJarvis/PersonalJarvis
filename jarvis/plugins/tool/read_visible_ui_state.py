"""read_visible_ui_state-Tool: liefert den aktuellen UI-State als Feedback.

Liefert dem Agent strukturierte Information darueber, was gerade auf dem
Bildschirm sichtbar ist — Fenster-Titel, sichtbare Texte, Anzahl Nodes.
Optional zusaetzlich einen Screenshot-Artifact fuer Vision-faehige Brains.

Risk-Tier: ``safe`` — read-only, kein State-Change.

Architektur: Das Tool instanziiert eine ``UIATreeSource`` lazy beim ersten
Call. Alternativ kann der Caller via Factory eine bestehende Source
injizieren (siehe ``brain.factory``-Verdrahtung mit VisionEngine).
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class ReadVisibleUIStateTool:
    name: str = "read_visible_ui_state"
    risk_tier: str = "safe"
    description: str = (
        "Liest den aktuellen UI-Zustand: Fenster-Titel, sichtbarer Text und "
        "UI-Element-Count. Optional zusaetzlich einen Screenshot."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_screenshot": {
                "type": "boolean",
                "default": False,
                "description": "Falls True, zusaetzlich Screenshot als image-artifact",
            },
            "max_text_chars": {
                "type": "integer",
                "default": 2000,
                "description": "Limit fuer aggregierten Text — verhindert Token-Explosion",
            },
        },
        "required": [],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        # Optional injizierbar fuer Tests + zentrale Engine-Wiederverwendung.
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        include_screenshot = bool(args.get("include_screenshot", False))
        max_text_chars = int(args.get("max_text_chars", 2000))

        try:
            from jarvis.vision.tree_factory import make_ui_tree_source
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"UI-tree source unavailable: {exc}",
            )

        source = self._vision_source or make_ui_tree_source()
        try:
            obs = await source.observe()
        except Exception as exc:  # noqa: BLE001 — UIA kann diverse Fehler werfen
            return ToolResult(
                success=False, output=None,
                error=f"UI-Observation fehlgeschlagen: {exc}",
            )

        # Texte aus den Nodes aggregieren
        texts: list[str] = []
        char_count = 0
        for node in obs.nodes:
            t = (node.text or "").strip()
            if not t:
                continue
            if char_count + len(t) > max_text_chars:
                texts.append("…")
                break
            texts.append(t)
            char_count += len(t)

        state = {
            "window_title": obs.window_title,
            "active_pid": obs.active_pid,
            "node_count": len(obs.nodes),
            "visible_texts": texts,
        }

        artifacts: tuple[dict[str, Any], ...] = ()
        if include_screenshot and obs.screenshot_path:
            try:
                import base64
                from pathlib import Path
                data = Path(obs.screenshot_path).read_bytes()
                artifacts = (
                    {
                        "type": "image",
                        "mime": "image/png",
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                )
            except OSError:
                # Screenshot-Datei nicht lesbar — kein Hard-Fail
                pass

        return ToolResult(
            success=True,
            output=state,
            artifacts=artifacts,
        )
