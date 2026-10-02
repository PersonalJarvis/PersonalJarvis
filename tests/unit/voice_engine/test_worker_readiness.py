"""A successfully loaded model is not yet a proven voice."""

from __future__ import annotations

import pytest

from jarvis.voice_engine import protocol as p
from jarvis.voice_engine import runtime
from jarvis.voice_engine.worker import Worker


class Transport:
    def __init__(self) -> None:
        self.messages = []

    def send(self, data: bytes) -> None:
        self.messages.extend(p.FrameReader().feed(data))


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["pass", "fail", "raise"])
async def test_ready_requires_a_real_speech_and_llm_selftest(
    verdict: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = object()

    def build(config, progress):
        return models, {"voices": {"en": "piper"}}

    def selftest(actual, languages):
        assert actual is models and languages == ["en"]
        if verdict == "raise":
            raise RuntimeError("empty voice output")
        return {"ok": verdict == "pass"}

    monkeypatch.setattr(runtime, "build_models", build)
    monkeypatch.setattr(runtime, "selftest", selftest)
    transport = Transport()
    worker = Worker(transport)
    await worker._configure({"type": "configure", "languages": ["en"]})
    states = [m for m in transport.messages if m["type"] == "state"]
    assert states[-2]["stage"] == "selftest"
    assert states[-1]["phase"] == ("ready" if verdict == "pass" else "failed")
    assert (worker._models is models) == (verdict == "pass")
    assert ("ready" in [m["phase"] for m in states]) == (verdict == "pass")
