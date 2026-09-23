"""Model transactions preserve the previous choice and one conversation owner."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from jarvis.realtime.local_runtime.events import NativeAudioError
from jarvis.realtime.local_runtime.launch import (
    LfmBindings,
    NativeLaunchPlan,
    NativeRunner,
    prepare_lfm_launch,
)
from jarvis.realtime.local_runtime.paths import windows_extended_path
from jarvis.realtime.local_runtime.runtime import LocalVoiceRuntime
from tests.fakes.fake_local_voice_model import model_manifest
from tests.fakes.fake_native_runtime import FakeNativeProcess


def plan(name: str) -> NativeLaunchPlan:
    return NativeLaunchPlan(name, name, "test-hash", "test", (name,))


@pytest.mark.asyncio
async def test_loading_is_not_readiness_until_audio_is_proven(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    with pytest.raises(NativeAudioError, match="speech test"):
        await runtime.warm(plan("silent"))
    assert runtime.snapshot()["phase"] == "failed"
    assert not runtime.snapshot()["audio_ready"]
    assert not runtime.snapshot()["jarvis_qualified"]


@pytest.mark.asyncio
async def test_failed_switch_restores_previous_working_model(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    await runtime.warm(plan("working"))
    original = runtime._worker
    with pytest.raises(NativeAudioError):
        await runtime.warm(plan("broken"))
    assert runtime.snapshot()["model_id"] == "working"
    assert runtime.snapshot()["audio_ready"]
    assert "restored" in runtime.snapshot()["error"]
    assert not original.running
    assert runtime._worker is not original
    await runtime.stop()


@pytest.mark.asyncio
async def test_listening_conversation_keeps_ownership_between_model_turns(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    token, process = await runtime.acquire_conversation(plan("working"))
    assert process.discarded
    assert not process.busy
    with pytest.raises(NativeAudioError, match="conversation"):
        await runtime.acquire_conversation(plan("working"))
    with pytest.raises(NativeAudioError, match="conversation"):
        await runtime.warm(plan("other"))
    with pytest.raises(NativeAudioError, match="conversation"):
        await runtime.stop()
    assert runtime.snapshot()["model_id"] == "working"
    with pytest.raises(NativeAudioError, match="does not own"):
        await runtime.release_conversation("wrong-owner")
    await runtime.release_conversation(token)
    assert runtime.snapshot()["audio_ready"]
    assert not runtime.snapshot()["conversation_active"]
    await runtime.stop()


@pytest.mark.asyncio
async def test_crash_recovery_constructs_a_fresh_inference_instance(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    await runtime.warm(plan("working"))
    crashed = runtime._worker
    crashed.running = False
    assert runtime.snapshot()["phase"] == "failed"
    await runtime.warm(plan("working"))
    assert runtime.snapshot()["audio_ready"]
    assert runtime._worker is not crashed
    await runtime.stop()


@pytest.mark.asyncio
async def test_unfinished_conversation_reaps_its_native_instance(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    token, process = await runtime.acquire_conversation(plan("working"))
    process.busy = True
    await runtime.release_conversation(token)
    assert not process.running
    assert runtime.snapshot()["phase"] == "stopped"


@pytest.mark.asyncio
async def test_recovery_retains_the_chat_owner_but_not_uncertain_model_state(
    tmp_path: Path,
) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    owner, failed = await runtime.acquire_conversation(plan("working"))
    failed.running = False
    recovered = await runtime.recover_conversation(owner)
    assert recovered is not failed
    assert recovered.discarded
    assert runtime.snapshot()["conversation_active"]
    with pytest.raises(NativeAudioError, match="conversation"):
        await runtime.acquire_conversation(plan("working"))
    await runtime.release_conversation(owner)
    await runtime.stop()


@pytest.mark.asyncio
async def test_failed_recovery_does_not_trap_the_conversation_lease(tmp_path: Path) -> None:
    calls = 0

    def factory(command, directory, **kwargs):
        nonlocal calls
        calls += 1
        return FakeNativeProcess(command if calls == 1 else ("broken",), directory, **kwargs)

    runtime = LocalVoiceRuntime(tmp_path, process_factory=factory)
    owner, _ = await runtime.acquire_conversation(plan("working"))
    with pytest.raises(NativeAudioError):
        await runtime.recover_conversation(owner)
    assert runtime.snapshot()["phase"] == "failed"
    await runtime.release_conversation(owner)
    assert not runtime.snapshot()["conversation_active"]
    await runtime.stop()


def test_custom_names_bind_to_model_components_without_shell_parsing(tmp_path: Path) -> None:
    names = ("my model.gguf", "encoder.gguf", "tokens.gguf", "voice.gguf")
    digest = hashlib.sha256(b"model").hexdigest()
    manifest = model_manifest(
        family="lfm2-audio-gguf",
        artifacts=[{"path": name, "sha256": digest, "size_bytes": 5} for name in names],
    )
    for name in names:
        (tmp_path / name).write_bytes(b"model")
    executable = tmp_path / "native worker.exe"
    executable.write_bytes(b"binary")
    runner = NativeRunner(executable, hashlib.sha256(b"binary").hexdigest(), "test")
    bindings = LfmBindings(*names)
    selected = prepare_lfm_launch(manifest, tmp_path, runner, bindings)
    assert Path(selected.command[2]).read_bytes() == b"model"
    assert selected.model_id == manifest.id
    assert selected.manifest_fingerprint == manifest.fingerprint
    assert (
        selected.identity
        != prepare_lfm_launch(manifest, tmp_path, runner, bindings, threads=2).identity
    )
    with pytest.raises(NativeAudioError, match="four required"):
        prepare_lfm_launch(manifest, tmp_path, runner, LfmBindings("../outside.gguf", *names[1:]))
    executable.write_bytes(b"tampered")
    with pytest.raises(NativeAudioError, match="runtime failed verification"):
        prepare_lfm_launch(manifest, tmp_path, runner, bindings)


def test_native_windows_paths_cover_long_files_and_unc_without_changing_the_filename() -> None:
    name = "C:\\models\\" + "nested\\" * 45 + "function-head.gguf"
    assert len(name) > 260
    assert windows_extended_path(name) == "\\\\?\\" + name
    assert (
        windows_extended_path("\\\\storage\\models\\voice.gguf")
        == "\\\\?\\UNC\\storage\\models\\voice.gguf"
    )
    assert windows_extended_path("\\\\?\\C:\\models\\voice.gguf") == "\\\\?\\C:\\models\\voice.gguf"
    with pytest.raises(ValueError):
        windows_extended_path("relative/model.gguf")


@pytest.mark.asyncio
async def test_cancelled_model_switch_restores_the_previous_selection(tmp_path: Path) -> None:
    runtime = LocalVoiceRuntime(tmp_path, process_factory=FakeNativeProcess)
    await runtime.warm(plan("working"))
    old = runtime._worker
    stopping = asyncio.Event()

    async def blocked_close():
        stopping.set()
        try:
            await asyncio.sleep(60)
        finally:
            old.running = False

    old.close = blocked_close
    switching = asyncio.create_task(runtime.warm(plan("other")))
    await stopping.wait()
    switching.cancel()
    with pytest.raises(asyncio.CancelledError):
        await switching
    assert runtime.snapshot()["model_id"] == "working"
    assert runtime.snapshot()["audio_ready"]
    assert runtime._worker is not old
    await runtime.stop()
