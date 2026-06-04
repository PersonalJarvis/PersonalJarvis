"""``awareness-snapshot``-Tool — synchroner State-Read fuer den Router.

Plan §5 verbindlich: dieses Tool ist Router-Tier-only (NICHT in
SUB_TOOLS). Es macht KEINEN Brain-Call und KEIN IO — nur einen
synchronen Read auf ``AwarenessState.snapshot_for_prompt()``.

Wann nutzt es der Hauptjarvis: bei Utterances wie "Was mache ich
gerade?" oder "In welcher Datei bin ich?" — die Antwort steht im
Awareness-State und braucht keinen LLM-Roundtrip oder OpenClaw-Spawn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis.awareness.manager import AwarenessManager


@dataclass
class ToolResult:
    """Minimal-Wrapper fuer Tool-Output. Vollstaendiger Contract liegt
    in jarvis.core.protocols; hier nur was wir tatsaechlich ruecksenden.

    Wenn das echte Tool-Result-Protocol erweitert wird, muss diese Klasse
    strukturell kompatibel bleiben (oder direkt durch das Protocol ersetzt
    werden).
    """
    success: bool
    output: str
    error: str | None = None


class AwarenessSnapshotTool:
    """Synchroner State-Read auf ``manager.state.snapshot_for_prompt()``."""

    name: str = "awareness-snapshot"
    description: str = (
        "Liefert den aktuellen Awareness-State (aktives Fenster, Idle-Status, "
        "letzter Episode-Summary wenn vorhanden). NUTZE das BEVOR du den User "
        "nach Kontext fragst — die Antwort ist oft schon hier drin."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, manager: AwarenessManager) -> None:
        self._manager = manager

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Synchroner State-Read — KEIN Brain-Call, KEIN IO.

        ``args`` und ``ctx`` werden ignoriert (Schema hat keine Required).
        """
        snap = self._manager.state.snapshot_for_prompt()
        return ToolResult(success=True, output=snap)
