"""Parity guards for the Command Registry (the AP-4 anti-drift contract).

The registry is only trustworthy if every entry stays true on three axes:
(1) its endpoint really exists in the live OpenAPI schema, (2) its ui_section
is a real sidebar section, (3) its danger flag is at least as strict as the
CLI safety heuristic for the same path. These tests turn all three from
"hopefully" into a contract.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from jarvis.commands.registry import (
    REPLY_LANGUAGES,
    get_command,
    get_registry,
    registry_as_dicts,
)

_KEBAB = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _openapi_paths() -> dict:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig
    from jarvis.ui.web.server import WebServer

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    server = WebServer(cfg, bus=EventBus())
    return server.app.openapi()["paths"]


def test_ids_are_unique_and_kebab_case() -> None:
    ids = [c.id for c in get_registry()]
    assert len(ids) == len(set(ids)), "duplicate command ids"
    for cid in ids:
        assert _KEBAB.match(cid), f"command id {cid!r} is not kebab-case"


def test_every_command_endpoint_exists_in_openapi() -> None:
    """A registry entry pointing at a missing/renamed route is a defect —
    the exact drift class this registry exists to eliminate."""
    paths = _openapi_paths()
    for cmd in get_registry():
        assert cmd.path in paths, (
            f"{cmd.id}: path {cmd.path!r} is not a mounted route"
        )
        assert cmd.method.lower() in paths[cmd.path], (
            f"{cmd.id}: {cmd.method} not offered on {cmd.path!r} "
            f"(has: {sorted(paths[cmd.path])})"
        )


def test_every_ui_section_is_a_real_sidebar_section() -> None:
    from jarvis.plugins.tool.navigate import KNOWN

    for cmd in get_registry():
        assert cmd.ui_section in KNOWN, (
            f"{cmd.id}: ui_section {cmd.ui_section!r} is not a sidebar section"
        )


def test_params_schemas_are_wellformed_objects() -> None:
    for cmd in get_registry():
        if not cmd.params:
            continue
        assert cmd.params.get("type") == "object", f"{cmd.id}: params not an object"
        props = cmd.params.get("properties", {})
        assert isinstance(props, dict)
        for req in cmd.params.get("required", []):
            assert req in props, f"{cmd.id}: required {req!r} missing in properties"
        # Every path placeholder must be a declared path param AND a property.
        placeholders = set(_PLACEHOLDER.findall(cmd.path))
        assert placeholders == set(cmd.path_params), (
            f"{cmd.id}: path placeholders {placeholders} != path_params "
            f"{set(cmd.path_params)}"
        )
        for p in cmd.path_params:
            assert p in props, f"{cmd.id}: path param {p!r} missing in properties"


def test_danger_flags_at_least_as_strict_as_cli_heuristic() -> None:
    """The CLI's method+path heuristic is the floor: anything IT calls
    destructive must be dangerous here too (the registry may be stricter)."""
    from jarvis.cli_ctl import safety

    for cmd in get_registry():
        if safety.is_dangerous(cmd.method, cmd.path):
            assert cmd.dangerous, (
                f"{cmd.id}: {cmd.method} {cmd.path} is dangerous per the CLI "
                "heuristic but not flagged dangerous in the registry"
            )


def test_reply_languages_match_brain_source_of_truth() -> None:
    from jarvis.brain.manager import SUPPORTED_REPLY_LANGUAGES

    assert REPLY_LANGUAGES == SUPPORTED_REPLY_LANGUAGES


def test_brain_switch_enum_excludes_subagent_only_providers() -> None:
    """codex/antigravity are brain_switchable=False — the LLM must never even
    see them as valid brain-switch targets."""
    cmd = get_command("brain-switch")
    assert cmd is not None
    enum = cmd.params["properties"]["provider"].get("enum") or []
    assert enum, "brain-switch provider enum missing (catalog import failed?)"
    assert "codex" not in enum
    assert "antigravity" not in enum


def test_registry_served_over_rest() -> None:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig
    from jarvis.ui.web.server import WebServer

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    server = WebServer(cfg, bus=EventBus())
    with TestClient(server.app) as client:
        resp = client.get("/api/commands")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == len(get_registry())
        assert {c["id"] for c in body["commands"]} == {c.id for c in get_registry()}

        one = client.get("/api/commands/brain-switch")
        assert one.status_code == 200
        assert one.json()["path"] == "/api/brain/switch"

        missing = client.get("/api/commands/does-not-exist")
        assert missing.status_code == 404


def test_registry_dicts_are_json_serializable_and_small() -> None:
    import json

    payload = json.dumps(registry_as_dicts(), ensure_ascii=False)
    # "Simple storage": the whole catalog must stay small (target << 100 KB).
    assert len(payload.encode("utf-8")) < 100_000


@pytest.mark.parametrize("locale", ["de", "en", "es"])
def test_every_command_has_voice_aliases_for_all_supported_locales(locale) -> None:
    """Supported languages are equal (CLAUDE.md §1) — no de/en-only bias."""
    for cmd in get_registry():
        assert cmd.voice_aliases.get(locale), (
            f"{cmd.id}: missing {locale} voice alias"
        )
