"""Search terms for Chinese/Japanese text, which has no spaces between words.

SQLite's ``unicode61`` tokenizer splits on whitespace and punctuation, so a
Japanese sentence becomes ONE token and a question never matches the page that
answers it ("which colour do I like?" vs. "the user likes jade green" share
no whole sentence). The standard dictionary-free remedy is n-grams:

* **Indexing** stores every CJK character (unigram) and every adjacent pair
  (bigram) of a page in a dedicated FTS column, so both a one-character noun
  and a two-character word are whole tokens there.
* **Querying** keeps the content-bearing part of a question: kanji as single
  characters and katakana words as bigrams. Hiragana is dropped — in a
  question it is mostly grammar (particles, endings), and matching on it would
  make every page look relevant. A question written only in hiragana falls
  back to its bigrams so it is still searchable.

Pure string work, no dictionary and no model: safe on the voice path.
"""

from __future__ import annotations

import re

_HAN = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HIRAGANA = r"\u3040-\u309f"
_KATAKANA = r"\u30a0-\u30ff\u31f0-\u31ff\uff66-\uff9f"

_CJK_RUN_RE = re.compile(f"[{_HAN}{_HIRAGANA}{_KATAKANA}]+")
_HAN_RE = re.compile(f"[{_HAN}]")
_KATAKANA_RUN_RE = re.compile(f"[{_KATAKANA}]{{2,}}")
_HIRAGANA_RUN_RE = re.compile(f"[{_HIRAGANA}]{{2,}}")

#: Single kanji that only shape a question ("what", "I", "who", "now", ...)
#: and would otherwise count as content terms.
_QUESTION_KANJI = frozenset(
    "\u4f55\u79c1\u50d5\u4ffa\u541b\u8ab0\u4eca\u4e8b\u6642\u65b9\u7269"
)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RUN_RE.search(text or ""))


def _bigrams(run: str) -> list[str]:
    return [run[i : i + 2] for i in range(len(run) - 1)] or [run]


def index_terms(text: str) -> str:
    """Space-separated unigrams + bigrams of every CJK run (for the FTS column)."""
    out: list[str] = []
    for run in _CJK_RUN_RE.findall(text or ""):
        out.extend(run)
        if len(run) > 1:
            out.extend(_bigrams(run))
    return " ".join(out)


def query_terms(text: str) -> list[str]:
    """FTS query terms for the CJK part of ``text``, deduplicated, order kept."""
    terms: list[str] = []
    for ch in _HAN_RE.findall(text or ""):
        if ch not in _QUESTION_KANJI:
            terms.append(ch)
    for run in _KATAKANA_RUN_RE.findall(text or ""):
        terms.extend(_bigrams(run))
    if not terms:
        for run in _HIRAGANA_RUN_RE.findall(text or ""):
            terms.extend(_bigrams(run))
    return list(dict.fromkeys(terms))


def content_terms(text: str) -> list[str]:
    """Relevance terms: content kanji one by one, katakana words whole."""
    terms = [ch for ch in _HAN_RE.findall(text or "") if ch not in _QUESTION_KANJI]
    terms.extend(_KATAKANA_RUN_RE.findall(text or ""))
    if not terms:
        terms.extend(_HIRAGANA_RUN_RE.findall(text or ""))
    return list(dict.fromkeys(terms))


def strip_cjk(text: str) -> str:
    """``text`` with its CJK runs replaced by spaces (the non-CJK remainder)."""
    return _CJK_RUN_RE.sub(" ", text or "")


__all__ = ["content_terms", "has_cjk", "index_terms", "query_terms", "strip_cjk"]
