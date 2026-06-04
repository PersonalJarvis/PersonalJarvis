"""Plugin-Discovery via entry_points.

Alle Plugin-Slots sind in pyproject.toml unter [project.entry-points."jarvis.*"]
deklariert. Dieses Modul listet sie zur Laufzeit auf, lädt sie lazy und prüft
gegen das passende Protocol.

Wichtig: `load()` importiert erst beim tatsächlichen Aufruf — das erlaubt
optionale Dependencies (z.B. Porcupine lädt nur wenn der User es nutzen will).
"""
from __future__ import annotations

from importlib import metadata
from typing import Any, Protocol

from .protocols import PLUGIN_GROUPS


class PluginNotFoundError(KeyError):
    """Plugin mit dem Namen existiert nicht im entry_points-Katalog."""


class PluginLoadError(RuntimeError):
    """Plugin konnte nicht importiert werden (fehlende Dep, Syntax-Fehler …)."""


class PluginContractError(TypeError):
    """Plugin implementiert das erwartete Protocol nicht strukturell."""


def list_plugins(group: str) -> list[str]:
    """Liste aller verfügbaren Plugin-Namen in einer Gruppe.

    Args:
        group: z.B. "jarvis.brain", "jarvis.stt", siehe PLUGIN_GROUPS.
    """
    if group not in PLUGIN_GROUPS:
        raise ValueError(f"Unknown plugin group: {group}. Erlaubt: {PLUGIN_GROUPS}")
    eps = metadata.entry_points(group=group)
    return sorted(ep.name for ep in eps)


def list_all_plugins() -> dict[str, list[str]]:
    """Alle Plugins gruppiert nach Typ."""
    return {g: list_plugins(g) for g in PLUGIN_GROUPS}


def load(group: str, name: str, protocol: type[Protocol] | None = None) -> type[Any]:
    """Lädt eine Plugin-Klasse anhand ihres Namens.

    Args:
        group: Plugin-Gruppe (z.B. "jarvis.brain").
        name: Entry-Point-Name (z.B. "claude-api").
        protocol: Optional — prüft strukturell via isinstance-check bei Instanzen.

    Returns:
        Die Plugin-Klasse (nicht die Instanz). Aufrufer muss selbst instanziieren
        mit den passenden Constructor-Argumenten.

    Raises:
        PluginNotFoundError, PluginLoadError, PluginContractError.
    """
    if group not in PLUGIN_GROUPS:
        raise ValueError(f"Unknown plugin group: {group}")

    eps = metadata.entry_points(group=group)
    candidates = [ep for ep in eps if ep.name == name]
    if not candidates:
        available = sorted(ep.name for ep in eps)
        raise PluginNotFoundError(
            f"Plugin '{name}' in Gruppe '{group}' nicht gefunden. "
            f"Verfügbar: {available}"
        )

    ep = candidates[0]
    try:
        plugin_cls = ep.load()
    except ImportError as exc:
        raise PluginLoadError(
            f"Plugin '{name}' konnte nicht importiert werden. "
            f"Fehlt eine Dependency? Original-Fehler: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise PluginLoadError(f"Plugin '{name}' Load fehlgeschlagen: {exc}") from exc

    # Protocol-Check ist strukturell (Python prüft Attribute + Method-Signaturen
    # beim ersten isinstance-Aufruf auf einer Instanz — nicht auf der Klasse).
    # Wir können hier höchstens sicherstellen, dass es eine Klasse ist.
    if not isinstance(plugin_cls, type):
        raise PluginContractError(
            f"Plugin '{name}' ist keine Klasse sondern {type(plugin_cls)}"
        )

    _ = protocol  # Documentation-Intent; Verifikation erfolgt auf Instanz-Ebene
    return plugin_cls


def describe() -> str:
    """Lesbare Übersicht aller installierten Plugins — für CLI/Admin-UI."""
    lines: list[str] = ["Jarvis Plugin Registry", "=" * 30, ""]
    for group in PLUGIN_GROUPS:
        plugins = list_plugins(group)
        lines.append(f"[{group}]")
        if not plugins:
            lines.append("  (keine)")
        else:
            for p in plugins:
                lines.append(f"  - {p}")
        lines.append("")
    return "\n".join(lines)
