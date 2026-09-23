"""Real filesystem verification; never load an untrusted model as Python."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.realtime.local_runtime.__main__ import main
from jarvis.realtime.local_runtime.catalog import native_model_catalog
from jarvis.realtime.local_runtime.packages import load_manifest, verify_package
from tests.fakes.fake_local_voice_model import model_manifest


def test_complete_package_is_verified_but_not_a_live_runtime(tmp_path: Path, capsys) -> None:
    model = model_manifest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(model.model_dump_json(), encoding="utf-8")
    (tmp_path / "model.gguf").write_bytes(b"model")
    assert load_manifest(manifest) == model
    assert verify_package(model, tmp_path).verified
    assert main(["inspect", str(manifest), "--weights", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["package"]["verified"] is True
    assert result["runtime_qualified"] is False


@pytest.mark.parametrize(
    ("content", "state"),
    [(b"", "size_mismatch"), (b"broken", "size_mismatch"), (b"other", "hash_mismatch")],
)
def test_partial_or_modified_weights_are_never_ready(
    tmp_path: Path, content: bytes, state: str
) -> None:
    (tmp_path / "model.gguf").write_bytes(content)
    check = verify_package(model_manifest(), tmp_path)
    assert not check.verified
    assert check.artifacts[0].state == state


def test_missing_package_does_not_create_any_files(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    check = verify_package(model_manifest(), root)
    assert not root.exists()
    assert not check.verified
    assert check.artifacts[0].state == "missing"


def test_directory_cannot_stand_in_for_a_weight_file(tmp_path: Path) -> None:
    (tmp_path / "model.gguf").mkdir()
    assert verify_package(model_manifest(), tmp_path).artifacts[0].state == "unsafe"


def test_symlink_cannot_escape_the_model_package(tmp_path: Path) -> None:
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"model")
    package = tmp_path / "package"
    package.mkdir()
    try:
        (package / "model.gguf").symlink_to(outside)
    except OSError:
        pytest.skip("The test host cannot create symlinks.")
    assert verify_package(model_manifest(), package).artifacts[0].state == "unsafe"


def test_invalid_custom_manifest_does_not_echo_input(tmp_path: Path, capsys) -> None:
    path = tmp_path / "invalid.json"
    payload = model_manifest().model_dump(mode="json")
    payload["secret"] = "must-not-be-echoed"  # noqa: S105 - redaction test sentinel
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["inspect", str(path)]) == 2
    assert "must-not-be-echoed" not in capsys.readouterr().out


def test_schema_is_available_without_loading_an_inference_engine(capsys) -> None:
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False
    assert "artifacts" in schema["required"]


def test_native_catalog_does_not_claim_unverified_language_or_tools(capsys) -> None:
    model = native_model_catalog()[0]
    assert model.languages == ("en",)
    assert "tool_calls" not in model.capabilities
    assert "full_duplex" not in model.capabilities
    assert not model.memory
    assert len(model.artifacts) == 4
    assert main(["inspect", model.id]) == 0
    assert json.loads(capsys.readouterr().out)["runtime_qualified"] is False


def test_voicechat_candidate_keeps_model_claims_separate_from_hardware_qualification() -> None:
    models = {model.id: model for model in native_model_catalog()}
    candidate = models["nemotron-voicechat-11b-q4"]
    assert candidate.family == "nemotron-voicechat-gguf"
    assert {"tool_calls", "tool_results", "full_duplex"} <= candidate.capabilities
    assert candidate.languages == ("en",)
    assert candidate.memory == ()
    assert len(candidate.artifacts) == 4
    assert candidate.download_bytes == 6520209856


def test_local_import_is_reachable_through_the_command_line(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.gguf").write_bytes(b"model")
    manifest = tmp_path / "model.json"
    model = model_manifest(source={"kind": "local"})
    manifest.write_text(model.model_dump_json(), encoding="utf-8")
    assert (
        main(
            ["acquire", str(manifest), "--store", str(tmp_path / "store"), "--source", str(source)]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["package_verified"] is True
    assert result["runtime_qualified"] is False
    assert Path(result["weights"]).is_dir()


def test_native_model_management_imports_without_loading_inference() -> None:
    code = (
        "import sys; from jarvis.realtime.local_runtime import catalog, selection, store; "
        "assert not {'torch', 'transformers', 'ctranslate2', 'onnxruntime', 'mlx'} "
        "& set(sys.modules); "
        "assert store._HTTP._client is None; print('LOCAL_MODEL_IMPORT_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LOCAL_MODEL_IMPORT_OK"
