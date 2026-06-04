"""First-Run-Bootstrap fuer den User-Skill-Ordner.

Kopiert die mitgelieferten Builtin-Skills aus ``jarvis/skills/builtin/`` nach
``user_skills_dir()``, wenn sie dort noch nicht liegen. Idempotent — bei jedem
Start aufrufbar; vorhandene User-Edits werden nie ueberschrieben.

Warum ueberhaupt kopieren (statt nur aus ``builtin/`` laden)?

- Der ``SkillRegistry``-Watcher (``jarvis/skills/registry.py``) beobachtet *eine*
  Root. Wenn alle Skills an einem Ort liegen, funktioniert Hot-Reload out-of-the-box.
- Der User soll Builtins *sehen* und inspizieren koennen. Versteckte Read-only-
  Kopien im site-packages-Verzeichnis sind fuer Skills-Autoren intransparent.
- Admin-Edit-Protection (siehe ``skills_routes.py``) kann per Pfad-Check erkennen
  was ein Builtin ist (Name in ``BUILTIN_SKILL_NAMES``) — der Rest ist User-Space.

Bootstrap-Versionierung: eine ``.bootstrap-version`` im User-Skills-Dir speichert
die zuletzt gebootstrappte Package-Version. Bei Mismatch werden fehlende Skills
neu kopiert (bestehende bleiben). So kommt z.B. ein neuer Builtin-Skill in einer
spaeteren Version automatisch rueber, ohne User-Anpassungen zu zerstoeren.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from jarvis.core.paths import ensure_user_dirs, user_skills_dir

from .builtin import BUILTIN_SKILL_NAMES, BUILTIN_SKILLS_DIR
from .schema import RESOURCE_KINDS

log = logging.getLogger(__name__)

BOOTSTRAP_VERSION_FILE = ".bootstrap-version"
BOOTSTRAP_VERSION = "2"


def ensure_user_skills_dir() -> Path:
    """Legt ``user_skills_dir()`` an und kopiert fehlende Builtin-Skills hinein.

    Zwei Faelle pro Builtin:

    1. **Komplett neu:** User hat den Skill-Ordner noch nicht -> kompletter
       ``copytree`` von Builtin-Root nach User-Root.
    2. **Upgrade:** User hat schon eine ``SKILL.md`` (darf editiert sein, nicht
       ueberschreiben), aber dem Builtin wurden Bundle-Sibling-Ordner
       hinzugefuegt -> nur die fehlenden Kind-Ordner nachziehen.

    So behalten wir die "Never touch user-edited SKILL.md"-Garantie, rollen aber
    neue Bundle-Resources (``references/``, ``scripts/``, …) automatisch nach.

    Returns den User-Skills-Pfad, damit Aufrufer ihn direkt als Registry-Root
    verwenden koennen.
    """
    ensure_user_dirs()
    dst_root = user_skills_dir()

    copied: list[str] = []
    upgraded: list[str] = []
    for name in BUILTIN_SKILL_NAMES:
        src = BUILTIN_SKILLS_DIR / name
        dst = dst_root / name
        if not src.exists():
            log.warning("builtin skill '%s' fehlt im Package — skip", name)
            continue
        if not (dst / "SKILL.md").exists():
            # Fall 1: komplett neu
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                copied.append(name)
            except Exception as exc:  # noqa: BLE001
                log.exception("copy builtin skill '%s' fehlgeschlagen: %s", name, exc)
            continue

        # Fall 2: Upgrade — nur fehlende Sibling-Ordner mitnehmen
        added = _sync_missing_resources(src, dst)
        if added:
            upgraded.append(f"{name} (+{','.join(added)})")

    _write_version_marker(dst_root)
    if copied:
        log.info("bootstrap: %d builtin skills neu kopiert: %s",
                 len(copied), ", ".join(copied))
    if upgraded:
        log.info("bootstrap: %d builtin skills mit neuen Resources gepflegt: %s",
                 len(upgraded), ", ".join(upgraded))
    return dst_root


def _sync_missing_resources(src: Path, dst: Path) -> list[str]:
    """Kopiert Sibling-Ordner (references/scripts/assets/agents), die im
    Builtin existieren aber im User-Dir fehlen. Vorhandene User-Ordner werden
    **nicht** angefasst — auch wenn der Builtin dort neue Files hat. Das ist
    die konservative Default-Policy; ein expliziter "force-sync"-Modus waere
    eine spaetere Erweiterung.
    """
    added: list[str] = []
    for kind in RESOURCE_KINDS:
        src_kind = src / kind
        dst_kind = dst / kind
        if not src_kind.is_dir():
            continue
        if dst_kind.exists():
            continue  # User hat den Ordner schon — hands off
        try:
            shutil.copytree(src_kind, dst_kind)
            added.append(kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("sync resource '%s/%s' fehlgeschlagen: %s", dst.name, kind, exc)
    return added


def _write_version_marker(dst_root: Path) -> None:
    """Schreibt den Bootstrap-Version-Marker. Fehler werden geschluckt —
    der Marker ist reine Info, kein Blocker."""
    try:
        (dst_root / BOOTSTRAP_VERSION_FILE).write_text(
            BOOTSTRAP_VERSION, encoding="utf-8"
        )
    except OSError:  # pragma: no cover
        pass


__all__ = ["ensure_user_skills_dir", "BOOTSTRAP_VERSION"]
