"""The library browser's parsers must keep understanding the LIVE ollama.com.

The unit tests pin the parsers against snapshots, which by definition cannot
notice ollama.com shipping a redesign. This is the guard that can: it fetches
the real search and tags pages and asserts the parsers still read them. The
moment the markup drifts past what the anchors tolerate, this fails loudly at
CI time instead of a user finding an inexplicably empty library panel.

Network-dependent, so it is marked ``integration`` and self-skips when
ollama.com cannot be reached. Run explicitly with ``pytest -m integration``.
"""

from __future__ import annotations

import httpx
import pytest

from jarvis.brain.ollama_library import _tags_path, parse_search_html, parse_tags_html

pytestmark = pytest.mark.integration

_TIMEOUT = 15.0


def _fetch(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001 — offline is a skip, not a failure
        pytest.skip(f"ollama.com unreachable: {type(exc).__name__} {exc}")
    if resp.status_code != 200:
        pytest.skip(f"ollama.com answered {resp.status_code}")
    return resp.text


def test_the_live_search_page_still_parses() -> None:
    models = parse_search_html(_fetch("https://ollama.com/search?q=qwen"))
    assert len(models) >= 5, (
        "The live search page for 'qwen' parsed almost empty — ollama.com has "
        "likely changed its markup. Update parse_search_html and the fixture."
    )
    names = {m["name"] for m in models}
    assert any("qwen" in n for n in names)
    # At least the flagship entries carry the facts the panel renders.
    rich = [m for m in models if m["description"] and m["sizes"]]
    assert rich, "No parsed entry carries a description and sizes any more."


def test_the_live_tags_page_still_parses() -> None:
    tags = parse_tags_html(_fetch("https://ollama.com/library/qwen3.5/tags"), "qwen3.5")
    assert len(tags) >= 5, (
        "The live tags page parsed almost empty — ollama.com has likely "
        "changed its markup. Update parse_tags_html and the fixture."
    )
    with_size = [t for t in tags if t["size_gb"]]
    assert with_size, "No parsed tag carries a download size any more."
    assert any(t["tag"] == "latest" for t in tags)


def test_the_live_newest_sort_is_honoured() -> None:
    """``?o=newest`` must change the ORDER: the first rows are days or weeks
    old, never the years-old names that top the popular listing."""
    models = parse_search_html(_fetch("https://ollama.com/search?o=newest"))
    assert len(models) >= 5, "The live newest listing parsed almost empty."
    head = [m["updated"] for m in models[:5]]
    assert all(u and "year" not in u for u in head), head
    assert any(("day" in u or "week" in u or u == "yesterday") for u in head), head


def test_the_live_capability_filter_is_honoured() -> None:
    """``?c=embedding`` must change the SET: every row carries that badge."""
    models = parse_search_html(_fetch("https://ollama.com/search?c=embedding"))
    assert len(models) >= 3, "The live embedding filter parsed almost empty."
    assert all("embedding" in m["capabilities"] for m in models), [
        m["name"] for m in models if "embedding" not in m["capabilities"]
    ]


def test_the_live_tags_page_carries_quantized_tags() -> None:
    """A family that publishes quant variants must yield a non-empty
    ``quantization`` for them and an empty one for the default tag."""
    tags = parse_tags_html(_fetch("https://ollama.com/library/qwen3.6/tags"), "qwen3.6")
    quantized = [t for t in tags if t["quantization"]]
    assert quantized, "No tag on the live qwen3.6 page reads a quantization any more."
    assert next(t for t in tags if t["tag"] == "latest")["quantization"] == ""
    assert all(t["context"] for t in tags[:3]), "Context window no longer parses."


def test_a_live_namespaced_model_parses_from_its_own_path() -> None:
    name = "huihui_ai/qwen3-abliterated"
    tags = parse_tags_html(_fetch(f"https://ollama.com{_tags_path(name)}"), name)
    assert len(tags) >= 2, "The live community tags page parsed almost empty."
    assert tags[0]["id"].startswith(f"{name}:")
