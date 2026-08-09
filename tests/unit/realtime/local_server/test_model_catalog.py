from __future__ import annotations

import pytest

from jarvis.realtime.local_server import model_catalog


def test_catalog_separates_hearing_from_wake_word_and_speaking() -> None:
    catalog = model_catalog.voice_catalog("")

    assert catalog["hearing"]["id"] == "parakeet-tdt"
    assert "wake-word" in str(catalog["hearing"]["note"])
    assert catalog["current"] == "qwen3-tts-1.7b"
    assert len(catalog["models"]) == 6
    selectable = [item["id"] for item in catalog["models"] if item["selectable"]]
    assert selectable == ["qwen3-tts-1.7b", "qwen3-tts-0.6b"]


def test_voice_profile_replaces_all_qwen_flags_without_duplicates() -> None:
    command = "serve --tts qwen3 --qwen3_tts_model_name old/model --qwen3_tts_speaker Old"

    rewritten = model_catalog.apply_voice_profile(command, "qwen3-tts-0.6b")

    assert rewritten.count("--tts") == 1
    assert rewritten.count("--qwen3_tts_model_name") == 1
    assert "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice" in rewritten
    assert "--qwen3_tts_speaker Aiden" in rewritten


def test_unvalidated_upstream_voice_cannot_be_selected() -> None:
    with pytest.raises(ValueError, match="not yet validated"):
        model_catalog.apply_voice_profile("serve", "chattts")
