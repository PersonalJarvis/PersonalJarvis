"""Verdichter — direct Brain call (no Heavy-Worker spawn).

Plan §6 Hard Negative: NEVER via ``spawn_worker`` (Wave-4 rebrand —
previously ``spawn_sub_jarvis``). Verdichter obtains its Brain instance
directly from the ``BrainProviderRegistry`` (instantiated in
``factory.py``) and calls ``brain.complete(req)``.
Own timeout (5s default), own token caps.

Contract::

    summary, usage = await verdichter.call(
        frames=[{"timestamp_ns": ..., "process_name": ..., "window_title": ...}, ...],
        events=[{"ts_ns": ..., "kind": ..., "payload": {...}}, ...],
        primary_app="Code.exe",
    )

On empty input / timeout / brain error: ``summary == ""`` and
``usage["error_reason"]`` is set — the caller then persists the episode
with an empty summary instead of crashing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from jarvis.awareness.config import AwarenessVerdichterConfig
from jarvis.awareness.prompts import VERDICHTER_SYSTEM_PROMPT, build_verdichter_prompt
from jarvis.brain.streaming import aggregate
from jarvis.core.protocols import BrainMessage, BrainRequest

if TYPE_CHECKING:
    from jarvis.core.protocols import Brain

logger = logging.getLogger(__name__)

# Plan §6 Hard Negative: max 30 frames+events per call.
# On overflow: keep the NEWEST ones (chronological tail).
MAX_FRAMES_PLUS_EVENTS = 30


def _ts(item: dict[str, Any]) -> int:
    """Extract timestamp from a frame or event — both schemas accepted."""
    return int(item.get("timestamp_ns") or item.get("ts_ns") or 0)


class Verdichter:
    """Direct Brain call (no OpenClaw spawn).

    Stateless — no bus subscription, no internal counters. One instance
    is constructed in ``factory.py`` and passed to the ``StoryTracker``.
    """

    def __init__(
        self,
        *,
        brain: Brain,
        config: AwarenessVerdichterConfig,
    ) -> None:
        self._brain = brain
        self._config = config

    async def call(
        self,
        *,
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
        primary_app: str,
    ) -> tuple[str, dict[str, Any]]:
        """Synthesise an episode summary from frames and events.

        Returns:
            ``(summary_text, usage_dict)``. ``usage_dict`` contains::

                {
                    "tokens_in": int,
                    "tokens_out": int,
                    "duration_ms": int,
                    "error_reason": str | None,
                }

            Failure modes:
            - Empty input → ``("", {..., "error_reason": "empty_input"})``
            - Timeout (asyncio.TimeoutError) → ``("", {..., "error_reason": "timeout"})``
            - Brain exception → ``("", {..., "error_reason": str(exc)[:200]})``
        """
        # Empty-input gate (hard-cap required by Plan §6 AC).
        if not frames and not events:
            return "", {
                "tokens_in": 0,
                "tokens_out": 0,
                "duration_ms": 0,
                "error_reason": "empty_input",
            }

        # Hard cap: max 30 frames+events. Keep the NEWEST ones (tail).
        # Sort by timestamp ascending, then take the last N.
        frames, events = self._cap_to_max(frames, events)

        prompt = build_verdichter_prompt(
            frames=frames,
            events=events,
            primary_app=primary_app,
            max_chars=self._config.max_input_tokens * 4,
        )
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=prompt),),
            system=VERDICHTER_SYSTEM_PROMPT,
            max_tokens=self._config.max_output_tokens,
            temperature=0.5,    # less creative, more factual
            stream=True,
        )

        start_ns = time.time_ns()
        try:
            agg = await asyncio.wait_for(
                aggregate(self._brain.complete(request)),
                timeout=self._config.timeout_s,
            )
        except TimeoutError:
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            logger.warning(
                "Verdichter timeout after %dms (config.timeout_s=%.1f)",
                duration_ms, self._config.timeout_s,
            )
            return "", {
                "tokens_in": 0,
                "tokens_out": 0,
                "duration_ms": int(duration_ms),
                "error_reason": "timeout",
            }
        except Exception as exc:    # noqa: BLE001
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            logger.warning("Verdichter brain-call failed: %s", exc)
            return "", {
                "tokens_in": 0,
                "tokens_out": 0,
                "duration_ms": int(duration_ms),
                "error_reason": str(exc)[:200],
            }

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        return agg.text, {
            "tokens_in": int(agg.usage.get("input_tokens", 0)),
            "tokens_out": int(agg.usage.get("output_tokens", 0)),
            "duration_ms": int(duration_ms),
            "error_reason": None,
        }

    @staticmethod
    def _cap_to_max(
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """If ``len(frames)+len(events) > 30``: keep the newest 30.

        Strategy: merge all items with a type marker, sort by timestamp
        ascending, take the last ``MAX_FRAMES_PLUS_EVENTS``, then split
        back into frames and events.
        """
        total = len(frames) + len(events)
        if total <= MAX_FRAMES_PLUS_EVENTS:
            return frames, events

        annotated: list[tuple[int, str, dict[str, Any]]] = []
        for f in frames:
            annotated.append((_ts(f), "frame", f))
        for e in events:
            annotated.append((_ts(e), "event", e))
        annotated.sort(key=lambda x: x[0])
        tail = annotated[-MAX_FRAMES_PLUS_EVENTS:]

        kept_frames = [item for _, kind, item in tail if kind == "frame"]
        kept_events = [item for _, kind, item in tail if kind == "event"]
        logger.debug(
            "Verdichter capped %d -> %d (frames=%d, events=%d)",
            total, len(tail), len(kept_frames), len(kept_events),
        )
        return kept_frames, kept_events
