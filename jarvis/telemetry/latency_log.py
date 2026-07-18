"""Per-turn JSONL latency log writer (LATENCY_REPORT_001 deliverable).

Subscribes to ``LatencyTurnComplete`` and appends one self-contained row per
voice turn to ``state/latency_log.jsonl``. The row carries the anchor (turn t0
wall clock + monotonic), every stage offset that was marked, derived metrics
(TTFW, total), and best-effort context fields (token counts, audio length, TTS
char count).

Design constraints (mirrors AP-9 / AP-18 from CLAUDE.md):
  * Subscriber callback never blocks: write is enqueued, flushed in a daemon
    thread. The hot path returns instantly.
  * Stdlib only — runs unchanged on the €5/month VPS doctrine.
  * Append-only JSONL so ``jq``, ``tail -f``, and the aggregation CLI can read
    while writes are in flight. No locking visible to readers.
  * If the file path is unwritable the writer logs once and degrades silently
    (telemetry must never break the hot path).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.core.events import LatencyTurnComplete

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

__all__ = ["LatencyLogWriter"]

logger = logging.getLogger(__name__)

# Cap the in-memory queue — under pathological pressure (writer thread
# stuck on a hung disk) we drop rather than balloon RAM. 10 000 rows is
# ~2 MB of JSONL, plenty of headroom for a normal session.
_QUEUE_CAPACITY = 10_000

# Sentinel placed on the queue to ask the flusher thread to exit.
_STOP = object()


class LatencyLogWriter:
    """Bus subscriber that materializes LatencyTurnComplete events as JSONL.

    Lifecycle::

        writer = LatencyLogWriter(Path("state/latency_log.jsonl"))
        writer.attach(bus)
        # ... voice turns happen ...
        writer.close()        # drains queue, joins thread

    The flusher runs in a dedicated daemon thread to honour the "<2 ms per
    stage" budget — the bus callback path stays free of disk I/O.
    """

    def __init__(self, log_path: Path | str) -> None:
        self._log_path = Path(log_path)
        # Parent directory creation is best-effort; if it fails we degrade
        # to "writes are dropped" rather than crashing the hot path.
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(
                "latency log dir not writable: %s", self._log_path.parent,
            )
        self._queue: queue.Queue[object] = queue.Queue(maxsize=_QUEUE_CAPACITY)
        self._thread: threading.Thread | None = None
        self._dropped = 0
        self._written = 0
        self._lock = threading.Lock()
        self._attached_bus: EventBus | None = None
        self._handler_ref = self._on_event  # keep ref so unsubscribe matches
        self._start_thread()

    @property
    def written(self) -> int:
        return self._written

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def path(self) -> Path:
        return self._log_path

    # ----------------------------------------------------------------------
    # Bus binding
    # ----------------------------------------------------------------------
    def attach(self, bus: EventBus) -> None:
        """Subscribe to ``LatencyTurnComplete`` on ``bus``. Idempotent.

        ``EventBus.subscribe`` takes a per-event-type callback; we register
        narrowly (not via ``subscribe_all``) so the wildcard FlightRecorder
        path isn't duplicated.
        """
        if self._attached_bus is bus:
            return
        self._attached_bus = bus
        bus.subscribe(LatencyTurnComplete, self._handler_ref)

    async def _on_event(self, event: LatencyTurnComplete) -> None:
        # Guard wrongly-routed events (defensive — subscribe is narrowed).
        if not isinstance(event, LatencyTurnComplete):
            return
        row = self._build_row(event)
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            self._dropped += 1
            logger.warning(
                "latency log queue full (dropped=%d) — writer thread stuck?",
                self._dropped,
            )

    # ----------------------------------------------------------------------
    # Row construction
    # ----------------------------------------------------------------------
    def _build_row(self, event: LatencyTurnComplete) -> dict[str, object]:
        stages = dict(event.stages_ms)  # defensive copy
        # Derived metrics. Use TURN_TO_FIRST_AUDIO as the canonical TTFW
        # because BRAIN_FIRST_AUDIO + ACK_FIRST_AUDIO race depending on
        # whether the ack-brain fired first.
        ttfw_ms = stages.get("turn_to_first_audio")
        total_ms = stages.get("tts_stream_done")
        durations_ms = _derive_durations(stages)
        return {
            "turn_id": event.trace_id.hex,
            "iso_timestamp": _iso(event.timestamp_ns),
            "anchor_ns": event.anchor_ns,
            "stages_ms": stages,
            "durations_ms": durations_ms,
            "ttfw_ms": ttfw_ms,
            "total_ms": total_ms,
            "stt_input_audio_ms": (
                event.stt_input_audio_ms
                if event.stt_input_audio_ms >= 0
                else None
            ),
            "brain_input_tokens": (
                event.brain_input_tokens if event.brain_input_tokens >= 0 else None
            ),
            "brain_output_tokens": (
                event.brain_output_tokens if event.brain_output_tokens >= 0 else None
            ),
            "tts_input_chars": (
                event.tts_input_chars if event.tts_input_chars >= 0 else None
            ),
            "errors": list(event.errors),
        }

    # ----------------------------------------------------------------------
    # Flusher thread
    # ----------------------------------------------------------------------
    def _start_thread(self) -> None:
        thread = threading.Thread(
            target=self._flush_loop,
            name="latency-log-writer",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _flush_loop(self) -> None:
        # Each pass writes up to N rows in one append to amortize fsync
        # cost. Small batches keep latency for the aggregation CLI low.
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            batch: list[dict[str, object]] = [item]  # type: ignore[list-item]
            # Drain anything else already queued — keep the batch under 256
            # to bound write time.
            while len(batch) < 256:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    self._write_batch(batch)
                    return
                batch.append(nxt)  # type: ignore[arg-type]
            self._write_batch(batch)

    def _write_batch(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        try:
            with self._log_path.open("a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False))
                    fh.write("\n")
            with self._lock:
                self._written += len(rows)
        except OSError as exc:
            logger.warning("latency JSONL write failed: %s", exc)

    # ----------------------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------------------
    def close(self) -> None:
        """Drain the queue and join the writer thread.

        Safe to call multiple times. After close ``attach()`` is invalid;
        construct a new writer for a second pipeline.
        """
        thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put(_STOP, timeout=1.0)
        except queue.Full:
            # Queue jammed — best-effort: thread will see _STOP eventually
            # if we put without a timeout, but we don't block on shutdown.
            logger.warning("latency log shutdown: queue full, leaving thread")
            return
        thread.join(timeout=5.0)
        self._thread = None


def _iso(ts_ns: int) -> str:
    """ns since epoch (wall clock) → ISO-8601 UTC string."""
    seconds = ts_ns / 1_000_000_000
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


# Stage pairs whose ms-difference is meaningful for the bottleneck ranking.
# Order matters: each derived duration is one row in the report table.
_DURATION_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("vad_to_stt_first", "stt_first_partial", ""),
    ("stt_streaming", "stt_finalize", "stt_first_partial"),
    ("stt_to_brain_request", "brain_request_sent", "stt_finalize"),
    ("brain_ttft", "brain_first_token", "brain_request_sent"),
    ("brain_streaming", "brain_last_token", "brain_first_token"),
    ("brain_to_tts_request", "tts_request_sent", "brain_last_token"),
    ("tts_ttfb", "tts_first_chunk", "tts_request_sent"),
    ("tts_to_audio_out", "turn_to_first_audio", "tts_first_chunk"),
    ("tts_tail", "tts_stream_done", "turn_to_first_audio"),
)


def _derive_durations(stages: dict[str, float]) -> dict[str, float | None]:
    """Per-stage deltas in ms, computed from the stage offsets."""
    out: dict[str, float | None] = {}
    for label, end_phase, start_phase in _DURATION_PAIRS:
        end = stages.get(end_phase)
        if end is None:
            out[label] = None
            continue
        if not start_phase:
            out[label] = end
            continue
        start = stages.get(start_phase)
        if start is None:
            out[label] = None
            continue
        out[label] = round(end - start, 3)
    return out
