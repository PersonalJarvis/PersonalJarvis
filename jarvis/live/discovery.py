"""On-demand tool schemas for the Responses voice backend, without another model."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable

from jarvis.core.protocols import SupervisorToolDescriptor

_PAGE_SIZE = 5
_PAGE_BYTES = 12_000
_STOP_WORDS = frozenset("a an and are for in is it of on or the to with jarvis".split())


def _words(text: str) -> set[str]:
    # Split canonical snake/kebab names and punctuation as well as ordinary prose.
    words = set(re.findall(r"[^\W_]+", text.casefold())) - _STOP_WORDS
    return {
        word[:-1]
        if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is"))
        else word
        for word in words
    }


def discover(catalog: Iterable[SupervisorToolDescriptor], query: str, offset: int = 0) -> dict:
    """Rank keyword overlap; keep every matching tool and its complete schema reachable.

    Requiring every word of a natural-language query to occur in one descriptor
    returned no results even for available tools. Matching any meaningful word
    and ranking by coverage avoids repeated unsuccessful paid search turns.
    """
    terms = _words(query)
    indexed = [(d, _words(d.name), _words(d.description)) for d in catalog]
    frequencies = Counter(
        word for _, names, descriptions in indexed for word in names | descriptions
    )
    weights = {word: math.log(1 + len(indexed) / (1 + frequencies[word])) for word in terms}
    ranked: list[tuple[float, SupervisorToolDescriptor]] = []
    for descriptor, name_words, description_words in indexed:
        name = descriptor.name.casefold()
        if query.strip().casefold() == name:
            # An exact lookup must not bring along other tools mentioning this name.
            ranked = [(1, descriptor)]
            break
        name_hits = sum(weights[word] for word in terms & name_words)
        description_hits = sum(weights[word] for word in terms & description_words)
        # Rare capability words beat generic API prose. Normalize descriptions
        # so a long, unrelated declaration cannot win merely by being verbose.
        score = 3 * name_hits + description_hits / (1 + len(description_words) / 100)
        if score or not terms:
            ranked.append((score, descriptor))
    ranked.sort(key=lambda entry: (-entry[0], entry[1].name))
    offset = max(0, offset)
    page: list[dict] = []
    for _, descriptor in ranked[offset : offset + _PAGE_SIZE]:
        item = {
            "name": descriptor.name,
            "description": descriptor.description,
            "parameters": descriptor.input_schema,
        }
        # Never slice a schema. One large required schema is allowed to exceed
        # the page target; pagination must always advance without hiding tools.
        if page and len(json.dumps([*page, item]).encode("utf-8")) > _PAGE_BYTES:
            break
        page.append(item)
    next_offset = offset + len(page)
    result = {
        "tools": page,
        "total": len(ranked),
        "next_offset": next_offset if next_offset < len(ranked) else None,
    }
    if not ranked:
        result["hint"] = (
            "Try fewer English keywords, an exact tool name, or an empty query to browse."
        )
    return result
