# tests/unit/cli_ctl/test_render.py
import json

from jarvis.cli_ctl import render


def test_emit_json_mode_prints_raw_json(capsys):
    render.emit({"a": 1, "ä": "ö"}, as_json=True)  # i18n-allow: UTF-8 round-trip test data
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "ä": "ö"}  # UTF-8 preserved, not escaped (i18n-allow)


def test_emit_human_list_of_dicts_prints_table(capsys):
    rows = [{"id": "1", "state": "scheduled"}, {"id": "2", "state": "running"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert "id" in out and "state" in out and "scheduled" in out


def test_error_sets_message_on_stderr(capsys):
    render.error("boom")
    err = capsys.readouterr().err
    assert "boom" in err
