from __future__ import annotations

import pytest

from jarvis.realtime.local_server import configure


@pytest.mark.asyncio
async def test_setup_persists_only_after_the_voice_smoke_passes(monkeypatch) -> None:
    from jarvis.core import config_writer
    from jarvis.realtime.local_server import install, smoke, supervisor

    calls: list[str] = []
    monkeypatch.setattr(configure, "build_launch_command", lambda *args, **kwargs: "new")
    monkeypatch.setattr(
        supervisor,
        "replace_idle_managed_runtime",
        lambda **kwargs: calls.append("replace") or "spawned",
    )
    monkeypatch.setattr(
        supervisor, "wait_until_ready", lambda *args, **kwargs: calls.append("ready") or True
    )
    monkeypatch.setattr(supervisor, "warm_brain", lambda **kwargs: calls.append("warm") or True)

    async def voice_test(*args, **kwargs):
        calls.append("voice-test")
        return {"ok": True, "audio_bytes": 4_800}

    monkeypatch.setattr(smoke, "probe_voice_roundtrip", voice_test)
    monkeypatch.setattr(
        config_writer,
        "set_local_realtime_launch_command",
        lambda command: calls.append(f"persist:{command}"),
    )
    monkeypatch.setattr(
        install,
        "repair_smoke_marker_from_live_runtime",
        lambda base_url: calls.append("marker") or True,
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )
    monkeypatch.setattr(supervisor, "status", lambda base_url: {"ready": True})

    result = await configure.apply_and_test_stack(
        base_url="http://127.0.0.1:8765",
        current_command="old",
        brain_model="qwen3.5:4b",
        voice_model="qwen3-tts-1.7b",
        language="en",
    )

    assert result["ok"] is True
    assert calls.index("voice-test") < calls.index("persist:new")


@pytest.mark.asyncio
async def test_failed_voice_smoke_restores_previous_runtime(monkeypatch) -> None:
    from jarvis.core import config_writer
    from jarvis.realtime.local_server import smoke, supervisor

    replacements: list[str] = []
    monkeypatch.setattr(configure, "build_launch_command", lambda *args, **kwargs: "new")
    monkeypatch.setattr(
        supervisor,
        "replace_idle_managed_runtime",
        lambda **kwargs: replacements.append(kwargs["launch_command"]) or "spawned",
    )
    monkeypatch.setattr(supervisor, "wait_until_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "warm_brain", lambda **kwargs: True)
    monkeypatch.setattr(supervisor, "start_runtime_monitor", lambda **kwargs: True)

    async def failed_voice_test(*args, **kwargs):
        raise RuntimeError("no usable speech audio")

    monkeypatch.setattr(smoke, "probe_voice_roundtrip", failed_voice_test)
    monkeypatch.setattr(
        config_writer,
        "set_local_realtime_launch_command",
        lambda command: pytest.fail("a failed model must never be persisted"),
    )

    with pytest.raises(configure.ManagedSetupError, match="previous setup was restored"):
        await configure.apply_and_test_stack(
            base_url="http://127.0.0.1:8765",
            current_command="old",
            brain_model="qwen3.5:4b",
            voice_model="qwen3-tts-1.7b",
            language="en",
        )

    assert replacements == ["new", "old"]
