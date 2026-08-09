"""Release gate for realtime reliability and measured latency.

Two deliberately separate checks live here:

* ``--contract-soak`` drives the production ``RealtimeVoiceSession`` through
  an in-memory provider.  It proves provider-neutral lifecycle, barge-in,
  failback, and exactly-once PCM surface handoff without a microphone, network,
  account, or model.
* ``--report`` validates the schema and numerical thresholds of a report
  labelled as an instrumented local-inference target. Contract-mode output is
  schema-separated; authenticity still requires workflow-owned collection.

Physical microphone/acoustic-echo checks and macOS TCC permission prompts are
manual release sign-offs.  Neither mode claims to automate them.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

SCHEMA_VERSION = 2
MIN_WARM_CONNECTS = 100
MIN_TURNS = 500
MIN_BARGE_SAMPLES = 20
MIN_FAILBACK_SAMPLES = 20
MIN_REGRESSION_SAMPLES = 50

TTFA_P50_LIMIT_MS = 800.0
TTFA_P95_LIMIT_MS = 1_200.0
BARGE_STOP_P95_LIMIT_MS = 250.0
FAILBACK_MAX_LIMIT_MS = 2_000.0
UNAFFECTED_REGRESSION_MAX_MS = 50.0

CANONICAL_TTFA_FIELD = "first_final_to_first_audio_ms"
CANONICAL_COLLECTOR = "jarvis-realtime-target-collector"
CANONICAL_COLLECTOR_VERSION = 1
CANONICAL_EVENT_SOURCE = "RealtimeSessionPostmortem.first_final_to_first_audio_ms"
CANONICAL_CLOCK = "time.perf_counter_ns"
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REPORT_PLATFORMS = frozenset({"windows", "macos", "linux"})
_REPORT_ARCHITECTURES = frozenset({"x86_64", "arm64"})

REQUIRED_SCENARIOS = frozenset(
    {
        "three_turns_de",
        "barge_in_followup",
        "delegation",
        "detached_delegate_delivery",
        "provider_replacement",
        "classic_delivery",
    }
)


@dataclass(frozen=True, slots=True)
class GateResult:
    """One content-free release-gate verdict."""

    failures: tuple[str, ...]
    warm_connects: int = 0
    turns: int = 0
    ttfa_p50_ms: float = 0.0
    ttfa_p95_ms: float = 0.0
    barge_stop_p95_ms: float = 0.0
    failback_max_ms: float = 0.0
    unaffected_regression_max_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return a linearly interpolated percentile over finite measurements."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _measurements(
    report: Mapping[str, Any],
    key: str,
    failures: list[str],
    *,
    minimum: int,
    allow_negative: bool = False,
) -> list[float]:
    raw = report.get(key)
    if not isinstance(raw, list):
        failures.append(f"{key} must be a JSON array")
        return []
    values: list[float] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool):
            failures.append(f"{key}[{index}] is not a numeric measurement")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            failures.append(f"{key}[{index}] is not a numeric measurement")
            continue
        if not math.isfinite(number):
            failures.append(f"{key}[{index}] must be finite")
            continue
        if not allow_negative and number < 0.0:
            failures.append(f"{key}[{index}] must be non-negative")
            continue
        values.append(number)
    if len(values) < minimum:
        failures.append(f"{key} has {len(values)} samples; need at least {minimum}")
    return values


def report_evidence_sha256(report: Mapping[str, Any]) -> str:
    """Detect mismatches between provenance and canonical report fields.

    The collector writes this digest after capturing raw production
    ``RealtimeSessionPostmortem`` values. This unkeyed digest is an integrity
    check, not proof that measurements came from a real target; authenticity
    requires workflow-owned same-SHA collection plus manual acoustic sign-off.
    """
    payload = {
        "schema_version": report.get("schema_version"),
        "commit_sha": report.get("commit_sha"),
        "target": report.get("target"),
        "capabilities": report.get("capabilities"),
        CANONICAL_TTFA_FIELD: report.get(CANONICAL_TTFA_FIELD),
        "barge_stop_ms": report.get("barge_stop_ms"),
        "failback_ms": report.get("failback_ms"),
        "unaffected_turn_regression_ms": report.get(
            "unaffected_turn_regression_ms"
        ),
        "turns_completed": report.get("turns_completed"),
        "orphan_deliveries": report.get("orphan_deliveries"),
        "duplicate_deliveries": report.get("duplicate_deliveries"),
        "scenario_results": report.get("scenario_results"),
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical).hexdigest()


def _utc_timestamp(value: Any, key: str, failures: list[str]) -> datetime | None:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        failures.append(f"provenance.{key} must be an ISO-8601 UTC timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        failures.append(f"provenance.{key} must include the UTC offset")
        return None
    return parsed


def _validate_provenance(report: Mapping[str, Any], failures: list[str]) -> None:
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        failures.append("provenance must be a JSON object from the target collector")
        return
    expected = {
        "collector": CANONICAL_COLLECTOR,
        "collector_version": CANONICAL_COLLECTOR_VERSION,
        "event_source": CANONICAL_EVENT_SOURCE,
        "clock": CANONICAL_CLOCK,
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            failures.append(f"provenance.{key} must be {value!r}")
    try:
        UUID(str(provenance.get("measurement_id", "")))
    except (ValueError, AttributeError):
        failures.append("provenance.measurement_id must be a UUID")
    started = _utc_timestamp(provenance.get("started_at"), "started_at", failures)
    completed = _utc_timestamp(provenance.get("completed_at"), "completed_at", failures)
    if started is not None and completed is not None:
        if completed <= started:
            failures.append("provenance.completed_at must be after started_at")
        elif (completed - started).total_seconds() > 86_400:
            failures.append("provenance measurement window must not exceed 24 hours")
    evidence = str(provenance.get("evidence_sha256", "") or "").strip().lower()
    expected_evidence = report_evidence_sha256(report)
    if not expected_evidence or evidence != expected_evidence:
        failures.append("provenance.evidence_sha256 does not match the report evidence")


def evaluate_report(
    report: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_platform: str,
    expected_architecture: str,
) -> GateResult:
    """Validate one candidate target report against the numerical SLOs."""
    failures: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append(
            f"schema_version must be {SCHEMA_VERSION}, got {report.get('schema_version')!r}"
        )
    if report.get("measurement_mode") != "instrumented_target":
        failures.append(
            "measurement_mode must be 'instrumented_target'; synthetic or "
            "contract-only measurements cannot release the realtime path"
        )

    capabilities = report.get("capabilities")
    if not isinstance(capabilities, Mapping) or capabilities.get("local_inference") is not True:
        failures.append("capabilities.local_inference must be true")

    expected_sha = str(expected_sha or "").strip().lower()
    commit_sha = str(report.get("commit_sha", "") or "").strip().lower()
    if _FULL_SHA_RE.fullmatch(expected_sha) is None:
        failures.append("expected_sha must be a full 40-character Git commit SHA")
    if commit_sha != expected_sha:
        failures.append(
            f"commit_sha {commit_sha!r} does not match expected release SHA {expected_sha!r}"
        )

    expected_platform = str(expected_platform or "").strip().lower()
    expected_architecture = str(expected_architecture or "").strip().lower()
    if expected_platform not in _REPORT_PLATFORMS:
        failures.append(f"unsupported expected platform {expected_platform!r}")
    if expected_architecture not in _REPORT_ARCHITECTURES:
        failures.append(f"unsupported expected architecture {expected_architecture!r}")
    target = report.get("target")
    if not isinstance(target, Mapping):
        failures.append("target must identify the measured platform and architecture")
    else:
        if target.get("platform") != expected_platform:
            failures.append(
                f"target.platform must be {expected_platform!r}, got "
                f"{target.get('platform')!r}"
            )
        if target.get("architecture") != expected_architecture:
            failures.append(
                f"target.architecture must be {expected_architecture!r}, got "
                f"{target.get('architecture')!r}"
            )

    _validate_provenance(report, failures)

    ttfa = _measurements(
        report,
        CANONICAL_TTFA_FIELD,
        failures,
        minimum=MIN_WARM_CONNECTS,
    )
    barge = _measurements(
        report,
        "barge_stop_ms",
        failures,
        minimum=MIN_BARGE_SAMPLES,
    )
    failback = _measurements(
        report,
        "failback_ms",
        failures,
        minimum=MIN_FAILBACK_SAMPLES,
    )
    regression = _measurements(
        report,
        "unaffected_turn_regression_ms",
        failures,
        minimum=MIN_REGRESSION_SAMPLES,
        allow_negative=True,
    )

    raw_turns = report.get("turns_completed", 0)
    if isinstance(raw_turns, bool) or not isinstance(raw_turns, int):
        turns = 0
        failures.append("turns_completed must be an integer")
    else:
        turns = raw_turns
    if turns < MIN_TURNS:
        failures.append(f"turns_completed is {turns}; need at least {MIN_TURNS}")

    for key in ("orphan_deliveries", "duplicate_deliveries"):
        raw_value = report.get(key, -1)
        value = raw_value if isinstance(raw_value, int) and not isinstance(raw_value, bool) else -1
        if value != 0:
            failures.append(f"{key} must be exactly 0, got {report.get(key)!r}")

    scenarios = report.get("scenario_results")
    if not isinstance(scenarios, Mapping):
        failures.append("scenario_results must map scenario names to booleans")
    else:
        missing = sorted(REQUIRED_SCENARIOS - set(scenarios))
        if missing:
            failures.append(f"scenario_results missing: {', '.join(missing)}")
        failed = sorted(name for name in REQUIRED_SCENARIOS if scenarios.get(name) is not True)
        if failed:
            failures.append(f"scenario_results failed: {', '.join(failed)}")

    ttfa_p50 = _percentile(ttfa, 50.0)
    ttfa_p95 = _percentile(ttfa, 95.0)
    barge_p95 = _percentile(barge, 95.0)
    failback_max = max(failback, default=0.0)
    regression_max = max(regression, default=0.0)

    if ttfa and ttfa_p50 > TTFA_P50_LIMIT_MS:
        failures.append(f"warm TTFA p50 {ttfa_p50:.1f}ms exceeds {TTFA_P50_LIMIT_MS:.0f}ms")
    if ttfa and ttfa_p95 > TTFA_P95_LIMIT_MS:
        failures.append(f"warm TTFA p95 {ttfa_p95:.1f}ms exceeds {TTFA_P95_LIMIT_MS:.0f}ms")
    if barge and barge_p95 > BARGE_STOP_P95_LIMIT_MS:
        failures.append(f"barge stop p95 {barge_p95:.1f}ms exceeds {BARGE_STOP_P95_LIMIT_MS:.0f}ms")
    if failback and failback_max > FAILBACK_MAX_LIMIT_MS:
        failures.append(f"failback max {failback_max:.1f}ms exceeds {FAILBACK_MAX_LIMIT_MS:.0f}ms")
    if regression and regression_max > UNAFFECTED_REGRESSION_MAX_MS:
        failures.append(
            f"unaffected-turn regression max {regression_max:.1f}ms exceeds "
            f"{UNAFFECTED_REGRESSION_MAX_MS:.0f}ms"
        )

    return GateResult(
        failures=tuple(failures),
        warm_connects=len(ttfa),
        turns=turns,
        ttfa_p50_ms=ttfa_p50,
        ttfa_p95_ms=ttfa_p95,
        barge_stop_p95_ms=barge_p95,
        failback_max_ms=failback_max,
        unaffected_regression_max_ms=regression_max,
    )


class _Surface:
    """In-memory desktop surface used only by the contract soak."""

    def __init__(self) -> None:
        self.json: list[tuple[float, dict[str, Any]]] = []
        self.binary_count = 0
        self.binary_tick = asyncio.Event()
        self.json_tick = asyncio.Event()

    async def send_binary(self, data: bytes) -> None:
        if data:
            self.binary_count += 1
        self.binary_tick.set()

    async def send_json(self, message: dict[str, Any]) -> None:
        self.json.append((time.perf_counter(), dict(message)))
        self.json_tick.set()

    def mark(self) -> int:
        return len(self.json)

    async def wait_json(
        self,
        predicate: Any,
        *,
        since: int,
        timeout_s: float = 3.0,
    ) -> tuple[float, dict[str, Any]]:
        async def _wait() -> tuple[float, dict[str, Any]]:
            while True:
                self.json_tick.clear()
                for row in self.json[since:]:
                    if predicate(row[1]):
                        return row
                await self.json_tick.wait()

        return await asyncio.wait_for(_wait(), timeout=timeout_s)


class _FiniteWire:
    session_id = "reliability-finite"
    creates_responses_automatically = False
    isolates_response_generations = False
    supports_tool_updates = True

    def __init__(self, connection_index: int, turns: int) -> None:
        self._connection_index = connection_index
        self._turns = turns

    async def receive(self):  # noqa: ANN201 - protocol async generator
        from jarvis.core.protocols import AudioChunk
        from jarvis.realtime.protocol import RealtimeEvent

        for turn in range(self._turns):
            turn_id = f"c{self._connection_index}-t{turn}"
            yield RealtimeEvent(
                type="input_transcript",
                text=f"Question number {turn}",
                is_final=True,
                item_id=turn_id,
            )
            yield RealtimeEvent(
                type="output_transcript_delta",
                text=f"Answer number {turn} completed safely.",
            )
            yield RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x10\x01" * 240,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            )
            yield RealtimeEvent(type="turn_complete")
            await asyncio.sleep(0)

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_text(self, text: str) -> None:
        del text

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self, **kwargs: Any) -> None:
        del kwargs

    async def send_tool_result(self, *args: Any) -> None:
        del args

    async def close(self) -> None:
        return None


class _FiniteProvider:
    supports_realtime = True
    input_sample_rate = 24_000
    output_sample_rate = 24_000

    def __init__(
        self,
        turns_per_connection: Sequence[int],
        *,
        name: str = "reliability-contract",
    ) -> None:
        self._turns = list(turns_per_connection)
        self.name = name
        self.open_count = 0

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _FiniteWire:
        del config
        if self.open_count >= len(self._turns):
            raise RuntimeError("contract provider opened more sessions than planned")
        wire = _FiniteWire(self.open_count, self._turns[self.open_count])
        self.open_count += 1
        return wire


_QUEUE_END = object()


class _QueueWire:
    session_id = "reliability-queue"
    supports_tool_updates = False
    rebuild_on_transport_death = True

    def __init__(
        self,
        *,
        creates_responses_automatically: bool = True,
        supports_direct_tools: bool = False,
    ) -> None:
        self.creates_responses_automatically = creates_responses_automatically
        self.isolates_response_generations = creates_responses_automatically
        self.supports_direct_tools = supports_direct_tools
        self.direct_speech_is_authoritative = not supports_direct_tools
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self.interrupts = 0
        self.closed = False

    def push(self, *events: Any) -> None:
        for event in events:
            self._events.put_nowait(event)

    async def receive(self):  # noqa: ANN201 - protocol async generator
        while True:
            event = await self._events.get()
            if event is _QUEUE_END:
                return
            yield event

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_text(self, text: str) -> None:
        del text

    async def send_speech(self, text: str) -> None:
        del text

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self, **kwargs: Any) -> None:
        del kwargs
        self.interrupts += 1

    async def send_tool_result(self, *args: Any) -> None:
        del args

    def diagnostics(self) -> dict[str, int]:
        return {}

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._events.put_nowait(_QUEUE_END)


class _QueueProvider:
    supports_realtime = True
    input_sample_rate = 24_000
    output_sample_rate = 24_000

    def __init__(
        self,
        *,
        name: str = "reliability-contract",
        creates_responses_automatically: bool = True,
        supports_direct_tools: bool = False,
    ) -> None:
        self.name = name
        self.creates_responses_automatically = creates_responses_automatically
        self.supports_direct_tools = supports_direct_tools
        self.sessions: list[_QueueWire] = []

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _QueueWire:
        del config
        wire = _QueueWire(
            creates_responses_automatically=self.creates_responses_automatically,
            supports_direct_tools=self.supports_direct_tools,
        )
        self.sessions.append(wire)
        return wire


class _UnavailableProvider:
    """One capability-present family whose handshake fails deterministically."""

    name = "unavailable-contract-family"
    supports_realtime = True
    supports_direct_tools = True
    input_sample_rate = 24_000
    output_sample_rate = 24_000

    def __init__(self) -> None:
        self.open_count = 0

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> Any:
        del config
        self.open_count += 1
        raise RuntimeError("contract transport unavailable")


def _session_config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


async def _open_session(
    provider: Any | None,
    surface: _Surface,
    *,
    session_id: str,
    providers: Sequence[Any] | None = None,
    brain: Any | None = None,
    bus: Any | None = None,
) -> tuple[Any, list[Any]]:
    from jarvis.core.bus import EventBus
    from jarvis.core.events import RealtimeSessionPostmortem
    from jarvis.realtime.session import RealtimeVoiceSession

    bus = bus or EventBus()
    postmortems: list[Any] = []

    async def _capture(event: RealtimeSessionPostmortem) -> None:
        postmortems.append(event)

    bus.subscribe(RealtimeSessionPostmortem, _capture)
    session = RealtimeVoiceSession(
        session_id=session_id,
        send_binary=surface.send_binary,
        send_json=surface.send_json,
        provider=provider,
        providers=list(providers) if providers is not None else None,
        config=_session_config(),
        bus=bus,
        brain=brain,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=24_000,
    )
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": 24_000}),
        timeout=5.0,
    )
    return session, postmortems


async def _run_finite_connection(
    provider: _FiniteProvider,
    *,
    connection_index: int,
    expected_turns: int,
) -> tuple[Any, _Surface]:
    surface = _Surface()
    session, postmortems = await _open_session(
        provider,
        surface,
        session_id=f"reliability-finite-{connection_index}",
    )
    await asyncio.wait_for(session.wait_finished(), timeout=5.0)
    await asyncio.wait_for(session.end(reason="contract-soak"), timeout=5.0)
    if len(postmortems) != 1:
        raise AssertionError("each connection must publish exactly one postmortem")
    postmortem = postmortems[0]
    completions = sum(message.get("type") == "turn_complete" for _, message in surface.json)
    errors = [message for _, message in surface.json if message.get("type") == "provider_error"]
    if postmortem.turns_completed != expected_turns:
        raise AssertionError(
            f"connection completed {postmortem.turns_completed} turns; expected {expected_turns}"
        )
    if completions != expected_turns or surface.binary_count != expected_turns:
        raise AssertionError(
            "one logical turn must produce exactly one completion and one PCM surface handoff"
        )
    if errors or postmortem.unsafe_output_cancellations:
        raise AssertionError("a healthy contract turn was cancelled or errored")
    return postmortem, surface


async def _barge_sample(index: int) -> float:
    from jarvis.core.protocols import AudioChunk
    from jarvis.realtime.protocol import RealtimeEvent

    provider = _QueueProvider()
    surface = _Surface()
    session, _postmortems = await _open_session(
        provider,
        surface,
        session_id=f"reliability-barge-{index}",
    )
    try:
        wire = provider.sessions[0]
        wire.push(
            RealtimeEvent(
                type="input_transcript",
                text=(
                    "Please explain how the settings work and what I should do "
                    "next in clear English."
                ),
                is_final=True,
                item_id=f"barge-{index}",
            ),
            RealtimeEvent(
                type="output_transcript_delta",
                text=(
                    "Here is a clear explanation of how the settings work and "
                    "what you should do next."
                ),
            ),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=b"\x10\x01" * 240,
                    sample_rate=24_000,
                    timestamp_ns=0,
                ),
            ),
        )
        await asyncio.wait_for(surface.binary_tick.wait(), timeout=3.0)
        mark = surface.mark()
        started = time.perf_counter()
        await session.handle_control({"type": "barge_in"})
        stopped_at, _ = await surface.wait_json(
            lambda message: message.get("type") == "tts_cancel",
            since=mark,
        )
        if wire.interrupts != 1:
            raise AssertionError("one barge-in must interrupt the active provider once")
        return (stopped_at - started) * 1_000.0
    finally:
        await asyncio.wait_for(session.end(reason="contract-soak"), timeout=5.0)


async def _failback_sample(index: int) -> float:
    from jarvis.realtime.protocol import RealtimeEvent

    provider = _QueueProvider()
    surface = _Surface()
    session, _postmortems = await _open_session(
        provider,
        surface,
        session_id=f"reliability-failback-{index}",
    )
    try:
        first = provider.sessions[0]
        mark = surface.mark()
        started = time.perf_counter()
        first.push(
            RealtimeEvent(
                type="error",
                error="transport died",
                recoverable=True,
                reconnect_advised=True,
            ),
            RealtimeEvent(type="turn_complete"),
        )
        ready_at, _ = await surface.wait_json(
            lambda message: message.get("type") == "audio_ready",
            since=mark,
        )
        if len(provider.sessions) != 2:
            raise AssertionError("failback must open exactly one replacement transport")
        return (ready_at - started) * 1_000.0
    finally:
        await asyncio.wait_for(session.end(reason="contract-soak"), timeout=5.0)


async def _wait_until(predicate: Any, *, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("contract scenario did not reach its expected state")
        await asyncio.sleep(0.01)


async def _detached_delegate_delivery_scenario() -> dict[str, int]:
    """Prove an accepted action survives teardown and is delivered once."""
    from jarvis.core.bus import EventBus
    from jarvis.core.events import AnnouncementRequested
    from jarvis.realtime.protocol import RealtimeEvent

    class _BlockingBrain:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, text: str) -> str:
            return await self.generate(text)

        async def generate(self, text: str, **kwargs: Any) -> str:
            del kwargs
            self.calls.append(text)
            self.started.set()
            await self.release.wait()
            return "The requested wiki update completed successfully."

    brain = _BlockingBrain()
    bus = EventBus()
    announcements: list[Any] = []

    async def _capture(event: AnnouncementRequested) -> None:
        if event.kind == "completion":
            announcements.append(event)

    bus.subscribe(AnnouncementRequested, _capture)
    provider = _QueueProvider(
        name="delegate-contract-family",
        creates_responses_automatically=False,
        supports_direct_tools=True,
    )
    surface = _Surface()
    session, _postmortems = await _open_session(
        provider,
        surface,
        session_id="reliability-detached-delegate",
        brain=brain,
        bus=bus,
    )
    provider.sessions[0].push(
        RealtimeEvent(
            type="input_transcript",
            text="Write the current release status to my wiki page.",
            is_final=True,
            item_id="detached-delegate-turn",
        )
    )
    await asyncio.wait_for(brain.started.wait(), timeout=5.0)
    turn_id, turn_state = next(iter(session._delegate_turns.items()))  # noqa: SLF001

    await asyncio.wait_for(session.end(reason="contract-detach"), timeout=5.0)
    if brain.calls != ["Write the current release status to my wiki page."]:
        raise AssertionError("the detached action must be dispatched exactly once")

    brain.release.set()
    await _wait_until(
        lambda: (
            len(announcements) == 1
            and turn_state.result_complete
            and turn_state.delivery_completed
        )
    )
    if not turn_state.result_complete or not turn_state.delivery_completed:
        raise AssertionError("the detached result must remain complete and delivered")
    if turn_state.delivery_channel != "detached" or not turn_state.result_payload:
        raise AssertionError("the retained result must use detached delivery state")
    if await session._deliver_detached_delegate_result(  # noqa: SLF001
        turn_id,
        turn_state,
    ):
        raise AssertionError("a duplicate detached callback must not redeliver")
    if len(announcements) != 1:
        raise AssertionError("the detached result reached the surface more than once")
    return {
        "detached_delegate_deliveries": 1,
        "delegate_duplicates_suppressed": 1,
    }


async def _provider_replacement_scenario() -> dict[str, int]:
    """Cross from a failed provider family to one healthy alternate."""
    primary = _UnavailableProvider()
    alternate = _FiniteProvider([1], name="replacement-contract-family")
    surface = _Surface()
    session, postmortems = await _open_session(
        None,
        surface,
        session_id="reliability-provider-replacement",
        providers=[primary, alternate],
    )
    await asyncio.wait_for(session.wait_finished(), timeout=5.0)
    await asyncio.wait_for(session.end(reason="contract-soak"), timeout=5.0)
    fallback_frames = [
        message
        for _, message in surface.json
        if message.get("type") == "provider_fallback"
    ]
    completions = sum(
        message.get("type") == "turn_complete" for _, message in surface.json
    )
    if primary.open_count != 1 or alternate.open_count != 1:
        raise AssertionError("provider replacement must try each family exactly once")
    if len(fallback_frames) != 1 or completions != 1 or surface.binary_count != 1:
        raise AssertionError("the replacement provider must deliver the turn exactly once")
    if len(postmortems) != 1 or postmortems[0].turns_completed != 1:
        raise AssertionError("provider replacement must retain one completed turn")
    return {"provider_replacements": 1}


async def _classic_delivery_scenario() -> dict[str, int]:
    """Route one retained opening through the production Classic dispatcher."""
    import jarvis.speech.pipeline as pipeline_mod
    from jarvis.core.protocols import AudioChunk
    from jarvis.speech.pipeline import SpeechPipeline

    class _OneShotVad:
        def utterances(self, stream: Any) -> Any:
            async def _one() -> Any:
                async for chunk in stream:
                    yield chunk.pcm
                    return

            return _one()

    # This contract intentionally assembles a minimal pipeline without running
    # its hardware-owning constructor; ``Any`` keeps the injected test seams
    # explicit without pretending this partial object is a fully typed instance.
    pipe: Any = SpeechPipeline.__new__(SpeechPipeline)
    pipe._config = SimpleNamespace(
        voice=SimpleNamespace(mode="realtime", model_fields_set=set()),
        brain=SimpleNamespace(reply_language="en"),
    )
    pipe._ptt_mode = False
    pipe._hangup_event = asyncio.Event()
    pipe._vad = _OneShotVad()
    pipe._idle_timeout_s = 1.0
    pipe._idle_hangup_enabled = True
    pipe._session_end_reason = None
    pipe._carry_pcm = bytearray()
    pipe._carry_started_monotonic = None
    pipe._last_endpoint_reason = None
    pipe._last_announcement_spoken_monotonic = None
    pipe._last_answer_floor_monotonic = None
    pipe._active_voice_mode = "realtime"
    pipe._active_realtime_provider = ""
    pipe._active_realtime_model = ""
    pipe._voice_engine_transitioning = False
    pipe._muted = False
    pipe._input_suppressed_until_ns = 0

    captured: list[bytes] = []
    delivered: list[bytes] = []

    async def _realtime_unavailable(*, input_buffer: Any = None) -> None:
        del input_buffer
        return None

    async def _passthrough(source: Any) -> Any:
        async for chunk in source:
            yield chunk

    async def _set_state(state: Any) -> None:
        del state

    async def _publish(event: Any) -> None:
        del event

    async def _captured(pcm: bytes) -> None:
        captured.append(pcm)

    async def _classic_handle(pcm: bytes, **kwargs: Any) -> bool:
        del kwargs
        if pcm != b"retained-opening":
            raise AssertionError("Classic must receive the retained realtime opening")
        delivered.append(b"classic-reply-audio")
        return False

    pipe._active_realtime_session = _realtime_unavailable
    pipe._session_input_stream = _passthrough
    pipe._set_turn_state = _set_state
    pipe._publish_event = _publish
    pipe._publish_utterance_captured = _captured
    pipe._handle_utterance = _classic_handle

    opening = AudioChunk(
        pcm=b"retained-opening",
        sample_rate=16_000,
        timestamp_ns=1,
    )
    buffer = pipeline_mod._SessionInputBuffer(initial=(opening,))  # noqa: SLF001
    buffer.finish()
    await asyncio.wait_for(pipe._active_session(input_buffer=buffer), timeout=5.0)
    if captured != [opening.pcm] or delivered != [b"classic-reply-audio"]:
        raise AssertionError("Classic fallback must capture and deliver exactly once")
    return {"classic_deliveries": 1}


async def run_contract_soak(
    *,
    warm_connects: int = MIN_WARM_CONNECTS,
    turns_per_connect: int = 5,
    barge_samples: int = MIN_BARGE_SAMPLES,
    failback_samples: int = MIN_FAILBACK_SAMPLES,
) -> dict[str, Any]:
    """Run the key-free provider-neutral reliability soak.

    The first connection is a warm-up and is excluded.  Every subsequent
    connection carries ``turns_per_connect`` logical turns.
    """
    if warm_connects <= 0 or turns_per_connect <= 0:
        raise ValueError("warm_connects and turns_per_connect must be positive")
    # The injected reconnect failures are expected test stimuli. Suppress their
    # warning-level production diagnostics here; exceptions and gate failures
    # still propagate, and live runs retain their normal observability.
    session_log = logging.getLogger("jarvis.realtime.session")
    previous_level = session_log.level
    session_log.setLevel(logging.ERROR)
    try:
        provider = _FiniteProvider([1, *([turns_per_connect] * warm_connects)])
        await _run_finite_connection(provider, connection_index=0, expected_turns=1)

        ttfa: list[float] = []
        delivered = 0
        completions = 0
        for index in range(1, warm_connects + 1):
            postmortem, surface = await _run_finite_connection(
                provider,
                connection_index=index,
                expected_turns=turns_per_connect,
            )
            ttfa.append(float(postmortem.first_final_to_first_audio_ms))
            delivered += surface.binary_count
            completions += sum(
                message.get("type") == "turn_complete" for _, message in surface.json
            )

        turns = warm_connects * turns_per_connect
        if delivered != turns or completions != turns:
            raise AssertionError(
                f"delivery ledger mismatch: turns={turns} audio={delivered} "
                f"completions={completions}"
            )

        barge = [await _barge_sample(index) for index in range(barge_samples)]
        failback = [await _failback_sample(index) for index in range(failback_samples)]
        detached = await _detached_delegate_delivery_scenario()
        replacement = await _provider_replacement_scenario()
        classic = await _classic_delivery_scenario()
    finally:
        session_log.setLevel(previous_level)
    metrics = {
        "warm_connects": warm_connects,
        "turns": turns,
        "orphan_deliveries": max(0, turns - completions),
        "duplicate_deliveries": max(0, completions - turns),
        "ttfa_p50_ms": _percentile(ttfa, 50.0),
        "ttfa_p95_ms": _percentile(ttfa, 95.0),
        "barge_stop_p95_ms": _percentile(barge, 95.0),
        "failback_max_ms": max(failback, default=0.0),
        **detached,
        **replacement,
        **classic,
    }
    failures: list[str] = []
    if metrics["orphan_deliveries"] or metrics["duplicate_deliveries"]:
        failures.append("contract soak observed orphan or duplicate delivery")
    if metrics["ttfa_p50_ms"] > TTFA_P50_LIMIT_MS:
        failures.append("contract orchestration TTFA p50 exceeded the release SLO")
    if metrics["ttfa_p95_ms"] > TTFA_P95_LIMIT_MS:
        failures.append("contract orchestration TTFA p95 exceeded the release SLO")
    if metrics["barge_stop_p95_ms"] > BARGE_STOP_P95_LIMIT_MS:
        failures.append("contract barge stop p95 exceeded the release SLO")
    if metrics["failback_max_ms"] > FAILBACK_MAX_LIMIT_MS:
        failures.append("contract failback exceeded the release SLO")
    if metrics["detached_delegate_deliveries"] != 1:
        failures.append("contract soak did not retain one detached delegate result")
    if metrics["delegate_duplicates_suppressed"] != 1:
        failures.append("contract soak did not suppress the detached duplicate")
    if metrics["provider_replacements"] != 1:
        failures.append("contract soak did not cross to one replacement provider")
    if metrics["classic_deliveries"] != 1:
        failures.append("contract soak did not deliver one Classic fallback turn")
    if failures:
        raise AssertionError("; ".join(failures))
    return metrics


def _print_result(result: GateResult) -> None:
    if result.passed:
        print(
            "PASS: candidate target report met every numerical SLO threshold "
            f"({result.warm_connects} warm connects, {result.turns} turns; "
            f"TTFA p50={result.ttfa_p50_ms:.1f}ms "
            f"p95={result.ttfa_p95_ms:.1f}ms; "
            f"barge p95={result.barge_stop_p95_ms:.1f}ms; "
            f"failback max={result.failback_max_ms:.1f}ms)."
        )
        print(
            "TTFA ends at Jarvis's PCM output callback, not confirmed device "
            "playback; release still requires same-SHA collection and manual "
            "acoustic sign-off."
        )
    else:
        print("FAIL: realtime release gate did not pass:")
        for failure in result.failures:
            print(f"  - {failure}")
    print(
        "MANUAL SIGN-OFF STILL REQUIRED: physical microphone/acoustic echo, "
        "real speaker stop, device hot-plug, and macOS TCC permission flow."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--report",
        type=Path,
        help="instrumented target JSON report to validate",
    )
    mode.add_argument(
        "--contract-soak",
        action="store_true",
        help="run the key-free 100-connect/500-turn provider contract soak",
    )
    parser.add_argument(
        "--expected-sha",
        help="exact 40-character release commit SHA required by --report",
    )
    parser.add_argument(
        "--expected-platform",
        choices=sorted(_REPORT_PLATFORMS),
        help="canonical measured target platform required by --report",
    )
    parser.add_argument(
        "--expected-architecture",
        choices=sorted(_REPORT_ARCHITECTURES),
        help="canonical measured target architecture required by --report",
    )
    args = parser.parse_args(argv)

    if args.contract_soak:
        try:
            metrics = asyncio.run(run_contract_soak())
        except (AssertionError, TimeoutError, RuntimeError, ValueError) as exc:
            print(f"FAIL: provider-neutral contract soak: {exc}")
            return 1
        print(
            "PASS: provider-neutral contract soak "
            f"({metrics['warm_connects']} warm connects, {metrics['turns']} turns, "
            f"TTFA p95={metrics['ttfa_p95_ms']:.1f}ms, "
            f"barge p95={metrics['barge_stop_p95_ms']:.1f}ms, "
            f"failback max={metrics['failback_max_ms']:.1f}ms, "
            "detached delegate + provider replacement + Classic delivery, "
            "zero orphan/duplicate deliveries)."
        )
        print(
            "This is orchestration proof only; run --report with measured "
            "local-inference data before release."
        )
        print(
            "MANUAL SIGN-OFF STILL REQUIRED: physical microphone/acoustic echo, "
            "real speaker stop, device hot-plug, and macOS TCC permission flow."
        )
        return 0

    if not (
        args.expected_sha
        and args.expected_platform
        and args.expected_architecture
    ):
        print(
            "FAIL: --report requires --expected-sha, --expected-platform, "
            "and --expected-architecture"
        )
        return 2
    try:
        raw = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read reliability report: {exc}")
        return 1
    if not isinstance(raw, Mapping):
        print("FAIL: reliability report root must be a JSON object")
        return 1
    result = evaluate_report(
        raw,
        expected_sha=args.expected_sha,
        expected_platform=args.expected_platform,
        expected_architecture=args.expected_architecture,
    )
    _print_result(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main())
