"""BioGenerator — AI-generated self-observation for the Board.

Brainstorm spec 2026-05-02 (ten axes):

- Voice: Jarvis as first-person narrator.
- Tone: sharp, biting with a wink.
- Format: 3-5 sentences, one paragraph, no CTA.
- Update: evolutionarily on Sundays (delta from the previous week).
- Data sources: everything Jarvis sees (Board stats + awareness episodes +
  missions + self-mod audit + previous bio + feedback vector).
- Cold-start: first quiet observation from day 1.
- Interaction: three reaction buttons (Trifft / Trifft nicht / Haerter).  # i18n-allow: literal button/API-contract label values (see kind constants below)

## Brain Provider — DYNAMIC

Multi-provider requirement (memory ``feedback_brain_providers.md`` point 6).
Instead of accepting a fixed ``brain`` parameter, the generator receives
a ``brain_resolver: Callable[[], Brain]`` — called fresh on EVERY
``generate_bio()`` call. This way a provider switch at runtime (user
switches the UI from Gemini to Claude) takes effect immediately.

Default resolver: ``jarvis.brain.resolver.resolve_frontier_brain(config)``.

## Failure modes

- ``brain_resolver()`` raises (no provider available) → empty bio,
  old one stays visible (plan §5-B "Never an empty bio").
- ``brain.complete()`` raises / timeout → empty bio.
- LLM returns empty string → empty bio.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.brain.streaming import aggregate
from jarvis.core.protocols import BrainMessage, BrainRequest

from .prompts import render_bio_prompt
from .store import BoardStore

if TYPE_CHECKING:
    from jarvis.core.protocols import Brain

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Type aliases
# ----------------------------------------------------------------------

BrainResolverFn = Callable[[], "Brain"]


# ----------------------------------------------------------------------
# Bio-Store (append-only) — with feedback methods
# ----------------------------------------------------------------------

class BioStore:
    """Read/write wrapper for the ``bio`` and ``bio_feedback`` tables.

    Append-only. Older bios are retained for history purposes; the frontend
    renders only the most recent one via ``latest()``. ``previous()`` returns
    the bio before the latest date — fed into the generator as
    weekly-delta context.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        # Schema is idempotent — runs on every connection. No DDL-lock
        # risk in WAL mode, and new tables such as bio_feedback are
        # created in old DBs without a migration.
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema)
        return conn

    def insert(
        self,
        text: str,
        *,
        model_used: str | None = None,
        triggered_by: str = "manual",
    ) -> str:
        """Writes a new bio record. Returns the ISO timestamp."""
        now_iso = datetime.now(UTC).astimezone().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO bio (generated_at, text, model_used, triggered_by) "
                "VALUES (?, ?, ?, ?)",
                (now_iso, text, model_used, triggered_by),
            )
        finally:
            conn.close()
        return now_iso

    def latest(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT generated_at, text, model_used, triggered_by "
                "FROM bio ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return dict(row)

    def previous(self) -> dict[str, Any] | None:
        """Bio before the most recent one — empty when there are 0 or 1 entries.

        Used by the generator for the weekly-delta context: the LLM can
        explicitly reference "You are still X. New this week...".
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT generated_at, text, model_used, triggered_by "
                "FROM bio ORDER BY generated_at DESC LIMIT 1 OFFSET 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return dict(row)

    def record_feedback(self, bio_generated_at: str, kind: str) -> None:
        """Writes a reaction click. ``kind`` ∈ {trifft, trifft_nicht, haerter}.  # i18n-allow: API/DB contract identifiers, matched in logic

        Validates ``kind`` in addition to the CHECK constraint in the schema,
        so the API layer receives a clean ValueError instead of a
        sqlite3.IntegrityError.
        """
        if kind not in {"trifft", "trifft_nicht", "haerter"}:  # i18n-allow: API/DB contract values (see frontend useBoard.ts), matched in logic
            raise ValueError(
                f"Invalid feedback kind: {kind!r} "
                "(allowed: trifft, trifft_nicht, haerter)"  # i18n-allow: same API/DB contract values
            )
        now_iso = datetime.now(UTC).astimezone().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO bio_feedback (bio_generated_at, kind, created_at) "
                "VALUES (?, ?, ?)",
                (bio_generated_at, kind, now_iso),
            )
        finally:
            conn.close()

    def recent_feedback(self, *, days: int = 28) -> dict[str, int]:
        """Aggregates click counts for the last ``days`` days.

        Returns a dict with three keys (``trifft``, ``trifft_nicht``, ``haerter``).  # i18n-allow: API/DB contract identifiers, matched in logic
        Missing kinds are filled with 0 so the caller can safely iterate.
        """
        cutoff = (
            datetime.now(UTC).astimezone() - timedelta(days=max(0, days))
        ).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM bio_feedback "
                "WHERE created_at >= ? GROUP BY kind",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
        result = {"trifft": 0, "trifft_nicht": 0, "haerter": 0}  # i18n-allow: API/DB contract values, matched in logic
        for row in rows:
            kind = row["kind"]
            if kind in result:
                result[kind] = int(row["n"])
        return result


# ----------------------------------------------------------------------
# BioGenerator
# ----------------------------------------------------------------------

class BioGenerator:
    """Collects data, calls the brain, and writes the bio.

    The brain is lazily resolved via ``brain_resolver`` — no hardcoded
    provider, no hardcoded model. On resolver failure or brain outage the
    old bio stays visible.

    Data source selection is optionally controllable:
      - ``recall_db_path``: SQLite with awareness_episodes table (phase A2).
      - ``missions_db_path``: SQLite with missions events (phase 6).
      - ``self_mod_log_path``: JSON-lines log (phase 7 self-mod).

    If a file does not exist, the corresponding block silently drops out of
    the prompt — no error, just less context for the LLM.
    """

    def __init__(
        self,
        *,
        brain_resolver: BrainResolverFn | None,
        store: BoardStore,
        bio_store: BioStore,
        jsonl_dir: Path | None = None,
        recall_db_path: Path | None = None,
        missions_db_path: Path | None = None,
        self_mod_log_path: Path | None = None,
        temperature: float = 0.85,
        max_tokens: int = 400,
        timeout_s: float = 30.0,
    ) -> None:
        self._resolver = brain_resolver
        self._store = store
        self._bio_store = bio_store
        self._jsonl_dir = Path(jsonl_dir) if jsonl_dir is not None else None
        self._recall_db_path = (
            Path(recall_db_path) if recall_db_path is not None else None
        )
        self._missions_db_path = (
            Path(missions_db_path) if missions_db_path is not None else None
        )
        self._self_mod_log_path = (
            Path(self_mod_log_path) if self_mod_log_path is not None else None
        )
        self._temperature = float(temperature)
        self._max_tokens = int(max_tokens)
        self._timeout_s = float(timeout_s)

    # --------------- Public API ---------------

    async def generate_bio(
        self,
        *,
        memory_text: str = "",
        soul_text: str = "",
        triggered_by: str = "manual",
        model_hint: str | None = None,
    ) -> dict[str, Any] | None:
        """Generates a new bio.

        Returns:
            dict with bio fields on success; ``None`` when the old bio
            should stay visible (brain gone, timeout, empty output).
        """
        if self._resolver is None:
            log.info("BioGenerator: no brain_resolver configured — skip")
            return None

        # Call the resolver here — provider switches at runtime take effect this way.
        try:
            brain = self._resolver()
        except Exception:  # noqa: BLE001
            log.exception(
                "BioGenerator: brain_resolver failed — keeping old bio"
            )
            return None

        facts = await asyncio.to_thread(
            self._collect_facts, memory_text=memory_text, soul_text=soul_text,
        )
        system_prompt, user_prompt = render_bio_prompt(facts)

        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user_prompt),),
            system=system_prompt,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            stream=True,
        )

        start_ns = time.time_ns()
        try:
            agg = await asyncio.wait_for(
                aggregate(brain.complete(request)),
                timeout=self._timeout_s,
            )
        except TimeoutError:
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            log.warning("BioGenerator: timeout after %dms — keeping old bio", duration_ms)
            return None
        except Exception:  # noqa: BLE001
            log.exception("BioGenerator: brain call failed — keeping old bio")
            return None

        text = _post_process(agg.text, max_words=120)  # 5 sentences ≈ < 120 words
        if not text:
            log.warning("BioGenerator: empty output — keeping old bio")
            return None

        # Resolver-provider hint for the frontend. We know the class name
        # via ``type(brain).__name__``; that is good enough for telemetry
        # and sufficient for the ``model_used`` field (no privacy issue).
        if model_hint is None:
            model_hint = type(brain).__name__

        generated_at = self._bio_store.insert(
            text,
            model_used=model_hint,
            triggered_by=triggered_by,
        )
        return {
            "text": text,
            "generated_at": generated_at,
            "triggered_by": triggered_by,
            "model_used": model_hint,
        }

    # --------------- Stats collection ---------------

    def _collect_facts(self, *, memory_text: str, soul_text: str) -> dict[str, Any]:
        """Builds the facts dict for the prompt template.

        Accesses all available data sources. Missing sources yield empty
        blocks — no crash.
        """
        summary = self._store.summary(window_days=30)
        tools = self._store.tools(window_days=30)

        window = summary["window"]
        top_tools = [entry["tool"] for entry in tools["histogram"][:5]]

        peak_hour = None
        if self._jsonl_dir is not None:
            try:
                peak_hour = _peak_hour_from_jsonl(self._jsonl_dir)
            except Exception:  # noqa: BLE001
                peak_hour = None

        days_observed = 0
        try:
            days_observed = self._store.days_observed()
        except Exception:  # noqa: BLE001
            pass

        # --- Optional data sources ---
        episodes = _safe(_load_recent_episodes, self._recall_db_path, days=7, limit=3)
        missions = _safe(_load_mission_stats, self._missions_db_path, days=30)
        self_mod = _safe(_load_self_mod_summary, self._self_mod_log_path, days=7)

        # --- Previous bio (for delta) ---
        previous_bio_text = ""
        try:
            prev = self._bio_store.previous()
            if prev:
                previous_bio_text = prev.get("text") or ""
        except Exception:  # noqa: BLE001
            pass

        # --- Feedback vector (last 4 weeks) ---
        feedback_vector: dict[str, int] = {}
        try:
            feedback_vector = self._bio_store.recent_feedback(days=28)
        except Exception:  # noqa: BLE001
            pass

        return {
            "days_observed": days_observed,
            "top_tools": top_tools,
            "tasks_completed": window.get("tasks_completed", 0),
            "tasks_failed": window.get("tasks_failed", 0),
            "voice_first_try_rate": window.get("voice_first_try_rate"),
            "peak_hour": peak_hour,
            "streak_days": summary.get("streak_days", 0),
            "memory_excerpt": memory_text,
            "soul_excerpt": soul_text,
            "episodes": episodes or [],
            "missions": missions,
            "self_mod": self_mod,
            "previous_bio": previous_bio_text,
            "feedback_vector": feedback_vector,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Calls ``fn`` and swallows every exception — returns None instead of crashing.

    Data sources can be absent (awareness off, missions never used,
    self-mod log not present). Instead of burdening every caller with
    try/except, we have a central guard here.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def _post_process(raw: str, *, max_words: int) -> str:
    """Normalises the model output to a single paragraph within a word limit."""
    if not raw:
        return ""
    text = raw.strip().strip("`").strip('"').strip()
    if "\n\n" in text:
        text = text.split("\n\n", 1)[0]
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
        if not text.endswith((".", "!", "?")):
            text = text.rstrip(",;:") + "."
    return text


def _peak_hour_from_jsonl(jsonl_dir: Path | None) -> int | None:
    """Most active hour derived from the JSONLs of the last 30 days."""
    if jsonl_dir is None or not jsonl_dir.exists():
        return None
    counts: Counter[int] = Counter()
    scanned = 0
    for path in sorted(jsonl_dir.glob("*.jsonl"))[-30:]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    scanned += 1
                    if scanned > 50_000:
                        break
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    ts_ns = int(record.get("ts_ns") or 0)
                    if ts_ns <= 0:
                        continue
                    hour = datetime.fromtimestamp(ts_ns / 1e9).astimezone().hour
                    counts[hour] += 1
        except OSError:
            continue
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _load_recent_episodes(
    db_path: Path | None,
    *,
    days: int = 7,
    limit: int = 3,
) -> list[str]:
    """Top-N awareness episodes from the last ``days`` days as summary strings.

    Reads directly from the ``awareness_episodes`` schema (see
    ``jarvis/memory/schema.sql:94``). Sorted by ``frame_count`` * duration
    as a pseudo-salience proxy — the more accurate values live in
    ``jarvis/awareness/salience.py``; a simple proxy is sufficient here.
    """
    if db_path is None or not Path(db_path).exists():
        return []
    cutoff_ns = time.time_ns() - days * 24 * 3600 * 1_000_000_000
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT summary, frame_count, ended_at_ns - started_at_ns AS duration_ns
            FROM awareness_episodes
            WHERE started_at_ns >= ?
            ORDER BY (frame_count * (ended_at_ns - started_at_ns)) DESC
            LIMIT ?
            """,
            (cutoff_ns, max(1, limit)),
        ).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for row in rows:
        summary = (row["summary"] or "").strip()
        if summary:
            out.append(summary)
    return out


def _load_mission_stats(
    db_path: Path | None,
    *,
    days: int = 30,
) -> dict[str, Any] | None:
    """Mission aggregate: approved / failed / aborted + long-open list.

    Reads the ``missions`` table (see
    ``jarvis/missions/missions_schema.sql``). Status field name per
    the phase-6 schema: ``status`` (TEXT). Returns None if the table is missing.
    """
    if db_path is None or not Path(db_path).exists():
        return None
    cutoff_ns = time.time_ns() - days * 24 * 3600 * 1_000_000_000
    overdue_cutoff_ns = time.time_ns() - 7 * 24 * 3600 * 1_000_000_000
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Does the table exist?
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missions'"
        ).fetchone()
        if tbl is None:
            return None
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM missions WHERE ts_ns >= ? GROUP BY status",
            (cutoff_ns,),
        ).fetchall()
        approved = failed = aborted = 0
        for row in rows:
            status = (row["status"] or "").lower()
            n = int(row["n"])
            if "approve" in status or "complete" in status or "success" in status:
                approved += n
            elif "fail" in status or "error" in status:
                failed += n
            elif "abort" in status or "cancel" in status:
                aborted += n

        overdue_rows = conn.execute(
            "SELECT title FROM missions WHERE ts_ns < ? AND status IN ('open','running','pending') "
            "ORDER BY ts_ns ASC LIMIT 3",
            (overdue_cutoff_ns,),
        ).fetchall()
        overdue = [str(r["title"] or "")[:80] for r in overdue_rows if r["title"]]
    finally:
        conn.close()
    if approved + failed + aborted == 0 and not overdue:
        return None
    return {
        "approved": approved,
        "failed": failed,
        "aborted": aborted,
        "open_overdue": overdue,
    }


def _load_self_mod_summary(
    log_path: Path | None,
    *,
    days: int = 7,
) -> dict[str, int] | None:
    """Aggregates config mutations from the self-mod audit log.

    JSON-lines with ``timestamp``, ``path``, ``operator``, ... (see
    ``jarvis/core/self_mod/audit.py``). We read ONLY the paths — values
    are redacted and must not appear in a bio prompt.
    """
    if log_path is None or not Path(log_path).exists():
        return None
    cutoff = datetime.now(UTC).astimezone() - timedelta(days=max(1, days))
    counts: Counter[str] = Counter()
    try:
        with Path(log_path).open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = entry.get("timestamp") or ""
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                path = str(entry.get("path") or "").strip()
                if path:
                    counts[path] += 1
    except OSError:
        return None
    return dict(counts) if counts else None


# ----------------------------------------------------------------------
# Backward-compat — for tests/callers that expect a ``brain`` parameter
# ----------------------------------------------------------------------

def make_resolver_from_brain(brain: Any) -> BrainResolverFn:
    """Wraps an existing brain object in a resolver.

    Allows tests and callers to pass a fake brain directly without going
    through the full ``resolve_frontier_brain`` path. Production should
    use ``resolve_frontier_brain`` so that provider switches take effect.
    """
    if brain is None:
        raise ValueError("make_resolver_from_brain: brain must not be None")

    def _resolver() -> Any:
        return brain

    return _resolver
