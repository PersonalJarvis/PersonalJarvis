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

from jarvis.brain.ollama_library import parse_search_html, parse_tags_html

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
