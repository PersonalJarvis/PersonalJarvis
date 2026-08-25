"""Turn metered speech calls into one priced event per turn.

:mod:`jarvis.speech.usage_meter` reports every individual call: a streaming
reply is synthesised sentence by sentence, so a single spoken answer can be
fifteen of them. Fifteen rows a turn is exactly the kind of chatty stream the
session recorder deliberately keeps out, and none of them is interesting on
its own — what anyone wants to know is what the turn cost.

So this sums them per (stage, provider, voice) while a turn is in flight and
publishes once, priced, when the turn ends. Two rows a turn, whatever happened
inside it.

AP-9: nothing here may sit on the voice critical path. ``record`` is
arithmetic on a dict and returns; the publish is a fire-and-forget task, and
every failure is swallowed with a log — telemetry never breaks a turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from jarvis.core.events import SpeechUsageRecorded
from jarvis.costs.speech_rates import price_speech

from .usage_meter import SpeechUsage

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Bucket:
    """One (stage, provider, voice) within one turn."""

    chars: int = 0
    audio_ms: int = 0
    ts_ms: int = 0


class SpeechSpendRecorder:
    """A :class:`~jarvis.speech.usage_meter.UsageSink` that publishes per turn.

    Buckets are keyed by trace id as well, so a usage record arriving for a
    new turn flushes the previous one on its own. An explicit :meth:`flush`
    at the end of a turn is still the normal path — this is the safety net for
    a turn that ended in a way that never reached it.
    """

    #: A bucket older than this when the next call arrives belongs to an
    #: earlier moment — an announcement spoken between turns, a readback
    #: during a realtime call — and is settled on its own rather than folded
    #: into whatever turn happens to end next.
    IDLE_FLUSH_S = 20.0

    def __init__(self, bus: Any | None) -> None:
        """``bus`` is anything with an awaitable ``publish`` — typed loosely so
        this module never drags the bus implementation onto the import path."""
        self._bus = bus
        self._trace = ""
        self._buckets: dict[tuple[str, str, str], _Bucket] = {}
        self._last_record_s = 0.0

    # -- UsageSink ---------------------------------------------------------

    def record(self, usage: SpeechUsage) -> None:
        """Take one metered call. Must not block and must not raise."""
        try:
            now_s = time.monotonic()
            if self._buckets and now_s - self._last_record_s > self.IDLE_FLUSH_S:
                # Out-of-turn speech has no turn boundary to flush it; time is
                # the boundary then. Realtime calls speak all their readbacks
                # this way (trace_id is "" outside the classic pipeline).
                self.flush()
            self._last_record_s = now_s
            if usage.trace_id and usage.trace_id != self._trace:
                # A new turn began without anyone flushing the last one.
                self.flush()
                self._trace = usage.trace_id
            key = (usage.stage, usage.provider, usage.model_or_voice)
            slot = self._buckets.get(key)
            if slot is None:
                slot = _Bucket(ts_ms=usage.ts_ms or int(time.time() * 1000))
                self._buckets[key] = slot
            slot.chars += max(0, usage.chars)
            slot.audio_ms += max(0, usage.audio_ms)
        except Exception:  # noqa: BLE001 — a meter must never break a turn
            log.debug("speech usage record failed", exc_info=True)

    # -- turn boundary -----------------------------------------------------

    def flush(self) -> None:
        """Publish what this turn spent, one event per stage and provider."""
        if not self._buckets:
            return
        buckets, self._buckets = self._buckets, {}
        trace = self._trace
        for (stage, provider, voice), slot in buckets.items():
            try:
                cost, source = price_speech(
                    stage,
                    provider,
                    voice,
                    chars=slot.chars,
                    audio_ms=slot.audio_ms,
                )
                self._publish(
                    SpeechUsageRecorded(
                        trace_id=trace,
                        source_layer="speech.usage",
                        stage=stage,
                        provider=provider,
                        voice=voice,
                        chars=slot.chars,
                        audio_ms=float(slot.audio_ms),
                        cost_usd=cost,
                        price_source=source,
                    )
                )
            except Exception:  # noqa: BLE001 — see the module docstring
                log.debug("speech usage flush failed", exc_info=True)

    def _publish(self, event: SpeechUsageRecorded) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Flushed from a thread with no loop — the turn is over and there
            # is nothing to await on. Dropping one row beats raising into a
            # caller that is shutting the pipeline down.
            log.debug("speech usage: no running loop, dropping %s", event.stage)
            return
        loop.create_task(bus.publish(event))  # noqa: RUF006 — fire-and-forget


__all__ = ["SpeechSpendRecorder"]
