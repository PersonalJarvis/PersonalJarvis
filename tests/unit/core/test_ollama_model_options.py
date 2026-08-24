"""Per-model Ollama options: values that never brick, a writer that round-trips.

Two properties matter here. First, AP-16: the table
``[brain.providers.ollama.models."<tag>"]`` is reachable by hand in
``jarvis.toml``, so an out-of-range number is clamped and a value of the wrong
shape is dropped — never a ``ValidationError`` that costs a boot. Second, the
writer: it is the only sanctioned way to touch the table (AP-7), it refuses
keys outside the closed list, and what it writes is exactly what
``load_config`` reads back — BOM included.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.config import (
    OLLAMA_MODEL_OPTION_KEYS,
    BrainProviderConfig,
    JarvisConfig,
    OllamaModelOptions,
    load_config,
    ollama_hf_enabled,
)
from jarvis.core.config_writer import (
    PROVIDER_FLAG_KEYS,
    clear_ollama_model_options,
    set_ollama_hf_enabled,
    set_ollama_model_options,
    set_provider_flag,
)

#: ``field -> (below the floor, above the ceiling, clamped floor, clamped ceiling)``.
NUMERIC_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "num_ctx": (1, 99_999_999, 512, 1_048_576),
    "num_gpu": (-9, 5000, -1, 999),
    "num_thread": (-1, 9999, 0, 512),
    "num_predict": (-99, 99_999_999, -2, 1_048_576),
    "temperature": (-1.0, 9.0, 0.0, 2.0),
    "top_p": (-0.5, 4.0, 0.0, 1.0),
    "min_p": (-0.5, 4.0, 0.0, 1.0),
    "top_k": (-3, 99_999, 0, 1000),
    "repeat_penalty": (-1.0, 42.0, 0.0, 3.0),
    "seed": (-7, 2**40, 0, 2**31 - 1),
}


# ----------------------------------------------------------------------
# Clamping (AP-16)
# ----------------------------------------------------------------------


def test_everything_is_unset_by_default() -> None:
    opts = OllamaModelOptions()
    for key in OLLAMA_MODEL_OPTION_KEYS:
        assert getattr(opts, key) is None, key


@pytest.mark.parametrize("field", sorted(NUMERIC_BOUNDS))
def test_numeric_knobs_are_clamped_never_rejected(field: str) -> None:
    below, above, floor, ceiling = NUMERIC_BOUNDS[field]
    assert getattr(OllamaModelOptions(**{field: below}), field) == floor
    assert getattr(OllamaModelOptions(**{field: above}), field) == ceiling
    # Garbage falls back to "unset" — Ollama's own default is a working value.
    assert getattr(OllamaModelOptions(**{field: "nonsense"}), field) is None
    assert getattr(OllamaModelOptions(**{field: float("nan")}), field) is None


def test_keep_alive_accepts_go_durations_and_seconds() -> None:
    assert OllamaModelOptions(keep_alive="30m").keep_alive == "30m"
    assert OllamaModelOptions(keep_alive="1h30m").keep_alive == "1h30m"
    assert OllamaModelOptions(keep_alive=600).keep_alive == 600
    assert OllamaModelOptions(keep_alive="-1").keep_alive == -1
    assert OllamaModelOptions(keep_alive=-99).keep_alive == -1
    assert OllamaModelOptions(keep_alive="forever").keep_alive is None
    assert OllamaModelOptions(keep_alive=True).keep_alive is None


def test_think_accepts_bool_and_graded_levels() -> None:
    assert OllamaModelOptions(think=True).think is True
    assert OllamaModelOptions(think="off").think is False
    assert OllamaModelOptions(think="HIGH").think == "high"
    assert OllamaModelOptions(think="ultra").think is None
    assert OllamaModelOptions(think=3).think is None


def test_stop_takes_a_string_or_a_list() -> None:
    assert OllamaModelOptions(stop="###").stop == ["###"]
    assert OllamaModelOptions(stop=["</s>", "", 7]).stop == ["</s>"]
    assert OllamaModelOptions(stop=[]).stop is None
    assert OllamaModelOptions(stop=42).stop is None


def test_unknown_keys_survive() -> None:
    """A newer build's knob must not be dropped by an older one (extra="allow")."""
    opts = OllamaModelOptions(num_batch=256)
    assert opts.model_dump()["num_batch"] == 256


def test_the_whole_block_at_once_never_raises() -> None:
    """The AP-16 sweep: every key wrong at the same time still loads."""
    junk = {key: object() for key in OLLAMA_MODEL_OPTION_KEYS}
    opts = OllamaModelOptions(**junk)
    for key in OLLAMA_MODEL_OPTION_KEYS:
        assert getattr(opts, key) is None, key


def test_provider_config_defaults_and_typo_tolerance() -> None:
    provider = BrainProviderConfig()
    assert provider.models == {}
    assert provider.hf_enabled is False
    # A ``models`` that is not a table of tables is ignored, not fatal.
    assert BrainProviderConfig(models="oops").models == {}
    assert BrainProviderConfig(models={"a:1": {"num_ctx": 9}, "b": 3}).models["a:1"].num_ctx == 512
    assert "b" not in BrainProviderConfig(models={"a:1": {}, "b": 3}).models


def test_a_config_written_before_the_feature_still_loads() -> None:
    cfg = JarvisConfig(brain={"providers": {"ollama": {"model": "qwen3.5:9b"}}})
    ollama = cfg.brain.providers["ollama"]
    assert ollama.model == "qwen3.5:9b"
    assert ollama.models == {}
    assert ollama_hf_enabled(cfg) is False


# ----------------------------------------------------------------------
# Writer round trip (AP-7)
# ----------------------------------------------------------------------


@pytest.fixture()
def toml_file(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text(
        '﻿# keep me\n[brain]\nprimary = "ollama"\n\n'
        '[brain.providers.ollama]\nmodel = "qwen3.5:9b"\n',
        encoding="utf-8",
    )
    return path


def test_writer_round_trips_through_load_config(toml_file: Path) -> None:
    written = set_ollama_model_options(
        "qwen3.5:9b",
        {
            "num_ctx": 16384,
            "num_gpu": -1,
            "keep_alive": "30m",
            "think": False,
            "stop": ["</s>"],
            "temperature": 99,  # clamped on the way in
            "top_k": None,  # unset -> not written
        },
        path=toml_file,
    )
    assert written == {
        "num_ctx": 16384,
        "num_gpu": -1,
        "temperature": 2.0,
        "stop": ["</s>"],
        "keep_alive": "30m",
        "think": False,
    }
    raw = toml_file.read_text(encoding="utf-8")
    assert raw.startswith("﻿# keep me")  # BOM and comment preserved
    assert '[brain.providers.ollama.models."qwen3.5:9b"]' in raw
    assert 'model = "qwen3.5:9b"' in raw  # sibling key untouched

    opts = load_config(toml_file).brain.providers["ollama"].models["qwen3.5:9b"]
    assert opts.num_ctx == 16384
    assert opts.num_gpu == -1
    assert opts.temperature == 2.0
    assert opts.stop == ["</s>"]
    assert opts.keep_alive == "30m"
    assert opts.think is False
    assert opts.top_k is None


def test_writer_replaces_the_whole_set(toml_file: Path) -> None:
    set_ollama_model_options("qwen3.5:9b", {"num_ctx": 8192, "top_k": 40}, path=toml_file)
    set_ollama_model_options("qwen3.5:9b", {"num_ctx": 4096}, path=toml_file)
    opts = load_config(toml_file).brain.providers["ollama"].models["qwen3.5:9b"]
    assert opts.num_ctx == 4096
    assert opts.top_k is None


def test_two_tags_live_side_by_side(toml_file: Path) -> None:
    set_ollama_model_options("qwen3.5:9b", {"num_ctx": 8192}, path=toml_file)
    set_ollama_model_options("hf.co/user/repo:Q4_K_M", {"num_ctx": 4096}, path=toml_file)
    models = load_config(toml_file).brain.providers["ollama"].models
    assert models["qwen3.5:9b"].num_ctx == 8192
    assert models["hf.co/user/repo:Q4_K_M"].num_ctx == 4096


def test_writer_refuses_unknown_keys(toml_file: Path) -> None:
    with pytest.raises(ValueError, match="num_batch"):
        set_ollama_model_options("qwen3.5:9b", {"num_batch": 256}, path=toml_file)
    with pytest.raises(ValueError):
        set_ollama_model_options("   ", {"num_ctx": 4096}, path=toml_file)


def test_clear_removes_the_table_and_reports_it(toml_file: Path) -> None:
    set_ollama_model_options("qwen3.5:9b", {"num_ctx": 8192}, path=toml_file)
    assert clear_ollama_model_options("qwen3.5:9b", path=toml_file) is True
    assert clear_ollama_model_options("qwen3.5:9b", path=toml_file) is False
    assert 'qwen3.5:9b"]' not in toml_file.read_text(encoding="utf-8")
    assert load_config(toml_file).brain.providers["ollama"].models == {}


def test_an_empty_set_clears_like_reset(toml_file: Path) -> None:
    set_ollama_model_options("qwen3.5:9b", {"num_ctx": 8192}, path=toml_file)
    assert set_ollama_model_options("qwen3.5:9b", {"num_ctx": None}, path=toml_file) == {}
    assert load_config(toml_file).brain.providers["ollama"].models == {}


def test_hf_flag_round_trips(toml_file: Path) -> None:
    assert ollama_hf_enabled(load_config(toml_file)) is False
    set_ollama_hf_enabled(True, path=toml_file)
    assert ollama_hf_enabled(load_config(toml_file)) is True
    set_provider_flag("ollama", "hf_enabled", False, path=toml_file)
    assert ollama_hf_enabled(load_config(toml_file)) is False
    assert "hf_enabled" in PROVIDER_FLAG_KEYS
    with pytest.raises(ValueError):
        set_provider_flag("ollama", "model", True, path=toml_file)  # type: ignore[arg-type]
