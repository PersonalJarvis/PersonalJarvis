"""Small data-only local voice packages for filesystem and adapter contracts."""

from __future__ import annotations

import hashlib

from jarvis.realtime.local_runtime.models import LocalModelManifest


def model_manifest(**changes: object) -> LocalModelManifest:
    payload: dict[str, object] = {
        "id": "test-voice",
        "label": "Test voice",
        "family": "test-audio",
        "source": {"kind": "huggingface", "repository": "test/voice", "revision": "a" * 40},
        "license": "test-only",
        "languages": ["en", "de"],
        "capabilities": [
            "audio_input",
            "audio_output",
            "streaming_output",
            "full_duplex",
            "tool_calls",
            "tool_results",
            "interruption",
            "conversation_context",
        ],
        "artifacts": [
            {"path": "model.gguf", "size_bytes": 5, "sha256": hashlib.sha256(b"model").hexdigest()}
        ],
        "memory": [
            {"device": device, "working_memory_bytes": 4_000, "host_memory_bytes": 1_000}
            for device in ("cpu", "cuda", "metal")
        ],
    }
    payload.update(changes)
    return LocalModelManifest.model_validate(payload)
