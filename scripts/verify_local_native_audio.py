"""Real native audio proof through the application-owned process controller.

Synthetic English audio, context, cancellation and shutdown; no microphone,
German, full-duplex or Jarvis tool-execution qualification is implied.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import platform
import time
import wave
from pathlib import Path

import psutil

from jarvis.realtime.local_runtime.catalog import catalog_model
from jarvis.realtime.local_runtime.events import NativeAudioError
from jarvis.realtime.local_runtime.launch import LfmBindings, NativeRunner, prepare_lfm_launch
from jarvis.realtime.local_runtime.process import NativeAudioProcess
from jarvis.realtime.local_runtime.runtime import LocalVoiceRuntime


async def verify(args: argparse.Namespace) -> int:
    with args.runner.open("rb") as executable:
        runner_sha256 = hashlib.file_digest(executable, "sha256").hexdigest()
    model = catalog_model("lfm2.5-audio-1.5b-q4")
    assert model is not None
    plan = prepare_lfm_launch(
        model,
        args.weights,
        NativeRunner(args.runner, runner_sha256, "ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b"),
        LfmBindings(*(artifact.path for artifact in model.artifacts)),
        context_tokens=8192,
    )
    controller = LocalVoiceRuntime(args.report.parent / "native-proof-runtime")
    runtime: NativeAudioProcess | None = None
    owner = ""
    results: list[dict] = []

    async def turn(name: str, *, interrupt: bool = False, **kwargs) -> tuple[dict, bytes]:
        assert runtime is not None
        begin = time.monotonic()
        text: list[str] = []
        audio = bytearray()
        rate = 0
        first_audio = None
        cancel_at = None
        terminal = "missing"
        async for event in runtime.generate(max_tokens=256, **kwargs):
            if event.kind == "text":
                text.append(event.text)
            elif event.kind == "audio":
                first_audio = first_audio or time.monotonic() - begin
                audio.extend(event.pcm)
                rate = event.sample_rate
            elif event.kind in {"done", "cancelled"}:
                terminal = event.kind
            if interrupt and cancel_at is None and event.kind in {"text", "audio"}:
                cancel_at = time.monotonic()
                await runtime.interrupt()
        result = {
            "case": name,
            "terminal": terminal,
            "text": "".join(text),
            "pcm_bytes": len(audio),
            "sample_rate": rate,
            "first_audio_s": first_audio,
            "elapsed_s": time.monotonic() - begin,
            "cancel_s": time.monotonic() - cancel_at if cancel_at else None,
        }
        results.append(result)
        print(json.dumps(result), flush=True)
        wav = io.BytesIO()
        if audio:
            with wave.open(wav, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(audio)
        return result, wav.getvalue()

    success = False
    try:
        started = time.monotonic()
        status = await controller.warm(plan)
        assert status["audio_ready"] and not status["jarvis_qualified"]
        owner, runtime = await controller.acquire_conversation(plan)
        first_pid = runtime.pid
        assert first_pid is not None
        await runtime.start()
        assert runtime.pid == first_pid, "A warm model must be reused"
        assert not [
            connection
            for connection in psutil.Process(first_pid).net_connections(kind="inet")
            if connection.status == psutil.CONN_LISTEN
        ]
        results.append(
            {
                "case": "load",
                "elapsed_s": time.monotonic() - started,
                "tools": runtime.loaded_capabilities["tools"],
                "full_duplex": runtime.loaded_capabilities["full_duplex"],
            }
        )
        result, question = await turn(
            "question",
            text="What is two plus three?",
            output_mode="audio",
            instructions="Perform TTS. Use the US female voice.",
        )
        assert result["terminal"] == "done" and question
        result, _ = await turn("audio_answer", audio_wav=question)
        assert result["terminal"] == "done" and result["pcm_bytes"] > 0
        assert "five" in result["text"].lower() or "5" in result["text"]
        await turn("context_first", text="My favorite color is violet. Repeat the color.")
        result, _ = await turn(
            "context_second", text="What color did I mention?", continue_context=True
        )
        assert result["terminal"] == "done" and "violet" in result["text"].lower()
        result, _ = await turn(
            "interruption", text="Count from one to one hundred slowly.", interrupt=True
        )
        assert result["terminal"] == "cancelled"
        try:
            await turn("stale_context", text="Continue.", continue_context=True)
        except NativeAudioError as exc:
            assert "reset" in str(exc)
            results.append(
                {"case": "stale_context", "terminal": "error", "code": "context_reset_required"}
            )
        else:
            raise AssertionError("Stale native context was accepted")
        result, _ = await turn("after_cancel", text="Say hello.")
        assert result["terminal"] == "done" and result["pcm_bytes"] > 0
        success = True
    finally:
        if owner:
            await controller.release_conversation(owner)
        await controller.stop()
        process = runtime._process if runtime else None
        success = (
            success
            and runtime is not None
            and not runtime.running
            and process is not None
            and process.returncode == 0
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "passed": success,
                    "transport": "stdio",
                    "controller": "LocalVoiceRuntime / NativeAudioProcess",
                    "model_fingerprint": model.fingerprint,
                    "runner_sha256": runner_sha256,
                    "os": platform.system(),
                    "architecture": platform.machine(),
                    "returncode": process.returncode if process else None,
                    "results": results,
                    "scope": "synthetic English audio, context, cancellation; "
                    "no Jarvis tool execution",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print("LOCAL_NATIVE_AUDIO_OK" if success else "LOCAL_NATIVE_AUDIO_FAILED")
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
