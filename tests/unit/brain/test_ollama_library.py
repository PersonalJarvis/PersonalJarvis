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
    assert [t["tag"] for t in tags] == ["latest", "cloud", "0.8b", "0.3b"]


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


def test_tags_parser_flags_cloud_from_the_name_only(tags_page: str) -> None:
    """Cloud is a NAME fact. A local tag whose size fails to parse must stay
    size-unknown, never become cloud-only."""
    tags = library.parse_tags_html(tags_page, "qwen3.5")
    cloud = next(t for t in tags if t["tag"] == "cloud")
    assert cloud["cloud"] is True
    assert cloud["size_gb"] is None

    broken = library.parse_tags_html(
        '<a href="/library/x:4b">x:4b</a> no size markers here', "x"
    )
    assert broken[0]["cloud"] is False
    assert broken[0]["size_gb"] is None


# ── async surfaces: degradation, enrichment, caching ─────────────────────


def _fake_fetch(page: str | None, error: str | None, calls: list[str]):
    async def fetch(
        path: str, params: dict[str, str] | None = None
    ) -> tuple[str | None, str | None]:
        calls.append(path)
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
async def test_search_installed_state_is_never_cached(
    monkeypatch, search_page: str
) -> None:
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
