"""Unit tests for the skill validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.loader import parse_skill
from jarvis.skills.validator import validate_skill


GOOD_SKILL = """---
schema_version: "1"
name: good
triggers:
  - type: voice
    pattern: "hallo"
  - type: hotkey
    combo: "ctrl+alt+j"
requires_tools: [echo]
token_budget_estimate: 500
---
body
"""

BAD_REGEX_SKILL = """---
schema_version: "1"
name: bad_regex
triggers:
  - type: voice
    pattern: "["
---
body
"""

TOO_BIG_BUDGET = """---
schema_version: "1"
name: big
token_budget_estimate: 50000
---
body
"""


def _parse(tmp_path: Path, name: str, content: str):
    d = tmp_path / name
    d.mkdir()
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return parse_skill(p)


def test_validate_good_skill(tmp_path: Path):
    sk = _parse(tmp_path, "good", GOOD_SKILL)
    report = validate_skill(sk, tool_registry=["echo"])
    assert report.ok, report.errors


def test_validate_bad_regex(tmp_path: Path):
    sk = _parse(tmp_path, "bad", BAD_REGEX_SKILL)
    # The trigger-payload check doesn't catch the regex, but the validator compiles it
    report = validate_skill(sk, tool_registry=[])
    assert not report.ok
    assert any("regex" in e.lower() for e in report.errors)


def test_validate_missing_tool_warns(tmp_path: Path):
    sk = _parse(tmp_path, "good", GOOD_SKILL)
    report = validate_skill(sk, tool_registry=[])
    # missing tool is warning, not error
    assert any("echo" in w for w in report.warnings)


def test_validate_budget_too_big(tmp_path: Path):
    # The budget is already caught by the pydantic schema (ge=1, le=100_000, but
    # the validator additionally checks > 10_000)
    sk = _parse(tmp_path, "big", TOO_BIG_BUDGET)
    assert sk.frontmatter is not None
    report = validate_skill(sk, tool_registry=[])
    assert not report.ok
    assert any("token_budget" in e for e in report.errors)
