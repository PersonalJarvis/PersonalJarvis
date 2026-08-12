"""Tests for the contacts commands (CRUD flags + vCard import/export)."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_list(capture_api):
    runner.invoke(app, ["contacts", "list"])
    assert capture_api["calls"][-1]["path"] == "/api/contacts"


def test_show(capture_api):
    runner.invoke(app, ["contacts", "show", "anna"])
    assert capture_api["calls"][-1]["path"] == "/api/contacts/anna"


def test_add_builds_body_from_flags(capture_api):
    res = runner.invoke(
        app,
        [
            "contacts", "add", "--yes",
            "--name", "Anna Schmidt",
            "--email", "a@example.com",
            "--email", "b@example.com",
            "--phone", "+49 151 1",
            "--tag", "uni",
            "--organization", "ACME",
            "--birthday", "1990-04-12",
            "--favorite",
        ],
    )
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "POST" and call["path"] == "/api/contacts"
    assert call["body"] == {
        "name": "Anna Schmidt",
        "emails": ["a@example.com", "b@example.com"],
        "phones": ["+49 151 1"],
        "tags": ["uni"],
        "organization": "ACME",
        "birthday": "1990-04-12",
        "favorite": True,
    }


def test_add_requires_name_or_json_body(capture_api):
    res = runner.invoke(app, ["contacts", "add", "--yes", "--email", "a@example.com"])
    assert res.exit_code == 2
    assert capture_api["calls"] == []


def test_edit_sends_only_given_flags(capture_api):
    res = runner.invoke(
        app,
        ["contacts", "edit", "anna", "--yes", "--no-favorite", "--role", "CTO"],
    )
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "PATCH" and call["path"] == "/api/contacts/anna"
    assert call["body"] == {"favorite": False, "role": "CTO"}


def test_edit_with_no_flags_is_an_error(capture_api):
    res = runner.invoke(app, ["contacts", "edit", "anna", "--yes"])
    assert res.exit_code == 2
    assert capture_api["calls"] == []


def test_import_reads_file_and_posts(capture_api, tmp_path: Path):
    vcf = tmp_path / "book.vcf"
    vcf.write_text("BEGIN:VCARD\nFN:Anna\nEND:VCARD\n", encoding="utf-8")
    res = runner.invoke(app, ["contacts", "import", str(vcf), "--yes"])
    assert res.exit_code == 0, res.output
    call = capture_api["calls"][-1]
    assert call["method"] == "POST" and call["path"] == "/api/contacts/import"
    assert "BEGIN:VCARD" in call["body"]["vcf"]


def test_import_missing_file_is_an_error(capture_api, tmp_path: Path):
    res = runner.invoke(app, ["contacts", "import", str(tmp_path / "nope.vcf")])
    assert res.exit_code == 2
    assert capture_api["calls"] == []


def test_export_prints_by_default(capture_api):
    res = runner.invoke(app, ["contacts", "export"])
    assert res.exit_code == 0, res.output
    assert capture_api["calls"][-1]["path"] == "/api/contacts/export"


def test_export_out_writes_file(capture_api, tmp_path: Path):
    target = tmp_path / "book.vcf"
    res = runner.invoke(app, ["contacts", "export", "--out", str(target)])
    assert res.exit_code == 0, res.output
    assert capture_api["calls"][-1]["path"] == "/api/contacts/export"
    assert target.is_file()
