"""Unit-Tests für den Persona-Loader (`jarvis.brain.persona_loader`).

Deckt ab:
- Extraktion des ersten Code-Fence nach `## System-Prompt`.
- Cache-Verhalten (lru_cache + invalidate).
- Fehlende Datei / fehlender Fence → leerer String (kein Raise).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# `jarvis.brain.__init__` zieht den BrainManager + Safety-Chain mit —
# letztere hat zur Refactor-Zeit WIP-Dependencies ausserhalb dieses Scopes.
# Der Persona-Loader selbst hat keine Brain-Imports, daher isolieren wir
# ihn mit `importlib.util` aus dem Datei-Pfad.
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
    # Quellen-Sektion danach darf nicht drin sein
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
    """Integrationstest gegen die echte JARVIS_PERSONA.md im Repo.

    Geprueft werden Sektionen, die seit Persona-Mandat-Phase-2 (2026-04-25)
    in der MD live sind — kritisch fuer Hangup-Contract und Echo-Schutz.
    """
    invalidate_cache()
    try:
        prompt = load_persona_prompt()
    finally:
        invalidate_cache()
    assert prompt
    assert "JARVIS" in prompt
    assert "LANGUAGE POLICY" in prompt
    # Mandat-Phase-2: ECHO-PARAPHRASE-Sektion direkt nach OUTPUT RULES
    assert "ECHO-PARAPHRASE" in prompt
    # Hangup-Contract muss im Persona-Block sein, sonst kommt er nie beim
    # Brain an (Probe-Drift Szenario 10: 'Bis dann.' statt einem klaren Abschied).
    # The farewell is now profile-name-driven — no hardcoded owner name.
    assert "Goodbye" in prompt or "Auf Wiedersehen" in prompt


def test_load_persona_prompt_missing_file_returns_empty(monkeypatch, tmp_path) -> None:
    """Wenn die MD fehlt, liefert der Loader leeren String — kein Raise."""
    invalidate_cache()
    fake_missing = tmp_path / "does_not_exist.md"
    monkeypatch.setattr(pl, "_persona_md_path", lambda: fake_missing)
    try:
        assert load_persona_prompt() == ""
    finally:
        invalidate_cache()


def test_load_persona_prompt_cache_reuses_read(monkeypatch, tmp_path) -> None:
    """`load_persona_prompt` liest die Datei nur einmal pro Cache-Fenster."""
    invalidate_cache()
    md_file = tmp_path / "JARVIS_PERSONA.md"
    md_file.write_text(
        "## System-Prompt\n```\nFirst version.\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(pl, "_persona_md_path", lambda: md_file)
    try:
        first = load_persona_prompt()
        assert "First version." in first
        # Datei ändern — ohne invalidate bleibt der Cache-Wert.
        md_file.write_text(
            "## System-Prompt\n```\nSecond version.\n```\n", encoding="utf-8"
        )
        cached = load_persona_prompt()
        assert cached == first  # Cache-Hit
        invalidate_cache()
        fresh = load_persona_prompt()
        assert "Second version." in fresh
    finally:
        invalidate_cache()


def test_load_persona_prompt_crlf_normalized(monkeypatch, tmp_path) -> None:
    """Windows-CRLF wird als LF verarbeitet."""
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
    """Sanity: Der Default-Pfad zeigt auf eine existierende Datei."""
    path = pl._persona_md_path()
    assert isinstance(path, Path)
    assert path.exists(), f"Erwartet: {path} existiert"


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
