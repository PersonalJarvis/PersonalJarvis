"""Readers that turn each store's own shape into :class:`CostEntry` rows.

Every reader is defensive by design: a missing file, an older schema without
the column it wants, or a corrupt row must degrade to "this source
contributed nothing" and never to a failed request. A fresh install has none
of these databases yet, and the section still has to open.

Read-only, always: connections are opened in SQLite's immutable URI mode where
possible so a live voice turn writing to the same file is never blocked.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .model import (
    ROLE_AGENT,
    ROLE_PIPELINE,
    ROLE_REALTIME,
    ROLE_STT,
    ROLE_TOOL,
    ROLE_TTS,
    ROLE_WORKER,
    SUBSCRIPTION_RUNNERS,
    SURFACE_AGENT_CHAT,
    SURFACE_AGENTIC_IDE,
    SURFACE_JARVIS_VOICE,
    SURFACE_MISSION,
    SURFACE_VOICE,
    CostEntry,
    price_entry,
)

log = logging.getLogger(__name__)

_LABEL_MAX = 90

# A realtime provider reports its usage under this finish reason. It is the
# one reliable marker that separates audio spend from the text model a turn
# delegated to — both land in the same event stream.
_REALTIME_FINISH = "realtime_usage"

# Voice tiers that are not realtime run the classic pipeline.
_REALTIME_TIER = "realtime"


@dataclass(frozen=True, slots=True)
class CostSources:
    """Where the read model looks. Absent files are simply skipped."""

    sessions_db: Path | None = None
    missions_db: Path | None = None
    agent_chat_db: Path | None = None
    #: Where :mod:`jarvis.costs.cli_usage_index` keeps its index. A source
    #: like any other, so a caller that did not ask for coding-CLI spend —
    #: a test with its own fixtures, above all — does not silently get this
    #: machine's.
    cli_index_dir: Path | None = None

    def existing(self) -> list[Path]:
        candidates = (self.sessions_db, self.missions_db, self.agent_chat_db)
        return [p for p in candidates if p and p.exists()]

    def newest_mtime(self) -> float:
        """Latest mtime across the sources — the cache key for a report."""
        stamps = []
        for p in self.existing():
            try:
                stamps.append(p.stat().st_mtime)
            except OSError as exc:  # pragma: no cover — race with a prune
                log.debug("cost source %s not stat-able: %s", p, exc)
        return max(stamps, default=0.0)


def default_sources(data_dir: Path | None = None) -> CostSources:
    """Resolve the stores from the configured data directory.

    The data dir — not the process CWD — decides, so a second instance
    (``JARVIS_INSTANCE=dev``, which points ``memory.data_dir`` at ``data-dev/``)
    reports its own spend rather than the default app's.
    """
    root = data_dir
    if root is None:
        from jarvis.core import config as cfg

        root = Path(cfg.DATA_DIR)
    root = Path(root)
    return CostSources(
        sessions_db=root / "sessions.db",
        missions_db=root / "missions.db",
        agent_chat_db=root / "agent_chat.db",
        cli_index_dir=root,
    )


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _connect(path: Path | None) -> sqlite3.Connection | None:
    """Open a read-only connection, or ``None`` when the file is unusable."""
    if path is None or not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        log.warning("cost read model: %s not readable (%s)", path, exc)
        return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def _clip(text: object, limit: int = _LABEL_MAX) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        # Silence is right: one malformed cell in a historic row means that
        # counter is 0, not that the section fails to open. The row still
        # carries its provider, model and timestamp.
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # Silence is right for the same reason as _int: a broken price cell
        # falls back to 0.0 and is then re-derived from the rate tables.
        return 0.0


# ---------------------------------------------------------------------------
# Voice — sessions.db
# ---------------------------------------------------------------------------


def _voice_entries(path: Path | None, since_ms: int, until_ms: int) -> Iterator[CostEntry]:
    """Voice spend, at the finest granularity the store offers.

    ``voice_events`` records one ``BrainTurnCompleted`` per model call, which
    is what makes the realtime/tool split visible: a single realtime turn that
    delegates a tool call emits one event with ``realtime_usage`` (the audio
    model) and one with a normal finish reason (the text model that ran the
    tool). ``voice_turns`` only keeps the turn's winning provider, so it is
    used for the turn context — and as the fallback for turns recorded before
    the event stream existed.
    """
    conn = _connect(path)
    if conn is None:
        return
    try:
        if not _has_table(conn, "voice_turns"):
            return
        turns: dict[str, dict[str, object]] = {}
        for row in conn.execute(
            "SELECT id, session_id, started_ms, tier, provider, model, "
            "       tokens_in, tokens_out, cost_usd, user_text "
            "FROM voice_turns WHERE started_ms BETWEEN ? AND ?",
            (since_ms, until_ms),
        ):
            turns[str(row["id"])] = dict(row)

        covered: set[str] = set()
        if _has_table(conn, "voice_events"):
            for row in conn.execute(
                "SELECT turn_id, session_id, ts_ms, payload_json FROM voice_events "
                "WHERE kind = 'BrainTurnCompleted' AND ts_ms BETWEEN ? AND ?",
                (since_ms, until_ms),
            ):
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError) as exc:
                    log.debug("cost read model: unparsable voice event payload (%s)", exc)
                    continue
                if not isinstance(payload, dict):
                    continue
                turn_id = str(row["turn_id"] or "")
                turn = turns.get(turn_id)
                if turn is not None:
                    covered.add(turn_id)
                finish = str(payload.get("finish_reason") or "")
                tier = str((turn or {}).get("tier") or "")
                yield _voice_entry(
                    ts_ms=_int(row["ts_ms"]),
                    role=_voice_role(finish, tier),
                    provider=str(payload.get("provider") or ""),
                    model=str(payload.get("model") or ""),
                    tokens_in=_int(payload.get("tokens_in")),
                    tokens_out=_int(payload.get("tokens_out")),
                    recorded_usd=_float(payload.get("cost_usd")),
                    ref_id=str(row["session_id"] or ""),
                    label=_clip((turn or {}).get("user_text")),
                )

        for turn_id, turn in turns.items():
            if turn_id in covered:
                continue
            tokens = _int(turn.get("tokens_in")) + _int(turn.get("tokens_out"))
            if tokens <= 0 and _float(turn.get("cost_usd")) <= 0:
                continue
            tier = str(turn.get("tier") or "")
            yield _voice_entry(
                ts_ms=_int(turn.get("started_ms")),
                role=ROLE_REALTIME if tier == _REALTIME_TIER else ROLE_PIPELINE,
                provider=str(turn.get("provider") or ""),
                model=str(turn.get("model") or ""),
                tokens_in=_int(turn.get("tokens_in")),
                tokens_out=_int(turn.get("tokens_out")),
                recorded_usd=_float(turn.get("cost_usd")),
                ref_id=str(turn.get("session_id") or ""),
                label=_clip(turn.get("user_text")),
            )
    except sqlite3.Error as exc:
        log.warning("cost read model: voice source failed (%s)", exc)
    finally:
        conn.close()


def _voice_role(finish_reason: str, tier: str) -> str:
    """Which model in the turn spent this.

    Audio usage names itself. Anything else inside a realtime turn is the
    delegated tool model; outside one it is the pipeline brain.
    """
    if finish_reason == _REALTIME_FINISH:
        return ROLE_REALTIME
    return ROLE_TOOL if tier == _REALTIME_TIER else ROLE_PIPELINE


def _voice_entry(
    *,
    ts_ms: int,
    role: str,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    recorded_usd: float,
    ref_id: str,
    label: str,
) -> CostEntry:
    cost, source = price_entry(
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        recorded_usd=recorded_usd,
    )
    return CostEntry(
        ts_ms=ts_ms,
        surface=SURFACE_VOICE,
        role=role,
        provider=provider or "unknown",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=0,
        cost_usd=cost,
        price_source=source,
        ref_id=ref_id,
        label=label,
    )


# ---------------------------------------------------------------------------
# Agent chat — agent_chat.db
# ---------------------------------------------------------------------------

# Every runner names its usage differently; these are the buckets each key
# belongs in. Cache CREATION is billed like input (at a premium), cache READS
# are the cheap bucket — they are kept apart so the UI can show both.
_AGENT_IN_KEYS = (
    "input_tokens",
    "prompt_tokens",
    "cache_creation_input_tokens",
    "cache_write_input_tokens",
)
_AGENT_OUT_KEYS = ("output_tokens", "completion_tokens")

# Reasoning is a BREAKDOWN of the output count, not a sibling of it — both
# Anthropic and OpenAI report it inside ``output_tokens_details``. Summing it
# alongside counted those tokens twice. It stands in only when a runner reports
# no primary output count at all.
_AGENT_OUT_DETAIL_KEYS = ("reasoning_output_tokens", "thinking_tokens")

_AGENT_CACHED_KEYS = (
    "cache_read_input_tokens",
    "cached_input_tokens",
    "cache_read_tokens",
    # What the in-process API runner and the brain plugins call it.
    "cache_hit_tokens",
)


def _agent_chat_entries(path: Path | None, since_ms: int, until_ms: int) -> Iterator[CostEntry]:
    """One entry per finished agent-chat turn (Claude Code, Codex, …)."""
    conn = _connect(path)
    if conn is None:
        return
    try:
        if not _has_table(conn, "agent_chat_events"):
            return
        sessions: dict[str, sqlite3.Row] = {}
        if _has_table(conn, "agent_chat_sessions"):
            for row in conn.execute(
                "SELECT session_id, title, provider, model FROM agent_chat_sessions"
            ):
                sessions[str(row["session_id"])] = row

        # ``turn_started`` is where the runner is named, and it is the only
        # place that says whether a monthly seat or an API key answered: the
        # ``claude-api`` row means either, decided at call time. It also holds
        # the model AS IT WAS, which the session row does not — that one moves
        # with the picker and would re-label every past turn.
        #
        # Unfiltered by time on purpose: a turn that started before the window
        # and finished inside it still needs its own runner.
        starts: dict[str, dict[str, str]] = {}
        for row in conn.execute(
            "SELECT payload FROM agent_chat_events WHERE kind = 'turn_started'"
        ):
            try:
                started = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError) as exc:
                log.debug("cost read model: unparsable turn_started (%s)", exc)
                continue
            if not isinstance(started, dict):
                continue
            turn_id = str(started.get("turn_id") or "")
            if turn_id:
                starts[turn_id] = {
                    "runner": str(started.get("runner") or ""),
                    "provider": str(started.get("provider") or ""),
                    "model": str(started.get("model") or ""),
                }

        for row in conn.execute(
            "SELECT session_id, ts_ms, payload FROM agent_chat_events "
            "WHERE kind = 'turn_finished' AND ts_ms BETWEEN ? AND ?",
            (since_ms, until_ms),
        ):
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError) as exc:
                log.debug("cost read model: unparsable agent chat payload (%s)", exc)
                continue
            if not isinstance(payload, dict):
                continue
            usage = payload.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            tokens_in = sum(_int(usage.get(k)) for k in _AGENT_IN_KEYS)
            tokens_out = sum(_int(usage.get(k)) for k in _AGENT_OUT_KEYS)
            if tokens_out <= 0:
                tokens_out = sum(_int(usage.get(k)) for k in _AGENT_OUT_DETAIL_KEYS)
            tokens_cached = sum(_int(usage.get(k)) for k in _AGENT_CACHED_KEYS)
            recorded = _float(payload.get("cost_usd"))
            if tokens_in + tokens_out + tokens_cached <= 0 and recorded <= 0:
                continue
            session = sessions.get(str(row["session_id"] or ""))
            start = starts.get(str(payload.get("turn_id") or ""), {})
            session_provider = str(session["provider"] if session is not None else "")
            provider = start.get("provider") or session_provider or "agent-cli"
            model = start.get("model") or str(session["model"] if session is not None else "")
            cost, source = price_entry(
                provider=provider,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                recorded_usd=recorded,
                subscription=start.get("runner", "") in SUBSCRIPTION_RUNNERS,
                tokens_cached=tokens_cached,
            )
            yield CostEntry(
                ts_ms=_int(row["ts_ms"]),
                surface=SURFACE_AGENT_CHAT,
                role=ROLE_AGENT,
                provider=provider,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_cached=tokens_cached,
                cost_usd=cost,
                price_source=source,
                ref_id=str(row["session_id"] or ""),
                label=_clip(session["title"] if session is not None else ""),
            )
    except sqlite3.Error as exc:
        log.warning("cost read model: agent chat source failed (%s)", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Missions — missions.db
# ---------------------------------------------------------------------------


def _mission_entries(path: Path | None, since_ms: int, until_ms: int) -> Iterator[CostEntry]:
    """Autonomous worker spend, per draft the worker delivered.

    ``WorkerDraftReady`` carries the worker's own ``tokens_used`` / ``cost_usd``.
    A worker driven by a subscription CLI reports zeros — that is the honest
    number (the seat is paid monthly), and it is labelled as such rather than
    left looking like missing data.
    """
    conn = _connect(path)
    if conn is None:
        return
    try:
        if not _has_table(conn, "mission_events") or not _has_table(conn, "missions"):
            return
        prompts: dict[str, str] = {
            str(r["id"]): _clip(r["prompt"])
            for r in conn.execute("SELECT id, prompt FROM missions")
        }
        for row in conn.execute(
            "SELECT mission_id, worker_id, ts_ms, payload_json FROM mission_events "
            "WHERE event_type = 'WorkerDraftReady' AND ts_ms BETWEEN ? AND ?",
            (since_ms, until_ms),
        ):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError) as exc:
                log.debug("cost read model: unparsable mission payload (%s)", exc)
                continue
            if not isinstance(payload, dict):
                continue
            tokens = _int(payload.get("tokens_used"))
            recorded = _float(payload.get("cost_usd"))
            if tokens <= 0 and recorded <= 0:
                continue
            provider = str(payload.get("provider") or "mission-worker")
            model = str(payload.get("model") or "")
            cost, source = price_entry(
                provider=provider,
                model=model,
                tokens_in=tokens,
                tokens_out=0,
                recorded_usd=recorded,
            )
            mission_id = str(row["mission_id"] or "")
            yield CostEntry(
                ts_ms=_int(row["ts_ms"]),
                surface=SURFACE_MISSION,
                role=ROLE_WORKER,
                provider=provider,
                model=model,
                # The worker reports a single total; splitting it into a made-up
                # in/out ratio would be a lie, so it counts as input.
                tokens_in=tokens,
                tokens_out=0,
                tokens_cached=0,
                cost_usd=cost,
                price_source=source,
                ref_id=mission_id,
                label=prompts.get(mission_id, ""),
            )
    except sqlite3.Error as exc:
        log.warning("cost read model: mission source failed (%s)", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Speech — sessions.db, but nothing like the token path above
# ---------------------------------------------------------------------------


def _speech_entries(path: Path | None, since_ms: int, until_ms: int) -> Iterator[CostEntry]:
    """What a turn spent on hearing and on speaking.

    These rows carry no tokens and never will: an STT provider bills per audio
    second and a TTS provider per character. Writing those quantities into the
    token columns would make them add up with the brain's tokens, which is a
    different unit and a wrong number. They stay zero, the cost is real, and
    the quantity that WAS billed goes into the label so the line item can say
    what was actually bought.
    """
    conn = _connect(path)
    if conn is None:
        return
    try:
        if not _has_table(conn, "voice_events"):
            return
        for row in conn.execute(
            "SELECT session_id, ts_ms, payload_json FROM voice_events "
            "WHERE kind = 'SpeechUsageRecorded' AND ts_ms BETWEEN ? AND ?",
            (since_ms, until_ms),
        ):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError) as exc:
                log.debug("cost read model: unparsable speech payload (%s)", exc)
                continue
            if not isinstance(payload, dict):
                continue
            stage = str(payload.get("stage") or "")
            if stage not in (ROLE_STT, ROLE_TTS):
                continue
            chars = _int(payload.get("chars"))
            audio_ms = _float(payload.get("audio_ms"))
            if chars <= 0 and audio_ms <= 0:
                continue
            source = str(payload.get("price_source") or "unknown")
            yield CostEntry(
                ts_ms=_int(row["ts_ms"]),
                surface=SURFACE_JARVIS_VOICE,
                role=stage,
                provider=str(payload.get("provider") or "speech"),
                model=str(payload.get("voice") or ""),
                tokens_in=0,
                tokens_out=0,
                tokens_cached=0,
                cost_usd=_float(payload.get("cost_usd")),
                # The meter priced it against the vendor's own unit; re-deriving
                # it here from a token rate would be nonsense.
                price_source=source,  # type: ignore[arg-type]
                ref_id=str(row["session_id"] or ""),
                label=_speech_label(stage, chars, audio_ms),
            )
    except sqlite3.Error as exc:
        log.warning("cost read model: speech source failed (%s)", exc)
    finally:
        conn.close()


def _speech_label(stage: str, chars: int, audio_ms: float) -> str:
    """The quantity that was billed, in the unit it was billed in."""
    if stage == ROLE_TTS and chars > 0:
        return f"{chars:,} characters spoken".replace(",", " ")
    if audio_ms > 0:
        return f"{audio_ms / 1000:.1f} s heard"
    return ""


# ---------------------------------------------------------------------------
# Coding CLIs — the index, not the transcripts
# ---------------------------------------------------------------------------

def _cli_entries(
    data_dir: Path | None, since_ms: int, until_ms: int, bucket_ms: int
) -> Iterator[CostEntry]:
    """Coding agents run through a vendor CLI.

    Their transcripts are gigabytes and live outside this app, so nothing is
    parsed here: :mod:`jarvis.costs.cli_usage_index` reads each file once in
    the background and this walks the small table it leaves behind.

    Every one of these ran on a monthly seat, so the amount is what the same
    work would have cost through the API and is labelled ``subscription``
    rather than counted as money that moved.
    """
    if data_dir is None:
        return
    try:
        from .cli_usage_index import rollups as indexed_rollups
    except ImportError as exc:  # pragma: no cover — the module ships with us
        log.warning("cost read model: cli index unavailable (%s)", exc)
        return
    try:
        turns = list(
            indexed_rollups(
                data_dir=data_dir,
                since_ms=since_ms,
                until_ms=until_ms,
                bucket_ms=bucket_ms,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a broken index must not 500 the page
        log.warning("cost read model: cli index read failed (%s)", exc)
        return
    for turn in turns:
        if turn.tokens_in + turn.tokens_out + turn.tokens_cached <= 0:
            continue
        # ``claude-cli`` / ``codex-cli`` already carry their vendor in the
        # name, which is all the logo lookup needs — no translation table.
        provider = turn.agent
        cost, source = price_entry(
            provider=provider,
            model=turn.model,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            recorded_usd=0.0,
            subscription=True,
            tokens_cached=turn.tokens_cached,
        )
        yield CostEntry(
            ts_ms=turn.ts_ms,
            surface=SURFACE_AGENTIC_IDE,
            role=ROLE_AGENT,
            provider=provider,
            model=turn.model,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            tokens_cached=turn.tokens_cached,
            cost_usd=cost,
            price_source=source,
            ref_id=turn.session_id,
            label=_clip(turn.label or turn.cwd),
        )


def collect_entries(
    sources: CostSources,
    *,
    since_ms: int = 0,
    until_ms: int = 2**62,
    bucket_ms: int = 86_400_000,
) -> list[CostEntry]:
    """Every priced line item across all sources, newest last."""
    entries: list[CostEntry] = []
    entries.extend(_voice_entries(sources.sessions_db, since_ms, until_ms))
    entries.extend(_agent_chat_entries(sources.agent_chat_db, since_ms, until_ms))
    entries.extend(_mission_entries(sources.missions_db, since_ms, until_ms))
    entries.extend(_speech_entries(sources.sessions_db, since_ms, until_ms))
    entries.extend(
        _cli_entries(sources.cli_index_dir, since_ms, until_ms, bucket_ms)
    )
    entries.sort(key=lambda e: e.ts_ms)
    return entries
