"""Headless live-call probe for the codex-subscription realtime provider.

Drives ONE real ChatGPT-Live call end to end - real adapter, real app-server,
real aiortc media path, real local recognizer - with committed speech
fixtures instead of a microphone, against the production
``RealtimeVoiceSession`` in its desktop half-duplex shape. No sound card is
needed; aiortc encodes and decodes in-process.

This module is a developer diagnostic (invoked by
``scripts/codex_live_probe.py`` and by the env-gated integration test). It is
imported by nothing on the boot path (AP-26/AP-9), and every jarvis import
below is lazy so importing the module costs nothing.

Every run consumes the maintainer's ChatGPT subscription realtime usage; the
CLI caps calls per invocation. The desktop app must be STOPPED - the
fail-closed profile lock otherwise refuses the probe (exit 3), by design.

Capture format (``round.jsonl``): one JSON object per row -
``{"ts": epoch_s, "mono": monotonic_s, "src": ..., "kind": ..., "data": {...}}``
with ``src`` in {probe, surface, log, postmortem, notif}. The ``postmortem``
row is what ``scripts/diag_voice_sessions.py --harness`` and
``jarvis.diagnostics.realtime_forensics`` judge.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

INPUT_RATE = 24_000
CHUNK_MS = 20
CHUNK_BYTES = INPUT_RATE * 2 * CHUNK_MS // 1000
LEAD_SILENCE_MS = 300
TRAIL_SILENCE_MS = 700
DEFAULT_REPLY_TIMEOUT_S = 15.0
#: Cold subscription handshakes legitimately take tens of seconds; the
#: provider declares 45 s and the probe adds slack rather than guessing.
HANDSHAKE_SLACK_S = 15.0
SCENARIO_WALL_CAP_S = 120.0
END_BOUND_S = 10.0
#: Greeting allowance: at most one assistant turn with no grounded user
#: utterance, and only within this window after audio_ready.
GREETING_WINDOW_S = 10.0
#: A mid-reply arrival gap larger than this (beyond the audio the previous
#: chunk carried) counts as a client-side hole. Above the 1.2 s quiescence
#: backstop on purpose: longer pauses legitimately become turn boundaries.
CONTINUITY_GAP_S = 1.5

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "audio" / "realtime"
SCENARIO_DIR = _REPO_ROOT / "tests" / "integration" / "realtime" / "scenarios"


class ProbeEnvironmentError(RuntimeError):
    """The environment cannot run a live probe; ``exit_code`` says why.

    2 = missing prerequisite (login, fixtures, aiortc, network) ·
    3 = the desktop app holds the voice-profile lock (close it first).
    """

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def load_manifest() -> list[dict[str, Any]]:
    manifest_path = FIXTURE_DIR / "manifest.json"
    if not manifest_path.exists():
        raise ProbeEnvironmentError(
            "speech fixtures are missing - run "
            "scripts/gen_realtime_speech_fixtures.py once"
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_fixture_pcm(fixture_id: str) -> bytes:
    entry = next((e for e in load_manifest() if e.get("id") == fixture_id), None)
    if entry is None:
        raise ProbeEnvironmentError(f"unknown fixture id {fixture_id!r}")
    path = FIXTURE_DIR / str(entry["path"])
    with wave.open(str(path), "rb") as handle:
        if handle.getframerate() != INPUT_RATE or handle.getnchannels() != 1:
            raise ProbeEnvironmentError(
                f"fixture {fixture_id!r} is not {INPUT_RATE} Hz mono"
            )
        return handle.readframes(handle.getnframes())


def load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        raise ProbeEnvironmentError(
            f"unknown scenario {name!r} (no {path.name} in {SCENARIO_DIR})"
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


class Capture:
    """Ordered probe timeline; one JSONL row per note."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def note(self, src: str, kind: str, data: Any = None) -> None:
        self.rows.append(
            {
                "ts": time.time(),
                "mono": time.monotonic(),
                "src": src,
                "kind": kind,
                "data": data if data is not None else {},
            }
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in self.rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")


class _LogTap(logging.Handler):
    """Mirror jarvis log records into the capture (bounded message length)."""

    def __init__(self, capture: Capture) -> None:
        super().__init__(level=logging.INFO)
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken record must not kill the probe
            message = "<unformattable log record>"
        self._capture.note(
            "log",
            record.levelname,
            {"logger": record.name, "message": message[:400]},
        )


# ---------------------------------------------------------------------------
# Surface + microphone
# ---------------------------------------------------------------------------


class ProbeSurface:
    """Desktop-surface stand-in recording everything with timestamps."""

    def __init__(self, capture: Capture) -> None:
        self._capture = capture
        self.json: list[dict[str, Any]] = []
        self.binary_bytes = 0
        self._tick = asyncio.Event()

    async def send_json(self, message: dict[str, Any]) -> None:
        payload = dict(message)
        self.json.append(payload)
        self._capture.note("surface", "json", payload)
        self._tick.set()

    async def send_binary(self, data: bytes) -> None:
        self.binary_bytes += len(data)
        self._capture.note("surface", "binary", {"bytes": len(data)})
        self._tick.set()

    def mark(self) -> int:
        return len(self.json)

    async def wait_json(
        self, predicate: Any, *, since: int = 0, timeout_s: float
    ) -> dict[str, Any] | None:
        async def _loop() -> dict[str, Any]:
            while True:
                self._tick.clear()
                for message in self.json[since:]:
                    if predicate(message):
                        return message
                await self._tick.wait()

        try:
            return await asyncio.wait_for(_loop(), timeout_s)
        except TimeoutError:
            return None


class MicFeeder:
    """Continuous 24 kHz microphone stream: room tone, with spliced speech.

    Models the real desktop pipeline, which streams microphone frames for the
    whole call: silence between utterances is quiet ROOM TONE (the committed
    ~-55 dBFS fixture), never digital zero, so the energy gates under test see
    exactly what a real microphone would give them. Paced against an absolute
    deadline - a sleep-per-chunk loop would drift (the webrtc sender lesson).
    """

    def __init__(self, session: Any, capture: Capture) -> None:
        self._session = session
        self._capture = capture
        self._noise = load_fixture_pcm("room_noise")
        self._noise_at = 0
        self._segments: deque[bytes] = deque()
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self.resyncs = 0

    def speak(self, pcm: bytes) -> None:
        lead = b"\x00\x00" * (INPUT_RATE * LEAD_SILENCE_MS // 1000)
        trail = b"\x00\x00" * (INPUT_RATE * TRAIL_SILENCE_MS // 1000)
        self._segments.append(lead + pcm + trail)

    @property
    def speaking(self) -> bool:
        return bool(self._segments)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="probe-mic-feeder")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _next_chunk(self) -> bytes:
        if self._segments:
            segment = self._segments[0]
            chunk, rest = segment[:CHUNK_BYTES], segment[CHUNK_BYTES:]
            if rest:
                self._segments[0] = rest
            else:
                self._segments.popleft()
            if len(chunk) < CHUNK_BYTES:
                chunk += b"\x00\x00" * ((CHUNK_BYTES - len(chunk)) // 2)
            return chunk
        start = self._noise_at
        end = start + CHUNK_BYTES
        if end <= len(self._noise):
            chunk = self._noise[start:end]
            self._noise_at = end if end < len(self._noise) else 0
            return chunk
        chunk = self._noise[start:] + self._noise[: end - len(self._noise)]
        self._noise_at = end - len(self._noise)
        return chunk

    async def _run(self) -> None:
        interval = CHUNK_MS / 1000.0
        next_at = time.monotonic()
        while not self._stopped.is_set():
            chunk = self._next_chunk()
            try:
                await self._session.handle_audio_frame(chunk)
            except Exception:  # noqa: BLE001 - a dying session ends the feed
                log.debug("probe mic feed rejected a frame", exc_info=True)
            next_at += interval
            now = time.monotonic()
            if next_at < now - 0.5:
                # The probe process itself hiccuped; restart the clock rather
                # than bursting stale audio (mirrors the sender's own rule).
                self.resyncs += 1
                self._capture.note(
                    "probe", "mic_resync", {"behind_s": round(now - next_at, 3)}
                )
                next_at = now
            await asyncio.sleep(max(0.0, next_at - now))


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------


def _require_webrtc() -> None:
    from jarvis.realtime.webrtc_transport import webrtc_unavailable_reason

    reason = webrtc_unavailable_reason()
    if reason:
        raise ProbeEnvironmentError(f"WebRTC transport unavailable: {reason}")


def _provider_class() -> Any:
    from jarvis.plugins.realtime.codex_subscription import (
        CodexSubscriptionRealtimeProvider,
    )

    return CodexSubscriptionRealtimeProvider


def _require_login() -> None:
    if not _provider_class().external_login_ready():
        raise ProbeEnvironmentError(
            "no ready Codex subscription voice profile - connect the ChatGPT "
            "subscription (Codex) card in the app first"
        )


def _is_profile_lock_error(message: str) -> bool:
    return "another jarvis process" in message.lower()


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


async def run_scenario(
    scenario: dict[str, Any],
    *,
    out_dir: Path,
    budgets: dict[str, Any] | None = None,
    with_delegate: bool = False,
) -> dict[str, Any]:
    """Run one scenario as one live call; return the summary dict."""
    _require_webrtc()
    _require_login()
    manifest = load_manifest()

    from jarvis.core.bus import EventBus
    from jarvis.core.config import load_config
    from jarvis.core.events import RealtimeSessionPostmortem
    from jarvis.realtime.session import RealtimeVoiceSession

    cfg = load_config()
    provider = _provider_class().from_runtime_config(cfg)

    capture = Capture()
    capture.note("probe", "scenario", {"name": scenario.get("name", "?")})
    surface = ProbeSurface(capture)
    bus = EventBus()
    postmortems: list[dict[str, Any]] = []

    async def _on_postmortem(event: RealtimeSessionPostmortem) -> None:
        payload = dataclasses.asdict(event)
        payload.pop("trace_id", None)
        postmortems.append(payload)
        capture.note("postmortem", "session", payload)

    bus.subscribe(RealtimeSessionPostmortem, _on_postmortem)

    brain = None
    if with_delegate:
        brain = _build_delegate_brain(cfg)

    session = RealtimeVoiceSession(
        session_id=f"probe-{int(time.time())}",
        send_binary=surface.send_binary,
        send_json=surface.send_json,
        providers=[provider],
        config=cfg,
        bus=bus,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=INPUT_RATE,
        brain=brain,
        allow_classic_fallback=False,
    )

    tap = _LogTap(capture)
    jarvis_logger = logging.getLogger("jarvis")
    jarvis_logger.addHandler(tap)
    feeder: MicFeeder | None = None
    end_duration_s = 0.0
    try:
        handshake_budget = float(
            getattr(provider, "handshake_budget_s", 45.0) or 45.0
        )
        started = surface.mark()
        capture.note("probe", "audio_start", {})
        open_task = asyncio.create_task(
            session.handle_control(
                {"type": "audio_start", "sample_rate": INPUT_RATE}
            )
        )
        ready = await surface.wait_json(
            lambda m: m.get("type") in {"audio_ready", "provider_error", "hangup"},
            since=started,
            timeout_s=handshake_budget + HANDSHAKE_SLACK_S,
        )
        await asyncio.wait_for(open_task, timeout=HANDSHAKE_SLACK_S)
        if ready is None or ready.get("type") != "audio_ready":
            detail = str((ready or {}).get("error", "no audio_ready arrived"))
            if _is_profile_lock_error(detail):
                raise ProbeEnvironmentError(
                    "the desktop app is running and holds the subscription "
                    "voice profile - close the Jarvis app first",
                    exit_code=3,
                )
            raise ProbeEnvironmentError(f"session did not open: {detail}")
        capture.note("probe", "ready", ready)

        feeder = MicFeeder(session, capture)
        feeder.start()

        deadline = time.monotonic() + SCENARIO_WALL_CAP_S
        for step in scenario.get("steps", ()):  # noqa: PLR1702 - a flat script
            if time.monotonic() > deadline:
                capture.note("probe", "wall_cap", {"cap_s": SCENARIO_WALL_CAP_S})
                break
            if "speak" in step:
                fixture_id = str(step["speak"])
                capture.note("probe", "speak", _fixture_meta(manifest, fixture_id))
                feeder.speak(load_fixture_pcm(fixture_id))
            elif "wait_reply" in step:
                timeout_s = float(
                    (step["wait_reply"] or {}).get("timeout_s", DEFAULT_REPLY_TIMEOUT_S)
                )
                mark = surface.mark()
                reply = await surface.wait_json(
                    lambda m: m.get("type") == "turn_complete",
                    since=mark,
                    timeout_s=timeout_s,
                )
                capture.note(
                    "probe",
                    "wait_reply",
                    {"timed_out": reply is None, "timeout_s": timeout_s},
                )
            elif "silence_s" in step:
                capture.note("probe", "silence", {"seconds": step["silence_s"]})
                await asyncio.sleep(float(step["silence_s"]))
            elif "barge_in" in step:
                spec = step["barge_in"] or {}
                after_ms = float(spec.get("after_reply_ms", 700))
                mark = surface.mark()
                # Wait for a reply to actually be playing before cutting it.
                await surface.wait_json(
                    lambda m: m.get("type") == "transcript"
                    and m.get("role") == "assistant",
                    since=mark,
                    timeout_s=DEFAULT_REPLY_TIMEOUT_S,
                )
                await asyncio.sleep(after_ms / 1000.0)
                capture.note("probe", "barge_in", {"after_reply_ms": after_ms})
                await session.handle_control({"type": "barge_in"})
                then_speak = spec.get("then_speak")
                if then_speak:
                    capture.note(
                        "probe", "speak", _fixture_meta(manifest, str(then_speak))
                    )
                    feeder.speak(load_fixture_pcm(str(then_speak)))
            elif "end" in step:
                break
            else:  # An unknown step is a scenario bug, not a soft skip.
                raise ProbeEnvironmentError(f"unknown scenario step: {step!r}")
    finally:
        if feeder is not None:
            await feeder.stop()
        end_started = time.monotonic()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.end(reason="hotkey"), END_BOUND_S + 5.0)
        end_duration_s = time.monotonic() - end_started
        capture.note("probe", "end", {"duration_s": round(end_duration_s, 3)})
        jarvis_logger.removeHandler(tap)

    postmortem = postmortems[-1] if postmortems else {}
    verdicts = evaluate_scenario(
        capture.rows,
        postmortem,
        scenario,
        manifest,
        budgets=budgets or {},
        end_duration_s=end_duration_s,
    )
    summary = {
        "scenario": scenario.get("name", "?"),
        "asserts": verdicts,
        "postmortem": postmortem,
        "passed": all(v["status"] != "fail" for v in verdicts.values()),
    }
    await asyncio.to_thread(_persist, out_dir, capture, summary)
    return summary


def _persist(out_dir: Path, capture: Capture, summary: dict[str, Any]) -> None:
    """Synchronous capture/summary write, thread-hopped by the async runners."""
    out_dir.mkdir(parents=True, exist_ok=True)
    capture.write(out_dir / "round.jsonl")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _fixture_meta(manifest: list[dict[str, Any]], fixture_id: str) -> dict[str, Any]:
    entry = next((e for e in manifest if e.get("id") == fixture_id), {})
    return {
        "id": fixture_id,
        "text": entry.get("text", ""),
        "language": entry.get("language", ""),
    }


def _build_delegate_brain(cfg: Any) -> Any:
    try:
        from jarvis.brain.factory import build_brain_manager  # type: ignore[attr-defined]

        return build_brain_manager(cfg)
    except Exception as exc:  # noqa: BLE001 - opt-in extra, honest refusal
        raise ProbeEnvironmentError(
            f"--with-delegate needs a configured brain: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    cleaned = "".join(
        c.lower() if c.isalnum() or c.isspace() else " " for c in text
    )
    return " ".join(cleaned.split())


def evaluate_scenario(
    rows: list[dict[str, Any]],
    postmortem: dict[str, Any],
    scenario: dict[str, Any],
    manifest: list[dict[str, Any]],
    *,
    budgets: dict[str, Any],
    end_duration_s: float,
) -> dict[str, dict[str, str]]:
    """Judge one capture against the scenario's assert list.

    Pure over the capture rows + postmortem, so it is unit-testable without a
    live call. Statuses: pass | warn | fail.
    """
    wanted = list(scenario.get("asserts", ()))
    verdicts: dict[str, dict[str, str]] = {}

    surface = [r for r in rows if r["src"] == "surface"]
    ready_rows = [r for r in rows if r["src"] == "probe" and r["kind"] == "ready"]
    ready_mono = ready_rows[0]["mono"] if ready_rows else 0.0

    # Timeline primitives. Live round 1 (2026-08-06) taught the judge two
    # realities: user FINALS can trail their own turn's boundary by seconds
    # (the recognizer round trip), and a greeting's boundary can arrive long
    # after ready even though its speech STARTED immediately - so exchanges
    # are counted question->answer, and the greeting allowance anchors on
    # when the boundary's speech began, not on when it ended.
    user_finals: list[float] = []
    assistant_text_rows: list[tuple[float, str]] = []
    boundaries: list[float] = []
    for row in surface:
        data = row["data"]
        if row["kind"] != "json":
            continue
        if data.get("type") == "transcript":
            text = str(data.get("text", "")).strip()
            if data.get("role") == "user" and data.get("is_final") and text:
                user_finals.append(row["mono"])
            elif data.get("role") == "assistant" and text:
                assistant_text_rows.append((row["mono"], text))
        elif data.get("type") == "turn_complete":
            boundaries.append(row["mono"])

    # A grounded exchange: a user final that is ANSWERED - assistant output
    # begins after it and before the next user final.
    exchanges = 0
    for index, final_mono in enumerate(user_finals):
        next_final = (
            user_finals[index + 1] if index + 1 < len(user_finals) else float("inf")
        )
        if any(final_mono < mono < next_final for mono, _ in assistant_text_rows):
            exchanges += 1

    # Ungrounded boundaries. A user final may TRAIL its own turn's boundary
    # by the recognizer's round trip, but a bare trailing final could equally
    # belong to the NEXT turn - so a final only grounds a boundary when
    # assistant output sits between the final and that boundary(+slack): the
    # answer is the tie-breaker. Each final grounds at most one boundary.
    _FINAL_TRAIL_SLACK_S = 2.5
    ungrounded_turns: list[float] = []
    ungrounded_speech_start: dict[float, float] = {}
    unconsumed_finals = list(user_finals)
    previous_boundary = float("-inf")
    for boundary in boundaries:
        horizon = boundary + _FINAL_TRAIL_SLACK_S
        grounding_final: float | None = None
        for final in unconsumed_finals:
            if not (previous_boundary < final <= horizon):
                continue
            if any(final < mono <= horizon for mono, _ in assistant_text_rows):
                grounding_final = final
                break
        if grounding_final is not None:
            unconsumed_finals.remove(grounding_final)
        else:
            # When did this boundary's speech begin? (greeting anchor)
            speech_start = next(
                (
                    mono
                    for mono, _ in assistant_text_rows
                    if mono > previous_boundary
                ),
                boundary,
            )
            ungrounded_speech_start[boundary] = speech_start
            ungrounded_turns.append(boundary)
        previous_boundary = boundary

    def _verdict(name: str, status: str, detail: str) -> None:
        verdicts[name] = {"status": status, "detail": detail}

    for name in wanted:
        if name.startswith("turns>="):
            need = int(name.split(">=", 1)[1])
            _verdict(
                name,
                "pass" if exchanges >= need else "fail",
                f"{exchanges} grounded exchange(s), need {need}",
            )
        elif name == "no_ungrounded":
            allowed = [
                mono
                for mono in ungrounded_turns
                if ready_mono
                and ungrounded_speech_start.get(mono, mono) - ready_mono
                <= GREETING_WINDOW_S
            ][:1]
            extra = [m for m in ungrounded_turns if m not in allowed]
            _verdict(
                name,
                "pass" if not extra else "fail",
                f"{len(ungrounded_turns)} ungrounded boundary(ies), "
                f"{len(allowed)} allowed as the greeting",
            )
        elif name == "no_selftalk":
            late_assistant = [
                mono
                for mono, _ in assistant_text_rows
                if ready_mono and mono - ready_mono > GREETING_WINDOW_S
            ]
            _verdict(
                name,
                "pass" if not late_assistant else "fail",
                f"{len(late_assistant)} assistant transcript(s) past the "
                f"{GREETING_WINDOW_S:.0f}s greeting window on a silent mic",
            )
        elif name == "no_roleplay":
            spoken_ids = [
                r["data"].get("id")
                for r in rows
                if r["src"] == "probe" and r["kind"] == "speak"
            ]
            questions = [
                _normalize(str(e.get("text", "")))
                for e in manifest
                if e.get("id") in spoken_ids and e.get("text")
            ]
            assistant_all = _normalize(" ".join(t for _, t in assistant_text_rows))
            echoed = [q for q in questions if len(q) >= 15 and q in assistant_all]
            _verdict(
                name,
                "pass" if not echoed else "fail",
                "assistant transcript never contains a full probe question"
                if not echoed
                else f"assistant echoed the user's question verbatim: {echoed[0][:60]!r}",
            )
        elif name == "audio_continuity":
            holes = _continuity_holes(rows)
            drops = int(postmortem.get("recv_dropped_frames", 0) or 0)
            ok = not holes and drops == 0
            _verdict(
                name,
                "pass" if ok else "fail",
                f"{len(holes)} client-side hole(s) > {CONTINUITY_GAP_S}s, "
                f"{drops} recv drop(s)",
            )
        elif name == "no_rebuilds":
            rebuilds = int(postmortem.get("rebuilds", 0) or 0)
            stun = int(postmortem.get("stun_retries", 0) or 0)
            _verdict(
                name,
                "pass" if rebuilds == 0 and stun == 0 else "fail",
                f"rebuilds={rebuilds} stun_retries={stun}",
            )
        elif name == "mic_wall_clock":
            resyncs = int(postmortem.get("sender_pacing_resyncs", 0) or 0)
            shed = int(postmortem.get("sender_shed_frames", 0) or 0)
            _verdict(
                name,
                "pass" if resyncs == 0 and shed == 0 else "fail",
                f"sender resyncs={resyncs} shed_frames={shed}",
            )
        elif name == "clean_close":
            clean = bool(postmortem.get("close_clean", False))
            bounded = end_duration_s <= END_BOUND_S
            _verdict(
                name,
                "pass" if clean and bounded else "fail",
                f"close_clean={clean} end_duration={end_duration_s:.1f}s",
            )
        elif name == "spawn_budget":
            ready_ms = int(postmortem.get("ready_ms", 0) or 0)
            first_ms = int(postmortem.get("first_audio_ms", 0) or 0)
            ready_cap = float(budgets.get("ready_s", 4.0)) * 1000
            first_cap = float(budgets.get("first_audio_s", 6.0)) * 1000
            over = ready_ms > ready_cap or (first_ms and first_ms > first_cap)
            enforce = bool(budgets.get("enforce", False))
            status = "pass" if not over else ("fail" if enforce else "warn")
            _verdict(
                name,
                status,
                f"ready={ready_ms}ms (cap {ready_cap:.0f}), "
                f"first_audio={first_ms}ms (cap {first_cap:.0f})",
            )
        else:
            _verdict(name, "fail", "unknown assert - fix the scenario")

    return verdicts


def _continuity_holes(rows: list[dict[str, Any]]) -> list[float]:
    """Arrival gaps between audio chunks of one open turn, beyond the audio
    the previous chunk itself carried and beyond CONTINUITY_GAP_S."""
    holes: list[float] = []
    prev_mono: float | None = None
    prev_audio_s = 0.0
    for row in rows:
        if row["src"] != "surface":
            continue
        data = row["data"]
        if row["kind"] == "binary":
            mono = row["mono"]
            if prev_mono is not None:
                gap = mono - prev_mono - prev_audio_s
                if gap > CONTINUITY_GAP_S:
                    holes.append(round(gap, 3))
            prev_mono = mono
            prev_audio_s = int(data.get("bytes", 0)) / 2 / INPUT_RATE
        elif row["kind"] == "json" and data.get("type") == "turn_complete":
            prev_mono = None  # a boundary legitimately pauses the stream
    return holes


# ---------------------------------------------------------------------------
# Dump mode (contract discovery)
# ---------------------------------------------------------------------------


async def run_dump(*, out_dir: Path, listen_s: float = 25.0) -> dict[str, Any]:
    """One adapter-bypass call that records EVERY app-server notification.

    Purpose: convert the guessed ``_TERMINAL_RESPONSE_ITEMS`` spelling into
    live evidence, and answer whether ``realtime_stop`` -> ``realtime_start``
    works on the SAME thread (the STUN-retry salvage question). Writes the
    full notification stream to the (gitignored) capture; the returned
    summary carries only type names, never transcript payloads.
    """
    _require_webrtc()
    _require_login()

    from jarvis.codex_app_server import get_shared_codex_app_server
    from jarvis.plugins.realtime.codex_subscription import (
        _TERMINAL_RESPONSE_ITEMS,
        _THREAD_BASE_INSTRUCTIONS,
        _THREAD_DEVELOPER_INSTRUCTIONS,
        _cleanup_remote_thread,
        _thread_id_from_result,
    )
    from jarvis.realtime.webrtc_transport import RealtimeWebRtcAudioEndpoint

    capture = Capture()
    capture.note("probe", "dump_start", {"listen_s": listen_s})
    client = get_shared_codex_app_server()
    endpoint = RealtimeWebRtcAudioEndpoint(None)
    thread_id = ""
    methods: dict[str, int] = {}
    item_types: dict[str, int] = {}
    audio_bytes = 0
    second_start: dict[str, Any] = {"attempted": False}
    try:
        offer_sdp = await endpoint.create_offer()
        try:
            thread_result = await client.thread_start(
                base_instructions=_THREAD_BASE_INSTRUCTIONS,
                developer_instructions=_THREAD_DEVELOPER_INSTRUCTIONS,
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001 - map the lock to exit 3
            if _is_profile_lock_error(str(exc)):
                raise ProbeEnvironmentError(
                    "the desktop app is running and holds the subscription "
                    "voice profile - close the Jarvis app first",
                    exit_code=3,
                ) from exc
            raise
        thread_id = _thread_id_from_result(thread_result)
        if not thread_id:
            raise ProbeEnvironmentError(
                "Codex app-server did not return a thread id"
            )
        subscription = client.subscribe(thread_id)
        start = await client.realtime_start(
            thread_id,
            output_modality="audio",
            offer_sdp=offer_sdp,
            prompt="",
            model=None,
            voice="cove",
            version="v3",
            include_startup_context=False,
            client_managed_handoffs=True,
        )
        answer_sdp = str(getattr(start, "answer_sdp", "") or "")
        capture.note("probe", "realtime_start", {"version": "v3"})
        await endpoint.apply_answer(answer_sdp)
        await endpoint.wait_connected()

        async def _pump_notifications() -> None:
            while True:
                notification = await subscription.get(timeout_s=listen_s)
                method = str(getattr(notification, "method", "") or "")
                params = getattr(notification, "params", None) or {}
                methods[method] = methods.get(method, 0) + 1
                item = params.get("item") if isinstance(params, dict) else None
                if isinstance(item, dict) and item.get("type"):
                    item_type = str(item["type"])
                    item_types[item_type] = item_types.get(item_type, 0) + 1
                capture.note(
                    "notif", method or "?", params if isinstance(params, dict) else {}
                )

        async def _pump_audio_out() -> None:
            nonlocal audio_bytes
            while True:
                pcm = await endpoint.next_output_pcm()
                if pcm is None:
                    return
                audio_bytes += len(pcm)

        async def _feed_mic() -> None:
            pcm = load_fixture_pcm("en_giraffe")
            lead = b"\x00\x00" * (INPUT_RATE * LEAD_SILENCE_MS // 1000)
            trail = b"\x00\x00" * (INPUT_RATE * TRAIL_SILENCE_MS // 1000)
            stream = lead + pcm + trail
            interval = CHUNK_MS / 1000.0
            next_at = time.monotonic()
            for offset in range(0, len(stream), CHUNK_BYTES):
                endpoint.send_pcm(stream[offset : offset + CHUNK_BYTES], INPUT_RATE)
                next_at += interval
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))

        pumps = [
            asyncio.create_task(_pump_notifications(), name="dump-notifs"),
            asyncio.create_task(_pump_audio_out(), name="dump-audio"),
        ]
        try:
            await _feed_mic()
            await asyncio.sleep(listen_s)
        finally:
            for pump in pumps:
                pump.cancel()
            for pump in pumps:
                with contextlib.suppress(
                    asyncio.CancelledError, Exception
                ):
                    await pump

        # Same-thread restart probe: does v3 accept a second realtime_start?
        second_start["attempted"] = True
        try:
            await client.realtime_stop(thread_id)
            fresh_endpoint = RealtimeWebRtcAudioEndpoint(None)
            try:
                fresh_offer = await fresh_endpoint.create_offer()
                restart = await client.realtime_start(
                    thread_id,
                    output_modality="audio",
                    offer_sdp=fresh_offer,
                    prompt="",
                    model=None,
                    voice="cove",
                    version="v3",
                    include_startup_context=False,
                    client_managed_handoffs=True,
                )
                second_start["ok"] = bool(
                    str(getattr(restart, "answer_sdp", "") or "").strip()
                )
            finally:
                await fresh_endpoint.close()
        except Exception as exc:  # noqa: BLE001 - the refusal IS the answer
            second_start["ok"] = False
            second_start["error"] = f"{type(exc).__name__}: {exc}"[:300]
        capture.note("probe", "same_thread_restart", second_start)
    finally:
        with contextlib.suppress(Exception):
            await endpoint.close()
        if thread_id:
            with contextlib.suppress(Exception):
                await _cleanup_remote_thread(client, thread_id)

    observed_terminals = sorted(
        t for t in item_types if t in _TERMINAL_RESPONSE_ITEMS
    )
    candidate_terminals = sorted(
        t
        for t in item_types
        if "done" in t or "complete" in t or "finished" in t or "stop" in t
    )
    summary = {
        "methods": dict(sorted(methods.items())),
        "item_types": dict(sorted(item_types.items())),
        "audio_bytes": audio_bytes,
        "terminal_in_frozenset": observed_terminals,
        "terminal_candidates": candidate_terminals,
        "same_thread_restart": second_start,
    }
    await asyncio.to_thread(_persist, out_dir, capture, summary)
    return summary


