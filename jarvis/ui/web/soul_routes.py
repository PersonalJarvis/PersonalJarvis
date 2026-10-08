"""REST API for the SOUL.md section — the assistant's own profile.

The section shows every file that shapes who the assistant is, in one place:

* ``SOUL.md`` (``data/workspace/SOUL.md``) — its character: name, role, tone,
  limits, and the notes it keeps about itself (``update_soul``);
* the standing instructions the person writes for it
  (``data/agent_instructions/<Name>.md``, edited through
  ``/api/settings/agent-instructions``);
* ``MEMORY.md`` and ``USER.md`` in the vault (``society/jarvis/``) — what it
  was asked to remember, its working notes, and what it noticed about the
  person (``jarvis.memory.learning.notebook``).

Endpoints:
    GET    /api/soul                              → the whole profile
    PUT    /api/soul/file                         → save SOUL.md's Markdown
    PUT    /api/soul/initiative                   → how much initiative it takes
    DELETE /api/soul/entries/{target}/{entry_id}  → forget one learned note

Reads never create a file: a fresh install shows the files as missing
rather than seeding them from a GET.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/soul", tags=["soul"])

#: How many recent learning-ledger lines the activity list shows.
_ACTIVITY_LIMIT = 12
#: Longest quote of what the person said that one activity line carries.
_EVIDENCE_CHARS = 220
#: The ledger rotates at 256 KB; reading its tail is enough for the list.
_LEDGER_TAIL_BYTES = 64 * 1024


class InitiativeBody(BaseModel):
    level: Literal["off", "balanced", "high"]


class SoulBody(BaseModel):
    # The Markdown under the frontmatter. Empty is refused: SOUL.md is the
    # assistant's identity and the page has no "delete" for it.
    content: str = Field(..., min_length=1)


def _config(request: Request) -> Any:
    return getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)


def _mtime_ms(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:  # a missing file has no mtime
        return None


def _entry(entry: Any) -> dict[str, Any]:
    from jarvis.memory.learning.notebook import EXPLICIT_ORIGIN

    return {
        "id": entry.id,
        "text": entry.text,
        "importance": entry.importance,
        "origin": entry.origin,
        "explicit": entry.origin == EXPLICIT_ORIGIN,
    }


def _labelled(lines: list[str]) -> list[dict[str, str]]:
    """``- **Role:** text`` bullets as ``{label, text}``; plain bullets keep no label."""
    out: list[dict[str, str]] = []
    for line in lines:
        text = line.strip()
        if text[:2] in ("- ", "* "):
            text = text[2:].strip()
        label = ""
        if text.startswith("**") and ":**" in text:
            label, text = text[2:].split(":**", 1)
            label, text = label.strip(), text.strip()
        elif text.startswith("**") and text.count("**") >= 2:
            label, text = text[2:].split("**", 1)
            label, text = label.strip().rstrip(":"), text.strip().lstrip(":").strip()
        if text or label:
            out.append({"label": label, "text": text})
    return out


def _soul_file(path: Path) -> dict[str, Any]:
    from jarvis.memory.soul import Soul

    base: dict[str, Any] = {
        "id": "soul",
        "filename": path.name,
        "exists": path.is_file(),
        "editable": True,
        "updated_ms": _mtime_ms(path),
        "content": "",
        "chars": 0,
        "character": {"who": [], "tone": [], "limits": []},
        "learned": [],
        "name_in_file": "",
    }
    if not base["exists"]:
        return base
    try:
        soul = Soul.load(path)
    except (OSError, ValueError):
        log.warning("soul: SOUL.md could not be read", exc_info=True)
        base["exists"] = False
        return base
    sections = soul.sections()
    base.update(
        content=soul.body,
        chars=len(soul.body),
        character={key: _labelled(lines) for key, lines in sections.items()},
        learned=[_entry(e) for e in soul.learned()],
        name_in_file=soul.name,
    )
    return base


def _instructions_file(config: Any) -> dict[str, Any]:
    from jarvis.brain import agent_instructions

    path = agent_instructions.instructions_path(config)
    content = agent_instructions.read_agent_instructions(config) or ""
    return {
        "id": "instructions",
        "filename": path.name,
        "exists": bool(content),
        "editable": True,
        "updated_ms": _mtime_ms(path) if content else None,
        "content": content,
        "chars": len(content),
        "template": agent_instructions.seed_template(config),
    }


def _notebook_folder(config: Any) -> Path | None:
    from jarvis.memory.learning.notebook import OWNER_ID
    from jarvis.society.memory import resolve_society_vault
    from jarvis.society.memory_books import folder_for

    try:
        return folder_for(resolve_society_vault(config), OWNER_ID)
    except Exception:  # noqa: BLE001 — no vault means no notebooks to show, not a failure
        log.debug("soul: the notebook folder is unavailable", exc_info=True)
        return None


def _book_file(folder: Path | None, target: str) -> dict[str, Any]:
    from jarvis.society.memory_books import FILES, body
    from jarvis.society.notebook import parse

    filename = FILES[target]
    path = folder / filename if folder is not None else None
    base: dict[str, Any] = {
        "id": target,
        "filename": filename,
        "exists": bool(path and path.is_file()),
        "editable": False,
        "updated_ms": _mtime_ms(path) if path else None,
        "entries": [],
        "chars": 0,
    }
    if not base["exists"] or path is None:
        return base
    try:
        entries = parse(body(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        log.warning("soul: %s could not be read", filename, exc_info=True)
        return base
    # Newest first: the page answers "what did it learn lately" before "since when".
    ordered = sorted(entries, key=lambda e: e.revision, reverse=True)
    base["entries"] = [_entry(e) for e in ordered]
    base["chars"] = sum(len(e.text) for e in entries)
    return base


def _activity(folder: Path | None) -> list[dict[str, Any]]:
    """The most recent learning-ledger lines, newest first."""
    from jarvis.memory.learning.notebook import LEDGER_NAME

    if folder is None:
        return []
    ledger = folder / LEDGER_NAME
    try:
        with ledger.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _LEDGER_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:  # no ledger yet means no history
        return []
    lines = tail.splitlines()
    if size > _LEDGER_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line of a mid-file tail is cut off
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:  # a torn ledger line is skipped
            continue
        if not isinstance(record, dict):
            continue
        operation = str(record.get("operation", ""))
        text = str(record.get("before" if operation == "remove" else "after", "") or "")
        evidence = str(record.get("evidence", "") or "")
        if len(evidence) > _EVIDENCE_CHARS:
            evidence = evidence[: _EVIDENCE_CHARS - 1].rstrip() + "…"
        out.append(
            {
                "ts": str(record.get("ts", "")),
                "source": str(record.get("source", "")),
                "target": str(record.get("target", "")),
                "operation": operation,
                "text": text,
                "evidence": evidence,
            }
        )
        if len(out) >= _ACTIVITY_LIMIT:
            break
    return out


def _wake_phrase(config: Any) -> str:
    trigger = getattr(config, "trigger", None)
    wake_word = getattr(trigger, "wake_word", None) if trigger is not None else None
    return str(getattr(wake_word, "phrase", "") or "") if wake_word is not None else ""


def _payload(request: Request) -> dict[str, Any]:
    from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name
    from jarvis.brain.identity import PRODUCT_NAME, soul_path
    from jarvis.memory.learning import notebook as notebook_module

    config = _config(request)
    try:
        name = resolve_assistant_name(config)
    except Exception:  # noqa: BLE001 — the page still renders with the fallback name
        log.debug("soul: assistant name unavailable", exc_info=True)
        name = DEFAULT_ASSISTANT_NAME
    folder = _notebook_folder(config)
    return {
        "name": name,
        "named": name != DEFAULT_ASSISTANT_NAME,
        "product": PRODUCT_NAME,
        "wake_phrase": _wake_phrase(config),
        "learning": notebook_module.active() is not None,
        "files": [
            _soul_file(soul_path()),
            _instructions_file(config),
            _book_file(folder, "memory"),
            _book_file(folder, "user"),
        ],
        "activity": _activity(folder),
        "initiative": _initiative(config),
    }


def _initiative(config: Any) -> dict[str, Any]:
    """The initiative level and the dated plans the assistant may bring up."""
    from jarvis.brain import proactivity

    level = proactivity.current_level(config)
    upcoming = [] if level == "off" else proactivity.upcoming_notes()
    return {
        "level": level,
        "levels": list(proactivity.LEVELS),
        "upcoming": [{"date": day.isoformat(), "text": text} for day, text in upcoming],
    }


@router.get("", summary="The assistant's profile: SOUL.md and every file that shapes it")
def get_soul(request: Request) -> dict[str, Any]:
    return _payload(request)


@router.put("/file", summary="Save SOUL.md")
def put_soul(body: SoulBody, request: Request) -> dict[str, Any]:
    """Write SOUL.md's Markdown under its lock; the next prompt reads it."""
    from jarvis.brain.identity import soul_path
    from jarvis.memory.soul import edit_soul

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="SOUL.md must not be empty.")
    path = soul_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="SOUL.md does not exist yet.")
    try:
        edit_soul(path, lambda soul: soul.set_body(body.content))
    except TimeoutError as exc:
        raise HTTPException(status_code=409, detail="SOUL.md is busy; try again.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc
    return {"ok": True, **_payload(request)}


@router.put("/initiative", summary="Set how much initiative the assistant takes")
def put_initiative(body: InitiativeBody, request: Request) -> dict[str, Any]:
    """Persist ``[brain] proactivity``, then apply it live to every surface.

    The write comes first: a level that could not be saved is not shown as set.
    """
    from jarvis.brain import proactivity
    from jarvis.core import config_writer
    from jarvis.core.config import resolve_config_path

    try:
        # Honour JARVIS_CONFIG so the write lands in the file load_config reads.
        config_writer.set_proactivity(body.level, path=resolve_config_path())
    except Exception as exc:  # noqa: BLE001 — reported to the page, nothing applied
        log.warning("soul: initiative level not saved", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc
    level = proactivity.apply_level(body.level)
    brain = getattr(_config(request), "brain", None)
    if brain is not None:
        try:
            brain.proactivity = level
        except Exception:  # noqa: BLE001 — the live level above already answers
            log.debug("soul: in-memory brain.proactivity not updated", exc_info=True)
    return {"ok": True, **_payload(request)}


@router.delete("/entries/{target}/{entry_id}", summary="Forget one learned note")
def forget_entry(
    target: Literal["soul", "memory", "user"], entry_id: str, request: Request
) -> dict[str, Any]:
    """Remove one note; the learning ledger keeps its old text, so it is recoverable."""
    from jarvis.memory.learning import notebook as notebook_module

    notebook = notebook_module.active()
    if notebook is None:
        raise HTTPException(
            status_code=503, detail="The learning loop is not running in this session."
        )
    try:
        notebook.apply(
            target=target,
            operation="remove",
            entry_id=entry_id,
            source="soul page: forgotten by the user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, **_payload(request)}
