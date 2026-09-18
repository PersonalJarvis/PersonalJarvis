"""Managed llama-server: offload planning, presets and discovery."""

from __future__ import annotations

from pathlib import Path

from jarvis.local_models import llama_server as ls

MB = 1024 * 1024


def test_full_offload_when_vram_holds_weights_context_and_margin() -> None:
    # 2.6 GB Q4_K_M 4B on an idle 4 GB card (~3.9 GB free) -> all layers on GPU.
    assert ls.choose_tier(2614 * MB, 3900) == "full"


def test_partial_fit_when_vram_is_short() -> None:
    # A 7B Q4_K_M (~4.4 GB) cannot be resident on the same card.
    assert ls.choose_tier(4400 * MB, 3900) == "fit"


def test_partial_offload_is_sized_in_layers_when_the_count_is_known() -> None:
    # Qwen3.5-9B Q4_K_M, 32 layers, idle 4 GB card: measured best was 22.
    assert ls.choose_tier(5417 * MB, 3965, 32) == "layers:21"


def test_presets_give_a_partial_model_the_smaller_context(tmp_path: Path) -> None:
    text = ls.render_presets([tmp_path / "B.gguf"], {"B": "layers:21"}, ctx=32768)
    assert "n-gpu-layers = 21" in text and f"ctx-size = {ls.PARTIAL_CTX}" in text


def test_gguf_layer_count_is_read_from_the_header(tmp_path: Path) -> None:
    import struct

    key = b"qwen35.block_count"
    blob = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, 2)
    other = b"general.name"
    blob += struct.pack("<Q", len(other)) + other + struct.pack("<I", 8)
    blob += struct.pack("<Q", 4) + b"test"
    blob += struct.pack("<Q", len(key)) + key + struct.pack("<I", 4) + struct.pack("<I", 32)
    path = tmp_path / "m.gguf"
    path.write_bytes(blob)
    assert ls.gguf_block_count(path) == 32
    assert ls.gguf_block_count(tmp_path / "missing.gguf") is None


def test_cpu_when_no_gpu_or_almost_no_free_vram() -> None:
    assert ls.choose_tier(2614 * MB, None) == "cpu"
    assert ls.choose_tier(2614 * MB, 600) == "cpu"


def test_tiers_step_down_in_order() -> None:
    assert ls.OFFLOAD_TIERS == ("full", "fit", "cpu")


def test_presets_one_section_per_model_with_its_tier(tmp_path: Path) -> None:
    a = tmp_path / "A-Q4_K_M.gguf"
    b = tmp_path / "B-Q4_K_M.gguf"
    text = ls.render_presets([a, b], {"A-Q4_K_M": "full", "B-Q4_K_M": "fit"}, ctx=8192)
    assert "[A-Q4_K_M]" in text and "[B-Q4_K_M]" in text
    a_block, b_block = text.split("\n\n")
    assert "n-gpu-layers = 99" in a_block
    assert "fit = on" in b_block and "n-gpu-layers" not in b_block
    assert "ctx-size = 8192" in a_block and "reasoning = off" in a_block


def test_list_models_skips_vision_projectors(tmp_path: Path) -> None:
    (tmp_path / "chat.gguf").write_bytes(b"x" * 10)
    (tmp_path / "mmproj-F16.gguf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("n")
    assert [p.name for p in ls.list_models(tmp_path)] == ["chat.gguf"]


def test_list_models_missing_dir_is_empty(tmp_path: Path) -> None:
    assert ls.list_models(tmp_path / "absent") == []


def test_start_without_install_is_absent_not_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ls, "llama_home", lambda: tmp_path)
    monkeypatch.setattr(ls, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(ls.shutil, "which", lambda _name: None)
    assert ls.LlamaServer().start() is None


def test_presets_cap_the_host_ram_prompt_cache(tmp_path: Path) -> None:
    text = ls.render_presets([tmp_path / "A.gguf"], {"A": "full"})
    assert "cache-ram = 1536" in text


def test_low_first_vram_reading_is_resampled(monkeypatch) -> None:
    readings = iter([3325, 3880])
    monkeypatch.setattr(ls, "free_vram_mb", lambda: next(readings))
    monkeypatch.setattr(ls.time, "sleep", lambda _s: None)
    assert ls._settled_free_vram_mb(2614 * MB) == 3880


def test_presets_attach_an_installed_vision_projector_on_the_cpu(tmp_path: Path) -> None:
    model = tmp_path / "A.gguf"
    (tmp_path / "mmproj-A.gguf").write_bytes(b"x")
    text = ls.render_presets([model, tmp_path / "B.gguf"], {"A": "full", "B": "full"})
    block_a, block_b = text.split("\n\n")
    assert f"mmproj = {tmp_path / 'mmproj-A.gguf'}" in block_a
    assert "mmproj-offload = false" in block_a
    assert "mmproj" not in block_b
