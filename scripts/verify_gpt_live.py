"""Opt-in, synthetic Live API proof. Never captures microphone or screen data."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


async def verify(model: str, provider_name: str = "openai") -> dict:
    from jarvis.core.config import get_secret_any
    from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
    from jarvis.core.runtime_refs import set_supervisor_tool_gateway
    from jarvis.live import session as live_session
    from jarvis.live.config import LiveConfig
    from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    provider_class = OpenAILiveProvider if provider_name == "openai" else GeminiLiveProvider

    class ProbeGateway:
        calls = 0

        def catalog(self):
            return (
                SupervisorToolDescriptor(
                    "probe_status",
                    "Read the synthetic integration-test status.",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    "safe",
                ),
            )

        async def execute(self, name, arguments, request):
            if name != "probe_status":
                return ToolResult(False, None, "Unknown probe tool")
            self.calls += 1
            return ToolResult(True, {"status": "The test succeeded", "verified": True})

        async def cancel_pending(self, trace):
            return False

    key = get_secret_any(provider_class.credential_candidates)
    if not key:
        raise RuntimeError("Configure an OpenAI key in the application first.")
    gateway = ProbeGateway()
    set_supervisor_tool_gateway(gateway)
    audio_bytes = 0
    transcripts = []
    heard = asyncio.Event()
    ready_at = 0.0
    first_audio = None

    async def audio(data):
        nonlocal audio_bytes, first_audio
        audio_bytes += len(data)
        if first_audio is None:
            first_audio = time.monotonic() - ready_at
        if (
            gateway.calls
            and (provider_name != "openai" or len(session._completed) >= 2)
            and re.search(r"test.{0,30}succeed", "".join(transcripts), re.IGNORECASE)
        ):
            heard.set()

    async def control(event):
        if event.get("type") == "transcript" and event.get("role") == "assistant":
            transcripts.append(event.get("fragment", event.get("text", "")))
            if gateway.calls and audio_bytes and re.search(
                r"test.{0,30}succeed", "".join(transcripts), re.IGNORECASE
            ):
                heard.set()

    with tempfile.TemporaryDirectory(prefix="jarvis-live-proof-") as folder:
        live_session.user_data_dir = lambda: Path(folder)
        profile = LiveConfig(configured=True, backend_model=model, web_search=False)
        cfg = SimpleNamespace(
            live=profile,
            brain=SimpleNamespace(
                reply_language="en",
                providers={},
                realtime=SimpleNamespace(model=model),
            ),
        )
        session_class = live_session.LiveVoiceSession
        if provider_name != "openai":
            from jarvis.live import native

            native.user_data_dir = lambda: Path(folder)
            session_class = native.NativeLiveVoiceSession
        session = session_class(
            session_id="synthetic-proof",
            send_binary=audio,
            send_json=control,
            providers=[provider_class(api_key=key)],
            config=cfg,
        )
        pump = None
        try:
            await session.handle_control({"type": "audio_start", "sample_rate": 24000})
            ready_at = time.monotonic()

            async def silence():
                while session.is_active:
                    await session.handle_audio_frame(bytes(4800))
                    await asyncio.sleep(0.1)

            pump = asyncio.create_task(silence())
            await session.handle_control(
                {
                    "type": "text_input",
                    "text": "Call probe_status exactly once, then say its returned status.",
                }
            )
            await asyncio.wait_for(heard.wait(), timeout=45)
            await asyncio.sleep(1)
        finally:
            if pump is not None:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            await session.end()
        return {
            "model": model,
            "tool_calls": gateway.calls,
            "audio_bytes": audio_bytes,
            "first_audio_seconds": first_audio,
            "voice_seconds": session._voice_seconds,
            "transcript": "".join(transcripts),
            "verified_result_spoken": heard.is_set(),
            "failed": session.failed,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="Authorize a short billed API test")
    parser.add_argument(
        "--model", default="gpt-5.6-terra", help="Synthetic test backend, not an app default"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--provider", choices=("openai", "gemini"), default="openai")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("Pass --run-live to run this billed, synthetic API test.")
    result = asyncio.run(verify(args.model, args.provider))
    encoded = json.dumps(result, indent=2)
    print(encoded)
    if args.report:
        args.report.write_text(encoded + "\n", encoding="utf-8")
    if result["failed"] or result["tool_calls"] != 1 or not result["verified_result_spoken"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
