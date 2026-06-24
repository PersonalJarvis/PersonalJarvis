"""Unit tests for the user-editable agent-instructions file.

The agent-instructions feature is an ``AGENTS.md`` / ``CLAUDE.md`` equivalent: a
user-owned Markdown file of personal standing instructions, named dynamically
after the assistant (assistant "Ruben" -> ``Ruben.md``), injected into the brain
system prompt as a block that is distinct from the packaged persona.

These tests mirror ``test_custom_system_prompt.py``: an autouse fixture redirects
``DATA_DIR`` to a tmp dir, and the path helpers read ``core_config.DATA_DIR``
fresh so the redirect takes effect.
"""
from __future__ import annotations

import types

import pytest

from jarvis.brain import agent_instructions
from jarvis.core import config as core_config


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    # This module's contract is "given a RESOLVED assistant name -> <name>.md".
    # *Which* config field yields that name (persona.name vs. wake phrase) is
    # ``resolve_assistant_name``'s concern — a separate module whose semantics
    # evolve independently. Pin the resolver to the config's ``persona.name`` so
    # these tests exercise THIS module's filename/IO logic deterministically,
    # without coupling to (or breaking on) the resolver's current behaviour.
    monkeypatch.setattr(
        agent_instructions,
        "resolve_assistant_name",
        lambda cfg: (getattr(getattr(cfg, "persona", None), "name", "") or "") or "Assistant",
    )
    return tmp_path


def make_config(name: str = "", phrase: str = ""):
    """A minimal stand-in for ``JarvisConfig`` (only the fields the resolver reads)."""
    return types.SimpleNamespace(
        persona=types.SimpleNamespace(name=name),
        trigger=types.SimpleNamespace(wake_word=types.SimpleNamespace(phrase=phrase)),
    )


# --------------------------------------------------------------------------- #
# Filename derivation                                                          #
# --------------------------------------------------------------------------- #


def test_filename_follows_persona_name():
    assert agent_instructions.instructions_filename(make_config(name="Ruben")) == "Ruben.md"


def test_filename_falls_back_to_assistant_when_unset():
    assert agent_instructions.instructions_filename(make_config()) == "Assistant.md"


def test_filename_transliterates_umlauts():
    assert agent_instructions.instructions_filename(make_config(name="Jürgen")) == "Juergen.md"


def test_filename_strips_path_separators_and_unsafe_chars():
    fn = agent_instructions.instructions_filename(make_config(name='a/b:c*?"<>|'))
    base = fn[:-3]  # drop ".md"
    for bad in '/\\:*?"<>|':
        assert bad not in base
    assert fn.endswith(".md")


def test_filename_guards_windows_reserved_device_name():
    assert agent_instructions.instructions_filename(make_config(name="CON")) == "CON_.md"
    assert agent_instructions.instructions_filename(make_config(name="com1")) == "com1_.md"


def test_filename_falls_back_when_empty_after_sanitize():
    assert agent_instructions.instructions_filename(make_config(name="...")) == "Assistant.md"


# --------------------------------------------------------------------------- #
# Read / save / reset round-trips                                             #
# --------------------------------------------------------------------------- #


def test_no_file_reads_as_none():
    cfg = make_config(name="Ruben")
    assert agent_instructions.read_agent_instructions(cfg) is None
    assert agent_instructions.has_agent_instructions(cfg) is False


def test_render_for_prompt_without_file_emits_current_empty_state():
    cfg = make_config(name="Ruben")
    block = agent_instructions.render_for_prompt(cfg)
    assert "Ruben.md" in block
    assert "No active user preferences are currently set" in block
    assert "Ignore any earlier Jarvis.md instructions" in block
    assert "END USER PREFERENCES & STANDING INSTRUCTIONS" in block


def test_save_then_read_roundtrips_and_strips():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "  Be concise.  ")
    assert agent_instructions.read_agent_instructions(cfg) == "Be concise."
    assert agent_instructions.has_agent_instructions(cfg) is True


def test_save_writes_to_the_named_file(_isolate_data_dir):
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "x")
    assert (_isolate_data_dir / "agent_instructions" / "Ruben.md").exists()


def test_whitespace_only_reads_as_none():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "   \n\t  ")
    assert agent_instructions.read_agent_instructions(cfg) is None


def test_save_is_utf8_without_bom_and_roundtrips_unicode(_isolate_data_dir):
    cfg = make_config(name="Ruben")
    text = "Grüße — café ✓ 日本語"
    agent_instructions.save_agent_instructions(cfg, text)
    raw = (_isolate_data_dir / "agent_instructions" / "Ruben.md").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") is False  # no BOM (AP-7)
    assert agent_instructions.read_agent_instructions(cfg) == text


def test_reset_deletes_and_is_idempotent():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "x")
    assert agent_instructions.reset_agent_instructions(cfg) is True
    assert agent_instructions.read_agent_instructions(cfg) is None
    assert agent_instructions.reset_agent_instructions(cfg) is False


# --------------------------------------------------------------------------- #
# Prompt rendering                                                            #
# --------------------------------------------------------------------------- #


def test_render_for_prompt_wraps_content_with_filename_and_guardrail():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "Always answer in German.")
    block = agent_instructions.render_for_prompt(cfg)
    assert "Ruben.md" in block
    assert "Always answer in German." in block
    assert "END USER PREFERENCES & STANDING INSTRUCTIONS" in block
    assert "Only the instructions inside this current block are active" in block
    assert "override default style" in block.lower()
    # The block must frame the content as preferences that never override safety.
    assert "never override" in block.lower()


# --------------------------------------------------------------------------- #
# Rename-follows-the-name migration                                          #
# --------------------------------------------------------------------------- #


def test_read_migrates_a_single_stray_file_to_the_current_name(_isolate_data_dir):
    # User wrote rules while the assistant was "Ruben", then renamed it to "Tony".
    agent_instructions.save_agent_instructions(make_config(name="Ruben"), "My rules.")
    cfg_tony = make_config(name="Tony")
    assert agent_instructions.read_agent_instructions(cfg_tony) == "My rules."
    d = _isolate_data_dir / "agent_instructions"
    assert (d / "Tony.md").exists()
    assert (d / "Ruben.md").exists() is False


def test_no_migration_when_multiple_files_present(_isolate_data_dir):
    d = _isolate_data_dir / "agent_instructions"
    d.mkdir(parents=True)
    (d / "Ruben.md").write_text("a", encoding="utf-8")
    (d / "Tony.md").write_text("b", encoding="utf-8")
    cfg_new = make_config(name="Steve")
    assert agent_instructions.read_agent_instructions(cfg_new) is None
    assert (d / "Steve.md").exists() is False


# --------------------------------------------------------------------------- #
# Seed template                                                               #
# --------------------------------------------------------------------------- #


def test_seed_template_is_nonempty_and_mentions_the_assistant_name():
    tpl = agent_instructions.seed_template(make_config(name="Ruben"))
    assert tpl.strip()
    assert "Ruben" in tpl


# --------------------------------------------------------------------------- #
# Flash-tier rendering (ack preamble + spawn announcement)                    #
# --------------------------------------------------------------------------- #


def test_render_for_flash_is_empty_without_a_file():
    assert agent_instructions.render_for_flash(make_config(name="Ruben")) == ""


def test_render_for_flash_carries_content_with_override_framing():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "Always start every sentence with 'Chef'.")
    block = agent_instructions.render_for_flash(cfg)
    assert "Always start every sentence with 'Chef'." in block
    # The flash block must authorise overriding the default style/address guidance.
    assert "override" in block.lower()
    # ...while keeping the hard invariants (brevity / safety / no internal names).
    assert "safety" in block.lower()


def test_render_for_flash_is_concise_relative_to_deep_block():
    cfg = make_config(name="Ruben")
    agent_instructions.save_agent_instructions(cfg, "Be terse.")
    flash = agent_instructions.render_for_flash(cfg)
    deep = agent_instructions.render_for_prompt(cfg)
    # The flash framing must be shorter than the deep-brain framing (latency-tier).
    assert len(flash) < len(deep)
