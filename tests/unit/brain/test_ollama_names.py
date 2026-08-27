"""Readable names from tags + manifest facts, and alias folding."""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_names as names


@pytest.mark.parametrize(
    ("tag", "params", "quant", "name", "label"),
    [
        ("qwen3.5:4b", "4.7B", "Q4_K_M", "Qwen 3.5", "Qwen 3.5 4B"),
        ("gemma4:12b-it-qat", "11.9B", "Q4_0", "Gemma 4", "Gemma 4 12B"),
        ("deepseek-r1:latest", "8.2B", "Q4_K_M", "DeepSeek R1", "DeepSeek R1 8.2B"),
        ("deepseek-r1:14b", "14.8B", "Q4_K_M", "DeepSeek R1", "DeepSeek R1 14B"),
        ("qwen3.8-16gb:latest", "26.9B", "IQ2_S", "Qwen 3.8", "Qwen 3.8 27B"),
        ("qwen3-coder:30b", "30.5B", "Q4_K_M", "Qwen 3 Coder", "Qwen 3 Coder 30B"),
        (
            "nemotron-cascade-2:latest",
            "31.6B",
            "Q4_K_M",
            "Nemotron Cascade 2",
            "Nemotron Cascade 2 32B",
        ),
        ("bge-m3:latest", "566.70M", "F16", "BGE M3", "BGE M3 567M"),
        ("deepseek-llm:latest", "7B", "Q4_0", "DeepSeek LLM", "DeepSeek LLM 7B"),
        ("ornith:9b", "9.0B", "Q4_K_M", "Ornith", "Ornith 9B"),
        ("embeddinggemma", "307.58M", "F16", "EmbeddingGemma", "EmbeddingGemma 308M"),
        ("qwen2.5:7b", "7.6B", "Q4_K_M", "Qwen 2.5", "Qwen 2.5 7B"),
    ],
)
def test_describe_humanises_the_tag_and_takes_the_size_from_the_facts(
    tag: str, params: str, quant: str, name: str, label: str
) -> None:
    shown = names.describe(tag, params, quant)
    assert shown.name == name
    assert shown.label == label
    assert shown.quant == quant


def test_the_tags_size_token_wins_over_the_manifests_decimals() -> None:
    # Everyone calls it "the 4B"; 4.7B next to it reads like a different model.
    assert names.describe("qwen3.5:4b", "4.7B").params == "4B"
    # With no size token the manifest decides, rounded the way people say it.
    assert names.describe("qwen3.8-16gb", "26.9B").params == "27B"
    assert names.params_label("9.0B") == "9B"
    assert names.params_label("566.70M") == "567M"
    assert names.params_label("") == ""


def test_variant_markers_stay_off_the_name_but_on_the_variant() -> None:
    shown = names.describe("gemma4:12b-it-qat", "11.9B", "Q4_0")
    assert shown.variant == "it-qat"
    assert "qat" not in shown.name.lower()


def test_hugging_face_imports_keep_their_source() -> None:
    shown = names.describe("hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M", "8.2B", "")
    assert shown.source == "hf.co/unsloth"
    assert shown.name == "Qwen 3"
    assert shown.params == "8B"
    # The quantisation comes from the tag when the manifest has none.
    assert shown.quant == "Q4_K_M"


def test_alias_kind_tells_jarvis_profiles_from_downloads() -> None:
    assert names.alias_kind("ornith:9b-voice-32k") == "voice_profile"
    assert names.alias_kind("ornith-9b-jarvis-04c5d3f6:latest") == "tune_profile"
    assert names.alias_kind("ornith:9b") == ""
    assert names.alias_kind("qwen3.5:4b-voice-8k") == "voice_profile"


def test_base_of_folds_aliases_back_onto_their_download() -> None:
    assert names.base_of("ornith:9b-voice-32k") == "ornith:9b"
    assert names.base_of("ornith:9b-voice-32k:latest") == "ornith:9b"
    # A Tune profile folded ':' into '-'; the installed candidate says which.
    assert (
        names.base_of("ornith-9b-jarvis-04c5d3f6:latest", ["ornith:9b", "qwen3.5:4b"])
        == "ornith:9b"
    )
    assert names.base_of("ornith-9b-jarvis-04c5d3f6", []) == "ornith-9b"
    assert names.base_of("qwen3.5:4b", ["qwen3.5:4b"]) == "qwen3.5:4b"


def test_an_empty_tag_describes_to_nothing() -> None:
    assert names.describe("").label == ""
