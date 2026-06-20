"""CLI smoke tests against a temp-file DB (the CLI opens its own store)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keyproxy import cli


@pytest.fixture()
def db(tmp_path: Path) -> str:
    return str(tmp_path / "keyproxy.sqlite")


def test_issue_then_list_then_revoke(db: str, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["--db", db, "--json", "issue-token", "--label", "alice"])
    assert rc == 0
    issued = json.loads(capsys.readouterr().out)
    assert issued["label"] == "alice"
    assert issued["token"].startswith("kp_")
    token_id = issued["id"]

    rc = cli.main(["--db", db, "--json", "list-tokens"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["id"] == token_id
    assert rows[0]["revoked_at"] is None

    rc = cli.main(["--db", db, "--json", "revoke", token_id])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["revoked"] is True

    # After revoke it still lists, now with a revoked_at timestamp.
    cli.main(["--db", db, "--json", "list-tokens"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["revoked_at"] is not None


def test_revoke_unknown_returns_nonzero(db: str, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["--db", db, "--json", "revoke", "no-such-id"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["revoked"] is False


def test_usage_report_empty(db: str, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["--db", db, "--json", "usage"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_human_readable_issue(db: str, capsys: pytest.CaptureFixture) -> None:
    rc = cli.main(["--db", db, "issue-token", "--label", "bob"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "token: kp_" in out
    assert "cannot be shown again" in out
