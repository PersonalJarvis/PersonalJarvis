"""REST-Routes fuer die Voice-Session-Transcription-View.

Endpoints:
    GET  /api/sessions                       Liste (neueste zuerst, max 100)
    GET  /api/sessions/{session_id}          Detail mit Turns + Roh-Events
    GET  /api/sessions/{session_id}/export   Markdown / Plain-Text fuer Copy
    POST /api/sessions/{session_id}/save     Schreibt Datei ins Windows-Downloads-Verz.

Wird vom WebServer in ``_build_app()`` eingehaengt::

    from .sessions_routes import router as sessions_router
    app.include_router(sessions_router)

Der zugrundeliegende ``SessionStore`` wird beim App-Start in
``server.py::_init_sessions_stack()`` per ``bootstrap_sessions(...)``
erzeugt und auf ``app.state.session_store`` gelegt.

Loopback-only (Server bindet auf 127.0.0.1) — kein Auth-Token noetig.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from jarvis.sessions.formatter import format_session_markdown, format_session_plain
from jarvis.sessions.models import SessionDetail, SessionListItem, VoiceSessionRow
from jarvis.sessions.store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ----------------------------------------------------------------------
# DI Helper — Store aus app.state ziehen
# ----------------------------------------------------------------------


def _require_store(request: Request) -> SessionStore:
    store: SessionStore | None = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="session-recorder-disabled",
        )
    return store


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("", response_model=list[SessionListItem])
async def list_sessions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SessionListItem]:
    """Liste aller Voice-Sessions, neueste zuerst.

    Frontend ruft das beim Tab-Wechsel auf "Transkription" sowie nach
    einem ``VoiceSessionEnded``-WS-Event (Re-Fetch).
    """
    store = _require_store(request)
    return store.list_sessions(limit=limit)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session_detail(session_id: str, request: Request) -> SessionDetail:
    """Komplette Session: Header + Turns + Roh-Events fuer Replay."""
    store = _require_store(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session-not-found")
    turns = store.get_turns(session_id)
    events = store.get_events(session_id)
    return SessionDetail(session=session, turns=turns, events=events)


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    request: Request,
    format: Literal["markdown", "plain", "json"] = Query(default="markdown"),
) -> Response:
    """Formatierte Session fuer Click-to-Copy.

    - ``format=markdown`` (Default) — strukturiert mit Emojis, fuer Chat-/
      Notion-/Obsidian-Copy.
    - ``format=plain`` — ASCII-only, fuer Plain-Text-Editoren.
    - ``format=json`` — Maschinen-lesbares Komplett-Dump (gleicher Inhalt
      wie ``GET /api/sessions/{id}``).

    Returns ``text/markdown`` bzw. ``text/plain`` mit dem fertigen Text
    im Body — Frontend kann ``response.text()`` direkt in
    ``navigator.clipboard.writeText`` reichen.
    """
    store = _require_store(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session-not-found")
    turns = store.get_turns(session_id)
    # Raw events carry the SpeechSpoken track (every voiced non-reply phrase).
    # All three export formats document it: markdown tags each by kind, plain
    # folds them into the dialogue, JSON ships the raw events.
    events = store.get_events(session_id)

    if format == "markdown":
        body = format_session_markdown(session, turns, events)
        return PlainTextResponse(content=body, media_type="text/markdown; charset=utf-8")
    if format == "plain":
        body = format_session_plain(session, turns, events)
        return PlainTextResponse(content=body, media_type="text/plain; charset=utf-8")
    # JSON-Variant — wir geben den vollen Detail-Payload zurueck.
    detail = SessionDetail(session=session, turns=turns, events=events)
    return Response(
        content=detail.model_dump_json(indent=2),
        media_type="application/json",
    )


# ----------------------------------------------------------------------
# Save-to-Downloads — Backend schreibt direkt ins Filesystem
# ----------------------------------------------------------------------


class SaveSessionResponse(BaseModel):
    """Antwort des Save-Endpoints — voller Pfad fuer Toast/Anzeige."""

    saved_path: str
    bytes_written: int
    filename: str


@router.post(
    "/{session_id}/save",
    response_model=SaveSessionResponse,
)
async def save_session_to_downloads(
    session_id: str,
    request: Request,
    format: Literal["markdown", "plain", "json"] = Query(default="markdown"),
) -> SaveSessionResponse:
    """Schreibt die Session als Datei in den Windows-Downloads-Ordner.

    Pfad: ``%USERPROFILE%\\Downloads\\voice-session-YYYY-MM-DD_HH-mm-{slug}.{ext}``.
    Bei Kollision wird ein Suffix ``-1``, ``-2``, ... angehaengt — niemals
    ueberschreiben, keine Datenverluste.

    Begruendung: Browser-Downloads in pywebview/WebView2 landen in einem
    Edge-internen Pfad, nicht zwingend im Windows-Default. Indem das
    Backend direkt schreibt, kontrollieren wir den Zielort und melden
    den vollen absoluten Pfad zurueck — User sieht klar wo die Datei liegt.
    """
    store = _require_store(request)
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session-not-found")
    turns = store.get_turns(session_id)

    # Format-Body rendern.
    if format == "markdown":
        body = format_session_markdown(session, turns)
    elif format == "plain":
        body = format_session_plain(session, turns)
    else:
        events = store.get_events(session_id)
        detail = SessionDetail(session=session, turns=turns, events=events)
        body = detail.model_dump_json(indent=2)

    # Filename aus Session-Metadaten + erstem User-Text bauen.
    first_user = next((t.user_text for t in turns if t.user_text), "")
    filename = _build_filename(session, first_user, format)

    # Zielpfad: %USERPROFILE%\Downloads\.
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target = _avoid_collision(downloads / filename)

    # Schreiben — UTF-8 mit BOM nur fuer plain-Text damit Notepad korrekt
    # erkennt; Markdown + JSON bleiben pure UTF-8.
    encoding = "utf-8"
    target.write_text(body, encoding=encoding)
    log.info(
        "SessionSave: format=%s session=%s -> %s (%d bytes)",
        format, session_id, target, len(body.encode("utf-8")),
    )
    return SaveSessionResponse(
        saved_path=str(target),
        bytes_written=len(body.encode("utf-8")),
        filename=target.name,
    )


# --- Helpers ----------------------------------------------------------


def _build_filename(
    session: VoiceSessionRow,
    first_user_text: str,
    format: Literal["markdown", "plain", "json"],
) -> str:
    """Erzeugt einen Filesystem-tauglichen Dateinamen.

    Pattern: ``voice-session-YYYY-MM-DD_HH-mm-{slug}.{ext}``.
    """
    ext = "md" if format == "markdown" else ("txt" if format == "plain" else "json")
    dt = datetime.fromtimestamp(session.started_ms / 1000.0)
    stamp = dt.strftime("%Y-%m-%d_%H-%M")
    slug = _slugify(first_user_text) or session.id[:8]
    return f"voice-session-{stamp}-{slug}.{ext}"


def _slugify(text: str) -> str:
    """Reduziert Text auf [a-z0-9-], maximal 4 Woerter, 40 Zeichen."""
    if not text:
        return ""
    # Umlaute / Diakritika strippen
    import unicodedata as _u
    norm = _u.normalize("NFKD", text)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9\s-]+", " ", ascii_text)
    words = cleaned.strip().split()
    return "-".join(words[:4])[:40]


def _avoid_collision(target: Path) -> Path:
    """Hängt -1, -2, ... an, falls die Datei schon existiert."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(1, 1000):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    # Fallback — hochgradig unwahrscheinlich
    return parent / f"{stem}-{int(datetime.now().timestamp())}{suffix}"
