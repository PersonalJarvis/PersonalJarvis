"""Data model for CLI specs.

A ``CliSpec`` fully describes **one** CLI (gcloud, gh, stripe, ...):
install methods, auth flow, risk policy, and tool-description examples. The specs
are *metadata* — they contain no runtime state (no version, no auth status).
``CliStatusProber`` derives a ``CliStatus`` projection from them at runtime.

Serialization: specs are stored as JSON in ``catalog/seed_catalog.json`` for
the curated catalog and in ``~/.jarvis/clis/custom.json`` for user specs.
Both are merged via ``CliCatalog.load_all()``. Validation runs through
``CliSpecModel`` (Pydantic); the runtime form remains a frozen dataclass for
hashability + performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AuthType = Literal["oauth_cli", "api_key", "config_file", "none"]

StatusParseStrategy = Literal[
    "json_accounts",
    "json_object_exists",
    "json_array_nonempty",
    "json_array_nonempty_or_error",
    "json_has_field_username",
    "text_contains_email",
    "text_contains_username",
    "text_contains_logged_in",
    "text_contains_key",
    "text_nonempty",
]

RiskTier = Literal["safe", "monitor", "ask", "block"]


@dataclass(frozen=True, slots=True)
class InstallMethods:
    winget_id: str | None = None
    scoop_package: str | None = None
    npm_package: str | None = None
    pip_package: str | None = None
    cargo_package: str | None = None
    script_url: str | None = None
    manual_url: str = ""
    # Preference explicitly set by the catalog editor. None = the first
    # available method string from available_methods() is pre-selected as the
    # default in the UI. Important for CLIs whose winget ID does not exist
    # at all (e.g. supabase) — there, recommended must be explicitly set to
    # scoop/npm so the user is not led into a dead winget path.
    recommended: str | None = None

    def available_methods(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.winget_id: out.append("winget")
        if self.scoop_package: out.append("scoop")
        if self.npm_package: out.append("npm")
        if self.pip_package: out.append("pip")
        if self.cargo_package: out.append("cargo")
        if self.script_url: out.append("script")
        if self.manual_url and not out: out.append("manual")
        return tuple(out)


@dataclass(frozen=True, slots=True)
class AuthConfig:
    type: AuthType
    login_command: tuple[str, ...] | None = None
    logout_command: tuple[str, ...] | None = None
    status_command: tuple[str, ...] = ()
    status_parse: StatusParseStrategy = "text_nonempty"
    secret_keys: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskConfig:
    default_tier: RiskTier = "monitor"
    blacklist_patterns: tuple[str, ...] = ()
    whitelist_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CliSpec:
    name: str
    display_name: str
    description: str
    homepage: str
    binary_name: str
    check_command: tuple[str, ...]
    version_parse_regex: str
    install: InstallMethods
    auth: AuthConfig
    risk: RiskConfig
    tool_schema_examples: tuple[str, ...] = ()
    icon: str = ""
    category: str = "other"
    source: Literal["seed", "custom"] = "seed"

    @classmethod
    def from_model(cls, model: "CliSpecModel") -> "CliSpec":
        return cls(
            name=model.name,
            display_name=model.display_name,
            description=model.description,
            homepage=model.homepage,
            binary_name=model.binary_name,
            check_command=tuple(model.check_command),
            version_parse_regex=model.version_parse_regex,
            install=InstallMethods(
                winget_id=model.install.winget_id,
                scoop_package=model.install.scoop_package,
                npm_package=model.install.npm_package,
                pip_package=model.install.pip_package,
                cargo_package=model.install.cargo_package,
                script_url=model.install.script_url,
                manual_url=model.install.manual_url or "",
                recommended=model.install.recommended,
            ),
            auth=AuthConfig(
                type=model.auth.type,
                login_command=(
                    tuple(model.auth.login_command) if model.auth.login_command else None
                ),
                logout_command=(
                    tuple(model.auth.logout_command) if model.auth.logout_command else None
                ),
                status_command=tuple(model.auth.status_command),
                status_parse=model.auth.status_parse,
                secret_keys=tuple(model.auth.secret_keys),
                env_vars=tuple(model.auth.env_vars),
            ),
            risk=RiskConfig(
                default_tier=model.risk.default_tier,
                blacklist_patterns=tuple(model.risk.blacklist_patterns),
                whitelist_patterns=tuple(model.risk.whitelist_patterns),
            ),
            tool_schema_examples=tuple(model.tool_schema_examples),
            icon=model.icon or "",
            category=model.category or "other",
            source=model.source or "seed",
        )


@dataclass(slots=True)
class CliStatus:
    installed: bool = False
    version: str | None = None
    binary_path: str | None = None
    auth_status: Literal["connected", "expired", "not_connected", "unknown"] = "unknown"
    last_used_at: int | None = None
    usage_count_7d: int = 0
    error: str | None = None


class InstallMethodsModel(BaseModel):
    winget_id: str | None = None
    scoop_package: str | None = None
    npm_package: str | None = None
    pip_package: str | None = None
    cargo_package: str | None = None
    script_url: str | None = None
    manual_url: str = ""
    recommended: str | None = None


class AuthConfigModel(BaseModel):
    type: AuthType
    login_command: list[str] | None = None
    logout_command: list[str] | None = None
    status_command: list[str] = Field(default_factory=list)
    status_parse: StatusParseStrategy = "text_nonempty"
    secret_keys: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)

    @field_validator("env_vars")
    @classmethod
    def _env_vars_match_secret_keys(cls, v: list[str], info: Any) -> list[str]:
        secret_keys = info.data.get("secret_keys", [])
        if v and secret_keys and len(v) != len(secret_keys):
            raise ValueError(
                f"env_vars ({len(v)}) muss zu secret_keys ({len(secret_keys)}) passen "
                "oder leer sein."
            )
        return v


class RiskConfigModel(BaseModel):
    default_tier: RiskTier = "monitor"
    blacklist_patterns: list[str] = Field(default_factory=list)
    whitelist_patterns: list[str] = Field(default_factory=list)


class CliSpecModel(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")
    display_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    homepage: str = Field(default="")
    binary_name: str = Field(min_length=1, max_length=60)
    check_command: list[str] = Field(min_length=1)
    version_parse_regex: str
    install: InstallMethodsModel
    auth: AuthConfigModel
    risk: RiskConfigModel = Field(default_factory=RiskConfigModel)
    tool_schema_examples: list[str] = Field(default_factory=list, max_length=10)
    icon: str = ""
    category: str = "other"
    source: Literal["seed", "custom"] = "seed"

    @field_validator("install")
    @classmethod
    def _at_least_one_install_method(cls, v: InstallMethodsModel) -> InstallMethodsModel:
        if not any([
            v.winget_id, v.scoop_package, v.npm_package,
            v.pip_package, v.cargo_package, v.script_url, v.manual_url,
        ]):
            raise ValueError(
                "Mindestens eine Install-Methode oder manual_url muss gesetzt sein."
            )
        return v


__all__ = [
    "AuthType", "StatusParseStrategy", "RiskTier",
    "InstallMethods", "AuthConfig", "RiskConfig", "CliSpec", "CliStatus",
    "CliSpecModel", "InstallMethodsModel", "AuthConfigModel", "RiskConfigModel",
]
