"""Out-of-process host for the dictation preview engine.

Why a subprocess: building the CUDA engine in the DESKTOP process loads
cublas (~600 MB of DLLs) while holding the Windows loader lock — no other
thread can start during that, the audio queue overflows and the window stops
answering for 15-30 s on a cold boot (2026-08-28 forensics, BUG-189 class).
In its own process the load costs the app nothing, and a wedged native
engine (AP-24) is finally recoverable for real: the parent kills this
process and starts a fresh one — an in-process timeout could only ever stop
waiting.

The engine itself is the unchanged :class:`LocalPreviewTranscriber` device
ladder (cuda/float16 → cuda/int8_float16 → cpu/int8, warm-decode gated): this
module only moves WHERE it runs.

Protocol (stdin/stdout, binary, length-prefixed):

* every message is ``4-byte big-endian length`` + that many bytes of UTF-8
  JSON; a transcribe REQUEST is followed by exactly ``n`` raw PCM bytes
  (16 kHz s16le), where ``n`` comes from the JSON header
* worker → parent, once, after the engine built and warmed:
  ``{"ready": true, "device": ..., "compute": ...}`` or
  ``{"ready": false, "error": ...}`` (the worker then exits)
* parent → worker: ``{"n": <pcm byte count>, "language": <code or null>}``,
  optionally with ``"beam_size"`` (default 1 — greedy, the preview's choice;
  the dictation final pass asks for a real beam)
* worker → parent: ``{"text": ..., "language": ..., "probability": ...,
  "segments": [{"start": s, "end": s, "text": ...}, ...]}`` or
  ``{"error": ...}`` — ``segments`` carry the decoder's own timings so the
  final pass can tell a dropped tail from a slow speaker

stdout carries ONLY the protocol (stderr is discarded by the parent). The
worker exits when its stdin closes — parent exit or kill — so no orphan can
outlive the app.
"""

from __future__ import annotations

import json
import struct
import sys
from typing import IO, Any


def _read_exact(stream: IO[bytes], n: int) -> bytes | None:
    """``n`` bytes, or ``None`` on EOF — a torn read means the peer is gone."""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: IO[bytes]) -> dict[str, Any] | None:
    """One length-prefixed JSON message, or ``None`` on EOF."""
    header = _read_exact(stream, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    payload = _read_exact(stream, length)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


def write_message(stream: IO[bytes], message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


def serve(stdin: IO[bytes], stdout: IO[bytes], model_name: str, compute: str | None = None) -> int:
    """Build the engine, report readiness, then answer requests until EOF.

    ``compute`` pins the CUDA compute type (``"int8_float16"`` for the final
    pass, which shares the card with the wake model and the voice stack);
    ``None`` keeps the preview's own ladder.
    """
    from jarvis.dictation.local_preview import LocalPreviewTranscriber

    engine = (
        LocalPreviewTranscriber(model_name, compute_override=compute)
        if compute
        else LocalPreviewTranscriber(model_name)
    )
    engine._load_model()  # noqa: SLF001 — the worker IS the engine's host
    if not engine.ready:
        write_message(stdout, {"ready": False, "error": "no usable local engine"})
        return 1
    write_message(
        stdout,
        {
            "ready": True,
            "device": engine._engine_device,  # noqa: SLF001
            "compute": engine._engine_compute,  # noqa: SLF001
            "timings": getattr(engine, "_load_timings", {}),
        },
    )
    while True:
        request = read_message(stdin)
        if request is None:
            return 0  # parent closed stdin: normal shutdown
        pcm = _read_exact(stdin, int(request.get("n", 0)))
        if pcm is None:
            return 0
        try:
            language_hint = request.get("language") or None
            beam_size = max(1, int(request.get("beam_size", 1) or 1))
            detailed = getattr(engine, "_transcribe_sync_detailed", None)
            segments: list[dict[str, Any]] = []
            if callable(detailed):
                text, language, probability, segments = detailed(
                    pcm, language_hint, beam_size=beam_size
                )
            else:  # an engine without timings still answers the preview
                text, language, probability = engine._transcribe_sync(  # noqa: SLF001
                    pcm, language_hint
                )
            write_message(
                stdout,
                {
                    "text": text,
                    "language": language,
                    "probability": probability,
                    "segments": list(segments),
                },
            )
        except Exception as exc:  # noqa: BLE001 — one bad clip must not kill the worker
            write_message(stdout, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "base"
    compute = sys.argv[2] if len(sys.argv) > 2 else None
    return serve(sys.stdin.buffer, sys.stdout.buffer, model_name, compute)


if __name__ == "__main__":
    raise SystemExit(main())
