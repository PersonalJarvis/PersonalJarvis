"""Tests für die Self-Mod-Brain-Tools (Phase 7.3).

Plan-Akzeptanzkriterien §7.3:
- Tool-Calls funktionieren via Hauptjarvis-Brain-Manager (Adapter testbar
  ohne LLM-Roundtrip — Defense-in-Depth gegen "trust the model output").
- `set_config_value` legt Pending an, schreibt nicht.
- SAFE-Tier wird automatisch konfirmiert ohne User-Interaktion.
- Pending läuft nach 5min ab.
- `get_config_value` für `security.admin_password_hash` wirft Forbidden.
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
# Test-Fixtures
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
    """Minimaler ExecutionContext-Stub — die Tools nutzen ihn nicht."""
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
# Schema-Inspektion (Plan-§AD-9)
# ----------------------------------------------------------------------


class TestSchemaCompliance:
    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_strict_mode_enabled(self, tool_cls: type) -> None:
        assert tool_cls.schema.get("strict") is True, (
            f"{tool_cls.__name__} muss strict-Mode aktiviert haben"
        )

    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_no_additional_properties(self, tool_cls: type) -> None:
        assert tool_cls.schema.get("additionalProperties") is False, (
            f"{tool_cls.__name__} muss additionalProperties=false setzen"
        )

    @pytest.mark.parametrize(
        "tool_cls",
        [ListMutableSettingsTool, GetConfigValueTool, SetConfigValueTool],
    )
    def test_all_properties_required(self, tool_cls: type) -> None:
        """Strict-Mode-Anforderung: alle Properties müssen in `required`."""
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
            f"{tool_cls.__name__}: nur {len(examples)} input_examples (Plan: ≥{min_examples})"
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
    def test_returns_nine_entries(self, tools: dict[str, Any]) -> None:
        # Allowlist: 8 base settings + voice-tunable computer_use.step_budget.
        result = _exec(tools["list_mutable_settings"], {})
        assert result.success is True
        assert len(result.output) == 9
        for entry in result.output:
            assert {"path", "current_value", "description", "risk_tier", "needs_restart"} == set(
                entry.keys()
            )
        # The voice-tunable computer-use step ceiling must be present. Points at
        # step_budget (the field the loop reads), not the legacy max_steps no-op.
        paths = {entry["path"] for entry in result.output}
        assert "computer_use.step_budget" in paths

    def test_current_values_match_fixture(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["list_mutable_settings"], {})
        by_path = {entry["path"]: entry for entry in result.output}
        # Aus minimal_jarvis.toml:
        assert by_path["tts.provider"]["current_value"] == "gemini-flash-tts"
        assert by_path["tts.speed"]["current_value"] == 1.0
        assert by_path["profile.language"]["current_value"] == "de"

    def test_redacts_sensitive_paths_if_present(self) -> None:
        """Defense-in-Depth: selbst wenn ein Forbidden-Pfad versehentlich
        in ALLOWED landen würde, würde der current_value im Output redacted."""
        # Synthetischer Test — direkte _maybe_redact-Funktion:
        from jarvis.brain.tools.self_mod_tools import _maybe_redact

        assert _maybe_redact("tts.provider", "elevenlabs") == "elevenlabs"
        assert _maybe_redact("openai_api_key", "sk-1234") == "***"
        assert _maybe_redact("security.admin_password_hash", "abc") == "***"

    def test_rejects_non_empty_args(self, tools: dict[str, Any]) -> None:
        result = _exec(tools["list_mutable_settings"], {"foo": "bar"})
        assert result.success is False
        assert "invalid_input" in result.error


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
            "description": "TTS-Provider (Hot-Reload abgedeckt)",
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
        """Plan-§7.3-AC: get_config_value für security.admin_password_hash
        wirft Forbidden."""
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
        """Plan-§7.3-AC: SAFE-Tier wird automatisch konfirmiert."""
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
        # File wurde wirklich geschrieben
        assert _isolated_loader(fixture_path).tts.speed == 1.25
        # Audit-Eintrag vom Writer
        entries = _read_audit(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is True

    def test_ask_tier_creates_pending_no_write(
        self,
        tools: dict[str, Any],
        fixture_path: Path,
        pending_store: PendingMutationStore,
    ) -> None:
        """Plan-§7.3-AC: ASK-Tier legt Pending an, schreibt nicht."""
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
        # File unverändert
        assert fixture_path.read_bytes() == original_bytes
        # Pending im Store
        assert len(pending_store) == 1

    def test_ask_tier_pending_id_is_uuid(self, tools: dict[str, Any]) -> None:
        from uuid import UUID

        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        UUID(result.output["id"])  # darf nicht werfen

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
        """Auto-Confirm + invalider Wert → Pre-Validate scheitert,
        Tool gibt validate_failed zurück."""
        # tts.speed ist SAFE, also auto-confirm. Float-Coercion fail mit non-numeric string.
        # Aber Pydantic akzeptiert "1.25" → 1.25. Wir nutzen tts.provider als
        # ASK-Tier-Beispiel, geht via SAFE/Auto-Pfad nicht.
        # Stattdessen: tts.speed mit list-Wert (nicht durch Schema-Re-Validate
        # gefangen, weil dict/list explizit ausgeschlossen). Wir nutzen
        # einen Pfad-Test mit invalidem Bool-Wert für tts.provider (str-Field).
        # Da provider ASK ist, gibt es kein Auto-Confirm — also auch kein
        # Pre-Validate. → Test entfernt; siehe TestSetConfigValuePreValidate.
        original = fixture_path.read_bytes()
        # Sanity: Test-Fixture intakt
        assert original

    def test_invalid_new_value_type_rejected_by_handler(
        self, tools: dict[str, Any]
    ) -> None:
        """Defense-in-Depth-Schema-Re-Validate (kein Vertrauen in Modell-Output)."""
        # JSON-Schema akzeptiert strict nur primitive Types, aber das Modell könnte
        # versuchen, ein dict zu schicken.
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
# SAFE-Tier-Pre-Validate-Failure (echter PreValidateError-Pfad)
# ----------------------------------------------------------------------


class TestSetConfigValuePreValidate:
    def test_safe_tier_pre_validate_failure_returns_validate_failed(
        self, tools: dict[str, Any], fixture_path: Path
    ) -> None:
        """SAFE-Tier mit Wert, der Pydantic-Coercion sprengt.

        `tts.speed` ist `float`. Wir senden eine Boolean — Pydantic v2
        akzeptiert bool als float-Coerce nicht in strict-Mode-Kontext, aber
        Default ist lax. Bessere Strategie: extreme NaN-Werte gehen durch,
        aber ein fixer Schema-Mismatch via Strict-Mode lässt sich provozieren.
        """
        # Pydantic akzeptiert bool→float (`True` → 1.0). Wir gehen einen
        # anderen Weg: ein Tool-Setup mit forciertem Pre-Validate-Failure
        # via patched JarvisConfig-Loader.
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
        # File NICHT geschrieben
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
        # Pending wurde konsumiert
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
        # Audit-Eintrag mit error="rejected_by_user"
        entries = _read_audit(audit_log)
        rejected = [e for e in entries if e.get("error") == "rejected_by_user"]
        assert len(rejected) == 1

    def test_ttl_expires_pending(
        self,
        writer: AtomicConfigWriter,
    ) -> None:
        """Plan-§7.3-AC: Pending läuft nach 5min ab.

        Wir setzen TTL klein und triggern cleanup.
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
        # Nochmal — darf nicht crashen
        pending_store.reject(pending_id)


# ----------------------------------------------------------------------
# Tool-Sichtbarkeit (Plan-§AD-2)
# ----------------------------------------------------------------------


class TestToolVisibility:
    def test_self_mod_tools_only_in_router_loader(self) -> None:
        """Plan-§AD-2: Self-Mod-Tools sind ausschliesslich im Router-Tier verdrahtet.

        Welle-4-Migration: vorher pruefte der Test ``SUB_TOOLS`` (Sub-Jarvis-
        Tier-Tool-Set). Sub-Jarvis-Tier wurde durch die OpenClaw-Bridge
        ersetzt (siehe docs/openclaw-bridge.md §11) — ``SUB_TOOLS`` ist
        geloescht. Self-Mod-Tools werden jetzt im Router-Loader direkt
        registriert (``factory.py:_load_tools_for_tier`` mit ``tier="router"``)
        und der OpenClaw-Worker hat keinen Zugriff auf das Tool-Set des
        Routers (Subprocess-Boundary).
        """
        from jarvis.brain.factory import ROUTER_TOOLS, SELF_MOD_TOOL_NAMES_ROUTER

        # Self-Mod-Tools sind explizit AUSSERHALB des entry_points-Sets,
        # weshalb sie NICHT in ROUTER_TOOLS stehen — sie kommen ueber den
        # separaten ``build_self_mod_tools()``-Pfad rein.
        for tool_name in SELF_MOD_TOOL_NAMES:
            assert tool_name not in ROUTER_TOOLS, (
                f"Self-Mod-Tool '{tool_name}' darf NICHT in ROUTER_TOOLS sein — "
                f"sie werden separat ueber build_self_mod_tools() injected."
            )
            assert tool_name in SELF_MOD_TOOL_NAMES_ROUTER, (
                f"Self-Mod-Tool '{tool_name}' fehlt in SELF_MOD_TOOL_NAMES_ROUTER"
            )


# ----------------------------------------------------------------------
# build_self_mod_tools-Factory
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
        # Smoke: list_mutable_settings funktioniert
        result = _exec(tools["list_mutable_settings"], {})
        assert result.success is True


# ----------------------------------------------------------------------
# PendingMutation-Schema
# ----------------------------------------------------------------------


class TestPendingMutationSchema:
    def test_pending_mutation_serializes_to_json(
        self, tools: dict[str, Any]
    ) -> None:
        result = _exec(
            tools["set_config_value"],
            {"path": "tts.provider", "new_value": "elevenlabs", "reason": ""},
        )
        # Payload muss JSON-serialisierbar sein (für Anthropic-Tool-Use)
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
