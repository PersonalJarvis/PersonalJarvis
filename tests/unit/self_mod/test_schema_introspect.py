"""Tests for the schema introspector (Voice-First Config Control, Wave 1.1).

The introspector walks ``JarvisConfig`` and emits one ``MutableSpec`` per leaf
primitive field, so the voice-mutable set is derived from the schema instead of
a hand-maintained list. Secrets / self-lockout paths are excluded; a curated
override table refines risk_tier / needs_restart / description for known paths.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from jarvis.core.self_mod.registry import FORBIDDEN_PATTERNS, SelfModRegistry
from jarvis.core.self_mod.schema import MutableSpec
from jarvis.core.self_mod.schema_introspect import (
    SpecOverride,
    describe_field,
    introspect_mutable_specs,
    resolve_model_for_path,
)


def _paths(specs: tuple[MutableSpec, ...]) -> set[str]:
    return {s.path for s in specs}


class TestReach:
    def test_deep_nested_primitive_field_is_mutable(self) -> None:
        # A leaf two levels deep that is NOT in today's 13-entry list — proves
        # the reach now spans the whole schema, not a curated subset.
        paths = _paths(introspect_mutable_specs())
        assert "brain.plausibility.confidence_threshold" in paths
        assert "brain.policy.prompt_cache_heartbeat_seconds" in paths

    def test_top_level_primitive_field_is_mutable(self) -> None:
        paths = _paths(introspect_mutable_specs())
        assert "tts.speed" in paths
        assert "stt.bias_prompt" in paths

    def test_literal_enum_field_is_mutable(self) -> None:
        # ui.language is Literal["en","de","es"] — a constrained string, fully
        # settable by voice. A Literal must count as a primitive leaf.
        paths = _paths(introspect_mutable_specs())
        assert "ui.language" in paths

    def test_emits_far_more_than_the_legacy_handful(self) -> None:
        # The hand-list had 13; the schema has hundreds of leaves.
        assert len(introspect_mutable_specs()) > 50


class TestExclusions:
    @pytest.mark.parametrize("prefix", ["security.", "safety.", "harness.", "mcp_server."])
    def test_forbidden_sections_excluded(self, prefix: str) -> None:
        paths = _paths(introspect_mutable_specs())
        assert not any(p.startswith(prefix) for p in paths), (
            f"forbidden prefix {prefix} leaked into the mutable set"
        )

    def test_no_emitted_path_matches_forbidden_patterns(self) -> None:
        for spec in introspect_mutable_specs():
            assert not SelfModRegistry.is_forbidden(spec.path), (
                f"{spec.path} overlaps FORBIDDEN_PATTERNS"
            )

    def test_list_and_dict_fields_skipped(self) -> None:
        # brain.policy.voice_switch_patterns is a list[str] — not a scalar the
        # dotted-path writer can set, so it must not appear.
        paths = _paths(introspect_mutable_specs())
        assert "brain.policy.voice_switch_patterns" not in paths

    def test_self_lockout_provider_list_not_mutable(self) -> None:
        # brain.providers.enabled is a list — the only "kill the active brain"
        # vector. Because the walker skips lists, it is unreachable by voice, so
        # a user can never empty the provider list and lock out the brain. This
        # is the architectural self-lockout guard (no broad deny pattern needed).
        paths = _paths(introspect_mutable_specs())
        assert not any(p.endswith(".enabled") and "provider" in p for p in paths)


class TestSpecShape:
    def test_pydantic_model_name_and_field_resolve(self) -> None:
        # Every emitted spec must point at a real (or extra-allowed) field on the
        # model that actually owns it in the schema — the TestAllowlistFieldParity
        # contract, applied to the whole introspected set via schema navigation
        # (more robust than getattr(config, name): covers submodule section
        # models like AwarenessPrivacyConfig that config never re-exports).
        for spec in introspect_mutable_specs():
            model = resolve_model_for_path(spec.path)
            assert issubclass(model, BaseModel)
            assert model.__name__ == spec.pydantic_model_name, (
                f"{spec.path}: spec names '{spec.pydantic_model_name}' but the "
                f"owning model is '{model.__name__}'"
            )
            declared = spec.field_name in model.model_fields
            allows_extra = model.model_config.get("extra") == "allow"
            assert declared or allows_extra, (
                f"{spec.path}: field '{spec.field_name}' not on "
                f"{spec.pydantic_model_name} and extras forbidden"
            )

    def test_containing_class_is_used_not_root(self) -> None:
        spec = next(
            s for s in introspect_mutable_specs()
            if s.path == "brain.plausibility.confidence_threshold"
        )
        assert spec.pydantic_model_name == "BrainPlausibilityConfig"
        assert spec.field_name == "confidence_threshold"

    def test_unknown_field_defaults_to_needs_restart_true(self) -> None:
        # A field with no override gets the safe, honest "restart to be sure".
        spec = next(
            s for s in introspect_mutable_specs()
            if s.path == "brain.policy.prompt_cache_heartbeat_seconds"
        )
        assert spec.needs_restart is True


class TestOverrides:
    def test_override_wins_over_auto_derivation(self) -> None:
        overrides = {
            "tts.speed": SpecOverride(
                risk_tier="safe", needs_restart=False, description="Speech speed"
            ),
        }
        spec = next(
            s for s in introspect_mutable_specs(overrides=overrides)
            if s.path == "tts.speed"
        )
        assert spec.risk_tier == "safe"
        assert spec.needs_restart is False
        assert spec.description == "Speech speed"

    def test_override_can_force_an_undeclared_extra_key(self) -> None:
        # ui.theme is an undeclared extra="allow" key on UIConfig — the schema
        # walk can't see it. An override carrying pydantic_model_name + field_name
        # force-includes it.
        overrides = {
            "ui.theme": SpecOverride(
                risk_tier="safe", needs_restart=False,
                pydantic_model_name="UIConfig", field_name="theme",
                description="UI theme",
            ),
        }
        spec = next(
            (s for s in introspect_mutable_specs(overrides=overrides)
             if s.path == "ui.theme"), None
        )
        assert spec is not None
        assert spec.pydantic_model_name == "UIConfig"
        assert spec.field_name == "theme"
        assert spec.risk_tier == "safe"

    def test_override_does_not_force_a_forbidden_path(self) -> None:
        # A forced extra key still cannot override the deny layer.
        overrides = {
            "security.admin_password_hash": SpecOverride(
                pydantic_model_name="SecurityConfig", field_name="admin_password_hash",
            ),
        }
        paths = _paths(introspect_mutable_specs(overrides=overrides))
        assert "security.admin_password_hash" not in paths

    def test_forbidden_patterns_constant_still_exported(self) -> None:
        # Sanity: the introspector relies on the registry's forbidden set.
        assert "security.*" in FORBIDDEN_PATTERNS


class TestDescribeField:
    """Wave 2: value type + constraints, the material the brain needs to map a
    natural-language phrase ("talk slower") onto a concrete (path, value)."""

    def test_int_with_range(self) -> None:
        d = describe_field("computer_use.max_steps")
        assert d["value_type"] == "int"
        assert d["minimum"] == 1
        assert d["maximum"] == 1000

    def test_float_with_exclusive_min(self) -> None:
        d = describe_field("computer_use.per_step_timeout_s")
        assert d["value_type"] == "float"
        assert d["exclusive_minimum"] == 0.0
        assert d["maximum"] == 300.0

    def test_bool(self) -> None:
        d = describe_field("computer_use.enabled")
        assert d["value_type"] == "bool"

    def test_enum_literal(self) -> None:
        d = describe_field("ui.language")
        assert d["value_type"] == "enum"
        assert set(d["allowed_values"]) == {"en", "de", "es"}

    def test_undeclared_extra_key_is_graceful(self) -> None:
        # ui.theme is an extra="allow" key — no declared type to read.
        d = describe_field("ui.theme")
        assert d["value_type"] == "unknown"

    def test_unknown_path_is_graceful(self) -> None:
        d = describe_field("totally.made.up.path")
        assert d["value_type"] == "unknown"


class TestDescribeField:
    """Value-type + constraint extraction for NL value-mapping (Wave 2)."""

    def test_int_with_range(self) -> None:
        d = describe_field("computer_use.max_steps")
        assert d["value_type"] == "int"
        assert d["minimum"] == 1
        assert d["maximum"] == 1000

    def test_float_with_exclusive_min(self) -> None:
        d = describe_field("computer_use.per_step_timeout_s")
        assert d["value_type"] == "float"
        assert d.get("exclusive_minimum") == 0.0
        assert d["maximum"] == 300.0

    def test_bool(self) -> None:
        assert describe_field("computer_use.enabled")["value_type"] == "bool"

    def test_enum_literal_lists_allowed_values(self) -> None:
        d = describe_field("ui.language")
        assert d["value_type"] == "enum"
        assert set(d["allowed_values"]) == {"en", "de", "es"}

    def test_undeclared_extra_key_is_graceful(self) -> None:
        # ui.theme is an undeclared extra="allow" key — no schema type.
        assert describe_field("ui.theme")["value_type"] in ("str", "unknown")

    def test_unknown_path_is_graceful(self) -> None:
        assert describe_field("totally.made.up.path")["value_type"] == "unknown"
