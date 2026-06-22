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
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Final

from ..stream_evidence import clean_request_body
from .deliverable_paths import is_nondeliverable_scratch

logger = logging.getLogger(__name__)

#: Inline-list cap. Beyond this the spoken sentence becomes unreadable.
_MAX_NAMED_FILES: Final[int] = 3

#: A worker answer shorter than this is an acknowledgement ("ok", "done"), not a
#: report — materialising it would clutter the Outputs view with noise.
_MIN_REPORT_CHARS: Final[int] = 40

#: Synthetic per-task dir used only when the archive left no ``tasks/<id>/`` dir
#: at all (Edit-only mission, odd code path) — keeps the report inside the
#: canonical ``tasks/<id>/artifacts/files/`` deliverable subtree the Outputs view
#: lists, so a report is never orphaned outside the allowlisted layout.
_REPORT_TASK_DIRNAME: Final[str] = "answer"

#: Stable filename when the prompt slugifies to nothing (symbols-only request).
_DEFAULT_REPORT_STEM: Final[str] = "report"

#: Max length of the slugified filename stem (kept well under the 200-char
#: worktree path cap so deep archive paths never overflow on Windows).
_MAX_SLUG_LEN: Final[int] = 48

#: Transliteration for the handful of non-ASCII letters that survive NFKD
#: decomposition (ß has no combining form; the umlauts decompose to a+¨ which we
#: want rendered as the German digraph, not a bare vowel). The uppercase forms
#: intentionally map to lowercase digraphs because the slug is lowercased in full
#: by the trailing ``.lower()`` in ``_slugify_filename`` anyway.
_TRANSLIT: Final[dict[str, str]] = {
    "ä": "ae", "ö": "oe", "ü": "ue",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
    "ß": "ss",
}

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
                if path.is_file() and not is_nondeliverable_scratch(
                    path.relative_to(files_dir).as_posix()
                ):
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


def _existing_deliverable_files(mission_dir: Path) -> list[Path]:
    """Every genuine deliverable file the archive already wrote for the mission.

    Mirrors the Outputs view's allowlist (``tasks/<id>/artifacts/files/**``) so
    the "does a real deliverable already exist?" check uses the exact same
    definition of "output" the user sees.
    """
    out: list[Path] = []
    tasks_root = mission_dir / "tasks"
    if not tasks_root.is_dir():
        return out
    try:
        for task_dir in sorted(tasks_root.iterdir()):
            files_dir = task_dir / "artifacts" / "files"
            if not files_dir.is_dir():
                continue
            out.extend(
                p for p in files_dir.rglob("*")
                if p.is_file() and not is_nondeliverable_scratch(
                    p.relative_to(files_dir).as_posix()
                )
            )
    except OSError:
        return out
    return out


def _slugify_filename(text: str) -> str:
    """Lowercase ASCII-hyphen slug for a report filename, or the default stem.

    German umlauts/ß are transliterated (ä→ae, ß→ss) before NFKD strips any
    remaining accents, so the filename stays portable across filesystems and the
    download/`Content-Disposition` header never carries raw non-ASCII bytes.
    """
    text = "".join(_TRANSLIT.get(ch, ch) for ch in (text or ""))
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(slug) > _MAX_SLUG_LEN:
        slug = slug[:_MAX_SLUG_LEN].rstrip("-")
    return slug or _DEFAULT_REPORT_STEM


def _report_title(prompt: str) -> str:
    """First non-empty line of the request, condensed into a one-line H1 title."""
    for line in (prompt or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Result"


def materialize_answer_document(
    mission_dir: Path,
    *,
    answers: list[str],
    prompt: str,
    expected_output: str | None = None,
) -> Path | None:
    """Persist the worker's text answer as a Markdown report — but only when the
    mission produced NO genuine file deliverable.

    The "always a document" guarantee: a code/file task already wrote its
    artifact (HTML, ``.py``, …) and is shown in Outputs untouched — we never
    duplicate it. A pure research / Q&A task delivers its answer as text and
    would otherwise leave ``artifacts/files/`` empty (live forensic 2026-06-19:
    the same "relocate to SF" question produced a report once and an empty
    Outputs card the next time, depending on whether the worker happened to write
    a file). This writes that text answer into the canonical deliverable subtree

        <mission_dir>/tasks/<task_id>/artifacts/files/<slug>.md

    so it is listed, viewable, downloadable, and mirrored to the user's
    Jarvis-Outputs folder by :func:`deliver_to_user_folder` like any other
    deliverable.

    Args:
        mission_dir: ``<isolation_root>/mission_<id[:13]>/``.
        answers: the worker's per-task text answers (``readonly_answer`` output).
        prompt: the user's request — its first line becomes the report title and
            (slugified) its filename.
        expected_output: the planner's expected-output hint, appended as context
            when present.

    Returns:
        The written report path, or ``None`` when nothing was written: a real
        file deliverable already exists, the mission produced no substantive
        answer, or the mission dir is missing. Best-effort — never raises; a
        write failure returns ``None`` so it can never flip an APPROVED mission.

    Idempotent: a second call sees the report it just wrote as an existing
    deliverable and returns ``None``, so an approve re-run never duplicates it.
    """
    try:
        if not mission_dir.is_dir():
            return None
        # A genuine file deliverable already satisfies "show me a document".
        if _existing_deliverable_files(mission_dir):
            return None

        body = "\n\n".join(a.strip() for a in answers if a and a.strip()).strip()
        if len(body) < _MIN_REPORT_CHARS:
            return None

        # Land the report inside the archive's existing task dir when there is
        # one (so it sits next to that task's diff), else a synthetic task dir.
        # Either way the path stays in the allowlisted deliverable subtree.
        tasks_root = mission_dir / "tasks"
        try:
            existing_task_dirs = (
                sorted(p for p in tasks_root.iterdir() if p.is_dir())
                if tasks_root.is_dir()
                else []
            )
        except OSError:
            # A directory vanishing mid-enumeration must not swallow a valid
            # answer into a silent None — fall through to the synthetic task dir
            # so the report is still written. (Distinct from the outer handler,
            # which is the last-resort write-failure guard.)
            logger.debug(
                "materialize: could not enumerate %s, using synthetic dir",
                tasks_root,
            )
            existing_task_dirs = []
        task_dir = (
            existing_task_dirs[0]
            if existing_task_dirs
            else tasks_root / _REPORT_TASK_DIRNAME
        )
        files_dir = task_dir / "artifacts" / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        # Strip spawn_worker's standing quality-directive preamble so the title
        # and filename reflect the user's REAL request, not "Deliver a complete,
        # polished, production-quality …" (the preamble is the prompt's first
        # line otherwise).
        clean_prompt = clean_request_body(prompt)
        # The title comes from the user's prompt (may be German — user content).
        # The scaffold labels ("Requested:") are intentionally English per the
        # Output Language Policy (every generated artifact is English).
        title = _report_title(clean_prompt)
        sections = [f"# {title}", "", body]
        if expected_output and expected_output.strip():
            sections += ["", "---", "", f"_Requested: {expected_output.strip()}_"]
        report = "\n".join(sections).rstrip() + "\n"

        dst = files_dir / f"{_slugify_filename(clean_prompt)}.md"
        dst.write_text(report, encoding="utf-8")
        logger.info("materialised answer report: %s", dst)
        return dst
    except OSError as exc:
        logger.warning("materialize_answer_document failed in %s: %s", mission_dir, exc)
        return None


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
                if path.is_file() and not is_nondeliverable_scratch(
                    path.relative_to(files_dir).as_posix()
                ):
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
    "materialize_answer_document",
    "resolve_deliverables_dir",
]
