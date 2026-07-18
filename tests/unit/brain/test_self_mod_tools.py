"""Tests for the self-mod brain tools (Phase 7.3).

Plan acceptance criteria §7.3:
- Tool calls work via the Hauptjarvis brain manager (adapter testable
  without an LLM round-trip — defense-in-depth against "trust the model output").
- `set_config_value` creates a pending mutation, does not write.
- SAFE tier is auto-confirmed without user interaction.
- A pending mutation expires after 5min.
- `get_config_value` for `security.admin_password_hash` raises Forbidden.
"""
from __future__ import annotations

import asyncio
import json
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.tools import (
    SELF_MOD_TOOL_NAMES,
    GetConfigValueTool,
    ListMutableSettingsTool,
    SetConfigValueTool,
    build_self_mod_tools,
)
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.self_mod import (
    AtomicConfigWriter,
    PendingMutation,
    PendingMutationStore,
    SelfModAudit,
)

FIXTURE = (
    Path(__file__).parent.parent
    / "self_mod"
    / "fixtures"
    / "minimal_jarvis.toml"
)


# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------


def _isolated_loader(path: Path) -> JarvisConfig:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return JarvisConfig.model_validate(tomllib.loads(raw.decode("utf-8")))


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    target = tmp_path / "jarvis.toml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


@pytest.fixture
def audit_log(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def writer(
    fixture_path: Path, tmp_path: Path, audit_log: SelfModAudit
) -> AtomicConfigWriter:
    return AtomicConfigWriter(
        config_path=fixture_path,
        backup_dir=tmp_path / "backups",
        audit=audit_log,
        config_loader=_isolated_loader,
    )


@pytest.fixture
def pending_store(writer: AtomicConfigWriter) -> PendingMutationStore:
    return PendingMutationStore(writer=writer, auto_confirm_safe=True)


@pytest.fixture
def tools(
    writer: AtomicConfigWriter, pending_store: PendingMutationStore
) -> dict[str, Any]:
    return {
        ListMutableSettingsTool.name: ListMutableSettingsTool(writer=writer),
        GetConfigValueTool.name: GetConfigValueTool(writer=writer),
        SetConfigValueTool.name: SetConfigValueTool(pending_store=pending_store),
    }


def _read_audit(audit: SelfModAudit) -> list[dict]:
    if not audit.path.exists():
        return []
    return [
        json.loads(line)
        for line in audit.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_ctx() -> ExecutionContext:
    """Minimal ExecutionContext stub — the tools don't use it."""
    from uuid import uuid4

    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def _exec(tool: Any, args: dict[str, Any]) -> ToolResult:
    return asyncio.run(tool.execute(args, _make_ctx()))


# ----------------------------------------------------------------------
# Schema inspection (Plan-§AD-9)
# ----------------------------------------------------------------------


class TestSchemaCompliance:
    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_strict_mode_enabled(self, tool_cls: type) -> None:
        assert tool_cls.schema.get("strict") is True, (
            f"{tool_cls.__name__} must have strict mode enabled"
        )

    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_no_additional_properties(self, tool_cls: type) -> None:
        assert tool_cls.schema.get("additionalProperties") is False, (
            f"{tool_cls.__name__} must set additionalProperties=false"
        )

    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_all_properties_required(self, tool_cls: type) -> None:
        """Strict-mode requirement: all properties must be in `required`."""
        properties = tool_cls.schema.get("properties", {})
        required = set(tool_cls.schema.get("required", []))
        property_names = set(properties.keys())
        assert property_names == required, (
            f"{tool_cls.__name__}: properties {property_names} != required {required}"
        )

    @pytest.mark.parametrize(
        ("tool_cls", "min_examples"),
        [
            (ListMutableSettingsTool, 1),  # Plan: input_examples=[{}]
            (GetConfigValueTool, 2),
            (SetConfigValueTool, 2),
        ],
    )
    def test_input_examples_present(
        self, tool_cls: type, min_examples: int
    ) -> None:
        examples = tool_cls.schema.get("input_examples", [])
        assert len(examples) >= min_examples, (
            f"{tool_cls.__name__}: only {len(examples)} input_examples (Plan: ≥{min_examples})"
        )

    def test_self_mod_tool_names_constant(self) -> None:
        assert SELF_MOD_TOOL_NAMES == (
            "list_mutable_settings",
            "get_config_value",
            "set_config_value",
        )


# ----------------------------------------------------------------------
# list_mutable_settings
# ----------------------------------------------------------------------


class TestListMutableSettings:
    def test_returns_full_schema_set(self, tools: dict[str, Any]) -> None:
        # Wave 1.1: list_mutable_settings now returns the whole schema-derived
        # mutable set, not the 13-entry hand-list. Wave 2: each entry also
        # carries value_type (+ enum/range) for NL value-mapping.
        result = _exec(tools["list_mutable_settings"], {})
        assert result.success is True
        assert len(result.output) > 50
        core = {"path", "current_value", "description", "risk_tier", "needs_restart"}
        for entry in result.output:
            assert core <= set(entry.keys())
            assert "value_type" in entry
        # The voice-tunable computer-use step ceiling must be present. Points at
        # step_budget (the field the loop reads), not the legacy max_steps no-op.
        paths = {entry["path"] for entry in result.output}
        assert "computer_use.step_budget" in paths
        # The canonical reply-language path must be discoverable by agents.
        assert "brain.reply_language" in paths

    def test_enriches_with_type_and_constraints(self, tools: dict[str, Any]) -> None:
        # Wave 2: the brain needs the type + bounds to map "talk slower" to a
        # concrete value. tts.speed is a float; ui.language is an enum.
        result = _exec(tools["list_mutable_settings"], {})
        by_path = {entry["path"]: entry for entry in result.output}
        assert by_path["tts.speed"]["value_type"] == "float"
        assert by_path["ui.language"]["value_type"] == "enum"
        assert set(by_path["ui.language"]["allowed_values"]) == {"en", "de", "es"}

    def test_current_values_match_fixture(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["list_mutable_settings"], {})
        by_path = {entry["path"]: entry for entry in result.output}
        # From minimal_jarvis.toml:
        assert by_path["tts.provider"]["current_value"] == "gemini-flash-tts"
        assert by_path["tts.speed"]["current_value"] == 1.0
        assert by_path["profile.language"]["current_value"] == "de"

    def test_redacts_sensitive_paths_if_present(self) -> None:
        """Defense-in-depth: even if a forbidden path accidentally
        ended up in ALLOWED, the current_value in the output would still be redacted."""
        # Synthetic test — direct _maybe_redact function call:
        from jarvis.brain.tools.self_mod_tools import _maybe_redact

        assert _maybe_redact("tts.provider", "elevenlabs") == "elevenlabs"
        assert _maybe_redact("openai_api_key", "sk-1234") == "***"
        assert _maybe_redact("security.admin_password_hash", "abc") == "***"

    def test_rejects_non_empty_args(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["list_mutable_settings"], {"foo": "bar"})
        assert result.success is False
        assert "invalid_input" in result.error


# ----------------------------------------------------------------------
# build factory: auto_apply policy wiring (Wave 1.3)
# ----------------------------------------------------------------------


class TestBuildAutoApply:
    def test_all_policy_applies_ask_tier_immediately(
        self, writer: AtomicConfigWriter
    ) -> None:
        # The voice wiring passes auto_apply="all"; an ASK-tier change then
        # applies at once (no pending), matching "never ask, always now".
        tools = build_self_mod_tools(writer=writer, auto_apply="all")
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        assert result.success is True
        assert result.output["applied"] is True
        assert result.output["needs_confirmation"] is False

    def test_default_policy_defers_ask_tier(
        self, writer: AtomicConfigWriter
    ) -> None:
        # REST/CLI wiring (default) keeps the ASK confirm round-trip.
        tools = build_self_mod_tools(writer=writer)
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        assert result.success is True
        assert result.output["applied"] is False
        assert result.output["needs_confirmation"] is True


# ----------------------------------------------------------------------
# get_config_value
# ----------------------------------------------------------------------


class TestGetConfigValue:
    def test_known_path_returns_value(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["get_config_value"], {"path": "tts.provider"})
        assert result.success is True
        assert result.output == {
            "path": "tts.provider",
            "value": "gemini-flash-tts",
            "in_allowlist": True,
            "description": "TTS provider (hot-reload covered).",
        }

    def test_unknown_path_returns_in_allowlist_false(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["get_config_value"], {"path": "brain.fantasy_field"}
        )
        assert result.success is True
        assert result.output["in_allowlist"] is False
        assert result.output["value"] is None

    def test_security_path_returns_forbidden(self, tools: dict[str, Any]) -> None:
        """Plan-§7.3-AC: get_config_value for security.admin_password_hash
        raises Forbidden."""
        result = _exec(
            tools["get_config_value"],
            {"path": "security.admin_password_hash"},
        )
        assert result.success is False
        assert "forbidden_path" in result.error
        assert result.output["value"] == "***"

    def test_secret_pattern_path_returns_forbidden(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["get_config_value"], {"path": "anthropic_api_key"}
        )
        assert result.success is False
        assert "forbidden_path" in result.error

    def test_invalid_input_rejected(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["get_config_value"], {"path": ""})
        assert result.success is False
        assert "invalid_input" in result.error

    def test_missing_path_rejected(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["get_config_value"], {})
        assert result.success is False
        assert "invalid_input" in result.error


# ----------------------------------------------------------------------
# set_config_value
# ----------------------------------------------------------------------


class TestSetConfigValue:
    def test_safe_tier_auto_applies(
        self,
        tools: dict[str, Any],
        fixture_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Plan-§7.3-AC: SAFE tier is auto-confirmed."""
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.speed", "new_value": 1.25, "reason": "test"},
        )
        assert result.success is True
        out = result.output
        assert out["needs_confirmation"] is False
        assert out["risk_tier"] == "safe"
        assert out["applied"] is True
        assert out["backup_path"] is not None
        # File was actually written
        assert _isolated_loader(fixture_path).tts.speed == 1.25
        # Audit entry from the writer
        entries = _read_audit(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is True

    def test_ask_tier_creates_pending_no_write(
        self,
        tools: dict[str, Any],
        fixture_path: Path,
        pending_store: PendingMutationStore,
    ) -> None:
        """Plan-§7.3-AC: ASK tier creates a pending mutation, does not write."""
        original_bytes = fixture_path.read_bytes()
        result = _exec(
            tools["set_config_value"],
            {
                "path": "tts.provider",
                "new_value": "elevenlabs",
                "reason": "user prefers",
            },
        )
        assert result.success is True
        out = result.output
        assert out["needs_confirmation"] is True
        assert out["risk_tier"] == "ask"
        assert out["applied"] is False
        assert out["backup_path"] is None
        # File unchanged
        assert fixture_path.read_bytes() == original_bytes
        # Pending mutation in the store
        assert len(pending_store) == 1

    def test_ask_tier_pending_id_is_uuid(self, tools: dict[str, Any]) -> None:
        from uuid import UUID

        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        UUID(result.output["id"])  # must not raise

    def test_unknown_path_returns_path_not_allowed(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {"path": "brain.fantasy", "new_value": "x", "reason": ""},
        )
        assert result.success is False
        assert "path_not_allowed" in result.error

    def test_secret_path_returns_forbidden(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {
                "path": "security.admin_password_hash",
                "new_value": "x",
                "reason": "",
            },
        )
        assert result.success is False
        assert "forbidden_path" in result.error

    def test_safe_tier_pre_validate_failure(
        self, tools: dict[str, Any], fixture_path: Path
    ) -> None:
        """Auto-confirm + an invalid value → pre-validate fails,
        the tool returns validate_failed."""
        # tts.speed is SAFE, so auto-confirm. Float coercion fails with a non-numeric string.
        # But Pydantic accepts "1.25" → 1.25. We use tts.provider as an
        # ASK-tier example, which doesn't go through the SAFE/auto path.
        # Instead: tts.speed with a list value (not caught by schema
        # re-validate, since dict/list are explicitly excluded). We use
        # a path test with an invalid bool value for tts.provider (str field).
        # Since provider is ASK, there is no auto-confirm — so also no
        # pre-validate. → Test removed; see TestSetConfigValuePreValidate.
        original = fixture_path.read_bytes()
        # Sanity: test fixture intact
        assert original

    def test_invalid_new_value_type_rejected_by_handler(
        self, tools: dict[str, Any]
    ) -> None:
        """Defense-in-depth schema re-validate (no trust in model output)."""
        # JSON schema strict only accepts primitive types, but the model could
        # try to send a dict.
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": {"a": 1}, "reason": ""},
        )
        assert result.success is False
        assert "invalid_input" in result.error

    def test_missing_required_fields_rejected(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(tools["set_config_value"], {"path": "tts.speed"})
        assert result.success is False
        assert "invalid_input" in result.error


# ----------------------------------------------------------------------
# SAFE-tier pre-validate failure (real PreValidateError path)
# ----------------------------------------------------------------------


class TestSetConfigValuePreValidate:
    def test_safe_tier_pre_validate_failure_returns_validate_failed(
        self, tools: dict[str, Any], fixture_path: Path
    ) -> None:
        """SAFE tier with a value that blows up Pydantic coercion.

        `tts.speed` is `float`. We send a boolean — Pydantic v2 does not
        accept bool as a float coerce in strict-mode context, but the
        default is lax. Better strategy: extreme NaN values pass through,
        but a fixed schema mismatch can be provoked via strict mode.
        """
        # Pydantic accepts bool→float (`True` → 1.0). We take a
        # different route: a tool setup with a forced pre-validate failure
        # via a patched JarvisConfig loader.
        from unittest.mock import patch

        from jarvis.core.self_mod import schema as sm_schema  # noqa: F401

        with patch(
            "jarvis.core.self_mod.writer.JarvisConfig.model_validate",
            side_effect=ValueError("forced pre-validate failure"),
        ):
            result = _exec(
                tools["set_config_value"],
                {"path": "tts.speed", "new_value": 1.5, "reason": ""},
            )
        assert result.success is False
        assert "validate_failed" in result.error
        # File NOT written
        assert _isolated_loader(fixture_path).tts.speed == 1.0


# ----------------------------------------------------------------------
# PendingMutationStore — Confirm/Reject/TTL
# ----------------------------------------------------------------------


class TestPendingStoreLifecycle:
    def test_confirm_persists_value(
        self,
        tools: dict[str, Any],
        pending_store: PendingMutationStore,
        fixture_path: Path,
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        pending_id = __import__("uuid").UUID(result.output["id"])
        mutation_result = pending_store.confirm(pending_id)
        assert mutation_result.ok is True
        assert _isolated_loader(fixture_path).tts.provider == "elevenlabs"
        # Pending mutation was consumed
        assert pending_store.get(pending_id) is None

    def test_reject_does_not_persist(
        self,
        tools: dict[str, Any],
        pending_store: PendingMutationStore,
        fixture_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        original_bytes = fixture_path.read_bytes()
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        pending_id = __import__("uuid").UUID(result.output["id"])
        pending_store.reject(pending_id)
        assert fixture_path.read_bytes() == original_bytes
        assert pending_store.get(pending_id) is None
        # Audit entry with error="rejected_by_user"
        entries = _read_audit(audit_log)
        rejected = [e for e in entries if e.get("error") == "rejected_by_user"]
        assert len(rejected) == 1

    def test_ttl_expires_pending(
        self,
        writer: AtomicConfigWriter,
    ) -> None:
        """Plan-§7.3-AC: pending mutation expires after 5min.

        We set the TTL small and trigger cleanup.
        """
        from jarvis.core.self_mod import MutationRequest

        store = PendingMutationStore(writer=writer, ttl_seconds=0.05)
        request = MutationRequest(
            path="tts.provider",
            new_value="elevenlabs",
        )
        store.create(request)
        assert len(store) == 1
        time.sleep(0.1)
        removed = store.cleanup_expired()
        assert removed == 1
        assert len(store) == 0

    def test_confirm_unknown_id_raises(
        self, pending_store: PendingMutationStore
    ) -> None:
        from uuid import uuid4

        with pytest.raises(KeyError):
            pending_store.confirm(uuid4())

    def test_double_reject_idempotent(
        self,
        tools: dict[str, Any],
        pending_store: PendingMutationStore,
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        pending_id = __import__("uuid").UUID(result.output["id"])
        pending_store.reject(pending_id)
        # Again — must not crash
        pending_store.reject(pending_id)


# ----------------------------------------------------------------------
# Tool visibility (Plan-§AD-2)
# ----------------------------------------------------------------------


class TestToolVisibility:
    def test_self_mod_tools_only_in_router_loader(self) -> None:
        """Plan-§AD-2: self-mod tools are wired exclusively in the router tier.

        Wave-4 migration: previously the test checked ``SUB_TOOLS`` (the
        sub-Jarvis-tier tool set). The sub-Jarvis tier was replaced by the
        Jarvis-Agent bridge (see docs/jarvis-agents-bridge.md §11) —
        ``SUB_TOOLS`` has been deleted. Self-mod tools are now registered
        directly in the router loader (``factory.py:_load_tools_for_tier``
        with ``tier="router"``), and the Jarvis-Agent worker has no access
        to the router's tool set (subprocess boundary).
        """
        from jarvis.brain.factory import ROUTER_TOOLS, SELF_MOD_TOOL_NAMES_ROUTER

        # Self-mod tools are explicitly OUTSIDE the entry_points set,
        # which is why they are NOT in ROUTER_TOOLS — they come in via the
        # separate ``build_self_mod_tools()`` path.
        for tool_name in SELF_MOD_TOOL_NAMES:
            assert tool_name not in ROUTER_TOOLS, (
                f"Self-mod tool '{tool_name}' must NOT be in ROUTER_TOOLS — "
                f"it is injected separately via build_self_mod_tools()."
            )
            assert tool_name in SELF_MOD_TOOL_NAMES_ROUTER, (
                f"Self-mod tool '{tool_name}' is missing from SELF_MOD_TOOL_NAMES_ROUTER"
            )


# ----------------------------------------------------------------------
# build_self_mod_tools factory
# ----------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_three_tools(
        self,
        writer: AtomicConfigWriter,
    ) -> None:
        store = PendingMutationStore(writer=writer)
        tools = build_self_mod_tools(writer=writer, pending_store=store)
        assert set(tools.keys()) == set(SELF_MOD_TOOL_NAMES)

    def test_factory_uses_default_writer_when_none(
        self, tmp_path: Path, fixture_path: Path
    ) -> None:
        tools = build_self_mod_tools(
            config_path=fixture_path,
            writer_kwargs={
                "backup_dir": tmp_path / "backups",
                "audit": SelfModAudit(path=tmp_path / "audit.log"),
                "config_loader": _isolated_loader,
            },
        )
        assert len(tools) == 3
        # Smoke test: list_mutable_settings works
        result = _exec(tools["list_mutable_settings"], {})
        assert result.success is True

    def test_factory_forwards_bus_for_hot_reload(
        self, tmp_path: Path, fixture_path: Path
    ) -> None:
        """The voice path (factory.py) passes the EventBus via writer_kwargs so a
        SAFE-tier write dispatches ConfigReloaded -> the BrainManager language
        hot-reload. Without the bus, a voice "switch to English" only takes
        effect after a restart (the exact "self-mod doesn't work" symptom)."""
        from jarvis.core.events import ConfigReloaded
        from jarvis.core.self_mod import MutationRequest

        captured: list[Any] = []

        class _Bus:
            async def publish(self, ev: Any) -> None:
                captured.append(ev)

        bus = _Bus()
        tools = build_self_mod_tools(
            config_path=fixture_path,
            writer_kwargs={
                "bus": bus,
                "backup_dir": tmp_path / "backups",
                "audit": SelfModAudit(path=tmp_path / "audit.log"),
                "config_loader": _isolated_loader,
            },
        )
        # The bus reached the writer the tools share.
        writer = tools["get_config_value"]._writer
        assert writer._bus is bus
        # A SAFE write dispatches ConfigReloaded (sync call -> deterministic).
        writer.mutate(MutationRequest(path="tts.speed", new_value=1.3))
        assert any(
            isinstance(e, ConfigReloaded) and "tts.speed" in e.changed_keys
            for e in captured
        )


# ----------------------------------------------------------------------
# PendingMutation schema
# ----------------------------------------------------------------------


class TestPendingMutationSchema:
    def test_pending_mutation_serializes_to_json(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        # Payload must be JSON-serializable (for Anthropic tool use)
        json.dumps(result.output)

    def test_pending_mutation_pydantic_strict(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PendingMutation(
                id="not-a-uuid",  # type: ignore[arg-type]
                path="x",
                old_value=None,
                new_value=None,
                needs_confirmation=False,
                risk_tier="safe",
                requires_restart=False,
                applied=True,
                description="x",
            )
