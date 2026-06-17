"""Deterministic end-to-end proof for the CU-failure transcript ``detail`` track.

A Computer-Use give-up ("exit 5") only happens on ~1-in-10 real attempts, so the
fix cannot be verified by waiting for a live failure. This driver FORCES the exact
live chain with a fake harness that always fails, and prints what the Transcription
view would receive — no luck required:

    BrainManager._run_computer_use_background  (fake executor -> exit 5 + reason)
        -> AnnouncementRequested(kind="completion", detail="exit 5 · <reason>")
        -> SpeechPipeline._on_announcement  (humanized text spoken, detail forwarded)
        -> SpeechSpoken(text=humanized, detail=technical)
        -> SessionRecorder  -> voice_events row
        -> GET /api/sessions/{id}  payload   (what the UI fetches)
        -> markdown export                    (what "Copy as Markdown" yields)

Run::

    "C:\\Program Files\\Python311\\python.exe" scripts/verify_cu_failure_detail.py

Expected: the spoken line stays humanized (no bare "exit 5"), and a SEPARATE
``detail`` field / "detail:" line carries ``exit 5 · <harness reason>``.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ListeningStarted,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.formatter import format_session_markdown
from jarvis.sessions.models import SessionDetail
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from jarvis.speech.pipeline import SpeechPipeline

# UTF-8 stdout (Windows cp1252 default) so the "·" separator prints cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110
    pass


# --- fakes (mirror the unit-test doubles) ----------------------------------


@dataclass
class _OneShotTTS:
    name: str = "verify-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(pcm=b"\x00", sample_rate=24_000, timestamp_ns=0, channels=1)


@dataclass
class _SilentPlayer:
    consumed: list[str] = field(default_factory=list)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _ in chunks:
            self.consumed.append("frame")

    def stop(self) -> None:
        pass


class _AlwaysFailExecutor:
    """tool_executor stand-in: a CU run that gives up with exit 5 + a real reason."""

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        return SimpleNamespace(
            success=False,
            error="exit 5",  # what dispatch_to_harness mints for the model's `fail`
            output={
                "harness": "screenshot",
                "exit_code": 5,
                "stdout": "",
                "stderr": "[cu] fail at step-4: 5 guard-blocked actions this mission",
            },
        )


def _make_pipeline(bus: EventBus) -> SpeechPipeline:
    pipeline = SpeechPipeline(tts=_OneShotTTS(), bus=bus, enable_whisper_wake=False)
    pipeline._player = _SilentPlayer()  # type: ignore[assignment]
    pipeline._latency_tracker = None

    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cu-detail-verify-"))
    store = SessionStore(tmp / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        # Constructing the pipeline IS the live wiring: SpeechPipeline.__init__
        # subscribes _on_announcement to AnnouncementRequested (pipeline.py:1241),
        # which turns a completion announcement into a recorded SpeechSpoken
        # (incl. the technical detail). No manual subscribe — that would double it.
        _make_pipeline(bus)

        manager = BrainManager.__new__(BrainManager)
        manager._bus = bus
        manager._tool_executor = _AlwaysFailExecutor()

        sid = "verify-cu-detail"
        await bus.publish(
            VoiceSessionStarted(
                source_layer="verify",
                session_id=sid,
                wake_keyword="hey_jarvis",
                language="en",
            )
        )
        await bus.publish(ListeningStarted(source_layer="verify"))

        # THE live path under test — a background CU give-up.
        await manager._run_computer_use_background(
            tool=object(),
            harness_name="screenshot",
            prompt="open discord and check the news",
            timeout_s=180.0,
            user_text="open discord and check the news",
            trace_id=uuid4(),
            lang="en",
        )
        await asyncio.sleep(0.1)  # let the fire-and-forget SpeechSpoken + record run
        await bus.publish(
            VoiceSessionEnded(
                source_layer="verify", session_id=sid, hangup_reason="voice_pattern"
            )
        )

        # --- inspect what the Transcription view would receive ---------------
        session = store.get_session(sid)
        turns = store.get_turns(sid)
        events = store.get_events(sid)
        spoken = [e for e in events if e.kind == "SpeechSpoken"]

        print("=" * 70)
        print("1) PERSISTED SpeechSpoken rows (voice_events):")
        for e in spoken:
            print(f"   spoken_kind : {e.payload.get('spoken_kind')!r}")
            print(f"   text (SPOKEN): {e.payload.get('text')!r}")
            print(f"   detail (LOG) : {e.payload.get('detail')!r}")

        print("\n2) GET /api/sessions/{id} payload the UI fetches (trimmed):")
        detail = SessionDetail(session=session, turns=turns, events=events)
        ui = json.loads(detail.model_dump_json())
        ui_spoken = [
            ev for ev in ui["events"] if ev["kind"] == "SpeechSpoken"
        ]
        print(json.dumps(ui_spoken, indent=2, ensure_ascii=False))

        print("\n3) Markdown export ('Copy as Markdown') spoken section:")
        md = format_session_markdown(session, turns, events)
        for line in md.splitlines():
            if "spoken" in line.lower() or "detail" in line.lower() or "screen" in line.lower():
                print(f"   {line}")

        # --- assertions: humanized voice, technical detail preserved ---------
        ok = True
        if not spoken:
            print("\nFAIL: no SpeechSpoken recorded")
            return 1
        payload = spoken[-1].payload
        text = payload.get("text", "")
        det = payload.get("detail")
        if re.search(r"\bexit\s*\d+\b", text, re.IGNORECASE):
            print(f"\nFAIL: bare exit code leaked into SPOKEN text: {text!r}")
            ok = False
        if not det or "exit 5" not in det:
            print(f"\nFAIL: technical detail missing/incomplete: {det!r}")
            ok = False
        if det and "exit 5" not in md:
            print("\nFAIL: detail not rendered in markdown export")
            ok = False

        print("\n" + "=" * 70)
        if ok:
            print("PASS — voice humanized, transcript carries 'exit 5 · <reason>'.")
            return 0
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
