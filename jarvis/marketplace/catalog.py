"""Plugin Marketplace catalog schema.

Loaded from `data/plugin_catalog.json` at server startup and re-served to
the frontend via `/api/marketplace/plugins`. Five auth modes are modelled
today; each is its own Pydantic submodel and `AuthConfig` is a
discriminated union over the `mode` field.

Packaging policy: new marketplace plugins are submitted in the Agent
Plugins v1.0.0 format (https://agent-plugins.org/) and the existing
entries migrate to it — see docs/marketplace/agent-plugins-standard.md
for the field mapping and the per-plugin migration tracker before adding
or changing a catalog entry.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseAuth(BaseModel):
    """`extra="forbid"`: a typo in the JSON raises ValidationError instead
    of silently dropping fields. Catalog drift is the failure mode we paid
    for once already."""

    model_config = ConfigDict(extra="forbid")


class InstanceUrlSpec(_BaseAuth):
    """A per-user base address, for services the user hosts themselves.

    Home Assistant, Jellyfin, Nextcloud, Paperless and Immich have no fixed
    public endpoint: the address IS user data, so the catalog can only describe
    the field, never fill it. When present, the connect dialog asks for the
    address alongside the token and `validation_endpoint` is treated as a path
    RELATIVE to it.
    """

    label: str
    placeholder: str
    # Appended to the entered address to check the credential works. Kept
    # separate from the address so a user pasting a trailing slash, a path, or
    # the full URL of some page still validates.
    validation_path: str = "/"
    help_md: str | None = None


class PatPasteAuth(_BaseAuth):
    mode: Literal["pat_paste"]
    token_creation_url: str
    token_prefix: str
    validation_endpoint: str
    instruction_md: str
    # Set for self-hosted services: the user supplies the address, so
    # `validation_endpoint` above is ignored in favour of
    # `instance_url.validation_path` appended to what they entered.
    instance_url: InstanceUrlSpec | None = None
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


# Display order for the Plugins view, served to the frontend alongside the
# catalog. `category` is deliberately a plain string, NOT a Literal: the value
# crosses Python -> JSON -> TS -> the section grouping, and pinning it in every
# layer is the multi-layer drift class this repo has paid for four times
# (docs/anti-drift-three-layer.md). With the order declared here and the
# frontend appending anything it does not recognize, adding a category is a
# backend-only change. `test_catalog_seed` asserts every category the shipped
# seed uses appears below, so a typo is still caught — just not by breaking a
# user's local catalog on upgrade.
CATEGORY_ORDER: tuple[str, ...] = (
    "Home & Devices",
    "Lists & Tasks",
    "Calendar & Mail",
    "Messaging",
    "Knowledge & Reading",
    "Media & Creativity",
    "Files & Photos",
    "Developer",
)


# How long a connection survives once made — the honest answer to "this must
# never expire", surfaced on the card BEFORE the user connects.
#   permanent        - the credential has no expiry mechanism at all
#   self_renewing    - it expires, but background refresh keeps it alive
#                      indefinitely (the common OAuth case)
#   provider_limited - the provider forces re-authorization on a schedule we
#                      cannot extend; `longevity_note` must say how often
Longevity = Literal["permanent", "self_renewing", "provider_limited"]


class PluginSpec(_BaseAuth):
    id: str
    display_name: str
    description: str
    category: str
    # Provenance. `source` is a plain string, not a Literal, for the same
    # multi-layer-drift reason as `category` above: the value crosses
    # Python -> JSON -> TS, and the frontend only ever compares it against
    # "community" to decide whether to render the not-reviewed badge. Every
    # existing seed entry and user override stays valid via the default.
    source: str = "seed"
    # GitHub login that published a community entry; None for shipped seeds.
    publisher: str | None = None
    # Manifest version string from the Agent Plugins package (community
    # entries); the seed catalog versions as a whole via `PluginCatalog`.
    version: str | None = None
    # Where the entry came from (registry page for community entries).
    source_url: str | None = None
    logo_slug: str
    logo_color: str | None = None
    # When set, the frontend uses this URL instead of the simpleicons CDN.
    # Use for brands whose original logo is multicolor (e.g. Slack's hash).
    logo_url: str | None = None
    featured: bool = False
    # Honest connection lifetime, rendered as a badge on the card. Defaulted so
    # every already-shipped entry stays valid; each catalog entry should still
    # state it explicitly rather than inherit the default.
    longevity: Longevity = "self_renewing"
    # One plain sentence shown next to the badge — mandatory in spirit for
    # `provider_limited` ("Google requires re-approval every 7 days while the
    # OAuth app is in Testing mode"), optional otherwise.
    longevity_note: str | None = None
    # Which `<family>_oauth_client_id` secret pair overrides this plugin's
    # catalog client, for providers where the downloader must register their
    # own OAuth app. Lives here so a new plugin stays a catalog-only change:
    # this replaced a hand-maintained map that was duplicated in the connect
    # helper AND the frontend, where a missing entry silently removed the
    # "use your own OAuth client" affordance.
    oauth_client_family: str | None = None
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
