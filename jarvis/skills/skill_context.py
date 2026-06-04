"""Kontext-Registry fuer das Skill-System (Skills-Brain-Integration).

Vorbild: ``jarvis/harness/computer_use_context.py``.

Die Speech-Pipeline (Pre-Brain-Hook) und das ``run_skill``-Tool brauchen
Zugriff auf SkillRegistry + SkillRunner. Beide Komponenten werden aber an
verschiedenen Stellen instanziiert (BrainManager-Factory bzw. Pipeline-
Bootstrap), und die Plugin-Tools werden via ``entry_points`` ohne Args
geladen. Statt umstaendliche DI durch alle Layer zu ziehen, halten wir
einen prozessweiten Kontext.

Die App ruft einmal beim Start ``set_skill_context(ctx)``; Konsumenten
holen sich den Kontext mit ``get_skill_context()`` (raised RuntimeError
wenn nicht gesetzt) oder ``try_get_skill_context()`` (None wenn nicht
gesetzt — fuer optionale Hooks die gracefully skippen koennen).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import SkillRegistry
    from .runner import SkillRunner


@dataclass
class SkillContext:
    """Alle Deps des Skill-Pfads an einer Stelle."""
    registry: SkillRegistry
    runner: SkillRunner


_CONTEXT: SkillContext | None = None


def set_skill_context(ctx: SkillContext | None) -> None:
    """Setzt oder loescht (ctx=None) den globalen Skill-Context."""
    global _CONTEXT
    _CONTEXT = ctx


def get_skill_context() -> SkillContext:
    """Liefert den gesetzten Kontext oder wirft eine klare Fehlermeldung."""
    if _CONTEXT is None:
        raise RuntimeError(
            "Skill-Context nicht gesetzt. "
            "Die Haupt-App muss vor dem ersten Skill-Aufruf "
            "`set_skill_context(...)` aufrufen.",
        )
    return _CONTEXT


def try_get_skill_context() -> SkillContext | None:
    """Liefert den Kontext wenn gesetzt, sonst None.

    Fuer Code-Pfade die optional Skills nutzen (z.B. der Pre-Brain-Hook
    in der Speech-Pipeline darf gracefully skippen wenn die Registry
    nicht aufgesetzt wurde, etwa im Headless-Mock-Mode).
    """
    return _CONTEXT
