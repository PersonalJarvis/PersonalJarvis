"""Unit tests for CoreMemory."""
from __future__ import annotations

import json

from jarvis.memory import CoreMemory


def test_load_creates_defaults(tmp_path):
    path = tmp_path / "core.json"
    mem = CoreMemory.load(path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "persona" in data
    assert mem.get_section("persona")["name"] == "Jarvis"


def test_add_and_remove_fact(tmp_path):
    path = tmp_path / "core.json"
    mem = CoreMemory.load(path)
    mem.add_fact("Ich heiße Harald", category="identity")  # i18n-allow
    mem.add_fact("Ich mag Python", category="preference")

    # Persistence
    reloaded = CoreMemory.load(path)
    facts = reloaded.get_section("user_facts")
    assert "Ich heiße Harald" in facts["identity"]  # i18n-allow
    assert "Ich mag Python" in facts["preference"]

    # Remove
    assert mem.remove_fact("Ich mag Python", category="preference") is True
    assert mem.remove_fact("Unknown", category="x") is False


def test_dedup_in_add_fact(tmp_path):
    path = tmp_path / "core.json"
    mem = CoreMemory.load(path)
    mem.add_fact("X", category="cat")
    mem.add_fact("X", category="cat")
    assert len(mem.get_section("user_facts")["cat"]) == 1


def test_render_system_prompt_block(tmp_path):
    path = tmp_path / "core.json"
    mem = CoreMemory.load(path)
    mem.add_fact("Ich heiße Harald", category="identity")  # i18n-allow
    mem.set_value("current_projects", "Jarvis", "Voice-Assistant in Phase 2")
    prompt = mem.render_system_prompt_block()
    assert "Core-Memory" in prompt
    assert "Ich heiße Harald" in prompt  # i18n-allow
    assert "Jarvis" in prompt


def test_corrupt_file_is_backed_up(tmp_path):
    path = tmp_path / "core.json"
    path.write_text("not valid json {[", encoding="utf-8")
    mem = CoreMemory.load(path)
    # Backup file created
    assert (tmp_path / "core.corrupted.json").exists()
    # Defaults are back
    assert mem.get_section("persona")["name"] == "Jarvis"
