"""Agent templates: what a shared agent carries, what it never carries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.society.agent_template import (
    TEMPLATE_KEYS,
    TemplateError,
    build_draft,
    create_fields,
    load_overlay,
    public_avatar,
    save_overlay,
    scrub_text,
    submission,
    validate_template,
)
from jarvis.society.roster import AgentRecord

SECRET = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"


def _agent(**overrides: object) -> AgentRecord:
    row: dict[str, object] = {
        "agent_id": "inbox-butler",
        "name": "Inbox Butler",
        "title": "Keeps the inbox at zero",
        "description": (
            "You own the inbox of ruben.example@gmail.com.\n\n"
            "Sort mail into Action, Waiting and Read later. Save drafts to "
            "C:\\Users\\ruben\\Documents\\drafts. Never send without approval."
        ),
        "tier": "specialist",
        "state": "active",
        "avatar_json": json.dumps(
            {
                "contract": 1,
                "archetype": "biped",
                "base": "rogue",
                "parts": {"head": "hood"},
                "palette": {"skin": "#f4b68f"},
                "model": "/api/society/figures/mine.glb",
            }
        ),
        "provider": "claude-cli",
        "model": "claude-opus",
        "effort": "high",
        "account_id": "seat-artner",
        "grant_mode": "all",
        "grants_json": "[]",
        "focus_json": json.dumps(["plugin:gmail", "core:browser"]),
        "denies_json": json.dumps(["cli:gh"]),
        "skills_json": None,
        "workspace_dir": "society/inbox-butler/workspace",
        "wiki_namespace": "society/inbox-butler/",
        "knowledge_scope": "shared",
        "permission_ceiling": "ask",
        "approval_mode": "bypass",
        "approval_rules_json": json.dumps(
            {"require_approval": ["plugin:gmail:send"], "always_allow": ["core:shell"]}
        ),
        "daily_budget_usd": 50.0,
        "browser_mode": "attach",
        "browser_allowed_domains_json": json.dumps(["mail.google.com"]),
        "max_concurrent_runs": 5,
        "computer_id": "vps-1",
        "created_ms": 1,
        "updated_ms": 2,
    }
    row.update(overrides)
    return AgentRecord.from_row(row)


def test_template_is_an_allowlist_of_design_fields() -> None:
    template = build_draft(_agent()).template
    assert set(template) <= TEMPLATE_KEYS
    text = json.dumps(template)
    for leaked in (
        "seat-artner",
        "claude-cli",
        "claude-opus",
        "vps-1",
        "attach",
        "mail.google.com",
        "core:shell",  # an always-allow rule never travels
        "society/inbox-butler",
        "mine.glb",
    ):
        assert leaked not in text, leaked
    assert template["require_approval"] == ["plugin:gmail:send"]
    assert template["focus"] == ["plugin:gmail", "core:browser"]
    assert "model" not in template["avatar"]


def test_instructions_are_scrubbed_and_every_cut_is_listed() -> None:
    draft = build_draft(_agent())
    instructions = draft.template["instructions"]
    assert "gmail.com" not in instructions and "[email]" in instructions
    assert "C:\\Users\\ruben" not in instructions and "[folder]" in instructions
    kinds = {(f.kind, f.field) for f in draft.findings}
    assert ("email", "instructions") in kinds
    assert ("path", "instructions") in kinds
    assert validate_template(draft.template, draft.listing) == []


@pytest.mark.parametrize(
    ("text", "kind", "gone"),
    [
        (f"Use the key {SECRET} for the API.", "secret", SECRET),
        ("password: hunter2hunter2", "secret", "hunter2hunter2"),
        ("Call me on +49 151 2345 6789.", "phone", "2345 6789"),
        ("The NAS is at 192.168.1.20.", "address", "192.168.1.20"),
        ("Fetch https://x.test/feed?token=abcd1234efgh", "secret", "abcd1234efgh"),
        ("Notes live in /home/ruben/notes.", "path", "/home/ruben"),
        ("Notes live in /Users/ruben/notes.", "path", "/Users/ruben"),
    ],
)
def test_scrub_removes_personal_details(text: str, kind: str, gone: str) -> None:
    clean, findings = scrub_text(text, "instructions")
    assert gone not in clean
    assert [f.kind for f in findings] == [kind]
    # The hint names the match to its author without repeating it.
    assert gone not in findings[0].hint


def test_scrub_keeps_ordinary_text_and_example_addresses() -> None:
    text = "Reply within 2 days, version 1.2.3.4, write to someone@example.com."
    clean, findings = scrub_text(text, "summary")
    assert clean == text
    assert findings == []


def test_the_lead_cannot_be_shared() -> None:
    with pytest.raises(TemplateError):
        build_draft(_agent(agent_id="jarvis", name="Jarvis", tier="lead"))


def test_overlay_edits_are_scrubbed_too(tmp_path: Path) -> None:
    save_overlay(
        tmp_path,
        "inbox-butler",
        {"summary": f"Sorts mail. Token {SECRET}", "categories": ["Email", "  "]},
    )
    draft = build_draft(_agent(), load_overlay(tmp_path, "inbox-butler"))
    assert SECRET not in draft.listing["description"]
    assert draft.listing["categories"] == ["email"]
    assert any(f.field == "summary" and f.kind == "secret" for f in draft.findings)


def test_overlay_none_clears_a_key(tmp_path: Path) -> None:
    save_overlay(tmp_path, "a", {"summary": "x", "version": "2.0.0"})
    assert save_overlay(tmp_path, "a", {"summary": None}) == {"version": "2.0.0"}
    assert load_overlay(tmp_path, "missing") == {}


def test_next_version_follows_the_last_publish(tmp_path: Path) -> None:
    overlay = {"published": {"name": "inbox-butler", "version": "1.0.4"}}
    assert build_draft(_agent(), overlay).listing["version"] == "1.0.5"
    assert build_draft(_agent()).listing["version"] == "1.0.0"


def test_submission_carries_no_publisher() -> None:
    draft = build_draft(_agent())
    doc = submission(draft.template, draft.listing)
    assert doc["kind"] == "agent"
    assert doc["name"] == "inbox-butler"
    assert "publisher" not in doc and "publisher_id" not in doc


def test_validate_refuses_what_a_stranger_template_must_not_carry() -> None:
    template = build_draft(_agent()).template
    for key, value in (
        ("approval_mode", "bypass"),
        ("permission_ceiling", "monitor"),
        ("account_id", "x"),
        ("provider", "openai"),
    ):
        assert validate_template({**template, key: value}), key
    assert validate_template({**template, "name": "Jarvis"})
    assert validate_template({**template, "tier": "lead"})
    assert validate_template({**template, "avatar": {"model": "/x.glb"}})
    assert validate_template({**template, "instructions": f"use {SECRET}"})
    assert validate_template({**template, "instructions": "files in /home/ruben/x"})


def test_create_fields_start_from_safe_defaults() -> None:
    fields = create_fields(build_draft(_agent()).template)
    assert fields["approval_rules"] == {
        "require_approval": ["plugin:gmail:send"],
        "always_allow": [],
    }
    for absent in ("provider", "model", "account_id", "approval_mode", "permission_ceiling"):
        assert absent not in fields
    assert fields["description"].startswith("You own the inbox")
    with pytest.raises(TemplateError):
        create_fields({"schema": 1, "name": "x"})


def test_public_avatar_drops_an_invalid_companion() -> None:
    avatar = {"contract": 1, "base": "rogue", "companion": {"shape": "star", "color": "red"}}
    assert public_avatar(avatar) == {"contract": 1, "base": "rogue"}
