"""SOUL.md: the assistant's own character file.

Pins the two parts the assistant maintains itself (the name line and the
learned section), that hand-written parts survive every write, and what the
prompt sees.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.memory.soul import Soul, edit_soul, sync_name
from jarvis.memory.templates import render_soul_md
from jarvis.society.notebook import change


def _soul_file(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "SOUL.md"
    path.write_text(text if text is not None else render_soul_md(), encoding="utf-8")
    return path


def test_the_name_line_is_added_once_and_then_updated(tmp_path: Path) -> None:
    path = _soul_file(tmp_path)

    assert sync_name(path, "George") is True
    soul = Soul.load(path)
    assert soul.name == "George"
    body = path.read_text(encoding="utf-8")
    assert body.count("- **Name:**") == 1
    # Directly above the hand-written bullets of the same section.
    assert "## Who I am\n\n- **Name:** George\n- **Role:**" in body

    assert sync_name(path, "George") is False  # nothing to do, no write
    assert sync_name(path, "Athena") is True
    assert Soul.load(path).name == "Athena"
    assert path.read_text(encoding="utf-8").count("- **Name:**") == 1


def test_a_hand_written_file_without_the_section_gets_one(tmp_path: Path) -> None:
    path = _soul_file(tmp_path, "# Me\n\nDry and short.\n")

    assert sync_name(path, "George") is True

    body = path.read_text(encoding="utf-8")
    assert "Dry and short." in body
    assert "## Who I am\n\n- **Name:** George" in body


def test_a_missing_file_is_not_created(tmp_path: Path) -> None:
    assert sync_name(tmp_path / "SOUL.md", "George") is False
    assert not (tmp_path / "SOUL.md").exists()


def test_learned_entries_round_trip_and_keep_the_editorial_parts(tmp_path: Path) -> None:
    path = _soul_file(tmp_path)
    note = "The assistant introduces itself by its own name, never as the app."

    def add(soul: Soul) -> bool:
        soul.set_learned(change(soul.learned(), note, origin="review"))
        return True

    assert edit_soul(path, add) is True
    soul = Soul.load(path)
    assert [e.text for e in soul.learned()] == [note]
    body = path.read_text(encoding="utf-8")
    assert "## Tone rules" in body and "## Limits" in body
    assert body.index("<!-- curator:calibration:start -->") < body.index(note)
    assert body.index(note) < body.index("<!-- curator:calibration:end -->")

    entry = soul.learned()[0]
    replaced = "The assistant introduces itself as George."

    def replace(s: Soul) -> bool:
        s.set_learned(change(s.learned(), replaced, operation="replace", entry_id=entry.id))
        return True

    edit_soul(path, replace)
    assert [e.text for e in Soul.load(path).learned()] == [replaced]


def test_legacy_calibration_lines_are_read_as_entries(tmp_path: Path) -> None:
    text = render_soul_md().replace(
        "<!-- curator:calibration:start -->\n",
        "<!-- curator:calibration:start -->\n- [2026-08-03] User likes dry humour\n",
    )
    soul = Soul.parse(tmp_path / "SOUL.md", text)

    assert [e.text for e in soul.learned()] == ["[2026-08-03] User likes dry humour"]


def test_the_prompt_shows_character_and_learning_but_not_the_name_line(tmp_path: Path) -> None:
    path = _soul_file(tmp_path)
    sync_name(path, "Stalename")
    soul = Soul.load(path)
    soul.append_calibration("The assistant keeps jokes dry.")

    full = soul.render_for_prompt()
    assert "## Your character (SOUL.md)" in full
    assert "### Who you are" in full and "### Your tone" in full and "### Your limits" in full
    assert "The assistant keeps jokes dry." in full
    # The live name comes from the identity directive, never a stale file line.
    assert "Stalename" not in full

    compact = soul.render_for_prompt(compact=True)
    assert "### Your tone" not in compact
    assert "The assistant keeps jokes dry." in compact


def test_an_empty_file_renders_nothing(tmp_path: Path) -> None:
    assert Soul.parse(tmp_path / "SOUL.md", "").render_for_prompt() == ""
