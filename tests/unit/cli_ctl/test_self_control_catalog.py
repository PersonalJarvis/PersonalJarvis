"""jarvisctl must be a valid, brain-discoverable entry in the CLI catalog.

The prober -> ``cli_<name>`` loader turns a connected catalog CLI into a worker
tool, which is how Jarvis drives its own control CLI for self-management. The
entry must therefore (a) exist, (b) validate against the real ``CliSpecModel``
schema, and (c) gate its dangerous verbs via the risk policy. It is deliberately
NOT a router-spawn tool and ships no mission-spawn command (AP-5/AP-14).
"""
from __future__ import annotations

import json
from pathlib import Path

from jarvis.clis.spec import CliSpecModel

CATALOG = Path("jarvis/clis/catalog/seed_catalog.json")


def _entries() -> list[dict]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return data["entries"]


def test_jarvisctl_entry_present_and_self_control() -> None:
    entry = next((e for e in _entries() if e["name"] == "jarvisctl"), None)
    assert entry is not None, "jarvisctl missing from seed catalog"
    assert entry["binary_name"] == "jarvisctl"
    assert entry["check_command"] == ["jarvisctl", "version"]
    caps = entry["capabilities"][0]
    assert "jarvis-control" in caps["domains"]
    blacklist = entry["risk"]["blacklist_patterns"]
    # Dangerous verbs are gated; read-only verbs are whitelisted.
    assert any("delete" in p for p in blacklist)
    assert any("secrets" in p for p in blacklist)
    assert any("list" in p for p in entry["risk"]["whitelist_patterns"])


def test_jarvisctl_entry_validates_against_schema() -> None:
    entry = next(e for e in _entries() if e["name"] == "jarvisctl")
    model = CliSpecModel.model_validate(entry)  # raises ValidationError if invalid
    assert model.name == "jarvisctl"
    assert model.auth.type == "none"
