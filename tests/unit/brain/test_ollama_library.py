"""Browsing the full Ollama library: tolerant parsers, honest degradation.

The library browser scrapes ollama.com because there is no JSON search API and
the registry's ``tags/list`` answers 404. Scraping earns its keep only if it
fails HONESTLY: a page that stops parsing must surface as an error sentence,
never as a silently empty panel, and a missing size must read "unknown" — not
quietly reclassify a local tag as cloud-only. These tests pin the parsers
against trimmed snapshots of the real pages and the degradation contract
against a faked fetch layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.brain.ollama_library as library

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "brain"


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Module caches survive the process on purpose; tests must not share them."""
    library._search_cache.clear()
    library._tags_cache.clear()
    yield
    library._search_cache.clear()
    library._tags_cache.clear()


@pytest.fixture()
def search_page() -> str:
    return (_FIXTURES / "ollama_search.html").read_text(encoding="utf-8")


@pytest.fixture()
def tags_page() -> str:
    return (_FIXTURES / "ollama_tags.html").read_text(encoding="utf-8")


# ── search parser ────────────────────────────────────────────────────────


def test_search_parser_reads_the_full_entry(search_page: str) -> None:
    models = library.parse_search_html(search_page)
    assert [m["name"] for m in models] == ["qwen3.5", "qwen3-embedding", "deepscaler"]

    qwen = models[0]
    assert qwen["description"].startswith("Qwen 3.5 is a family")
    assert qwen["capabilities"] == ["vision", "tools", "thinking"]
    assert qwen["cloud"] is True
    assert qwen["sizes"] == ["0.8b", "2b", "122b"]
    assert qwen["pulls"] == "17.2M"
    assert qwen["updated"] == "2 months ago"


def test_search_parser_keeps_a_minimal_entry(search_page: str) -> None:
    """An entry the page renders without badges still lists — a partial row
    beats a vanished model."""
    minimal = library.parse_search_html(search_page)[2]
    assert minimal["name"] == "deepscaler"
    assert minimal["capabilities"] == []
    assert minimal["sizes"] == []
    assert minimal["pulls"] == ""
    assert minimal["cloud"] is False


def test_search_parser_skips_non_library_items(search_page: str) -> None:
    """The nav <li> linking /download must not become a phantom model."""
    names = {m["name"] for m in library.parse_search_html(search_page)}
    assert "download" not in {n.lower() for n in names}


def test_search_parser_answers_empty_on_garbage() -> None:
    assert library.parse_search_html("<html><body>maintenance</body></html>") == []


# ── tags parser ──────────────────────────────────────────────────────────


def test_tags_parser_dedupes_the_mobile_and_desktop_blocks(tags_page: str) -> None:
    tags = library.parse_tags_html(tags_page, "qwen3.5")
    assert [t["tag"] for t in tags] == ["latest", "cloud", "0.8b", "0.3b", "0.8b-q8_0"]


def test_tags_parser_reads_sub_gigabyte_sizes(tags_page: str) -> None:
    """The page writes "398MB", not "0.4GB". A GB-only parser dropped the size
    of exactly the tags a weak machine depends on, leaving them fit-unknown."""
    small = next(t for t in library.parse_tags_html(tags_page, "qwen3.5") if t["tag"] == "0.3b")
    assert small["size_gb"] == 0.4


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/library/x:a">x</a> 6.6GB', 6.6),
        ('<a href="/library/x:a">x</a> 398MB', 0.4),
        ('<a href="/library/x:a">x</a> 1.2TB', 1200.0),
        # "256K context window" must never be read as a size.
        ('<a href="/library/x:a">x</a> 256K context window', None),
    ],
)
def test_size_units_are_read_the_way_the_catalog_writes_them(
    markup: str, expected: float | None
) -> None:
    assert library.parse_tags_html(markup, "x")[0]["size_gb"] == expected


def test_tags_parser_reads_size_context_and_inputs(tags_page: str) -> None:
    latest = library.parse_tags_html(tags_page, "qwen3.5")[0]
    assert latest["id"] == "qwen3.5:latest"
    assert latest["size_gb"] == 6.6
    assert latest["context"] == "256K"
    assert latest["inputs"] == "Text, Image"
    assert latest["updated"] == "5 months ago"
    assert latest["cloud"] is False


def test_tags_parser_reads_quantization_from_the_tag_name(tags_page: str) -> None:
    """The page prints no quantization column, so the tag name is the only
    honest source: ``0.8b-q8_0`` says q8_0, a bare ``latest`` says nothing."""
    by_tag = {t["tag"]: t for t in library.parse_tags_html(tags_page, "qwen3.5")}
    assert by_tag["0.8b-q8_0"]["quantization"] == "q8_0"
    assert by_tag["0.8b-q8_0"]["context"] == "128K"
    assert by_tag["latest"]["quantization"] == ""
    assert by_tag["0.8b"]["quantization"] == ""


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("27b-q4_K_M", "q4_K_M"),
        ("27b-mtp-q8_0", "q8_0"),
        ("35b-a3b-coding-mxfp8", "mxfp8"),
        ("2b-nvfp4", "nvfp4"),
        ("0.8b-mlx-bf16", "bf16"),
        ("7b-instruct-fp16", "fp16"),
        ("4b-iq2_xs", "iq2_xs"),
        ("27b", ""),
        ("latest", ""),
        ("cloud", ""),
    ],
)
def test_quantization_is_read_the_way_the_library_names_tags(tag: str, expected: str) -> None:
    markup = f'<a href="/library/x:{tag}">x</a> 1GB'
    assert library.parse_tags_html(markup, "x")[0]["quantization"] == expected


def test_tags_parser_reads_a_namespaced_model_page() -> None:
    """Community models live at ``/{user}/{model}:{tag}`` — no ``/library/``
    prefix — and must parse with the same anchors."""
    page = (
        '<a href="/huihui_ai/qwen3-abliterated:latest">x</a> 5.2GB • 40K context window'
        " • Text input • 1 year ago"
        '<a href="/huihui_ai/qwen3-abliterated:0.6b-q8_0">x</a> 639MB'
    )
    tags = library.parse_tags_html(page, "huihui_ai/qwen3-abliterated")
    assert [t["id"] for t in tags] == [
        "huihui_ai/qwen3-abliterated:latest",
        "huihui_ai/qwen3-abliterated:0.6b-q8_0",
    ]
    assert tags[0]["context"] == "40K"
    assert tags[1]["quantization"] == "q8_0"


def test_tags_parser_flags_cloud_from_the_name_only(tags_page: str) -> None:
    """Cloud is a NAME fact. A local tag whose size fails to parse must stay
    size-unknown, never become cloud-only."""
    tags = library.parse_tags_html(tags_page, "qwen3.5")
    cloud = next(t for t in tags if t["tag"] == "cloud")
    assert cloud["cloud"] is True
    assert cloud["size_gb"] is None

    broken = library.parse_tags_html('<a href="/library/x:4b">x:4b</a> no size markers here', "x")
    assert broken[0]["cloud"] is False
    assert broken[0]["size_gb"] is None


# ── async surfaces: degradation, enrichment, caching ─────────────────────


def _fake_fetch(
    page: str | None,
    error: str | None,
    calls: list[str],
    params_seen: list[dict[str, str] | None] | None = None,
):
    async def fetch(
        path: str, params: dict[str, str] | None = None
    ) -> tuple[str | None, str | None]:
        calls.append(path)
        if params_seen is not None:
            params_seen.append(params)
        return page, error

    return fetch


@pytest.fixture()
def _machine(monkeypatch):
    """A machine with 32 GB RAM, no GPU, and qwen3.5:latest already pulled."""

    async def _installed() -> tuple[set[str], str | None]:
        return {"qwen3.5:latest"}, None

    monkeypatch.setattr(library, "installed_models", _installed)
    monkeypatch.setattr(library, "total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(library, "accelerator_gb", lambda: (0.0, "none"))


@pytest.mark.asyncio
async def test_search_marks_installed_models(monkeypatch, search_page: str, _machine) -> None:
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, []))
    result = await library.search_library("qwen")
    assert result["error"] is None
    by_name = {m["name"]: m for m in result["models"]}
    assert by_name["qwen3.5"]["installed"] is True
    assert by_name["deepscaler"]["installed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected_params"),
    [
        ({}, {"q": "qwen"}),
        ({"sort": "popular"}, {"q": "qwen"}),
        ({"sort": "newest"}, {"q": "qwen", "o": "newest"}),
        ({"capability": "tools"}, {"q": "qwen", "c": "tools"}),
        ({"sort": "newest", "capability": "vision"}, {"q": "qwen", "o": "newest", "c": "vision"}),
        # Values the page does not know are dropped, never forwarded.
        ({"sort": "loudest", "capability": "magic"}, {"q": "qwen"}),
    ],
)
async def test_search_forwards_sort_and_capability_as_the_page_expects(
    monkeypatch, search_page: str, _machine, kwargs, expected_params
) -> None:
    seen: list[dict[str, str] | None] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, [], seen))
    result = await library.search_library("qwen", **kwargs)
    assert seen == [expected_params]
    assert result["sort"] == expected_params.get("o", "popular")
    assert result["capability"] == expected_params.get("c")


@pytest.mark.asyncio
async def test_an_empty_newest_browse_sends_only_the_sort(
    monkeypatch, search_page: str, _machine
) -> None:
    seen: list[dict[str, str] | None] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, [], seen))
    await library.search_library("", sort="newest")
    assert seen == [{"o": "newest"}]


@pytest.mark.asyncio
async def test_search_limit_trims_the_answer_not_the_cache(
    monkeypatch, search_page: str, _machine
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, calls))
    short = await library.search_library("qwen", limit=1)
    assert len(short["models"]) == 1
    full = await library.search_library("qwen", limit=500)
    assert len(full["models"]) == 3
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_caches_per_sort_and_filter(monkeypatch, search_page: str, _machine) -> None:
    """Newest and popular are different pages; one must not answer for the other."""
    calls: list[str] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, calls))
    await library.search_library("qwen")
    await library.search_library("qwen", sort="newest")
    await library.search_library("qwen", capability="tools")
    await library.search_library("qwen", sort="newest")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_search_degrades_honestly_when_offline(monkeypatch, _machine) -> None:
    monkeypatch.setattr(
        library, "_fetch_page", _fake_fetch(None, "ollama.com did not answer …", [])
    )
    result = await library.search_library("qwen")
    assert result["models"] == []
    assert "ollama.com" in result["error"]


@pytest.mark.asyncio
async def test_search_caches_per_query(monkeypatch, search_page: str, _machine) -> None:
    calls: list[str] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, calls))
    await library.search_library("qwen")
    await library.search_library("qwen")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tags_enrich_with_fit_and_installed(monkeypatch, tags_page: str, _machine) -> None:
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(tags_page, None, []))
    result = await library.library_tags("qwen3.5")
    assert result["error"] is None
    by_tag = {t["tag"]: t for t in result["tags"]}
    assert by_tag["latest"]["installed"] is True
    assert by_tag["latest"]["fit"] == "comfortable"
    assert by_tag["0.8b"]["installed"] is False
    # No size → no invented verdict.
    assert by_tag["cloud"]["fit"] == "unknown"
    assert by_tag["cloud"]["fit_note"] == ""


@pytest.mark.asyncio
async def test_a_freshly_pulled_tag_stops_offering_download_immediately(
    monkeypatch, tags_page: str
) -> None:
    """The cache must hold the CATALOG, never this machine's inventory.

    Caching the enriched answer meant a tag downloaded from this very panel
    kept its "Download" button for the rest of the TTL — the one moment the
    panel is guaranteed to be wrong is right after it did its job.
    """
    inventory: set[str] = set()

    async def _installed() -> tuple[set[str], str | None]:
        return set(inventory), None

    monkeypatch.setattr(library, "installed_models", _installed)
    monkeypatch.setattr(library, "total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(library, "accelerator_gb", lambda: (0.0, "none"))
    fetches: list[str] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(tags_page, None, fetches))

    first = await library.library_tags("qwen3.5")
    assert next(t for t in first["tags"] if t["tag"] == "0.8b")["installed"] is False

    inventory.add("qwen3.5:0.8b")  # the pull completes

    second = await library.library_tags("qwen3.5")
    assert next(t for t in second["tags"] if t["tag"] == "0.8b")["installed"] is True
    # …and the catalog half still came from the cache, not a second fetch.
    assert len(fetches) == 1


@pytest.mark.asyncio
async def test_search_installed_state_is_never_cached(monkeypatch, search_page: str) -> None:
    inventory: set[str] = set()

    async def _installed() -> tuple[set[str], str | None]:
        return set(inventory), None

    monkeypatch.setattr(library, "installed_models", _installed)
    fetches: list[str] = []
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(search_page, None, fetches))

    first = await library.search_library("qwen")
    assert first["models"][0]["installed"] is False

    inventory.add("qwen3.5:9b")

    second = await library.search_library("qwen")
    assert second["models"][0]["installed"] is True
    assert len(fetches) == 1


@pytest.mark.asyncio
async def test_tags_reject_a_name_that_is_not_a_library_name(_machine) -> None:
    """The name doubles as a URL segment — a path-shaped one must never reach
    the fetch layer."""
    result = await library.library_tags("../evil")
    assert result["tags"] == []
    assert result["error"] == "Not a valid library model name."


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../evil", "a/../b", "a/b/c", ".hidden", "a?x", "a/.x"])
async def test_tags_reject_path_shaped_namespaces(_machine, name: str) -> None:
    result = await library.library_tags(name)
    assert result["tags"] == []
    assert result["error"] == "Not a valid library model name."


@pytest.mark.asyncio
async def test_tags_fetch_a_namespaced_model_from_its_own_path(monkeypatch, _machine) -> None:
    """``/library/{user}/{model}/tags`` answers 404 on ollama.com; a community
    model lives at ``/{user}/{model}/tags``."""
    calls: list[str] = []
    page = '<a href="/huihui_ai/qwen3-abliterated:latest">x</a> 5.2GB'
    monkeypatch.setattr(library, "_fetch_page", _fake_fetch(page, None, calls))
    result = await library.library_tags("huihui_ai/qwen3-abliterated")
    assert calls == ["/huihui_ai/qwen3-abliterated/tags"]
    assert result["error"] is None
    assert result["tags"][0]["id"] == "huihui_ai/qwen3-abliterated:latest"

    await library.library_tags("qwen3.5")
    assert calls[-1] == "/library/qwen3.5/tags"


@pytest.mark.asyncio
async def test_tags_error_when_the_page_stops_parsing(monkeypatch, _machine) -> None:
    """A shape change upstream must surface as a sentence, not an empty list
    that reads as 'this model has no versions'."""
    monkeypatch.setattr(
        library, "_fetch_page", _fake_fetch("<html>redesigned beyond recognition</html>", None, [])
    )
    result = await library.library_tags("qwen3.5")
    assert result["tags"] == []
    assert "qwen3.5" in result["error"]


@pytest.mark.asyncio
async def test_tags_pass_through_a_404_as_unknown_model(monkeypatch, _machine) -> None:
    monkeypatch.setattr(
        library,
        "_fetch_page",
        _fake_fetch(None, "The Ollama library does not know this model.", []),
    )
    result = await library.library_tags("nonexistent-model")
    assert result["tags"] == []
    assert "does not know" in result["error"]
