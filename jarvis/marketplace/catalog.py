"""Plugin Marketplace catalog schema.

Loaded from `data/plugin_catalog.json` at server startup and re-served to
the frontend via `/api/marketplace/plugins`. Five auth modes are modelled
today; each is its own Pydantic submodel and `AuthConfig` is a
discriminated union over the `mode` field.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseAuth(BaseModel):
    """`extra="forbid"`: a typo in the JSON raises ValidationError instead
    of silently dropping fields. Catalog drift is the failure mode we paid
    for once already."""

    model_config = ConfigDict(extra="forbid")


class PatPasteAuth(_BaseAuth):
    mode: Literal["pat_paste"]
    token_creation_url: str
    token_prefix: str
    validation_endpoint: str
    instruction_md: str
    # How to present the pasted token when validating + wiring downstream:
    #   bearer        -> Authorization: Bearer <token>  (GitHub/Vercel/Supabase)
    #   bot           -> Authorization: Bot <token>      (Discord)
    #   telegram_path -> token spliced into the URL {token}, no header; body ok==true
    auth_scheme: Literal["bearer", "bot", "telegram_path"] = "bearer"


class OAuthDeviceFlowAuth(_BaseAuth):
    mode: Literal["oauth_device_flow"]
    device_url: str
    verify_url: str
    token_url: str
    client_id: str
    scopes: list[str]
    access_token_ttl_seconds: int | None = None
    refresh_token_ttl_seconds: int | None = None


class HostedMcpOAuthDcrAuth(_BaseAuth):
    mode: Literal["hosted_mcp_oauth_dcr"]
    discovery_url: str
    mcp_url: str
    fallback_mcp_url: str | None = None
    access_token_ttl_seconds: int | None = None
    refresh_supported: bool = False
    capabilities: list[str] = Field(default_factory=list)


class OAuthPkceLoopbackAuth(_BaseAuth):
    mode: Literal["oauth_pkce_loopback"]
    authorization_url: str
    token_url: str
    revocation_url: str | None = None
    client_id: str
    client_secret: str | None = Field(default=None, exclude=True)
    callback_port: int = 0
    callback_path: str = "/oauth/callback"
    scopes: list[str]
    scope_separator: Literal["comma", "space"] = "comma"
    user_scopes_only: bool = False
    refresh_supported: bool = False
    refresh_token_ttl_days: int | None = None
    # RFC 8707 resource indicator (Asana V2 MCP needs resource=…/v2).
    resource: str | None = None
    # Google desktop clients need access_type=offline + prompt=consent to
    # return a refresh token.
    offline_access: bool = False


class HostedMcpAllowlistAuth(_BaseAuth):
    mode: Literal["hosted_mcp_allowlist"]
    mcp_url: str
    application_url: str | None = None


AuthConfig = Annotated[
    PatPasteAuth
    | OAuthDeviceFlowAuth
    | HostedMcpOAuthDcrAuth
    | OAuthPkceLoopbackAuth
    | HostedMcpAllowlistAuth,
    Field(discriminator="mode"),
]


Category = Literal["Developer", "Productivity", "Communication"]


class PluginSpec(_BaseAuth):
    id: str
    display_name: str
    description: str
    category: Category
    logo_slug: str
    logo_color: str | None = None
    # When set, the frontend uses this URL instead of the simpleicons CDN.
    # Use for brands whose original logo is multicolor (e.g. Slack's hash).
    logo_url: str | None = None
    featured: bool = False
    auth: AuthConfig
    # Installer/transport metadata for the eventual MCP-spawn wave. The route
    # layer does not consume this today, but the catalog already carries it,
    # and rejecting unknown subkeys here would just force a schema bump every
    # time a new transport variant lands. Keep it loosely typed.
    mcp_server: dict[str, Any] | None = None
    # Native in-process router tool backing this plugin when no MCP server
    # exists (Gmail uses the REST API directly with marketplace-stored tokens).
    native_tool: str | None = None
    post_install_hint_md: str | None = None
    future_v2_note: str | None = None


class PluginCatalog(_BaseAuth):
    version: int
    schema_version: str
    plugins: list[PluginSpec]

    def by_id(self, plugin_id: str) -> PluginSpec | None:
        return next((p for p in self.plugins if p.id == plugin_id), None)
