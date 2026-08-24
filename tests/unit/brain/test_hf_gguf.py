"""Hugging Face GGUF browsing: honest parsing, honest degradation, safe names.

The module is a read-only finder for ``hf.co/<user>/<repo>:<quant>`` names.
These tests pin the pieces that would silently lie if they broke — a filename
whose quant is misread (the pull then downloads the wrong file), a repository
name that smuggles a path into the URL, a rate limit that reads as "no
results", and a fit verdict that survives the cache when it must not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import jarvis.brain.hf_gguf as hf
import jarvis.brain.ollama_pull as pull

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "brain"


@pytest.fixture(autouse=True)
def _fresh_caches():
    hf._search_cache.clear()
    hf._files_cache.clear()
    yield
    hf._search_cache.clear()
    hf._files_cache.clear()


@pytest.fixture()
def search_payload() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "hf_models_search.json").read_text(encoding="utf-8"))


@pytest.fixture()
def tree_payload() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "hf_tree_main.json").read_text(encoding="utf-8"))


def _fake_fetch(payload: Any, error: str | None, calls: list[tuple[str, Any]]):
    async def fetch(
        path: str, params: list[tuple[str, str]] | None = None
    ) -> tuple[Any | None, str | None]:
        calls.append((path, params))
        return payload, error

    return fetch


@pytest.fixture()
def _machine(monkeypatch):
    """32 GB RAM, a 16 GB card."""
    monkeypatch.setattr(hf, "total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(hf, "accelerator_gb", lambda: (16.0, "cuda"))


# ── quant parsing & pull names ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Qwen3.8-27B-Q4_K_M.gguf", "Q4_K_M"),
        ("qwen3.8-27b-q4_k_m.gguf", "Q4_K_M"),
        ("model-IQ2_XS.gguf", "IQ2_XS"),
        ("model.Q8_0.gguf", "Q8_0"),
        ("model-BF16.gguf", "BF16"),
        ("gemma-4-12b-it-f16.gguf", "F16"),
        ("split/Qwen-Q5_K_S-00001-of-00002.gguf", "Q5_K_S"),
        ("model-without-a-label.gguf", None),
    ],
)
def test_quant_is_read_from_the_filename(filename: str, expected: str | None) -> None:
    assert hf.parse_quant(filename) == expected


def test_pull_name_builds_the_ollama_form() -> None:
    assert hf.pull_name("unsloth", "Qwen3.8-27B-GGUF") == "hf.co/unsloth/Qwen3.8-27B-GGUF"
    assert (
        hf.pull_name("unsloth", "Qwen3.8-27B-GGUF", "Q4_K_M")
        == "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M"
    )
    assert hf.pull_name("u", "r", "  ") == "hf.co/u/r"


@pytest.mark.parametrize(
    ("user", "repo", "quant"),
    [
        ("../etc", "repo", None),
        ("user", "repo/../../x", None),
        ("user?x=1", "repo", None),
        ("user", "repo", "Q4_K_M?download=true"),
        ("", "repo", None),
    ],
)
def test_pull_name_rejects_path_and_query_shaped_segments(
    user: str, repo: str, quant: str | None
) -> None:
    with pytest.raises(ValueError):
        hf.pull_name(user, repo, quant)


# ── search ───────────────────────────────────────────────────────────────


def test_search_parser_reads_the_gguf_block(search_payload) -> None:
    repos = hf.parse_search_payload(search_payload)
    assert [r["id"] for r in repos] == [
        "unsloth/Qwen3.8-27B-GGUF",
        "bartowski/gemma-4-12b-it-GGUF",
        "someone/mystery-GGUF",
    ]
    qwen = repos[0]
    assert qwen["author"] == "unsloth"
    assert qwen["downloads"] == 184213
    assert qwen["likes"] == 412
    assert qwen["last_modified"].startswith("2026-08-19")
    assert qwen["architecture"] == "qwen3"
    assert qwen["total_params"] == 27_200_000_000
    assert qwen["context_length"] == 262144


def test_search_parser_keeps_an_entry_without_gguf_metadata(search_payload) -> None:
    """A repo without the expanded block still lists — author falls back to
    the id, the unknown numbers stay ``None`` rather than 0."""
    mystery = hf.parse_search_payload(search_payload)[2]
    assert mystery["author"] == "someone"
    assert mystery["architecture"] == ""
    assert mystery["total_params"] is None
    assert mystery["context_length"] is None


def test_search_parser_answers_empty_on_garbage() -> None:
    assert hf.parse_search_payload({"error": "nope"}) == []
    assert hf.parse_search_payload("text") == []


@pytest.mark.asyncio
async def test_search_sends_the_documented_query(monkeypatch, search_payload) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(search_payload, None, calls))
    result = await hf.search("qwen", sort="lastModified", limit=10)
    assert result["error"] is None
    assert len(result["repos"]) == 3
    path, params = calls[0]
    assert path == "/api/models"
    assert dict(params) == {
        "filter": "gguf",
        "search": "qwen",
        "sort": "lastModified",
        "direction": "-1",
        "limit": "10",
        "expand[]": "gguf",
    }


@pytest.mark.asyncio
async def test_search_falls_back_to_downloads_on_an_unknown_sort(
    monkeypatch, search_payload
) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(search_payload, None, calls))
    await hf.search("", sort="evil; drop")
    params = dict(calls[0][1])
    assert params["sort"] == "downloads"
    assert "search" not in params


@pytest.mark.asyncio
async def test_search_caches_per_query_and_sort(monkeypatch, search_payload) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(search_payload, None, calls))
    await hf.search("qwen")
    await hf.search("Qwen")
    assert len(calls) == 1
    await hf.search("qwen", sort="trendingScore")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_search_degrades_honestly_when_offline(monkeypatch) -> None:
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(None, hf._OFFLINE_SENTENCE, []))
    result = await hf.search("qwen")
    assert result["repos"] == []
    assert "huggingface.co" in result["error"]


@pytest.mark.asyncio
async def test_search_names_the_rate_limit_window(monkeypatch) -> None:
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(None, hf._RATE_LIMIT_SENTENCE, []))
    result = await hf.search("qwen")
    assert result["repos"] == []
    assert "5-minute" in result["error"]
    assert "token" in result["error"]


@pytest.mark.asyncio
async def test_search_reports_an_unreadable_shape(monkeypatch) -> None:
    """A schema change upstream must surface as a sentence, not "no results"."""
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch([{"weird": 1}], None, []))
    result = await hf.search("qwen")
    assert result["repos"] == []
    assert "cannot read" in result["error"]


# ── files ────────────────────────────────────────────────────────────────


def test_tree_parser_keeps_only_gguf_files_with_sizes(tree_payload) -> None:
    files = hf.parse_tree_payload(tree_payload)
    names = [f["filename"] for f in files]
    assert ".gitattributes" not in names
    assert "README.md" not in names
    assert "imatrix" not in names
    # Smallest first, unknown size last.
    assert names == [
        "Qwen3.8-27B-IQ2_XS.gguf",
        "Qwen3.8-27B-Q4_K_M.gguf",
        "Qwen3.8-27B-Q8_0.gguf",
        "Qwen3.8-27B-BF16.gguf",
        "weird-no-size.gguf",
    ]
    q4 = files[1]
    assert q4["quant"] == "Q4_K_M"
    assert q4["size_gb"] == 16.7
    assert files[-1]["size_gb"] is None


@pytest.mark.asyncio
async def test_files_carry_a_fit_verdict_per_quant(monkeypatch, tree_payload, _machine) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(tree_payload, None, calls))
    result = await hf.files("unsloth", "Qwen3.8-27B-GGUF")
    assert result["error"] is None
    assert calls[0][0] == "/api/models/unsloth/Qwen3.8-27B-GGUF/tree/main"
    by_quant = {f["quant"]: f for f in result["files"] if f["quant"]}
    assert by_quant["IQ2_XS"]["fit"] == "comfortable"
    assert "graphics memory" in by_quant["IQ2_XS"]["fit_note"]
    assert by_quant["Q4_K_M"]["fit"] == "tight"
    assert by_quant["BF16"]["fit"] == "tight"
    unknown = next(f for f in result["files"] if f["size_gb"] is None)
    assert unknown["fit"] == "unknown"
    assert unknown["fit_note"] == ""


@pytest.mark.asyncio
async def test_files_verdicts_follow_the_machine_not_the_cache(monkeypatch, tree_payload) -> None:
    """The cache holds the CATALOGUE; the verdict is judged fresh each call."""
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hf, "_fetch_json", _fake_fetch(tree_payload, None, calls))
    monkeypatch.setattr(hf, "total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(hf, "accelerator_gb", lambda: (0.0, "none"))
    first = await hf.files("unsloth", "Qwen3.8-27B-GGUF")
    q4_first = next(f for f in first["files"] if f["quant"] == "Q4_K_M")
    assert q4_first["fit"] == "comfortable"
    assert "graphics" not in q4_first["fit_note"]

    monkeypatch.setattr(hf, "accelerator_gb", lambda: (24.0, "cuda"))
    second = await hf.files("unsloth", "Qwen3.8-27B-GGUF")
    q4_second = next(f for f in second["files"] if f["quant"] == "Q4_K_M")
    assert "graphics memory" in q4_second["fit_note"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_files_reject_a_path_shaped_repository(_machine) -> None:
    result = await hf.files("unsloth", "../../admin")
    assert result["files"] == []
    assert result["error"] == "Not a valid Hugging Face repository name."


@pytest.mark.asyncio
async def test_files_report_a_repository_without_gguf(monkeypatch, _machine) -> None:
    monkeypatch.setattr(
        hf,
        "_fetch_json",
        _fake_fetch([{"type": "file", "path": "model.safetensors", "size": 5}], None, []),
    )
    result = await hf.files("someone", "safetensors-only")
    assert result["files"] == []
    assert "no GGUF" in result["error"]


@pytest.mark.asyncio
async def test_files_pass_through_a_404(monkeypatch, _machine) -> None:
    monkeypatch.setattr(
        hf, "_fetch_json", _fake_fetch(None, "Hugging Face has no repository with that name.", [])
    )
    result = await hf.files("nobody", "nothing")
    assert result["files"] == []
    assert "no repository" in result["error"]


# ── the pull's 404 sentence ──────────────────────────────────────────────


def test_not_found_sentence_names_hugging_face_for_hf_names() -> None:
    plain = pull.not_found_message("no-such-model")
    assert "ollama.com/library" in plain

    repo = pull.not_found_message("hf.co/nobody/nothing-GGUF")
    assert "Hugging Face" in repo
    assert "nobody/nothing-GGUF" in repo
    assert "ollama.com/library" not in repo

    quant = pull.not_found_message("huggingface.co/unsloth/Qwen3.8-27B-GGUF:Q9_Z")
    assert "Q9_Z" in quant
    assert "quantization" in quant
