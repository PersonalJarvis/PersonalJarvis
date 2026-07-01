"""Tests for SelfModRegistry (Phase 7.1).

Plan acceptance criteria §7.1:
- Unit tests for the registry (list, lookup, reject `security.*`)
- Public API: `is_mutable(path)`, `get_spec(path)`, `list_all()`
- No file I/O on `jarvis.toml` in this phase
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from jarvis.core.self_mod import (
    FORBIDDEN_PATTERNS,
    AllowlistViolationError,
    MutableSpec,
    SecretAccessError,
    SelfModRegistry,
)
from jarvis.core.self_mod.schema_introspect import resolve_model_for_path

# The 13 CURATED paths — since Wave 1.1 the mutable set is derived from the
# whole JarvisConfig schema, but these keep their human-judgement overrides
# (risk_tier / needs_restart / description). They must remain present and keep
# those attributes; the rest of the schema is now mutable too with safe defaults.
CURATED_PATHS = {
    "tts.provider",
    "tts.voice_de",
    "tts.voice_en",
    "tts.speed",
    "stt.provider",
    "brain.primary",
    "brain.reply_language",
    "stt.language",
    "tts.language_code",
    "ui.theme",
    "ui.language",
    "profile.language",
    "computer_use.step_budget",
}

SAFE_TIER_PATHS = {"tts.speed", "ui.theme", "brain.reply_language", "ui.language"}
RESTART_REQUIRED_PATHS = {
    "stt.provider",
    "brain.primary",
    "stt.language",
    "tts.language_code",
}


# --- list_all + ALLOWED ---


class TestListAll:
    def test_returns_many_specs(self) -> None:
        # Wave 1.1: the set is schema-derived now, not a 13-entry hand-list.
        assert len(SelfModRegistry.list_all()) > 50

    def test_curated_paths_remain_present(self) -> None:
        paths = {spec.path for spec in SelfModRegistry.list_all()}
        assert CURATED_PATHS <= paths

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
    @pytest.mark.parametrize("path", sorted(CURATED_PATHS))
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
            # Plan-§AP-9 extended to further protected sections
            "mcp_server.transport",
            "mcp_server.auth_token_env",
            # Risk-tier whitelist/blacklist — added 2026-06-08 for the Control API
            "safety.whitelist",
            "safety.blacklist.commands",
            "harness.default_timeout_s",
            # Suffix patterns for secrets
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
        """Plan-§AD-10 bypass whitelist: tts.speed and ui.theme are SAFE."""
        for path in SAFE_TIER_PATHS:
            spec = SelfModRegistry.get_spec(path)
            assert spec is not None
            assert spec.risk_tier == "safe", (
                f"{path} should have risk_tier='safe'"
            )

    def test_ask_tier_paths_match_plan(self) -> None:
        for path in CURATED_PATHS - SAFE_TIER_PATHS:
            spec = SelfModRegistry.get_spec(path)
            assert spec is not None
            assert spec.risk_tier == "ask", (
                f"{path} should have risk_tier='ask'"
            )

    def test_restart_flags_match_plan(self) -> None:
        """Curated paths keep their overridden needs_restart flag."""
        for path in CURATED_PATHS:
            spec = SelfModRegistry.get_spec(path)
            assert spec is not None
            expected = path in RESTART_REQUIRED_PATHS
            assert spec.needs_restart is expected, (
                f"needs_restart flag of {path} deviates from the override table"
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
        """A path that is both forbidden and not in ALLOWED
        must raise SecretAccessError — not AllowlistViolationError."""
        with pytest.raises(SecretAccessError):
            SelfModRegistry.require_spec("foo_api_key")


# --- Defense-in-depth: allowlist and forbidden are disjoint ---


class TestForbiddenPatterns:
    def test_no_allowed_path_matches_forbidden_patterns(self) -> None:
        for spec in SelfModRegistry.list_all():
            assert not SelfModRegistry.is_forbidden(spec.path), (
                f"Allowlist path '{spec.path}' overlaps with FORBIDDEN_PATTERNS"
            )

    def test_forbidden_patterns_non_empty(self) -> None:
        assert len(FORBIDDEN_PATTERNS) >= 4

    def test_security_wildcard_present(self) -> None:
        assert "security.*" in FORBIDDEN_PATTERNS

    def test_secret_suffix_patterns_present(self) -> None:
        for pattern in ("*_api_key", "*_token", "*_secret", "*_password"):
            assert pattern in FORBIDDEN_PATTERNS, (
                f"Plan-§AP-9 requires pattern {pattern}"
            )


# --- AP-11: no dynamic register() ---


class TestNoDynamicRegistration:
    def test_registry_has_no_register_method(self) -> None:
        """AP-11: the allowlist must not be extensible at runtime."""
        for forbidden in ("register", "add", "register_path", "append"):
            assert not hasattr(SelfModRegistry, forbidden), (
                f"AP-11 violation: SelfModRegistry has an unexpected method "
                f"'{forbidden}' that would allow dynamic allowlist "
                f"extension."
            )

    def test_mutable_spec_is_frozen(self) -> None:
        """If a spec were mutable, a tool could switch a path's risk_tier
        (`ask → safe`) and bypass confirmation.
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
        """`MutableSpec` is `extra='forbid'`."""
        with pytest.raises(ValidationError):
            MutableSpec(
                path="x.y",
                pydantic_model_name="X",
                field_name="y",
                description="x",
                unexpected_field="boom",  # type: ignore[call-arg]
            )


# --- Plan-AC: no file I/O on jarvis.toml ---


class TestNoConfigFileIO:
    def test_registry_works_without_jarvis_toml(
        self, tmp_path, monkeypatch
    ) -> None:
        """Plan-AC §7.1: the registry must not read any file in this phase.

        Proof: in an empty CWD without `jarvis.toml`, the registry still
        works correctly because it reads exclusively from the ClassVar.
        """
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "jarvis.toml").exists()

        # All three public-API calls must work.
        assert len(SelfModRegistry.list_all()) > 50
        assert SelfModRegistry.is_mutable("tts.provider") is True
        assert SelfModRegistry.is_mutable("security.admin_password_hash") is False
        spec = SelfModRegistry.get_spec("brain.primary")
        assert spec is not None and spec.field_name == "primary"


# --- Allowlist <-> Pydantic field parity (anti-drift guard) ---


class TestAllowlistFieldParity:
    """Every allowlist entry must point at a real Pydantic field.

    Self-mod resolves ``pydantic_model_name`` via ``getattr(jarvis.core.config,
    name)`` and writes ``field_name``. A typo in either (e.g. ``reply_lang``
    instead of ``reply_language``) makes pre-validate fail at runtime for a
    write the user explicitly asked for. This guard catches the drift at test
    time across ALL specs, not just the new language keys.
    """

    @pytest.mark.parametrize("spec", SelfModRegistry.list_all(), ids=lambda s: s.path)
    def test_model_and_field_exist(self, spec: MutableSpec) -> None:
        # Resolve the owning model by navigating JarvisConfig along the path,
        # not getattr(config, name): a submodule section model (e.g.
        # AwarenessPrivacyConfig) is never re-exported into config, but it is
        # still the real owner. This is the stronger anti-drift check.
        model = resolve_model_for_path(spec.path)
        assert issubclass(model, BaseModel)
        assert model.__name__ == spec.pydantic_model_name, (
            f"{spec.path}: spec names '{spec.pydantic_model_name}' but the "
            f"owning model is '{model.__name__}'"
        )
        # A declared field is the strict case. A model with extra="allow"
        # legitimately stores undeclared keys (e.g. ui.theme on UIConfig), so
        # the write still survives pre-validate. Only an undeclared field on an
        # extra="forbid" model would break a mutation the user asked for — that
        # is the drift this guard must catch.
        declared = spec.field_name in model.model_fields
        allows_extra = model.model_config.get("extra") == "allow"
        assert declared or allows_extra, (
            f"{spec.path}: field '{spec.field_name}' is not declared on "
            f"{spec.pydantic_model_name} and the model forbids extras "
            "(allowlist <-> config drift; pre-validate would reject this write)"
        )


# --- Language keys (Jarvis Control API, 2026-06-08) ---


class TestLanguageKeys:
    def test_reply_language_is_canonical_safe_no_restart(self) -> None:
        """The headline ``brain.reply_language`` auto-applies (SAFE) and is
        hot-reloaded (no restart) — that is what makes "switch to English" work
        end-to-end via set_config_value."""
        spec = SelfModRegistry.require_spec("brain.reply_language")
        assert spec.pydantic_model_name == "BrainConfig"
        assert spec.field_name == "reply_language"
        assert spec.risk_tier == "safe"
        assert spec.needs_restart is False

    def test_stt_and_tts_language_are_mutable(self) -> None:
        assert SelfModRegistry.is_mutable("stt.language") is True
        assert SelfModRegistry.is_mutable("tts.language_code") is True
