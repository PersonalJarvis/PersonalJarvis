"""In-app publishing: sign in with GitHub, validate, submit, watch it go live.

The desktop half of the marketplace's publish path
(docs/marketplace/github-signin-implementation.md §7): the app must never
hold the website's cookie or any client secret, so identity comes from the
GitHub **device flow** on the marketplace App (public client id), the token
lands in the keyring ``TokenStore``, and the submission is POSTed to the
storefront endpoint as ``Authorization: Bearer``. The endpoint proves the
token belongs to OUR App (confused-deputy check), derives the publisher from
it, and opens the registry PR as the bot — one publishing implementation for
web and app.

Validation here MIRRORS the endpoint's rules (``_lib/validate.ts``, itself a
mirror of the registry CI) for instant field-level feedback in the form. It
is deliberately never the authority: the endpoint and the registry CI re-run
everything, so drift here can only cost a duplicate error message, never let
a bad submission merge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from jarvis.marketplace.auth.oauth_device import DeviceFlowConfig, DeviceFlowHandler
from jarvis.marketplace.token_store import Tokens, TokenStore

log = logging.getLogger(__name__)

# TokenStore key for the publisher identity. Not a valid package name (those
# never carry this prefix in the seed catalog), so it cannot collide with a
# plugin's own connect tokens.
PUBLISHER_TOKEN_ID = "marketplace-publisher"  # noqa: S105 - keyring key name, not a secret

_GITHUB_DEVICE_URL = "https://github.com/login/device/code"
_GITHUB_VERIFY_URL = "https://github.com/login/device"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 - URL, not a secret
_GITHUB_USER_URL = "https://api.github.com/user"

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)
_UA = {"User-Agent": "Personal-Jarvis/1.0"}

# ---------------------------------------------------------------------------
# The endpoint's rule set, mirrored (keep in sync with functions/_lib/
# validate.ts in the storefront and scripts/validate.py in the registry).
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_FILE_BYTES = 128 * 1024
MAX_SKILL_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 500
_STDIO_LAUNCHERS = frozenset({"npx", "uvx", "docker"})
# Verbatim copy of validate.ts's SECRET_PATTERNS — a mirror that misses a
# family gives a green "Check" the endpoint then refuses, which reads like a
# store bug. Keep the two lists identical when either changes.
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub token family
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained PAT
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI-style, incl. - and _
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # Google API key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}"),  # JWT
)


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units — the unit the endpoint's ``.length``
    counts, so an emoji-heavy description cannot pass here and 422 there."""
    return len(text.encode("utf-16-le")) // 2


class FieldError(dict):
    """``{"field": str | None, "error": str}`` — shaped for the form."""

    def __init__(self, error: str, field: str | None = None) -> None:
        super().__init__(error=error, field=field)


def validate_draft(draft: dict[str, Any]) -> tuple[dict[str, Any] | None, list[FieldError]]:
    """Normalize and check a submission draft.

    Returns ``(normalized, [])`` on success or ``(None, errors)`` — all
    collected errors, not just the first, because a form wants every red
    field at once while an API wants any refusal at all.
    """
    errors: list[FieldError] = []
    kind = draft.get("kind")
    if kind not in ("plugin", "skill"):
        return None, [FieldError('kind must be "plugin" or "skill"', "kind")]

    name = str(draft.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name) or ".." in name or "--" in name:
        errors.append(
            FieldError(
                "name must be 1-64 chars of a-z 0-9 - . "
                '(no leading/trailing separators, no ".." or "--")',
                "name",
            )
        )
    version = str(draft.get("version") or "").strip()
    if not _SEMVER_RE.fullmatch(version):
        errors.append(FieldError("version must be plain SemVer, e.g. 1.0.0", "version"))

    value: dict[str, Any] = {"kind": kind, "name": name, "version": version}

    if kind == "skill":
        title = str(draft.get("title") or "").strip()
        description = str(draft.get("description") or "").strip()
        raw_skill_md = draft.get("skill_md")
        skill_md = raw_skill_md if isinstance(raw_skill_md, str) else ""
        if not title:
            errors.append(FieldError("title is required for a skill", "title"))
        if not description:
            errors.append(FieldError("description is required for a skill", "description"))
        elif _utf16_len(description) > MAX_DESCRIPTION_CHARS:
            errors.append(
                FieldError(f"description longer than {MAX_DESCRIPTION_CHARS} chars", "description")
            )
        if not skill_md.lstrip().startswith("---"):
            errors.append(FieldError("skill_md must start with YAML frontmatter (---)", "skill_md"))
        elif len(skill_md.encode("utf-8")) > MAX_SKILL_BYTES:
            errors.append(FieldError(f"skill_md larger than {MAX_SKILL_BYTES} bytes", "skill_md"))
        raw_categories = draft.get("categories")
        categories = (
            [c for c in raw_categories if isinstance(c, str)][:10]
            if isinstance(raw_categories, list)
            else []
        )
        value.update(title=title, description=description, categories=categories, skill_md=skill_md)
    else:
        plugin_json = draft.get("plugin_json")
        if not isinstance(plugin_json, dict):
            errors.append(FieldError("plugin_json object is required for a plugin", "plugin_json"))
            plugin_json = {}
        desc = plugin_json.get("description")
        if isinstance(desc, str) and _utf16_len(desc) > MAX_DESCRIPTION_CHARS:
            errors.append(
                FieldError(
                    f"plugin_json.description longer than {MAX_DESCRIPTION_CHARS} chars",
                    "plugin_json",
                )
            )
        mcp_json = draft.get("mcp_json")
        if mcp_json is not None:
            if not isinstance(mcp_json, dict):
                errors.append(FieldError("mcp_json must be an object", "mcp_json"))
                mcp_json = None
            else:
                mcp_error = _validate_mcp(mcp_json)
                if mcp_error:
                    errors.append(FieldError(mcp_error, "mcp_json"))
        usage_card = draft.get("usage_card")
        value.update(
            plugin_json=plugin_json,
            mcp_json=mcp_json,
            usage_card=usage_card if isinstance(usage_card, str) else None,
        )

    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
        errors.append(FieldError(f"submission larger than {MAX_FILE_BYTES} bytes", None))
    for pattern in _SECRET_PATTERNS:
        if pattern.search(serialized):
            errors.append(
                FieldError(
                    "submission appears to contain a credential — remove it "
                    "and use $plugin_… placeholders",
                    None,
                )
            )
            break
    if errors:
        return None, errors
    return value, []


def _validate_mcp(mcp: dict[str, Any]) -> str | None:
    """Exactly one server; hosted must be https, local a pinned allowlisted
    launcher — the two fields where data flows or code executes."""
    servers = mcp.get("mcpServers", mcp.get("servers"))
    if not isinstance(servers, dict):
        return "mcp_json must contain an mcpServers object"
    if len(servers) != 1:
        return "mcp_json must define exactly one server"
    server = next(iter(servers.values()))
    if not isinstance(server, dict):
        return "server entry must be an object"
    url = server.get("url")
    if isinstance(url, str):
        if not url.lower().startswith("https://"):
            return "hosted server URL must be https://"
        return None
    command = server.get("command")
    if isinstance(command, str):
        if command not in _STDIO_LAUNCHERS:
            return "stdio launcher must be one of: npx, uvx, docker"
        args = [a for a in server.get("args") or [] if isinstance(a, str)]
        pinned = any(
            re.search(r"@\d+\.\d+\.\d+", a) or re.search(r":[\w.-]*\d[\w.-]*$", a) for a in args
        )
        if not pinned:
            return "stdio server must pin an exact version (e.g. @1.2.0 or image:1.2)"
        return None
    return "server must have either an https url or a pinned stdio command"


# ---------------------------------------------------------------------------
# Identity: device flow + keyring
# ---------------------------------------------------------------------------


def _client_id() -> str:
    from jarvis.core.config import load_config

    try:
        return str(load_config().marketplace.publish_github_client_id).strip()
    except Exception:  # noqa: BLE001 - config trouble must not kill the view
        log.warning("publish: could not read config for the client id")
        from jarvis.core.config import MarketplaceConfig

        return MarketplaceConfig().publish_github_client_id


def publish_endpoint() -> str:
    """The configured submit endpoint (empty string = publishing disabled)."""
    from jarvis.core.config import load_config

    try:
        return str(load_config().marketplace.publish_endpoint).strip()
    except Exception:  # noqa: BLE001
        log.warning("publish: could not read config for the endpoint")
        from jarvis.core.config import MarketplaceConfig

        return MarketplaceConfig().publish_endpoint


def make_device_handler() -> DeviceFlowHandler:
    """A device-flow handler for the marketplace GitHub App.

    Identity only — the scope list is empty on purpose, so the consent
    screen shows no repository or account permissions.
    """
    return DeviceFlowHandler(
        DeviceFlowConfig(
            plugin_id=PUBLISHER_TOKEN_ID,
            device_url=_GITHUB_DEVICE_URL,
            verify_url=_GITHUB_VERIFY_URL,
            token_url=_GITHUB_TOKEN_URL,
            client_id=_client_id(),
            scopes=[],
        )
    )


async def fetch_identity(
    tokens: Tokens, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """The GitHub account behind the stored token, or ``None`` when invalid.

    ``transport`` is injectable so unit tests can stub the HTTP layer — the
    same pattern as ``marketplace_routes._make_validator``.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            r = await client.get(
                _GITHUB_USER_URL,
                headers={**_UA, "Authorization": f"Bearer {tokens.access}"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GitHub is unreachable: {exc}") from exc
    if r.status_code != 200:
        return None
    data = r.json()
    return {"login": data.get("login"), "avatar_url": data.get("avatar_url")}


async def current_identity(
    store: TokenStore | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """``{"signed_in": bool, "login": ...}`` — refreshing a stale token once.

    A dead refresh signs the user out honestly instead of caching a broken
    token that would 401 at submit time.
    """
    store = store or TokenStore()
    try:
        # to_thread: the keyring backend talks to the OS credential store
        # synchronously and must not block the event loop.
        tokens = await asyncio.to_thread(store.load, PUBLISHER_TOKEN_ID)
    except Exception:  # noqa: BLE001 - a locked keyring reads as signed out
        log.warning("publish: token load failed", exc_info=True)
        return {"signed_in": False}
    if tokens is None:
        return {"signed_in": False}
    identity = await fetch_identity(tokens, transport)
    if identity is not None:
        return {"signed_in": True, **identity}
    try:
        refreshed = await make_device_handler().refresh(tokens)
    except RuntimeError as exc:
        # Expected lifecycle (revoked at GitHub, refresh token aged out) —
        # log it so "I keep getting signed out" is diagnosable.
        log.info("publish: token refresh failed, signing out: %s", exc)
        await asyncio.to_thread(store.delete, PUBLISHER_TOKEN_ID)
        return {"signed_in": False}
    await asyncio.to_thread(store.save, PUBLISHER_TOKEN_ID, refreshed)
    identity = await fetch_identity(refreshed, transport)
    if identity is None:
        await asyncio.to_thread(store.delete, PUBLISHER_TOKEN_ID)
        return {"signed_in": False}
    return {"signed_in": True, **identity}


# ---------------------------------------------------------------------------
# Submit + live check
# ---------------------------------------------------------------------------


class SubmitError(RuntimeError):
    """Endpoint refusal, carrying the HTTP status and optional field."""

    def __init__(self, status: int, error: str, field: str | None = None) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.field = field


async def submit(
    normalized: dict[str, Any],
    store: TokenStore | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST an already-validated submission; returns ``{pr_url, submission_path}``.

    The token goes as ``Authorization: Bearer`` — the endpoint verifies it
    belongs to the marketplace App and derives the publisher from it, so
    nothing identity-shaped travels in the body.
    """
    endpoint = publish_endpoint()
    if not endpoint:
        raise SubmitError(503, "publishing is disabled in this deployment")
    store = store or TokenStore()
    tokens = await asyncio.to_thread(store.load, PUBLISHER_TOKEN_ID)
    if tokens is None:
        raise SubmitError(401, "sign in with GitHub first")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            r = await client.post(
                endpoint,
                json=normalized,
                headers={**_UA, "Authorization": f"Bearer {tokens.access}"},
            )
    except httpx.HTTPError as exc:
        raise SubmitError(502, f"the publish endpoint is unreachable: {exc}") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 201:
        return {
            "pr_url": body.get("prUrl"),
            "submission_path": body.get("submissionPath"),
        }
    error = str(body.get("error") or f"publish failed (HTTP {r.status_code})")
    field = body.get("field")
    raise SubmitError(r.status_code, error, field if isinstance(field, str) else None)


async def live_status(name: str, version: str, *, force: bool = False) -> dict[str, Any]:
    """Whether ``name`` at ``version`` is in the community index yet.

    This is the honest "it is live" signal: the same feed every store client
    reads. ``force`` bypasses the TTL for the watch-until-live poller.
    """
    from jarvis.marketplace import community_source

    index, status = await community_source.get_index(force=force)
    live = False
    if index is not None:
        for entry in list(index.plugins) + list(index.skills):
            if entry.name == name and (entry.version or "") == version:
                live = True
                break
    return {"name": name, "version": version, "live": live, "feed_status": status}
