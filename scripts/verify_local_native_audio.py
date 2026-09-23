"""Exercise a real Jarvis native audio worker over pipes; no audio device or server.

Requires a built worker and acquired LFM2.5 Q4 weights. This proves synthetic
English audio, context and cancellation only, not German or Jarvis tool execution.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import platform
import queue
import subprocess
import threading
import time
import wave
from pathlib import Path

import psutil

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.realtime.local_runtime.catalog import catalog_model
from jarvis.realtime.local_runtime.packages import verify_package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    with args.runner.open("rb") as executable:
        runner_sha256 = hashlib.file_digest(executable, "sha256").hexdigest()
    model = catalog_model("lfm2.5-audio-1.5b-q4")
    assert model is not None
    if not verify_package(model, args.weights).verified:
        raise RuntimeError("The model package failed checksum verification")
    command = [
        str(args.runner.resolve()),
        "-m",
        str((args.weights / model.artifacts[0].path).resolve()),
        "-mm",
        str((args.weights / model.artifacts[1].path).resolve()),
        "--tts-speaker-file",
        str((args.weights / model.artifacts[2].path).resolve()),
        "-mv",
        str((args.weights / model.artifacts[3].path).resolve()),
        "-t",
        "4",
        "-ngl",
        "0",
        "-c",
        "8192",
    ]
    results: list[dict] = []
    events: queue.Queue = queue.Queue(maxsize=256)
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
        bufsize=1,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert process.stdin is not None and process.stdout is not None

    def read() -> None:
        try:
            for line in process.stdout:
                if line.strip():
                    events.put(json.loads(line), timeout=5)
        except (OSError, ValueError, queue.Full) as exc:
            events.put({"kind": "reader_error", "type": type(exc).__name__}, timeout=5)
        finally:
            try:
                events.put({"kind": "eof"}, timeout=5)
            except queue.Full:
                # The parent will fail its deadline and reap the child.
                pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(payload: dict) -> None:
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def turn(name: str, *, interrupt: bool = False, **kwargs) -> tuple[dict, bytes]:
        begin = time.monotonic()
        send({"id": name, "command": "generate", "max_tokens": 256, **kwargs})
        text: list[str] = []
        audio = bytearray()
        rate = 0
        first_audio = None
        cancel_at = None
        while True:
            event = events.get(timeout=90)
            if event.get("id") != name:
                raise RuntimeError(f"Unexpected worker event: {event.get('kind')}")
            kind = event["kind"]
            if kind == "text":
                text.append(event["text"])
            elif kind == "audio":
                first_audio = first_audio or time.monotonic() - begin
                audio.extend(base64.b64decode(event["pcm"], validate=True))
                rate = event["sample_rate"]
            if interrupt and cancel_at is None and kind in {"text", "audio"}:
                cancel_at = time.monotonic()
                send({"id": name, "command": "cancel"})
            if kind in {"done", "cancelled", "error"}:
                result = {
                    "case": name,
                    "terminal": kind,
                    "code": event.get("code"),
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
        loaded = events.get(timeout=120)
        assert loaded["kind"] == "loaded" and loaded["protocol"] == 1, loaded
        assert loaded["revision"] == "ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b"
        listeners = [
            connection
            for connection in psutil.Process(process.pid).net_connections(kind="inet")
            if connection.status == psutil.CONN_LISTEN
        ]
        assert not listeners, "The pipe worker must not open a network listener"
        results.append(
            {
                "case": "load",
                "elapsed_s": time.monotonic() - started,
                "tools": loaded["tools"],
                "full_duplex": loaded["full_duplex"],
            }
        )
        result, question = turn(
            "question",
            text="What is two plus three?",
            output_mode="audio",
            instructions="Perform TTS. Use the US female voice.",
        )
        assert result["terminal"] == "done" and question
        result, _ = turn("audio_answer", audio_wav=base64.b64encode(question).decode())
        assert result["terminal"] == "done" and result["pcm_bytes"] > 0
        assert "five" in result["text"].lower() or "5" in result["text"]
        turn("context_first", text="My favorite color is violet. Repeat the color.")
        result, _ = turn("context_second", text="What color did I mention?", reset_context=False)
        assert result["terminal"] == "done" and "violet" in result["text"].lower()
        result, _ = turn(
            "interruption", text="Count from one to one hundred slowly.", interrupt=True
        )
        assert result["terminal"] == "cancelled"
        result, _ = turn("stale_context", text="Continue.", reset_context=False)
        assert result["terminal"] == "error" and result["code"] == "context_reset_required"
        result, _ = turn("after_cancel", text="Say hello.")
        assert result["terminal"] == "done" and result["pcm_bytes"] > 0
        success = True
    finally:
        if process.poll() is None:
            send({"id": "shutdown", "command": "shutdown"})
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                success = False
        reader.join(timeout=5)
        success = success and process.returncode == 0 and not reader.is_alive()
        process.stdin.close()
        process.stdout.close()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "passed": success,
                    "transport": "stdio",
                    "model_fingerprint": model.fingerprint,
                    "runner_sha256": runner_sha256,
                    "os": platform.system(),
                    "architecture": platform.machine(),
                    "returncode": process.returncode,
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


if __name__ == "__main__":
    raise SystemExit(main())
