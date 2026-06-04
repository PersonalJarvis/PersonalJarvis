"""Unit tests for ``CliSpecModel`` validation and ``CliSpec.from_model``.

The catalog loads every entry through ``CliSpecModel.model_validate`` before
projecting it to the runtime ``CliSpec`` dataclass. These tests pin the
validation contract: name pattern, at-least-one-install-method, env_vars vs
secret_keys parity, and the model -> dataclass projection.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.clis.spec import CliSpec, CliSpecModel


def _minimal_payload(**overrides) -> dict:
    base = {
        "name": "demo",
        "display_name": "Demo CLI",
        "description": "A demo CLI",
        "homepage": "https://example.com",
        "binary_name": "demo",
        "check_command": ["demo", "--version"],
        "version_parse_regex": r"(\d+\.\d+\.\d+)",
        "install": {"winget_id": "Demo.Demo"},
        "auth": {"type": "none"},
    }
    base.update(overrides)
    return base


def test_minimal_spec_validates() -> None:
    model = CliSpecModel.model_validate(_minimal_payload())
    assert model.name == "demo"
    assert model.risk.default_tier == "monitor"  # default


def test_name_pattern_rejects_uppercase() -> None:
    with pytest.raises(ValidationError):
        CliSpecModel.model_validate(_minimal_payload(name="Demo"))


def test_name_pattern_rejects_leading_digit() -> None:
    with pytest.raises(ValidationError):
        CliSpecModel.model_validate(_minimal_payload(name="1demo"))


def test_install_requires_at_least_one_method() -> None:
    payload = _minimal_payload(install={})
    with pytest.raises(ValidationError) as exc:
        CliSpecModel.model_validate(payload)
    assert "Install-Methode" in str(exc.value)


def test_install_manual_url_alone_is_valid() -> None:
    payload = _minimal_payload(install={"manual_url": "https://example.com/install"})
    model = CliSpecModel.model_validate(payload)
    assert model.install.manual_url == "https://example.com/install"


def test_env_vars_must_match_secret_keys_length() -> None:
    payload = _minimal_payload(
        auth={
            "type": "api_key",
            "secret_keys": ["KEY_A", "KEY_B"],
            "env_vars": ["ENV_A"],  # mismatched length
        }
    )
    with pytest.raises(ValidationError):
        CliSpecModel.model_validate(payload)


def test_env_vars_empty_is_allowed() -> None:
    payload = _minimal_payload(
        auth={
            "type": "api_key",
            "secret_keys": ["KEY_A", "KEY_B"],
            "env_vars": [],
        }
    )
    model = CliSpecModel.model_validate(payload)
    assert model.auth.secret_keys == ["KEY_A", "KEY_B"]


def test_description_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        CliSpecModel.model_validate(_minimal_payload(description="x" * 301))


def test_from_model_projects_to_dataclass() -> None:
    payload = _minimal_payload(
        auth={
            "type": "api_key",
            "secret_keys": ["DEMO_KEY"],
            "env_vars": ["DEMO_KEY_ENV"],
            "status_command": ["demo", "auth", "status"],
            "status_parse": "text_contains_key",
        },
        risk={
            "default_tier": "ask",
            "blacklist_patterns": ["demo * delete *"],
            "whitelist_patterns": ["demo * list *"],
        },
        tool_schema_examples=["demo list", "demo describe foo"],
    )
    model = CliSpecModel.model_validate(payload)
    spec = CliSpec.from_model(model)

    assert isinstance(spec, CliSpec)
    assert spec.name == "demo"
    assert spec.auth.type == "api_key"
    assert spec.auth.secret_keys == ("DEMO_KEY",)
    assert spec.auth.env_vars == ("DEMO_KEY_ENV",)
    assert spec.risk.default_tier == "ask"
    assert spec.risk.blacklist_patterns == ("demo * delete *",)
    assert spec.tool_schema_examples == ("demo list", "demo describe foo")
    # frozen dataclass is hashable
    assert hash(spec) is not None


def test_seed_catalog_entries_all_validate() -> None:
    """Every shipped seed entry must validate — regression guard against a
    bad edit to ``seed_catalog.json``."""
    from jarvis.clis.catalog import CliCatalog

    catalog = CliCatalog()
    seeds = catalog.seed_names()
    assert len(seeds) >= 15, f"expected >=15 seed CLIs, got {len(seeds)}"
    for name in seeds:
        spec = catalog.get(name)
        assert spec is not None
        assert spec.binary_name
        assert spec.check_command
