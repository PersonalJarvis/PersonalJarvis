"""Tests für SelfModRegistry (Phase 7.1).

Plan-Akzeptanzkriterien §7.1:
- Unit-Tests für Registry (List, Lookup, Reject `security.*`)
- Public API: `is_mutable(path)`, `get_spec(path)`, `list_all()`
- Keine Datei-IO auf `jarvis.toml` in dieser Phase
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.self_mod import (
    FORBIDDEN_PATTERNS,
    AllowlistViolationError,
    MutableSpec,
    SecretAccessError,
    SelfModRegistry,
)

# Plan-§7.1 table + voice-tunable computer_use.step_budget (the field the
# screenshot loop actually reads; the legacy max_steps field was a no-op).
# 9 paths in the current allowlist.
EXPECTED_PATHS = {
    "tts.provider",
    "tts.voice_de",
    "tts.voice_en",
    "tts.speed",
    "stt.provider",
    "brain.primary",
    "ui.theme",
    "profile.language",
    "computer_use.step_budget",
}

SAFE_TIER_PATHS = {"tts.speed", "ui.theme"}
RESTART_REQUIRED_PATHS = {
    "stt.provider",
    "brain.primary",
}


# --- list_all + ALLOWED ---


class TestListAll:
    def test_returns_nine_specs(self) -> None:
        # Wave-3 T2 added the 9th spec (``computer_use.engine``).
        assert len(SelfModRegistry.list_all()) == 9

    def test_returns_expected_paths(self) -> None:
        paths = {spec.path for spec in SelfModRegistry.list_all()}
        assert paths == EXPECTED_PATHS

    def test_returns_independent_list_each_call(self) -> None:
        a = SelfModRegistry.list_all()
        b = SelfModRegistry.list_all()
        assert a is not b
        assert a == b

    def test_no_duplicate_paths(self) -> None:
        paths = [spec.path for spec in SelfModRegistry.list_all()]
        assert len(paths) == len(set(paths))


# --- is_mutable ---


class TestIsMutable:
    @pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
    def test_returns_true_for_allowed_path(self, path: str) -> None:
        assert SelfModRegistry.is_mutable(path) is True

    def test_returns_false_for_unknown_path(self) -> None:
        assert SelfModRegistry.is_mutable("brain.unknown_field") is False

    def test_returns_false_for_empty_string(self) -> None:
        assert SelfModRegistry.is_mutable("") is False

    def test_returns_false_for_random_path(self) -> None:
        assert SelfModRegistry.is_mutable("totally.made.up.path") is False

    @pytest.mark.parametrize(
        "path",
        [
            # Plan-§AC §7.1: Reject security.*
            "security.admin_password_hash",
            "security.foo",
            # Plan-§AP-9 erweitert auf weitere geschützte Sektionen
            "mcp_server.transport",
            "mcp_server.auth_token_env",
            "harness.default_timeout_s",
            # Suffix-Patterns für Secrets
            "anthropic_api_key",
            "openai_token",
            "deepgram_secret",
            "user_password",
            "admin_password_hash",
            "spotify_credential",
        ],
    )
    def test_returns_false_for_forbidden_path(self, path: str) -> None:
        assert SelfModRegistry.is_mutable(path) is False


# --- get_spec ---


class TestGetSpec:
    def test_known_path_returns_spec(self) -> None:
        spec = SelfModRegistry.get_spec("tts.provider")
        assert spec is not None
        assert spec.path == "tts.provider"
        assert spec.pydantic_model_name == "TTSConfig"
        assert spec.field_name == "provider"
        assert spec.risk_tier == "ask"
        assert spec.needs_restart is False
        assert spec.description != ""

    def test_unknown_path_returns_none(self) -> None:
        assert SelfModRegistry.get_spec("brain.foo") is None

    def test_forbidden_path_returns_none(self) -> None:
        assert SelfModRegistry.get_spec("security.admin_password_hash") is None

    def test_safe_tier_paths_match_plan(self) -> None:
        """Plan-§AD-10 Bypass-Whitelist: tts.speed und ui.theme sind SAFE."""
        for path in SAFE_TIER_PATHS:
            spec = SelfModRegistry.get_spec(path)
            assert spec is not None
            assert spec.risk_tier == "safe", (
                f"{path} sollte risk_tier='safe' haben"
            )

    def test_ask_tier_paths_match_plan(self) -> None:
        for path in EXPECTED_PATHS - SAFE_TIER_PATHS:
            spec = SelfModRegistry.get_spec(path)
            assert spec is not None
            assert spec.risk_tier == "ask", (
                f"{path} sollte risk_tier='ask' haben"
            )

    def test_restart_flags_match_plan(self) -> None:
        """Plan-§7.1-Tabelle: stt.provider und brain.primary brauchen Restart."""
        for spec in SelfModRegistry.list_all():
            expected = spec.path in RESTART_REQUIRED_PATHS
            assert spec.needs_restart is expected, (
                f"needs_restart-Flag von {spec.path} weicht vom Plan ab"
            )


# --- require_spec ---


class TestRequireSpec:
    def test_known_path_returns_spec(self) -> None:
        spec = SelfModRegistry.require_spec("brain.primary")
        assert spec.path == "brain.primary"

    def test_unknown_path_raises_allowlist_violation(self) -> None:
        with pytest.raises(AllowlistViolationError):
            SelfModRegistry.require_spec("brain.foo")

    def test_forbidden_path_raises_secret_access(self) -> None:
        with pytest.raises(SecretAccessError):
            SelfModRegistry.require_spec("security.admin_password_hash")

    def test_secret_takes_precedence_over_unknown(self) -> None:
        """Ein Pfad, der sowohl forbidden als auch nicht in ALLOWED ist,
        muss SecretAccessError werfen — nicht AllowlistViolationError."""
        with pytest.raises(SecretAccessError):
            SelfModRegistry.require_spec("foo_api_key")


# --- Defense-in-Depth: Allowlist und Forbidden disjunkt ---


class TestForbiddenPatterns:
    def test_no_allowed_path_matches_forbidden_patterns(self) -> None:
        for spec in SelfModRegistry.list_all():
            assert not SelfModRegistry.is_forbidden(spec.path), (
                f"Allowlist-Pfad '{spec.path}' überlappt mit FORBIDDEN_PATTERNS"
            )

    def test_forbidden_patterns_non_empty(self) -> None:
        assert len(FORBIDDEN_PATTERNS) >= 4

    def test_security_wildcard_present(self) -> None:
        assert "security.*" in FORBIDDEN_PATTERNS

    def test_secret_suffix_patterns_present(self) -> None:
        for pattern in ("*_api_key", "*_token", "*_secret", "*_password"):
            assert pattern in FORBIDDEN_PATTERNS, (
                f"Plan-§AP-9 verlangt Pattern {pattern}"
            )


# --- AP-11: kein dynamisches register() ---


class TestNoDynamicRegistration:
    def test_registry_has_no_register_method(self) -> None:
        """AP-11: Allowlist darf nicht zur Laufzeit erweiterbar sein."""
        for forbidden in ("register", "add", "register_path", "append"):
            assert not hasattr(SelfModRegistry, forbidden), (
                f"AP-11-Verletzung: SelfModRegistry hat unerwartete Methode "
                f"'{forbidden}', die dynamische Allowlist-Erweiterung erlauben "
                f"würde."
            )

    def test_mutable_spec_is_frozen(self) -> None:
        """Wenn ein Spec mutabel wäre, könnte ein Tool den risk_tier
        eines Pfads umstellen (`ask → safe`) und Confirmation umgehen.
        """
        spec = SelfModRegistry.list_all()[0]
        with pytest.raises(ValidationError):
            spec.path = "hacked"  # type: ignore[misc]


# --- MutableSpec-Validation ---


class TestMutableSpecValidation:
    def test_invalid_risk_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MutableSpec(
                path="x.y",
                pydantic_model_name="X",
                field_name="y",
                risk_tier="block",  # type: ignore[arg-type]
                description="x",
            )

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MutableSpec(
                path="",
                pydantic_model_name="X",
                field_name="y",
                description="x",
            )

    def test_extra_fields_rejected(self) -> None:
        """`MutableSpec` ist `extra='forbid'`."""
        with pytest.raises(ValidationError):
            MutableSpec(
                path="x.y",
                pydantic_model_name="X",
                field_name="y",
                description="x",
                unexpected_field="boom",  # type: ignore[call-arg]
            )


# --- Plan-AC: keine Datei-IO auf jarvis.toml ---


class TestNoConfigFileIO:
    def test_registry_works_without_jarvis_toml(
        self, tmp_path, monkeypatch
    ) -> None:
        """Plan-AC §7.1: Registry darf in dieser Phase keine Datei lesen.

        Beweis: Im leeren CWD ohne `jarvis.toml` arbeitet die Registry
        weiterhin korrekt, weil sie ausschließlich aus der ClassVar liest.
        """
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "jarvis.toml").exists()

        # Alle drei Public-API-Aufrufe müssen funktionieren.
        assert len(SelfModRegistry.list_all()) == 9
        assert SelfModRegistry.is_mutable("tts.provider") is True
        assert SelfModRegistry.is_mutable("security.admin_password_hash") is False
        spec = SelfModRegistry.get_spec("brain.primary")
        assert spec is not None and spec.field_name == "primary"
