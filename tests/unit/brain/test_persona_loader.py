"""Unit tests for the persona loader (`jarvis.brain.persona_loader`).

Covers:
- Extraction of the first code fence after `## System-Prompt`.
- Cache behavior (lru_cache + invalidate).
- Missing file / missing fence → empty string (no raise).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# `jarvis.brain.__init__` pulls in the BrainManager + safety chain —
# the latter had WIP dependencies outside this scope at refactor time.
# The persona loader itself has no brain imports, so we isolate it
# with `importlib.util` from the file path.
_loader_spec = importlib.util.spec_from_file_location(
    "jarvis.brain.persona_loader",
    Path(__file__).resolve().parents[3]
    / "jarvis"
    / "brain"
    / "persona_loader.py",
)
assert _loader_spec is not None and _loader_spec.loader is not None
pl = importlib.util.module_from_spec(_loader_spec)
_loader_spec.loader.exec_module(pl)

_extract_fence_after_marker = pl._extract_fence_after_marker
invalidate_cache = pl.invalidate_cache
load_persona_prompt = pl.load_persona_prompt


def test_extract_fence_returns_code_block_body() -> None:
    md = (
        "# Title\n"
        "Some intro.\n"
        "## System-Prompt\n"
        "\n"
        "```\n"
        "You are JARVIS.\n"
        "Be concise.\n"
        "```\n"
        "\n"
        "## Quellen\n"
    )
    block = _extract_fence_after_marker(md)
    assert "You are JARVIS." in block
    assert "Be concise." in block
    # The "Quellen" section after it must not be included
    assert "Quellen" not in block


def test_extract_fence_ignores_fences_before_marker() -> None:
    md = (
        "```\n"
        "This fence is BEFORE the marker.\n"
        "```\n"
        "## System-Prompt\n"
        "```\n"
        "Real prompt.\n"
        "```\n"
    )
    block = _extract_fence_after_marker(md)
    assert "Real prompt." in block
    assert "BEFORE the marker" not in block


def test_extract_fence_missing_marker_returns_empty() -> None:
    md = "# Title\n```\nprompt\n```\n"
    assert _extract_fence_after_marker(md) == ""


def test_extract_fence_missing_fence_returns_empty() -> None:
    md = "## System-Prompt\n\nNo fence here, just prose.\n"
    assert _extract_fence_after_marker(md) == ""


def test_load_persona_prompt_extracts_real_persona(monkeypatch) -> None:
    """Integration test against the real JARVIS_PERSONA.md in the repo.

    Checks sections that have been live in the MD since persona mandate
    phase 2 (2026-04-25) — critical for the hangup contract and echo
    protection.
    """
    invalidate_cache()
    try:
        prompt = load_persona_prompt()
    finally:
        invalidate_cache()
    assert prompt
    # Name-neutral persona (2026-06-29): the assistant name is injected at
    # runtime from the wake word (see assistant_name.py), so the default persona
    # must NOT bake in a fixed product name like "Jarvis". This guards against a
    # regression back to a hardcoded "You are JARVIS" opener.
    assert "JARVIS" not in prompt
    assert "voice companion" in prompt
    assert "LANGUAGE POLICY" in prompt
    # Mandate phase 2: ECHO-PARAPHRASE section right after OUTPUT RULES
    assert "ECHO-PARAPHRASE" in prompt
    # The hangup contract must be in the persona block, otherwise it never
    # reaches the brain (probe drift scenario 10: 'Bis dann.' instead of a
    # clear farewell).
    # The farewell is now profile-name-driven — no hardcoded owner name.
    assert "Goodbye" in prompt or "Auf Wiedersehen" in prompt  # i18n-allow


def test_load_persona_prompt_missing_file_returns_empty(monkeypatch, tmp_path) -> None:
    """When the MD is missing, the loader returns an empty string — no raise."""
    invalidate_cache()
    fake_missing = tmp_path / "does_not_exist.md"
    monkeypatch.setattr(pl, "_persona_md_path", lambda: fake_missing)
    try:
        assert load_persona_prompt() == ""
    finally:
        invalidate_cache()


def test_load_persona_prompt_cache_reuses_read(monkeypatch, tmp_path) -> None:
    """`load_persona_prompt` reads the file only once per cache window."""
    invalidate_cache()
    md_file = tmp_path / "JARVIS_PERSONA.md"
    md_file.write_text(
        "## System-Prompt\n```\nFirst version.\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(pl, "_persona_md_path", lambda: md_file)
    try:
        first = load_persona_prompt()
        assert "First version." in first
        # Change the file — without invalidate, the cached value stays.
        md_file.write_text(
            "## System-Prompt\n```\nSecond version.\n```\n", encoding="utf-8"
        )
        cached = load_persona_prompt()
        assert cached == first  # cache hit
        invalidate_cache()
        fresh = load_persona_prompt()
        assert "Second version." in fresh
    finally:
        invalidate_cache()


def test_load_persona_prompt_crlf_normalized(monkeypatch, tmp_path) -> None:
    """Windows CRLF is processed as LF."""
    invalidate_cache()
    md_file = tmp_path / "JARVIS_PERSONA.md"
    md_file.write_bytes(
        b"## System-Prompt\r\n```\r\nCRLF content.\r\n```\r\n"
    )
    monkeypatch.setattr(pl, "_persona_md_path", lambda: md_file)
    try:
        prompt = load_persona_prompt()
        assert "CRLF content." in prompt
    finally:
        invalidate_cache()


def test_persona_loader_path_points_to_real_file() -> None:
    """Sanity: the default path points to an existing file."""
    path = pl._persona_md_path()
    assert isinstance(path, Path)
    assert path.exists(), f"expected: {path} exists"


def test_persona_fence_carries_end_call_sentinel() -> None:
    from jarvis.brain.persona_loader import invalidate_cache, load_persona_prompt
    from jarvis.speech.hangup import END_CALL_SIGNAL

    invalidate_cache()
    try:
        prompt = load_persona_prompt()
    finally:
        invalidate_cache()
    assert prompt, "persona fence must load"
    assert END_CALL_SIGNAL in prompt, "persona must instruct the END_CALL sentinel"
    # Conservative bias must be spelled out: do not end when unsure.
    assert "do NOT" in prompt or "do not" in prompt
