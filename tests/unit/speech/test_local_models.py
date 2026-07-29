"""Guards for the on-disk readiness probe behind the local speech providers.

The regression these pin: a local provider card that reported "ready" on an
install where the engine was never installed and the model never downloaded
(the defect that removed the local Whisper card on 2026-07-03). Every test here
asserts the FAIL-CLOSED direction — an unproven readiness must read as not
ready, because the alternative is a provider the user activates and that then
cannot transcribe a word.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.speech import local_models


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every probe away from the developer's real model cache."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))


def test_missing_engine_is_never_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """No engine installed -> not ready, and the text says to install it."""
    monkeypatch.setattr(local_models, "runtime_installed", lambda runtime: False)

    status = local_models.whisper_status("large-v3")

    assert status.ready is False
    assert status.engine_installed is False
    assert "not installed" in status.detail


def test_installed_engine_without_weights_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine alone is not readiness — the model still has to be there.

    This is the half the removed card got wrong: it treated "the package is
    importable" as "this provider works".
    """
    monkeypatch.setattr(local_models, "runtime_installed", lambda runtime: True)
    monkeypatch.setattr(local_models, "whisper_model_cached", lambda name: False)

    status = local_models.whisper_status("large-v3")

    assert status.ready is False
    assert status.engine_installed is True
    assert status.model_present is False
    assert "downloaded" in status.detail


def test_engine_and_weights_present_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_models, "runtime_installed", lambda runtime: True)
    monkeypatch.setattr(local_models, "whisper_model_cached", lambda name: True)

    status = local_models.whisper_status("large-v3")

    assert status.ready is True
    assert "large-v3" in status.detail


def test_whisper_cache_probe_never_reaches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must ask the cache only — a status poll may not start a download."""
    seen: dict[str, object] = {}

    def _fake_download(name: str, **kwargs: object) -> str:
        seen.update(kwargs)
        return name

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper.utils",
        type("M", (), {"download_model": staticmethod(_fake_download)}),
    )

    assert local_models.whisper_model_cached("large-v3") is True
    assert seen.get("local_files_only") is True


def test_unknown_runtime_reports_absent() -> None:
    """An unrecognised runtime is 'not installed', never an exception."""
    assert local_models.runtime_installed("something-else") is False


def test_incomplete_sherpa_bundle_is_not_present(tmp_path: Path) -> None:
    """A half-extracted download must not count as a usable model.

    An interrupted unpack leaves a directory that exists but cannot load; if
    that read as ready, the failure would surface as a stack trace mid-sentence
    instead of an honest "download it again".
    """
    model_dir = local_models.sherpa_model_dir("demo-model")
    model_dir.mkdir(parents=True)
    (model_dir / "model.onnx").write_bytes(b"not-really-a-model")

    assert (
        local_models.sherpa_model_present(
            "demo-model", required_files=("model.onnx", "tokens.txt")
        )
        is False
    )
    assert local_models.sherpa_model_present("demo-model") is True


def test_catalog_entry_is_resolvable_and_cloud_ids_are_not() -> None:
    """Locality is a catalog fact, so no caller has to branch on a name (AP-21)."""
    entry = local_models.get_local_provider("faster-whisper")

    assert entry is not None
    assert entry.tier == "stt"
    assert entry.model_id == local_models.WHISPER_VOICE_MODEL
    assert local_models.get_local_provider("groq-api") is None
    assert local_models.local_status("groq-api") is None


def test_pip_requirements_match_the_local_voice_extra() -> None:
    """The in-app installer and the packaged extra must ask for the SAME thing.

    They are declared in two files that never meet — the catalog (what the
    install button runs) and pyproject's ``[local-voice]`` extra (what a normal
    pip install brings). Drift here is invisible until someone installs one way
    and gets a different engine version than the other way, which for
    sherpa-onnx means a silently English-only recogniser.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["local-voice"]

    for entry in local_models.LOCAL_PROVIDERS:
        package, _, floor = entry.pip_package.partition(">=")
        matching = [dep for dep in extra if dep.split(";")[0].strip().startswith(package)]
        assert matching, f"{package} is missing from the [local-voice] extra"
        assert any(floor in dep for dep in matching), (
            f"{package} floor {floor} in the catalog does not match "
            f"the [local-voice] extra: {matching}"
        )


def test_status_reports_the_model_the_config_actually_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An override must be reflected, or the card reassures about the wrong model."""
    monkeypatch.setattr(local_models, "runtime_installed", lambda runtime: True)
    monkeypatch.setattr(local_models, "whisper_model_cached", lambda name: False)

    status = local_models.local_status("faster-whisper", model_override="small")

    assert status is not None
    assert status.model_label == "small"
    assert "small" in status.detail
