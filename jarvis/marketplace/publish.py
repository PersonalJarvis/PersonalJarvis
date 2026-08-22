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

Two lanes share that one identity. A **package** (plugin or skill) travels
as JSON to ``/submit``; a **wallpaper** travels as multipart image bytes to
``/submit-wallpaper``, where the endpoint slugifies the title into a name and
commits straight to the public registry. Both end up in the same feed the
store reads back.

Validation here MIRRORS the endpoint's rules (``_lib/validate.ts``, itself a
mirror of the registry CI) for instant field-level feedback in the form. It
is deliberately never the authority: the endpoint and the registry CI re-run
everything, so drift here can only cost a duplicate error message, never let
a bad submission merge.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx

from jarvis.marketplace.agent_plugins_loader import (
    EXTENSION_NAMESPACE,
    MAX_SKILL_MD_BYTES,
    AgentPluginError,
)
from jarvis.marketplace.agent_plugins_loader import (
    validate_bundled_skills as _install_time_validate_bundled_skills,
)
from jarvis.marketplace.agent_plugins_loader import (
    validate_mcp_server as _install_time_validate_mcp_server,
)
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
# The wallpaper lane's own limits, mirrored from the endpoint's
# functions/_lib/wallpapers.ts. Same reasoning as the rules above: a mirror
# that drifts costs a duplicate error message, never a bad publish — the
# endpoint re-checks all of it and the registry CI re-checks it again.
MAX_WALLPAPER_BYTES = 8 * 1024 * 1024
MAX_TITLE_CHARS = 80
# Redistribution licenses only. A wallpaper published here is copied onto
# stranger's machines, so "all rights reserved" has no meaning in this lane.
WALLPAPER_LICENSES = ("CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0")
# Re-exported under this module's existing name, but the VALUE comes from
# agent_plugins_loader — the install-time authority — so the two numbers
# cannot silently drift apart.
MAX_SKILL_BYTES = MAX_SKILL_MD_BYTES
MAX_DESCRIPTION_CHARS = 500
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


def _is_valid_name(text: str) -> bool:
    """The Agent Plugins spec name rule, shared by the submission name and
    every bundled/standalone skill name (each becomes a directory)."""
    return bool(_NAME_RE.fullmatch(text)) and ".." not in text and "--" not in text


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
    name_is_valid = _is_valid_name(name)
    if not name_is_valid:
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
        if not skill_md:
            errors.append(FieldError("skill_md is required for a skill", "skill_md"))
        elif name_is_valid:
            # A standalone skill install runs through the exact same call
            # (community_install.install_community_skill), so this is not a
            # second opinion on the frontmatter shape/size/forbidden-key
            # rules — it is a preview of the one judgment that will actually
            # be made. Skipped when the submission name itself is invalid:
            # that error is already reported above, and using a bad name as
            # `plugin_name` here would only produce a confusing second one.
            try:
                _install_time_validate_bundled_skills(
                    [{"name": name, "skill_md": skill_md}], plugin_name=name
                )
            except AgentPluginError as exc:
                errors.append(FieldError(str(exc), "skill_md"))
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
                mcp_error = _validate_mcp(mcp_json, plugin_json=plugin_json)
                if mcp_error:
                    errors.append(FieldError(mcp_error, "mcp_json"))
        usage_card = draft.get("usage_card")
        value.update(
            plugin_json=plugin_json,
            mcp_json=mcp_json,
            usage_card=usage_card if isinstance(usage_card, str) else None,
        )
        # Skipped when the submission name itself is invalid, for the same
        # reason as the standalone-skill branch above: that error is already
        # reported, and an invalid `plugin_name` here would only add a
        # confusing second one.
        skills, skill_errors = (
            _validate_bundled_skills(draft.get("skills"), plugin_name=name)
            if name_is_valid
            else ([], [])
        )
        errors.extend(skill_errors)
        if skills:
            # Only present when non-empty, so a plain connector submission
            # keeps the exact body shape older endpoints already accept.
            value["skills"] = skills

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


def validate_wallpaper_draft(
    draft: Mapping[str, Any],
) -> tuple[dict[str, str] | None, list[FieldError]]:
    """Normalize and check a wallpaper submission's text fields.

    The image itself is checked separately (``prepare_wallpaper_image``) —
    bytes and metadata fail for different reasons and the form wants to say
    which. There is no ``name`` or ``version`` field here on purpose: the
    endpoint slugifies the title into a free name and stamps 1.0.0, so a
    client that invented either would only be describing something the server
    ignores.
    """
    errors: list[FieldError] = []
    title = str(draft.get("title") or "").strip()
    if not title:
        errors.append(FieldError("a title is required", "title"))
    elif _utf16_len(title) > MAX_TITLE_CHARS:
        errors.append(FieldError(f"title longer than {MAX_TITLE_CHARS} characters", "title"))
    elif not re.search(r"[a-z0-9]", title.lower()):
        # The endpoint slugifies the title and refuses an empty slug. Saying
        # so here, in the words the endpoint uses, beats a 422 after upload.
        errors.append(FieldError("the title needs at least a few letters or digits", "title"))

    description = str(draft.get("description") or "").strip()
    if _utf16_len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(
            FieldError(f"description longer than {MAX_DESCRIPTION_CHARS} characters", "description")
        )

    license_id = str(draft.get("license") or "").strip()
    if license_id not in WALLPAPER_LICENSES:
        errors.append(FieldError("pick one of the redistribution licenses", "license"))

    theme = str(draft.get("theme") or "").strip()
    if theme not in ("light", "dark", ""):
        errors.append(FieldError("theme must be 'light' or 'dark'", "theme"))

    if draft.get("rights") is not True:
        # Not decoration: this is the uploader's own statement, recorded in a
        # submission published under their name. Nobody inspects the picture
        # before it goes live, which is exactly why the statement is required.
        errors.append(
            FieldError(
                "confirm that you hold the rights and the image is legal to publish", "rights"
            )
        )

    if errors:
        return None, errors
    value = {"title": title, "license": license_id, "rights": "yes"}
    if description:
        value["description"] = description
    if theme:
        value["theme"] = theme
    return value, []


def prepare_wallpaper_image(data: bytes) -> tuple[bytes, str]:
    """Re-encode a picture into the bytes that will be published.

    Returns ``(webp_bytes, "wallpaper.webp")``. Nothing the caller supplied
    survives the round trip: Pillow decodes and re-encodes, so EXIF (GPS
    included), a forged header and anything appended past the image data are
    all gone. That is the same guarantee the website's uploader gives with a
    canvas, and the registry build re-encodes a third time — no byte an
    uploader crafted is ever served as-is.

    Raises ``SubmitError`` with a sentence meant for the person uploading.
    """
    if not data:
        raise SubmitError(422, "that file is empty", "file")
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
        raise SubmitError(503, "image support is unavailable on this install", "file") from exc

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            prepared = source.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decode failure means "not an image"
        raise SubmitError(422, "that file is not an image the app can read", "file") from exc

    # 4K cap, matching the picker's own store and the website's uploader.
    max_width = 3840
    if prepared.width > max_width:
        height = max(1, round(prepared.height * max_width / prepared.width))
        prepared = prepared.resize((max_width, height), Image.Resampling.LANCZOS)

    # Step down the quality until it fits rather than refusing a picture the
    # user cannot fix by hand — a 4K photograph can exceed 8 MB at q82, and
    # "export a smaller one" is not an instruction a wallpaper picker can act
    # on. The floor is deliberate: below q50 the result is not worth shipping.
    encoded = b""
    for quality in (82, 70, 60, 50):
        buffer = io.BytesIO()
        prepared.save(buffer, "WEBP", quality=quality, method=4)
        encoded = buffer.getvalue()
        if len(encoded) <= MAX_WALLPAPER_BYTES:
            break
    prepared.close()
    if len(encoded) > MAX_WALLPAPER_BYTES:
        raise SubmitError(
            413,
            f"even re-encoded, that image stays over "
            f"{MAX_WALLPAPER_BYTES // (1024 * 1024)} MB — publish a smaller one",
            "file",
        )
    return encoded, "wallpaper.webp"


def _validate_bundled_skills(
    raw: Any, *, plugin_name: str
) -> tuple[list[dict[str, str]], list[FieldError]]:
    """Validate a plugin submission's ``skills: [{name, skill_md}]`` block.

    Delegates the per-skill rules — frontmatter shape, the required ``name``/
    ``description`` keys, the forbidden ``risk_policy`` key, the size cap, and
    the "may only share the plugin's own name when it is the sole skill"
    rule — straight to ``agent_plugins_loader.validate_bundled_skills``. That
    is the exact function the install path calls (both for a plugin's bundled
    skills and, via ``community_install.install_community_skill``, for a
    standalone skill submission), so there is no second copy of the rule set
    left to drift out of sync.

    The trade-off against the rest of this module's "collect every error"
    form UX: the authority is fail-fast (one exception), so a bad bundle is
    reported by its first violation rather than all of them at once.
    """
    if raw is None:
        return [], []
    try:
        validated = _install_time_validate_bundled_skills(raw, plugin_name=plugin_name)
    except AgentPluginError as exc:
        return [], [FieldError(str(exc), "skills")]
    return [{"name": skill.name, "skill_md": skill.skill_md} for skill in validated], []


def _validate_mcp(mcp: dict[str, Any], *, plugin_json: Mapping[str, Any]) -> str | None:
    """Exactly one server, then delegate the transport/credential rules to
    ``agent_plugins_loader.validate_mcp_server`` — the exact function the
    install-time authority applies to a plugin's own ``mcp.json`` — so
    https-only, the ``npx``/``uvx``/``docker`` launcher allowlist, no
    ``headers`` on a hosted server, ``$plugin_…``-only ``env`` values and the
    ``sse``-transport ban are one rule set, not two hand-kept-in-sync copies.

    "Exactly one server" stays a LOCAL check, on top of the delegated one:
    the author-facing contract (docs/marketplace/package-layout.md) promises
    it, but the authority itself tolerates several servers when one is named
    after the plugin (a selection rule this pre-check has no use for). A
    stricter local check can only produce a duplicate error message, never
    let a submission through the authority would refuse
    (validator-parity.md, "the direction of a divergence decides whether it
    matters").

    A second local check stays for the same reason: the authority's own pin
    rule only forbids a literal ``@latest`` suffix, not the absence of any
    version marker at all, so a bare unversioned package name
    (``npx some-package``) would install today without ever being pinned.
    Catching that here is strictly stricter, never looser.
    """
    # Only "mcpServers" is a real key — a "servers" alias would validate here
    # and then vanish once CI (which reads only "mcpServers") looks at it,
    # producing a submission that describes a server nobody can find.
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict):
        return "mcp_json must contain an mcpServers object"
    if len(servers) != 1:
        return "mcp_json must define exactly one server"
    server_name, server = next(iter(servers.items()))

    extensions = plugin_json.get("extensions") if isinstance(plugin_json, Mapping) else None
    extension = extensions.get(EXTENSION_NAMESPACE) if isinstance(extensions, Mapping) else None
    try:
        converted = _install_time_validate_mcp_server(
            str(server_name), server, extension if isinstance(extension, Mapping) else None
        )
    except AgentPluginError as exc:
        return str(exc)

    if converted.get("transport") == "stdio":
        args = list(converted.get("install") or [])[1:]
        pinned = any(
            re.search(r"@\d+\.\d+\.\d+", a) or re.search(r":[\w.-]*\d[\w.-]*$", a) for a in args
        )
        if not pinned:
            return "stdio server must pin an exact version (e.g. @1.2.0 or image:1.2)"
    return None


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


def publish_wallpaper_endpoint() -> str:
    """The configured wallpaper endpoint (empty string = lane disabled)."""
    from jarvis.core.config import load_config

    try:
        return str(load_config().marketplace.publish_wallpaper_endpoint).strip()
    except Exception:  # noqa: BLE001 - config trouble must not kill the picker
        log.warning("publish: could not read config for the wallpaper endpoint")
        from jarvis.core.config import MarketplaceConfig

        return MarketplaceConfig().publish_wallpaper_endpoint


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


async def submit_wallpaper(
    fields: Mapping[str, str],
    image: bytes,
    filename: str = "wallpaper.webp",
    store: TokenStore | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST an already-validated wallpaper; returns ``{"name": ...}``.

    Multipart rather than JSON because the payload is image bytes, but the
    identity chain is the one ``submit`` uses: the token travels as
    ``Authorization: Bearer``, the endpoint proves it belongs to the
    marketplace App and derives the publisher from it. Nothing
    identity-shaped is in the form.
    """
    endpoint = publish_wallpaper_endpoint()
    if not endpoint:
        raise SubmitError(503, "wallpaper publishing is disabled in this deployment")
    store = store or TokenStore()
    tokens = await asyncio.to_thread(store.load, PUBLISHER_TOKEN_ID)
    if tokens is None:
        raise SubmitError(401, "sign in with GitHub first")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, transport=transport) as client:
            r = await client.post(
                endpoint,
                data=dict(fields),
                files={"file": (filename, image, "image/webp")},
                headers={**_UA, "Authorization": f"Bearer {tokens.access}"},
            )
    except httpx.HTTPError as exc:
        raise SubmitError(502, f"the publish endpoint is unreachable: {exc}") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 201:
        # The endpoint slugified the title into the name — the caller could
        # not have known it in advance, and it is what the live check needs.
        return {"name": str(body.get("name") or "")}
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
        entries: list[Any] = [*index.plugins, *index.skills, *index.wallpapers]
        for entry in entries:
            if entry.name == name and (entry.version or "") == version:
                live = True
                break
    return {"name": name, "version": version, "live": live, "feed_status": status}
