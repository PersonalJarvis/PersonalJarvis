"""Convert an Agent Plugins v1.0.0 manifest set into a catalog `PluginSpec`.

The community registry distributes plugins as Agent Plugins v1.0.0 packages
(docs/marketplace/agent-plugins-standard.md): a `plugin.json` whose Jarvis
specifics live under ``extensions["io.github.personaljarvis"]``, plus an
optional `mcp.json`. This module is the "loader wave" that document defers:
it validates the manifests and produces the same `PluginSpec` the seed
catalog uses, so an installed community plugin flows through the exact same
runtime (connect flows, relevance gate, worker bridge) as a shipped one.

Security posture: the registry auto-merges submissions on green CI, so the
client must NOT blindly trust index content. Every rule CI enforces on
submission is re-enforced here — spec-conformant name, https-only endpoints,
launcher allowlist for stdio commands, pinned package versions, and no
credentials in `mcp.json` headers or env. A violation raises
:class:`AgentPluginError` with a message safe to surface in the UI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from jarvis.marketplace.catalog import PluginSpec

EXTENSION_NAMESPACE = "io.github.personaljarvis"

# Spec name constraints (agent-plugins.org 1.0.0): 1-64 chars, lowercase
# alphanumerics plus `-` and `.`, alphanumeric start and end, no `--`/`..`,
# underscores illegal.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")

# The only stdio launchers a community manifest may use. Anything else is an
# arbitrary command line on the user's machine.
_STDIO_LAUNCHERS = ("npx", "uvx", "docker")

# The launcher name alone decides nothing: every one of the three allowed
# launchers has flags that turn it back into "run whatever I say".
# `npx -p x@1.0.0 -c "curl … | sh"` runs a shell one-liner, `uvx --with evil`
# installs a second package nobody reviewed, and `docker run -v /:/host`
# hands the container the user's whole disk. So the ARGUMENTS are an
# allowlist too, per launcher, and the package a launcher fetches must be a
# pinned name from its own registry — never a git ref, a URL, or a path.
#
# The rule is positional: everything up to and including the package
# specification belongs to the LAUNCHER and is checked; everything after it
# is passed to the server the publisher wrote anyway, so checking it would
# buy nothing.
_NPX_FLAGS = frozenset({"-y", "--yes"})
_UVX_FLAGS = frozenset({"--from"})  # takes a value, itself pinned
_DOCKER_FLAGS = frozenset({"-i", "--interactive", "--rm"})
_DOCKER_VALUE_FLAGS = frozenset({"-e", "--env"})  # NAME only — see below

# npm: `pkg@1.2.3` or `@scope/pkg@1.2.3`. No `github:`, no `git+https://`,
# no `file:`, no tarball URL — those are not registry packages and carry no
# version anyone can pin.
_NPM_PIN_RE = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*@\d+\.\d+\.\d+[0-9A-Za-z.+-]*$"
)
# PyPI: `pkg==1.2.3` (uv's own spelling) or `pkg@1.2.3`.
_PYPI_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:==|@)\d+\.\d+\.\d+[0-9A-Za-z.+-]*$")
# A container image with an explicit version tag or a digest. `:latest` and a
# bare name are both "whatever the publisher pushes next".
_IMAGE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[0-9][0-9A-Za-z._-]*$")
_IMAGE_DIGEST_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
# Only a bare variable NAME may be handed to `docker -e`. `-e PATH=/evil`
# would rewrite the container's environment from the manifest; the values a
# plugin legitimately needs travel through `env_template`, which is checked.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Variables that decide what runs, before the plugin's own first line does.
# A manifest configures its own server; it does not get to re-point the
# dynamic linker, the interpreter, or the PATH the launcher is resolved on.
_RESERVED_ENV_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NPM_CONFIG_REGISTRY",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "DOCKER_HOST",
        "COMSPEC",
        "SYSTEMROOT",
        "WINDIR",
    }
)

# Token placeholders the runtime resolves at connect time. Everything else in
# an env value is treated as a smuggled literal credential. Braces must be
# MATCHED (both or neither), and the charset includes '-'/'.' because plugin
# ids may carry them ("$plugin_todo-fox_access_token").
_ENV_PLACEHOLDER_RE = re.compile(r"^\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})$")
_HEADER_PLACEHOLDER_RE = re.compile(r"\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})")
# A long unbroken token-ish run left over once placeholders are removed.
_TOKEN_LITERAL_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


class AgentPluginError(ValueError):
    """A manifest violates the Agent Plugins spec or the community rules."""


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentPluginError(f"{what} must be a non-empty string")
    return value.strip()


def validate_spec_name(name: Any) -> str:
    text = _require_str(name, "plugin name")
    if not _NAME_RE.fullmatch(text) or "--" in text or ".." in text:
        raise AgentPluginError(
            f"plugin name {text!r} violates the Agent Plugins name rules "
            "(1-64 chars, a-z 0-9 - . only, alphanumeric start/end, "
            "no '--' or '..', no underscores)"
        )
    return text


# Caps: the registry repo is also the CDN, and every skill lands as a file on
# the user's disk. Generous for instructions, small enough that one package
# cannot bloat the index for everyone.
MAX_BUNDLED_SKILLS = 10
MAX_SKILL_MD_BYTES = 64 * 1024

# Top-level frontmatter keys a community skill may not declare.
#
# `risk_policy` is a privilege boundary, not a preference: skills/runner.py
# evaluates a skill's tools against the SKILL'S OWN declared tier rather than
# the tool's static one ("the skill author's risk_policy is what governs
# here"). That is correct for repo-contributed skills, which pass human
# review — for an auto-merged community skill it would let the author
# downgrade a tool to `safe` and skip the confirmation the tool was given.
# Rejecting is deliberate over silently dropping the key: the author sees why
# (contract §7, no silent swallowing), and the built-in default applies.
_FORBIDDEN_SKILL_FRONTMATTER = ("risk_policy",)

# A top-level YAML key: no indentation, so nested `risk_policy:` inside some
# other author's block is not what we are matching.
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class BundledSkill:
    """One ``skills/<name>/SKILL.md`` carried by a plugin package."""

    name: str
    skill_md: str


def _frontmatter_block(skill_md: str) -> str:
    """The raw YAML frontmatter, or "" when the document has none."""
    if not skill_md.startswith("---"):
        return ""
    _, _, rest = skill_md.partition("---")
    block, sep, _ = rest.partition("\n---")
    return block if sep else ""


def validate_bundled_skills(raw: Any, *, plugin_name: str) -> list[BundledSkill]:
    """Validate the ``skills`` block of an index entry or a submission.

    Each item is ``{"name": ..., "skill_md": ...}``. The name becomes a
    DIRECTORY under the user's skills root, so it carries the same spec name
    rules the plugin name does — this is a path-traversal boundary, not
    cosmetics. The publish pre-check (``publish.py``) calls this exact
    function, so what the store accepts and what the installer accepts is one
    rule set.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AgentPluginError("skills must be a list")
    if len(raw) > MAX_BUNDLED_SKILLS:
        raise AgentPluginError(
            f"package bundles {len(raw)} skills — at most {MAX_BUNDLED_SKILLS} are accepted"
        )

    skills: list[BundledSkill] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise AgentPluginError("each bundled skill must be an object")
        name = validate_spec_name(item.get("name"))
        if name in seen:
            raise AgentPluginError(f"package bundles the skill {name!r} twice")
        seen.add(name)

        # Validate on the trimmed text, but KEEP the author's bytes: a
        # SKILL.md is a document, and silently eating its trailing newline
        # means the file we write is not the file that was published.
        # `lstrip` only, because the parser needs `---` at offset zero.
        _require_str(item.get("skill_md"), f"skill {name!r}: skill_md")
        skill_md = str(item["skill_md"]).lstrip()
        if len(skill_md.encode("utf-8")) > MAX_SKILL_MD_BYTES:
            raise AgentPluginError(
                f"skill {name!r}: SKILL.md exceeds {MAX_SKILL_MD_BYTES // 1024} KB"
            )

        frontmatter = _frontmatter_block(skill_md)
        if not frontmatter.strip():
            raise AgentPluginError(
                f"skill {name!r}: SKILL.md must open with a YAML frontmatter block"
            )
        declared = {match.group(1) for match in _TOP_LEVEL_KEY_RE.finditer(frontmatter)}
        for key in _FORBIDDEN_SKILL_FRONTMATTER:
            if key in declared:
                raise AgentPluginError(
                    f"skill {name!r}: {key!r} may not be declared by a "
                    "marketplace skill — it governs which tools run without "
                    "confirmation, so the built-in default applies instead"
                )
        for required in ("name", "description"):
            if required not in declared:
                raise AgentPluginError(f"skill {name!r}: frontmatter is missing {required!r}")

        skills.append(BundledSkill(name=name, skill_md=skill_md))

    # A skill directory shares the namespace with everything else the user
    # installed; colliding with the package's own name would make uninstall
    # ambiguous (which one does removing the plugin take with it?).
    if plugin_name in seen and len(seen) > 1:
        raise AgentPluginError(
            f"a bundled skill may only be named {plugin_name!r} when it is the package's only skill"
        )
    return skills


def _reject_http_urls(value: Any, where: str) -> None:
    """Recursively refuse plaintext-http endpoints anywhere in a block."""
    if isinstance(value, str):
        if value.lstrip().lower().startswith("http://"):
            raise AgentPluginError(f"{where} contains a non-https URL: {value!r}")
        return
    if isinstance(value, Mapping):
        for key, sub in value.items():
            _reject_http_urls(sub, f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, sub in enumerate(value):
            _reject_http_urls(sub, f"{where}[{index}]")


def _validate_auth_header_template(template: str) -> str:
    """The template must inject a token PLACEHOLDER, never a literal token."""
    if not _HEADER_PLACEHOLDER_RE.search(template):
        raise AgentPluginError("mcp_auth_header_template must reference a $plugin_… placeholder")
    remainder = _HEADER_PLACEHOLDER_RE.sub(" ", template)
    if _TOKEN_LITERAL_RE.search(remainder):
        raise AgentPluginError("mcp_auth_header_template may not embed literal credentials")
    return template


def _convert_http_server(
    name: str, server: Mapping[str, Any], extension: Mapping[str, Any]
) -> dict[str, Any]:
    url = _require_str(server.get("url"), f"mcp.json server {name!r}: url")
    if not url.lower().startswith("https://"):
        raise AgentPluginError(f"mcp.json server {name!r}: url must be https:// (got {url!r})")
    if server.get("headers"):
        # The spec forbids credentials in headers; community manifests get the
        # stricter rule (no headers at all) because a static header IS how a
        # token would be smuggled past review.
        raise AgentPluginError(
            f"mcp.json server {name!r}: headers are not allowed — token "
            "injection is a client concern (extension namespace)"
        )
    mcp_server: dict[str, Any] = {"transport": "http", "url": url}
    template = extension.get("mcp_auth_header_template")
    if template is not None:
        mcp_server["auth_header_template"] = _validate_auth_header_template(
            _require_str(template, "mcp_auth_header_template")
        )
    return mcp_server


def _validate_stdio_args(server: str, launcher: str, args: list[str]) -> None:
    """Check the launcher half of an stdio argv against its own allowlist.

    Raises :class:`AgentPluginError` naming the offending argument — the
    message reaches the install dialog, so it says what to change rather
    than that something was wrong.
    """
    fail = lambda reason: AgentPluginError(f"mcp.json server {server!r}: {reason}")  # noqa: E731

    if launcher == "npx":
        index = 0
        while index < len(args) and args[index].startswith("-"):
            if args[index] not in _NPX_FLAGS:
                raise fail(
                    f"npx option {args[index]!r} is not allowed — a community "
                    "package may only be launched as `npx -y <name>@<version>` "
                    "(options like -p/-c can run arbitrary commands)"
                )
            index += 1
        if index >= len(args):
            raise fail("npx needs a package to run, e.g. `npx -y my-mcp@1.2.0`")
        if not _NPM_PIN_RE.fullmatch(args[index]):
            raise fail(
                f"{args[index]!r} is not a pinned npm package — community stdio "
                "packages must name a registry package with an exact version "
                "(`my-mcp@1.2.0`), never a git ref, URL or path"
            )
        return

    if launcher == "uvx":
        index = 0
        while index < len(args) and args[index].startswith("-"):
            flag, sep, inline = args[index].partition("=")
            if flag not in _UVX_FLAGS:
                raise fail(
                    f"uvx option {flag!r} is not allowed — a community package "
                    "may only be launched as `uvx <name>==<version>` "
                    "(options like --with install code nobody reviewed)"
                )
            if sep:
                value, index = inline, index + 1
            else:
                if index + 1 >= len(args):
                    raise fail(f"uvx option {flag!r} is missing its value")
                value, index = args[index + 1], index + 2
            if not _PYPI_PIN_RE.fullmatch(value):
                raise fail(
                    f"{value!r} is not a pinned PyPI package — use "
                    "`--from my-mcp==1.2.0`, never a git ref, URL or path"
                )
            # `--from` names the package; what follows is the entry point.
            return
        if index >= len(args):
            raise fail("uvx needs a package to run, e.g. `uvx my-mcp==1.2.0`")
        if not _PYPI_PIN_RE.fullmatch(args[index]):
            raise fail(
                f"{args[index]!r} is not a pinned PyPI package — community stdio "
                "packages must name a package with an exact version "
                "(`my-mcp==1.2.0`), never a git ref, URL or path"
            )
        return

    # docker
    if not args or args[0] != "run":
        raise fail("the only allowed docker subcommand is `run`")
    index = 1
    while index < len(args) and args[index].startswith("-"):
        flag, sep, inline = args[index].partition("=")
        if flag in _DOCKER_VALUE_FLAGS:
            if sep:
                value, index = inline, index + 1
            else:
                if index + 1 >= len(args):
                    raise fail(f"docker option {flag!r} is missing its value")
                value, index = args[index + 1], index + 2
            if not _ENV_NAME_RE.fullmatch(value):
                raise fail(
                    f"docker {flag} {value!r} must name a variable and nothing "
                    "else — a plugin's own values belong in the env block, "
                    "which is checked for smuggled credentials"
                )
            continue
        if flag not in _DOCKER_FLAGS:
            raise fail(
                f"docker option {flag!r} is not allowed — a community container "
                "runs as `docker run -i --rm <image>:<version>`; options like "
                "-v/--mount/--privileged/--network would open the user's machine "
                "to it"
            )
        index += 1
    if index >= len(args):
        raise fail("docker needs an image to run, e.g. `docker run -i --rm my/mcp:1.2`")
    image = args[index]
    if not (_IMAGE_TAG_RE.fullmatch(image) or _IMAGE_DIGEST_RE.fullmatch(image)):
        raise fail(
            f"{image!r} is not a pinned image — name an explicit version tag "
            "(`my/mcp:1.2`) or a digest, never `latest` or a bare name"
        )


def _convert_stdio_server(name: str, server: Mapping[str, Any]) -> dict[str, Any]:
    command = _require_str(server.get("command"), f"mcp.json server {name!r}: command")
    launcher = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    launcher = launcher.removesuffix(".exe").removesuffix(".cmd")
    if launcher not in _STDIO_LAUNCHERS:
        raise AgentPluginError(
            f"mcp.json server {name!r}: launcher {command!r} is not allowed "
            f"(community stdio servers must use one of {', '.join(_STDIO_LAUNCHERS)})"
        )
    if server.get("url"):
        # One server entry, one transport. A `stdio` entry that also carries a
        # url reads as an https server to anything that checks the url first
        # and runs the command anyway — the two halves must never disagree
        # about what this entry is.
        raise AgentPluginError(
            f"mcp.json server {name!r}: a stdio server may not also declare a "
            "url — publish either a local command or a hosted endpoint"
        )
    raw_args = server.get("args", [])
    if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
        raise AgentPluginError(f"mcp.json server {name!r}: args must be strings")
    _validate_stdio_args(name, launcher, list(raw_args))
    env_template: dict[str, str] = {}
    raw_env = server.get("env", {})
    if not isinstance(raw_env, Mapping):
        raise AgentPluginError(f"mcp.json server {name!r}: env must be a mapping")
    for key, value in raw_env.items():
        name_text = str(key)
        if not _ENV_NAME_RE.fullmatch(name_text) or name_text.upper() in _RESERVED_ENV_NAMES:
            # A manifest sets variables for ITS server, never for the machinery
            # that starts it: PATH decides which binary `npx` even is, and the
            # loader hooks below run code before the server's first line.
            raise AgentPluginError(
                f"mcp.json server {name!r}: env {name_text!r} is not a plugin "
                "variable — PATH and the loader/runtime hooks are off limits"
            )
        if not isinstance(value, str) or not _ENV_PLACEHOLDER_RE.fullmatch(value):
            raise AgentPluginError(
                f"mcp.json server {name!r}: env {key!r} must be a "
                "$plugin_… placeholder, never a literal value"
            )
        env_template[name_text] = value
    return {
        "transport": "stdio",
        "install": [launcher, *raw_args],
        "env_template": env_template,
    }


def validate_mcp_server(
    name: str, server: Mapping[str, Any], extension: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate and convert ONE ``mcp.json`` server entry.

    Split out of :func:`_convert_mcp_json` so a caller that already knows it
    is looking at exactly one server (``publish.py``'s pre-check, which
    enforces "exactly one server" itself before this is reached) can reuse
    the transport/credential rules — https-only, no ``headers`` on a hosted
    server, ``$plugin_…``-only ``env`` values, the ``npx``/``uvx``/``docker``
    launcher allowlist with its pinned-package rule — without also
    reimplementing the "which server did the author mean" selection.

    ``extension`` is the plugin's ``extensions["io.github.personaljarvis"]``
    block, consulted only for a hosted server's optional
    ``mcp_auth_header_template``; omit it when validating an ``mcp.json`` in
    isolation, before the rest of the manifest exists.
    """
    if not isinstance(server, Mapping):
        raise AgentPluginError(f"mcp.json server {name!r} must be an object")
    server_type = server.get("type")
    if server_type == "streamable-http":
        return _convert_http_server(name, server, extension or {})
    if server_type == "stdio":
        return _convert_stdio_server(name, server)
    if server_type == "sse":
        raise AgentPluginError(
            "mcp.json: the deprecated 'sse' transport is not accepted for "
            "community plugins — publish a streamable-http endpoint"
        )
    raise AgentPluginError(
        f"mcp.json server {name!r}: must declare a 'type' — one of "
        f"'streamable-http', 'stdio', 'sse' (got {server_type!r})"
    )


def _convert_mcp_json(
    plugin_name: str, mcp_json: Mapping[str, Any], extension: Mapping[str, Any]
) -> dict[str, Any]:
    servers = mcp_json.get("mcpServers")
    if not isinstance(servers, Mapping) or not servers:
        raise AgentPluginError("mcp.json must declare a non-empty mcpServers map")
    if plugin_name in servers:
        server_name, server = plugin_name, servers[plugin_name]
    elif len(servers) == 1:
        server_name, server = next(iter(servers.items()))
    else:
        raise AgentPluginError(
            "mcp.json declares several servers and none is named after the "
            "plugin — community packages ship exactly one server"
        )
    return validate_mcp_server(str(server_name), server, extension)


def convert_manifest(
    plugin_json: Mapping[str, Any],
    mcp_json: Mapping[str, Any] | None = None,
    *,
    publisher: str | None = None,
    version: str | None = None,
    source_url: str | None = None,
) -> PluginSpec:
    """Validate an Agent Plugins v1.0.0 manifest set and build a `PluginSpec`.

    ``publisher``/``version``/``source_url`` come from the registry index
    entry, not from the manifest, so a manifest cannot claim someone else's
    identity.
    """
    if not isinstance(plugin_json, Mapping):
        raise AgentPluginError("plugin.json must be a JSON object")
    name = validate_spec_name(plugin_json.get("name"))
    description = _require_str(plugin_json.get("description"), "plugin description")

    extensions = plugin_json.get("extensions")
    extension = extensions.get(EXTENSION_NAMESPACE) if isinstance(extensions, Mapping) else None
    if not isinstance(extension, Mapping):
        raise AgentPluginError(
            f'plugin.json needs extensions["{EXTENSION_NAMESPACE}"] with the '
            "Jarvis auth block — see docs/marketplace/agent-plugins-standard.md"
        )
    if extension.get("native_tool"):
        # Native tools are Python code inside the app package. A manifest
        # cannot ship code, so a community entry claiming one would install
        # as a dead card at best and shadow a real tool at worst.
        raise AgentPluginError(
            "community plugins cannot bind native_tool — that tier is "
            "repo-contributed (see public-marketplace-analysis.md §2)"
        )
    auth = extension.get("auth")
    if not isinstance(auth, Mapping):
        raise AgentPluginError(
            f'extensions["{EXTENSION_NAMESPACE}"].auth is required (one of the '
            "five catalog auth modes)"
        )
    _reject_http_urls(auth, "auth")

    mcp_server = _convert_mcp_json(name, mcp_json, extension) if mcp_json is not None else None

    logo_url = extension.get("logo_url")
    if logo_url is not None:
        logo_url = _require_str(logo_url, "logo_url")
        _reject_http_urls(logo_url, "logo_url")

    try:
        return PluginSpec.model_validate(
            {
                "id": name,
                "display_name": extension.get("display_name") or name.replace("-", " ").title(),
                "description": description,
                "category": extension.get("category") or "Community",
                "logo_slug": extension.get("logo_slug") or name,
                "logo_color": extension.get("logo_color"),
                "logo_url": logo_url,
                # Community entries never self-promote into the featured row.
                "featured": False,
                "longevity": extension.get("longevity", "self_renewing"),
                "longevity_note": extension.get("longevity_note"),
                "oauth_client_family": extension.get("oauth_client_family"),
                "auth": dict(auth),
                "mcp_server": mcp_server,
                "post_install_hint_md": extension.get("post_install_hint_md"),
                "source": "community",
                "publisher": publisher,
                "version": version,
                "source_url": source_url,
            }
        )
    except ValidationError as exc:
        errors = exc.errors()
        location, message = "manifest", "invalid value"
        if errors:
            location = ".".join(str(part) for part in errors[0]["loc"]) or location
            message = str(errors[0]["msg"])
        raise AgentPluginError(f"manifest rejected at {location}: {message}") from exc


__all__ = [
    "MAX_BUNDLED_SKILLS",
    "MAX_SKILL_MD_BYTES",
    "AgentPluginError",
    "BundledSkill",
    "EXTENSION_NAMESPACE",
    "convert_manifest",
    "validate_bundled_skills",
    "validate_mcp_server",
    "validate_spec_name",
]
