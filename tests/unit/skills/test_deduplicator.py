"""Unit-Tests für den Skill-Deduplicator."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.deduplicator import find_duplicates, jaccard
from jarvis.skills.loader import parse_skill


def test_jaccard_identical():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint():
    assert jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_jaccard_both_empty_is_one():
    assert jaccard(set(), set()) == 1.0


def test_jaccard_one_empty_is_zero():
    assert jaccard({"a"}, set()) == 0.0


SKILL_A = """---
schema_version: "1"
name: a
triggers:
  - type: voice
    pattern: "zeig mir mails"
requires_tools: [gmail_list, gmail_read]
---
body
"""

SKILL_B_DUP = """---
schema_version: "1"
name: b
triggers:
  - type: voice
    pattern: "zeig mir mails"
requires_tools: [gmail_list, gmail_read]
---
body
"""

SKILL_C_SIM = """---
schema_version: "1"
name: c
triggers:
  - type: voice
    pattern: "zeig mir mails"
requires_tools: [gmail_list, gmail_read, gmail_reply]
---
body
"""

SKILL_D_DIFF = """---
schema_version: "1"
name: d
triggers:
  - type: hotkey
    combo: "ctrl+shift+k"
requires_tools: [browser_navigate]
---
body
"""


def _write(root: Path, name: str, content: str):
    d = root / name
    d.mkdir()
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return parse_skill(p)


def test_find_duplicates_identical(tmp_path: Path):
    a = _write(tmp_path, "a", SKILL_A)
    b = _write(tmp_path, "b", SKILL_B_DUP)
    dups = find_duplicates([a, b], threshold=0.75)
    assert len(dups) == 1
    assert dups[0][2] == 1.0


def test_find_duplicates_similar(tmp_path: Path):
    a = _write(tmp_path, "a", SKILL_A)
    c = _write(tmp_path, "c", SKILL_C_SIM)
    dups = find_duplicates([a, c], threshold=0.5)
    assert len(dups) == 1
    assert 0.5 <= dups[0][2] < 1.0


def test_find_duplicates_disjoint(tmp_path: Path):
    a = _write(tmp_path, "a", SKILL_A)
    d = _write(tmp_path, "d", SKILL_D_DIFF)
    dups = find_duplicates([a, d], threshold=0.75)
    assert dups == []


def test_find_duplicates_sorts_desc(tmp_path: Path):
    a = _write(tmp_path, "a", SKILL_A)
    b = _write(tmp_path, "b", SKILL_B_DUP)
    c = _write(tmp_path, "c", SKILL_C_SIM)
    dups = find_duplicates([a, b, c], threshold=0.5)
    scores = [s for (_, _, s) in dups]
    assert scores == sorted(scores, reverse=True)


def test_find_duplicates_empty():
    assert find_duplicates([], threshold=0.75) == []
