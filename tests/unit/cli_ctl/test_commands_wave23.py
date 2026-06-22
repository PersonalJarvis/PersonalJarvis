"""Tests for the Wave 2-3 curated domains (skills, outputs, board, workflows,
conductor, contacts, telephony, marketplace, mcps, docs, frontier)."""
from __future__ import annotations

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def _last(cap):
    return cap["calls"][-1]


# --- skills ---------------------------------------------------------------
def test_skills_list(capture_api):
    runner.invoke(app, ["skills", "list"])
    assert _last(capture_api)["path"] == "/api/skills"


def test_skills_enable_proceeds(capture_api):
    res = runner.invoke(app, ["skills", "enable", "morning"])
    assert res.exit_code == 0
    assert _last(capture_api)["path"] == "/api/skills/morning/enable"


def test_skills_draft_sends_intent(capture_api):
    runner.invoke(app, ["skills", "draft", "do a thing"])
    call = _last(capture_api)
    assert call["path"] == "/api/skills/creator/draft"
    assert call["body"]["intent"] == "do a thing"


# --- outputs --------------------------------------------------------------
def test_outputs_list(capture_api):
    runner.invoke(app, ["outputs", "list"])
    assert _last(capture_api)["path"] == "/api/outputs"


def test_outputs_files(capture_api):
    runner.invoke(app, ["outputs", "files", "my-slug"])
    assert _last(capture_api)["path"] == "/api/outputs/my-slug/artifacts"


def test_preferred_opener_get_vs_set(capture_api):
    runner.invoke(app, ["outputs", "preferred-opener"])
    assert _last(capture_api)["method"] == "GET"
    runner.invoke(app, ["outputs", "preferred-opener", "code"])
    call = _last(capture_api)
    assert call["method"] == "PUT" and call["body"] == {"opener": "code"}


# --- board ----------------------------------------------------------------
def test_board_summary_window(capture_api):
    runner.invoke(app, ["board", "summary", "--window-days", "7"])
    call = _last(capture_api)
    assert call["path"] == "/api/board/personal/summary"
    assert call["query"]["window_days"] == "7"


def test_board_bio_regenerate_proceeds(capture_api):
    res = runner.invoke(app, ["board", "bio-regenerate"])
    assert res.exit_code == 0
    assert _last(capture_api)["path"] == "/api/board/bio/regenerate"


# --- workflows ------------------------------------------------------------
def test_workflows_delete_requires_yes(capture_api):
    assert runner.invoke(app, ["workflows", "delete", "w1"]).exit_code == 1
    assert capture_api["calls"] == []


def test_workflows_run_proceeds(capture_api):
    res = runner.invoke(app, ["workflows", "run", "w1"])
    assert res.exit_code == 0
    assert _last(capture_api)["path"] == "/api/workflows/w1/run"


# --- conductor ------------------------------------------------------------
def test_conductor_list(capture_api):
    runner.invoke(app, ["conductor", "list"])
    assert _last(capture_api)["path"] == "/api/conductor/jobs"


def test_conductor_toggle(capture_api):
    runner.invoke(app, ["conductor", "toggle", "j1", "--enabled"])
    call = _last(capture_api)
    assert call["method"] == "PATCH" and call["body"] == {"enabled": True}


def test_conductor_delete_requires_yes(capture_api):
    assert runner.invoke(app, ["conductor", "delete", "j1"]).exit_code == 1


# --- contacts -------------------------------------------------------------
def test_contacts_delete_requires_yes(capture_api):
    assert runner.invoke(app, ["contacts", "delete", "jane"]).exit_code == 1
    assert capture_api["calls"] == []


def test_contacts_add(capture_api):
    res = runner.invoke(app, ["contacts", "add", "--json-body", '{"name": "Jane"}'])
    assert res.exit_code == 0
    call = _last(capture_api)
    assert call["method"] == "POST" and call["body"] == {"name": "Jane"}


# --- telephony ------------------------------------------------------------
def test_telephony_outbound_requires_yes(capture_api):
    assert runner.invoke(app, ["telephony", "outbound", "+15551234567"]).exit_code == 1
    assert capture_api["calls"] == []


def test_telephony_outbound_with_yes(capture_api):
    res = runner.invoke(app, ["telephony", "outbound", "+15551234567", "--yes"])
    assert res.exit_code == 0
    call = _last(capture_api)
    assert call["method"] == "POST" and call["path"] == "/api/telephony/outbound"
    assert call["body"]["to"] == "+15551234567"


# --- marketplace ----------------------------------------------------------
def test_marketplace_list(capture_api):
    runner.invoke(app, ["marketplace", "list"])
    assert _last(capture_api)["path"] == "/api/marketplace/plugins"


def test_marketplace_disconnect_requires_yes(capture_api):
    assert runner.invoke(app, ["marketplace", "disconnect", "gmail"]).exit_code == 1


# --- mcps -----------------------------------------------------------------
def test_mcps_list(capture_api):
    runner.invoke(app, ["mcps", "list"])
    assert _last(capture_api)["path"] == "/api/mcps"


def test_mcps_enable_proceeds(capture_api):
    res = runner.invoke(app, ["mcps", "enable", "supabase"])
    assert res.exit_code == 0
    assert _last(capture_api)["path"] == "/api/mcps/supabase/enable"


def test_mcps_delete_requires_yes(capture_api):
    assert runner.invoke(app, ["mcps", "delete", "supabase"]).exit_code == 1


# --- docs -----------------------------------------------------------------
def test_docs_search(capture_api):
    runner.invoke(app, ["docs", "search", "wiki"])
    call = _last(capture_api)
    assert call["path"] == "/api/docs/search" and call["query"]["q"] == "wiki"


# --- frontier -------------------------------------------------------------
def test_frontier_pending(capture_api):
    runner.invoke(app, ["frontier", "pending"])
    assert _last(capture_api)["path"] == "/api/frontier/pending"


def test_frontier_ack_proceeds(capture_api):
    res = runner.invoke(app, ["frontier", "ack"])
    assert res.exit_code == 0
    assert _last(capture_api)["path"] == "/api/frontier/ack"
