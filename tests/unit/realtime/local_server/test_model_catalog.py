from __future__ import annotations

import pytest

from jarvis.realtime.local_server import model_catalog


def test_catalog_separates_hearing_from_wake_word_and_speaking() -> None:
    catalog = model_catalog.voice_catalog("")

    assert catalog["hearing"]["id"] == "parakeet-tdt"
    assert "wake-word" in str(catalog["hearing"]["note"])
    assert catalog["current"] == "qwen3-tts-1.7b"
    assert len(catalog["models"]) >= 12
    selectable = [item["id"] for item in catalog["models"] if item["selectable"]]
    assert selectable[:2] == ["qwen3-tts-1.7b", "qwen3-tts-0.6b"]
    assert "pocket-tts-de-24l" in selectable
    frontier = [item for item in catalog["models"] if item["frontier"]]
    assert any(item["id"] == "moss-tts-nano-100m" for item in frontier)
    assert all(item["source_url"].startswith("https://") for item in frontier)


def test_voice_profile_replaces_all_qwen_flags_without_duplicates() -> None:
    command = "serve --tts qwen3 --qwen3_tts_model_name old/model --qwen3_tts_speaker Old"

    rewritten = model_catalog.apply_voice_profile(command, "qwen3-tts-0.6b")

    assert rewritten.count("--tts") == 1
    assert rewritten.count("--qwen3_tts_model_name") == 1
    assert "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice" in rewritten
    assert "--qwen3_tts_speaker Aiden" in rewritten


def test_pocket_german_profile_encodes_language_without_a_new_cli_enum() -> None:
    command = "serve --tts qwen3 --pocket_tts_voice alba --pocket_tts_device cuda"

    rewritten = model_catalog.apply_voice_profile(command, "pocket-tts-de-24l")

    assert rewritten.count("--tts") == 1
    assert "--tts pocket" in rewritten
    assert rewritten.count("--pocket_tts_voice") == 1
    assert "--pocket_tts_voice @german_24l:juergen" in rewritten
    assert "--pocket_tts_device cpu" in rewritten
    assert model_catalog.current_voice_profile(rewritten) == "pocket-tts-de-24l"


def test_unvalidated_upstream_voice_cannot_be_selected() -> None:
    with pytest.raises(ValueError, match="has not passed the managed voice test"):
        model_catalog.apply_voice_profile("serve", "chattts")
