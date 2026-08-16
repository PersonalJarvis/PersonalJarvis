# tests/unit/cli_ctl/test_render.py
import json

from jarvis.cli_ctl import render


def test_emit_json_mode_prints_raw_json(capsys):
    render.emit({"a": 1, "ä": "ö"}, as_json=True)  # i18n-allow: UTF-8 round-trip test data
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "ä": "ö"}  # UTF-8 preserved, not escaped (i18n-allow)


def test_emit_human_list_of_dicts_prints_table(capsys, monkeypatch):
    # Force an interactive terminal so the human Rich-table path is exercised.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: True)
    rows = [{"id": "1", "state": "scheduled"}, {"id": "2", "state": "running"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert "id" in out and "state" in out and "scheduled" in out


def test_emit_non_tty_defaults_to_json(capsys, monkeypatch):
    # The brain / pipes / scripts: stdout is not a TTY → JSON even without --json.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: False)
    rows = [{"id": "1", "state": "scheduled"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert json.loads(out) == [{"id": "1", "state": "scheduled"}]


def test_emit_json_flag_wins_over_tty(capsys, monkeypatch):
    # An explicit --json forces JSON even in an interactive terminal.
    monkeypatch.setattr(render, "_stdout_isatty", lambda: True)
    render.emit({"k": "v"}, as_json=True)
    out = capsys.readouterr().out
    assert json.loads(out) == {"k": "v"}


def test_stdout_isatty_defaults_false_without_isatty(monkeypatch):
    # An exotic stdout wrapper without isatty must not crash; default to JSON.
    class _NoIsatty:
        pass

    monkeypatch.setattr(render.sys, "stdout", _NoIsatty())
    assert render._stdout_isatty() is False


def test_error_sets_message_on_stderr(capsys):
    render.error("boom")
    err = capsys.readouterr().err
    assert "boom" in err


def test_field_wraps_the_value_under_its_own_column(capsys):
    render.field("Status", " ".join(["word"] * 60))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) > 1, "a long value must wrap, not run off"
    # Continuation lines start under the value, not at column 0 — otherwise a
    # narrow terminal turns the status block into prose soup.
    indent = lines[0].index("word")
    assert all(ln.startswith(" " * indent) for ln in lines[1:])


def test_field_keeps_a_long_path_in_one_piece(capsys):
    path = r"C:\Users\someone\AppData\Local\Jarvis\skills\three-bullet-brief\SKILL.md"
    render.field("File", path)
    assert path in capsys.readouterr().out.replace("\n", "")


def test_field_does_not_eat_brackets_as_markup(capsys):
    render.field("Problem", "invalid [type=missing] in frontmatter")
    assert "[type=missing]" in capsys.readouterr().out
