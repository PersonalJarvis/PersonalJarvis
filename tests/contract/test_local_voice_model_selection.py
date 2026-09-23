"""Simulated adapter capabilities on every OS; no physical-device claims."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.realtime.local_runtime.models import LocalModelManifest
from jarvis.realtime.local_runtime.selection import RuntimeProbe, VoiceRequirements, assess_model
from tests.fakes.fake_local_voice_model import model_manifest


def runtime(**changes: object) -> RuntimeProbe:
    payload: dict[str, object] = {
        "adapter": "test-adapter",
        "os": "windows",
        "device": "cpu",
        "available": True,
        "families": ["test-audio"],
        "capabilities": model_manifest().capabilities,
        "working_memory_bytes": 6_000,
        "host_memory_bytes": 6_000,
    }
    payload.update(changes)
    return RuntimeProbe.model_validate(payload)


@pytest.mark.parametrize(
    ("os", "device"),
    [
        ("windows", "cpu"),
        ("windows", "cuda"),
        ("macos", "cpu"),
        ("macos", "metal"),
        ("linux", "cpu"),
        ("linux", "cuda"),
    ],
)
def test_fitting_model_does_not_need_a_global_gpu_memory_floor(os: str, device: str) -> None:
    report = assess_model(
        model_manifest(), runtime(os=os, device=device), VoiceRequirements(language="de")
    )
    assert report.eligible
    assert report.blockers == ()
    assert "ready" not in report.model_dump()


@pytest.mark.parametrize(
    ("os", "device"), [("windows", "metal"), ("linux", "metal"), ("macos", "cuda")]
)
def test_impossible_runtime_device_pair_is_not_eligible(os: str, device: str) -> None:
    assert not assess_model(
        model_manifest(), runtime(os=os, device=device), VoiceRequirements(language="en")
    ).eligible


@pytest.mark.parametrize("device", ["metal", "cpu"])
def test_shared_memory_includes_host_and_model_allocations(device: str) -> None:
    report = assess_model(
        model_manifest(),
        runtime(os="macos", device=device, working_memory_bytes=4_500),
        VoiceRequirements(language="en"),
    )
    assert not report.eligible
    assert any("shared memory" in reason for reason in report.blockers)


@pytest.mark.parametrize(
    ("working", "host"), [(3_999, 6_000), (6_000, 999), (None, 6_000), (6_000, None)]
)
def test_insufficient_or_unknown_memory_does_not_autoselect(
    working: int | None, host: int | None
) -> None:
    report = assess_model(
        model_manifest(),
        runtime(device="cuda", working_memory_bytes=working, host_memory_bytes=host),
        VoiceRequirements(language="en"),
    )
    assert not report.eligible


def test_model_cannot_grant_tools_to_an_adapter_without_a_result_channel() -> None:
    probe = runtime(capabilities=model_manifest().capabilities - {"tool_results"})
    report = assess_model(model_manifest(), probe, VoiceRequirements(language="en"))
    assert not report.eligible
    assert report.blockers == ("The adapter cannot provide tool_results.",)


def test_english_audio_model_is_not_a_german_recommendation() -> None:
    report = assess_model(
        model_manifest(languages=["en"]), runtime(), VoiceRequirements(language="de-DE")
    )
    assert not report.eligible
    assert any("speech support" in reason for reason in report.blockers)


def test_regional_request_can_use_explicit_primary_language_support() -> None:
    assert assess_model(model_manifest(), runtime(), VoiceRequirements(language="de-DE")).eligible


def test_other_architecture_or_unavailable_adapter_is_not_eligible() -> None:
    report = assess_model(
        model_manifest(),
        runtime(available=False, families=["tts-only"], reason="Runtime missing."),
        VoiceRequirements(language="en"),
    )
    assert report.blockers == (
        "Runtime missing.",
        "The runtime does not support this model architecture.",
    )


@pytest.mark.parametrize(
    "path",
    [
        "../model.gguf",
        "/model.gguf",
        "C:/model.gguf",
        "sub\\model.gguf",
        "sub//model.gguf",
        "sub/./model.gguf",
        "CON.gguf",
        "sub/NUL.json",
        "sub /model.gguf",
        "sub./model.gguf",
        "model.gguf:payload",
        "runner.py",
    ],
)
def test_custom_artifact_paths_are_safe_on_all_operating_systems(path: str) -> None:
    artifact = model_manifest().artifacts[0].model_dump()
    artifact["path"] = path
    with pytest.raises(ValidationError):
        model_manifest(artifacts=[artifact])


def test_custom_models_cannot_supply_executable_setup_instructions() -> None:
    with pytest.raises(ValidationError):
        model_manifest(launch_command="anything")
    with pytest.raises(ValidationError):
        model_manifest(trust_remote_code=True)


def test_mutable_revision_and_duplicate_paths_are_rejected() -> None:
    with pytest.raises(ValidationError):
        model_manifest(
            source={"kind": "huggingface", "repository": "test/voice", "revision": "main"}
        )
    artifact = model_manifest().artifacts[0].model_dump()
    with pytest.raises(ValidationError):
        model_manifest(artifacts=[artifact, {**artifact, "path": "MODEL.gguf"}])


def test_tts_only_checkpoint_is_not_a_native_realtime_model() -> None:
    with pytest.raises(ValidationError):
        model_manifest(capabilities=["audio_output"])


def test_manifest_roundtrip_has_a_stable_content_identity() -> None:
    original = model_manifest()
    restored = LocalModelManifest.model_validate_json(original.model_dump_json())
    assert restored.fingerprint == original.fingerprint
    assert restored.download_bytes == 5
    assert restored.artifact_url(restored.artifacts[0]) == (
        "https://huggingface.co/test/voice/resolve/" + "a" * 40 + "/model.gguf"
    )
    changed = model_manifest(
        source={"kind": "huggingface", "repository": "test/voice", "revision": "b" * 40}
    )
    assert changed.fingerprint != original.fingerprint


def test_own_local_weights_do_not_require_a_remote_repository() -> None:
    model = model_manifest(source={"kind": "local"})
    assert model.source.kind == "local"
    with pytest.raises(ValueError, match="no download URL"):
        model.artifact_url(model.artifacts[0])


@pytest.mark.parametrize("model_id", ["con", "nul", "com1", "lpt9", "model."])
def test_model_identity_is_a_portable_directory_name(model_id: str) -> None:
    with pytest.raises(ValidationError):
        model_manifest(id=model_id)
