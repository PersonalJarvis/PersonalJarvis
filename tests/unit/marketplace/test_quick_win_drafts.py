"""Quick-win DCR connector drafts stay valid and ready for promotion.

Each draft under ``jarvis/marketplace/drafts/`` listed in
``docs/marketplace/quick-win-pending-e2e.json`` must parse as a catalog entry,
use the one-click DCR flow, agree with its MCP template and paired skill, and
carry a pending record bound to its exact configuration. None of them may be in
the shipped seed catalog before a real browser journey passes
(docs/marketplace/quick-win-connectors.md).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from jarvis.marketplace.catalog import PluginSpec
from jarvis.skills.loader import parse_skill

ROOT = Path(__file__).resolve().parents[3]
DRAFTS = ROOT / "jarvis" / "marketplace" / "drafts"
SKILL_DRAFTS = ROOT / "jarvis" / "skills" / "drafts"
PENDING = json.loads(
    (ROOT / "docs" / "marketplace" / "quick-win-pending-e2e.json").read_text(encoding="utf-8")
)
ROWS = {row["plugin_id"]: row for row in PENDING["plugins"]}


def _gate():
    spec = importlib.util.spec_from_file_location(
        "check_plugin_auth_contract", ROOT / "scripts" / "ci" / "check_plugin_auth_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_form(pid: str) -> dict:
    manifest = json.loads((DRAFTS / pid / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == pid
    return {
        "id": pid,
        "description": manifest["description"],
        **manifest["extensions"]["io.github.personaljarvis"],
    }


def test_pending_record_covers_quick_win_drafts() -> None:
    assert PENDING["schema_version"] == 1
    assert len(ROWS) == len(PENDING["plugins"]) == 26


@pytest.mark.parametrize("pid", sorted(ROWS))
def test_draft_is_a_one_click_dcr_connector(pid: str) -> None:
    form = _seed_form(pid)
    spec = PluginSpec.model_validate(form)
    assert spec.auth.mode == "hosted_mcp_oauth_dcr"
    assert spec.browser_flow == "dcr"
    assert spec.oauth_client_family is None
    assert spec.fallback_auth is None
    disc = urlsplit(spec.auth.discovery_url)
    assert disc.scheme == "https" and disc.hostname
    assert spec.mcp_server is not None
    assert spec.mcp_server["transport"] == "http"
    assert spec.mcp_server["url"] == spec.auth.mcp_url
    assert f"plugin_{pid}_access_token" in spec.mcp_server["auth_header_template"]

    template = json.loads((DRAFTS / pid / "mcp.template.json").read_text(encoding="utf-8"))
    assert template["mcpServers"] == {pid: {"type": "streamable-http", "url": spec.auth.mcp_url}}

    card = (DRAFTS / pid / "usage_card.md").read_text(encoding="utf-8")
    assert f"plugin_id: {pid}\n" in card


@pytest.mark.parametrize("pid", sorted(ROWS))
def test_draft_skill_pairs_with_its_connector(pid: str) -> None:
    skill = parse_skill(SKILL_DRAFTS / f"plugin-{pid}" / "SKILL.md")
    fm = skill.frontmatter
    assert fm is not None, skill.error
    assert fm.plugin_id == pid
    assert fm.requires_tools == [pid]
    assert "use when" in (fm.when_to_use or "").lower()
    ask = _seed_form(pid)["mcp_server"].get("risk_tier") == "ask"
    assert fm.risk_policy.default_tier == ("ask" if ask else "monitor")


@pytest.mark.parametrize("pid", sorted(ROWS))
def test_pending_record_is_bound_to_the_draft_configuration(pid: str) -> None:
    gate = _gate()
    row = ROWS[pid]
    assert row["result"] == "BLOCKED"
    assert row["config_fingerprint"] == gate.config_fingerprint(_seed_form(pid))
    assert set(row["evidence"]) == set(gate.E2E_STAGES)
    assert all(item["status"] == "BLOCKED" for item in row["evidence"].values())


def test_drafts_are_not_shipped_before_a_real_journey() -> None:
    seed = json.loads(
        (ROOT / "jarvis" / "marketplace" / "seed_catalog.json").read_text(encoding="utf-8")
    )
    shipped = {plugin["id"] for plugin in seed["plugins"]}
    assert shipped.isdisjoint(ROWS)
