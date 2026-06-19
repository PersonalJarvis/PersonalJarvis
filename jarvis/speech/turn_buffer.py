"""TurnBuffer — Rolling-Window der letzten User-Transkripte.

Dient dem Voice-Korrektur-Command ("nein, ich meinte X") als Quelle fuer das
vorletzte Transkript. Echte Phase-6-Logik (Multi-Turn-Context-Replay) folgt
spaeter; diese Implementierung persistiert minimal das was die Pipeline
aktuell uebergibt, damit `last()` / `pop_last()` funktionieren sobald der
BrainManager-Korrektur-Command aktiv wird.

API-Contract (wie in `SpeechPipeline._handle_utterance` aufgerufen):

    turn_buffer.append(text=..., language=..., confidence=...)

Der frueheren Stub-Version fehlten die Kwargs — die Pipeline crashte mit
``TypeError: TurnBuffer.append() got an unexpected keyword argument 'text'``
sofort nach jedem User-Utterance und kam nie zum Brain-Call. Ergebnis:
Wake triggerte, "Sir?" wurde gesprochen, dann brach die Session lautlos ab.
Siehe AGENTS.md (BUG-001 — korrigierte Root-Cause) fuer die Geschichte.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """Ein User-Turn im Rolling-Buffer."""
    text: str
    language: str = ""
    confidence: float | None = None


class TurnBuffer:
    """Rolling-Window der letzten N User-Turns."""

    def __init__(self, maxlen: int = 10) -> None:
        self._maxlen = maxlen
        self._items: deque[Turn] = deque(maxlen=maxlen)

    def append(
        self,
        *,
        text: str,
        language: str = "",
        confidence: float | None = None,
    ) -> None:
        """Fuegt einen neuen Turn ein (aeltester wird verdraengt bei maxlen)."""
        self._items.append(
            Turn(text=text, language=language, confidence=confidence)
        )

    def last(self) -> Turn | None:
        """Letzter gespeicherter Turn oder ``None`` wenn leer."""
        if not self._items:
            return None
        return self._items[-1]

    def pop_last(self) -> Turn | None:
        """Entfernt und liefert den letzten Turn (fuer Korrektur-Command)."""
        if not self._items:
            return None
        return self._items.pop()

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
