"""Deliverable-summary builder for the Kontrollierer's voice readback.

After a mission completes successfully the Kontrollierer publishes a
``MissionApproved`` event whose ``summary_de`` is spoken to the user. For
read-only / informational tasks the worker's plain-text answer is already the
right thing to say. For CODE tasks (tasks that produce file changes) the
previous behaviour was the generic ``"Mission abgeschlossen."`` — the user
heard a confirmation but had no idea WHERE the generated file landed.

Live regression 2026-05-26: two real HTML deliverables existed on disk that
day (``Personal-Jarvis-Landing.html`` 33 KB, ``werbung.html`` 14 KB) and the
user heard about neither. This helper scans the archive directory laid down by
:func:`Kontrollierer._archive_task_artifacts` and produces a TTS-friendly
German sentence that names the file(s) so the user can act on them.

Archive layout the helper reads:

    <mission_dir>/tasks/<task_id[:13]>/artifacts/files/<rel-path>

Output discipline:

* TTS-safe — only bare basenames (``scrub_for_voice`` would mangle slashes).
* ``"Datei"`` is in the scrub whitelist so it survives the voice path.
* Short — 1-3 files inline, more collapses to a count.
* Empty string when there are no archived deliverables (caller falls back to
  the generic phrase).

Pure stdlib, no I/O on the hot path beyond a small ``rglob`` on a known shape.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: Inline-list cap. Beyond this the spoken sentence becomes unreadable.
_MAX_NAMED_FILES: Final[int] = 3

#: Folder name the mission deliverables are mirrored into so a non-coder can
#: actually find them. Lives under the user's Downloads on Windows (the place
#: every file dialog + Explorer sidebar surfaces), grouped in one subfolder so
#: it never clutters Downloads with dozens of loose files.
_DELIVERABLES_FOLDER_NAME: Final[str] = "Jarvis-Outputs"


def build_deliverable_summary(mission_dir: Path) -> str:
    """Return a TTS-safe German sentence naming the mission's archived files.

    Args:
        mission_dir: Path to ``<isolation_root>/mission_<id[:13]>/``.

    Returns:
        Empty string when there are no archived deliverables. Otherwise a
        speakable German sentence:

        * 1 file:           ``"Fertig. Datei <name> ist gespeichert."``
        * 2..3 files:       ``"Fertig. <n> Dateien gespeichert: A, B."``
        * 4+ files:         ``"Fertig. <n> Dateien gespeichert."``

        Caller (``Kontrollierer._approve_mission``) ``or``-fallbacks to
        ``"Mission abgeschlossen."`` when this returns empty.
    """
    tasks_root = mission_dir / "tasks"
    if not tasks_root.exists() or not tasks_root.is_dir():
        return ""

    names: list[str] = []
    try:
        for task_dir in sorted(tasks_root.iterdir()):
            files_dir = task_dir / "artifacts" / "files"
            if not files_dir.exists() or not files_dir.is_dir():
                continue
            for path in files_dir.rglob("*"):
                if path.is_file():
                    names.append(path.name)
    except OSError:
        # Defensive: filesystem hiccups must not crash the readback path.
        return ""

    if not names:
        return ""

    count = len(names)
    if count == 1:
        return f"Fertig. Datei {names[0]} ist gespeichert."
    if count <= _MAX_NAMED_FILES:
        joined = ", ".join(names)
        return f"Fertig. {count} Dateien gespeichert: {joined}."
    return f"Fertig. {count} Dateien gespeichert."


def resolve_deliverables_dir(override: str | None = None) -> Path:
    """Return the user-visible folder mission deliverables are mirrored into.

    Resolution order:
      * ``override`` (e.g. a future ``[phase6].deliverables_dir`` config key) —
        ``expanduser`` + ``resolve``, used verbatim.
      * Windows with a real ``~/Downloads``: ``~/Downloads/Jarvis-Outputs``.
      * Windows without Downloads: ``~/Desktop/Jarvis-Outputs``, else
        ``~/Jarvis-Outputs``.
      * Non-Windows / headless (cloud-first VPS): ``~/jarvis-outputs`` — never
        assumes a Desktop/Downloads or an Explorer.

    The directory is created (``parents=True, exist_ok=True``). Never raises for
    a missing parent; on an unwritable home it falls back to the system temp
    dir so a delivery attempt degrades to "somewhere" rather than crashing an
    already-approved mission.
    """
    if override:
        target = Path(override).expanduser()
    elif os.name == "nt":
        home = Path.home()
        downloads = home / "Downloads"
        if downloads.is_dir():
            target = downloads / _DELIVERABLES_FOLDER_NAME
        elif (home / "Desktop").is_dir():
            target = home / "Desktop" / _DELIVERABLES_FOLDER_NAME
        else:
            target = home / _DELIVERABLES_FOLDER_NAME
    else:
        # Cloud-first: no Explorer, no Downloads assumption on a Linux VPS.
        target = Path.home() / "jarvis-outputs"

    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError as exc:
        import tempfile

        fallback = Path(tempfile.gettempdir()) / _DELIVERABLES_FOLDER_NAME
        logger.warning(
            "deliverables dir %s not writable (%s) — using %s",
            target, exc, fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def deliver_to_user_folder(
    mission_dir: Path,
    *,
    mission_short_id: str = "",
    override_dir: str | None = None,
) -> list[Path]:
    """Copy a mission's archived deliverables into the user-visible folder.

    Reads the same archive layout ``build_deliverable_summary`` reads
    (``<mission_dir>/tasks/<task>/artifacts/files/<rel>``) and copies each
    genuine deliverable into :func:`resolve_deliverables_dir`. Returns the list
    of final on-disk paths (may be empty).

    Discipline:
      * **Idempotent** — if the target already holds a byte-identical file the
        copy is skipped (a re-run / duplicate approve never duplicates files).
      * **Collision-safe** — a same-name file with *different* bytes is written
        as ``<stem>__<mission_short_id><suffix>`` (deterministic, no random).
      * **Best-effort** — every per-file failure is logged and skipped; the
        function never raises, so a copy hiccup can't flip an APPROVED mission
        to FAILED (anti-BUG-020 silent-cascade adjacency).
      * Flat copy by basename: the user wants the file, not the mission's
        internal ``tasks/.../artifacts/files`` tree.
    """
    tasks_root = mission_dir / "tasks"
    if not tasks_root.is_dir():
        return []

    # Enumerate sources FIRST — if there are no deliverables we return before
    # creating any user-visible folder (a delivery dir must never be conjured
    # for a mission that produced nothing; this also keeps test runs from
    # touching the real ~/Downloads).
    try:
        sources: list[Path] = []
        for task_dir in sorted(tasks_root.iterdir()):
            files_dir = task_dir / "artifacts" / "files"
            if not files_dir.is_dir():
                continue
            for path in sorted(files_dir.rglob("*")):
                if path.is_file():
                    sources.append(path)
    except OSError as exc:
        logger.warning("deliver: enumerating %s failed: %s", tasks_root, exc)
        return []

    if not sources:
        return []

    try:
        target_dir = resolve_deliverables_dir(override_dir)
    except Exception as exc:  # noqa: BLE001 — never fail an approved mission
        logger.warning("could not resolve deliverables dir: %s", exc)
        return []

    delivered: list[Path] = []
    short = (mission_short_id or "out").replace("/", "-")[:13]

    for src in sources:
        dst = target_dir / src.name
        try:
            if dst.exists():
                # Idempotent: identical bytes -> nothing to do.
                if dst.stat().st_size == src.stat().st_size and (
                    dst.read_bytes() == src.read_bytes()
                ):
                    delivered.append(dst)
                    continue
                # Collision with different content -> deterministic suffix.
                dst = target_dir / f"{src.stem}__{short}{src.suffix}"
            shutil.copy2(src, dst)
            delivered.append(dst)
        except OSError as exc:
            logger.warning("deliver: copy %s -> %s failed: %s", src, dst, exc)
            continue

    if delivered:
        logger.info(
            "delivered %d file(s) to %s: %s",
            len(delivered), target_dir, [p.name for p in delivered],
        )
    return delivered


def build_delivered_summary(delivered: list[Path]) -> str:
    """TTS-safe German sentence naming delivered files AND their folder.

    Unlike :func:`build_deliverable_summary` (which only names the archive
    basenames), this also tells the user WHERE the file landed so they can open
    it. Empty string when nothing was delivered (caller falls back).
    """
    if not delivered:
        return ""
    folder = delivered[0].parent
    names = [p.name for p in delivered]
    count = len(names)
    if count == 1:
        return f"Fertig. Datei {names[0]} liegt im Ordner {folder.name}."
    if count <= _MAX_NAMED_FILES:
        joined = ", ".join(names)
        return f"Fertig. {count} Dateien im Ordner {folder.name}: {joined}."
    return f"Fertig. {count} Dateien im Ordner {folder.name} gespeichert."


__all__ = [
    "build_deliverable_summary",
    "build_delivered_summary",
    "deliver_to_user_folder",
    "resolve_deliverables_dir",
]
