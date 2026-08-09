"""Persistent, subscription-only transport for ``codex app-server``.

This module owns the local JSONL protocol, process lifecycle, and the dedicated
Codex identity used by subscription voice. The user signs in directly inside a
Jarvis-owned ``CODEX_HOME``; OAuth tokens are never copied, hard-linked, or
borrowed from an ordinary Codex/IDE profile. Credentials are forced into that
profile's file store, and live ``account/read`` is the authoritative account and
plan check.
API-key environment variables are removed so a subscription request cannot
silently become usage-billed API traffic.

The process is lazy and shared by callers.  A dead process fails every pending
request and thread subscription, then the next request starts a fresh process.
Requests wait on independent futures, while the write lock covers only one
JSONL frame and its drain.  This keeps high-frequency realtime audio appends
from serialising behind unrelated response waits.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

if TYPE_CHECKING:
    pass

from jarvis import __version__
from jarvis.core.process_tree import ProcessTree, make_process_tree
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT_S: Final = 20.0
# How long a passed cold-start audit may be ridden by a SECOND thread_start on
# the same process (the STUN media-path retry). Short on purpose: warm starts
# minutes later must re-audit in full. ``prime_startup_audit`` mints a wake
# ride under the same TTL — always from an audit that actually completed.
_STARTUP_AUDIT_TTL_S: Final = 10.0
# How long a per-host STUN media-path memory stays valid. Long enough to span
# a working session on one network, short enough that a laptop moving networks
# gets a fresh host-candidate attempt within the hour.
_STUN_MEDIA_PATH_TTL_S: Final = 3600.0
_SHUTDOWN_TIMEOUT_S: Final = 2.0
_DEFAULT_SDP_TIMEOUT_S: Final = 15.0
_DEFAULT_NOTIFICATION_QUEUE_SIZE: Final = 512
CodexAppServerPurpose = Literal["realtime", "text"]
_MAX_REALTIME_START_PROMPT_BYTES: Final = 64_000
_MAX_REALTIME_INITIAL_ITEMS: Final = 128
_MAX_REALTIME_INITIAL_TEXT_BYTES: Final = 32_768
_REALTIME_INITIAL_ROLES: Final = frozenset({"user", "developer", "assistant"})
_SUPPORTED_CODEX_VERSION: Final = "codex-cli 0.147.0"
_LEGACY_CODEX_VERSION: Final = "codex-cli 0.146.0"
_SUPPORTED_CODEX_VERSIONS: Final = (
    _SUPPORTED_CODEX_VERSION,
    _LEGACY_CODEX_VERSION,
)
_CHILD_LIFELINE_SCRIPT: Final = str(
    Path(__file__).parent / "core" / "child_lifeline.py"
)

# SHA-256 of the native executables in OpenAI's six official npm artifacts for
# @openai/codex 0.147.0. The app-server protocol used below is experimental;
# an unknown build is refused instead of assuming its safety semantics match.
_TRUSTED_CODEX_TARGETS: Final = {
    ("darwin", "arm64"): (
        "darwin-arm64",
        "aarch64-apple-darwin",
        "codex",
        "19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37",
    ),
    ("darwin", "x86_64"): (
        "darwin-x64",
        "x86_64-apple-darwin",
        "codex",
        "8080a42da4cef9c4216dace512f29acfe2e526aeeec2a0ce450e5a2b18b84d8a",
    ),
    ("linux", "arm64"): (
        "linux-arm64",
        "aarch64-unknown-linux-musl",
        "codex",
        "e23d0be344d2496986c985cd3db61e6f649b1ddd900e6afc1b5aaabbffcbb4e2",
    ),
    ("linux", "x86_64"): (
        "linux-x64",
        "x86_64-unknown-linux-musl",
        "codex",
        "cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40",
    ),
    ("win32", "arm64"): (
        "win32-arm64",
        "aarch64-pc-windows-msvc",
        "codex.exe",
        "1f0e8c2dd3c6b471e985fac76908366c1cf31155094fde606fb2d3052cf00584",
    ),
    ("win32", "x86_64"): (
        "win32-x64",
        "x86_64-pc-windows-msvc",
        "codex.exe",
        "935a1911ed2556e4ffcec995f4886ac2ac425863ba26fed264df62e30272ad9d",
    ),
}
# Keep the last audited protocol-compatible release available during the pin
# transition. This is a compatibility window, not a semver trust rule: each
# executable is still accepted only by its exact official artifact digest.
_LEGACY_TRUSTED_CODEX_TARGETS: Final = {
    ("darwin", "arm64"): (
        "darwin-arm64",
        "aarch64-apple-darwin",
        "codex",
        "ae1d3ffe6d48aec6a4dc3f50e7eb8e0d11962485a6a9406c5a7012139383da02",
    ),
    ("darwin", "x86_64"): (
        "darwin-x64",
        "x86_64-apple-darwin",
        "codex",
        "544e2df9e6f09b3f1ceb0405879c83dd099ec015aeed942bb091ff0f29f60dc2",
    ),
    ("linux", "arm64"): (
        "linux-arm64",
        "aarch64-unknown-linux-musl",
        "codex",
        "cb5e8cb8a333a408ce6adbe0d4fad1845c69772c2216af7c1f88c98a11460dc6",
    ),
    ("linux", "x86_64"): (
        "linux-x64",
        "x86_64-unknown-linux-musl",
        "codex",
        "2e863156ed35ecc5253b1e2f907a9143077b9f7cb51942070c61996471ff6e04",
    ),
    ("win32", "arm64"): (
        "win32-arm64",
        "aarch64-pc-windows-msvc",
        "codex.exe",
        "d52efa1d816b305c84c525335f451aafc56398a7e8515b6c6db095c4e4fb0d1d",
    ),
    ("win32", "x86_64"): (
        "win32-x64",
        "x86_64-pc-windows-msvc",
        "codex.exe",
        "bc343ba420dc2e2e9f59e6fc5e5bf0aae1cd8c771fc319665241fc9c0271fddb",
    ),
}
_SUBSCRIPTION_HOME_DIRNAME: Final = "codex-subscription-voice"
_SUBSCRIPTION_LOCK_DIRNAME: Final = ".codex-subscription-voice-lock"
_SUBSCRIPTION_LOCK_FILENAME: Final = "owner.lock"
_SUBSCRIPTION_PROFILE_MARKER: Final = ".jarvis-realtime-transport.json"
_SUBSCRIPTION_PROFILE_SCHEMA: Final = 1
_ALLOWED_SUBSCRIPTION_HOME_ENTRIES: Final = frozenset(
    {
        "auth.json",
        "installation_id",
        # Codex 0.146 writes this tiny one-time migration marker into its
        # CODEX_HOME on a normal run. Without it in the allowlist the
        # fail-closed profile check bricked every install right after the
        # CLI's first use of the profile.
        ".sandbox_migration",
        "tmp",
        _SUBSCRIPTION_PROFILE_MARKER,
    }
)
# Desktop-shell droppings, not Codex state: Finder writes ``.DS_Store`` the
# moment anyone LOOKS at the folder, Explorer writes ``Thumbs.db`` /
# ``desktop.ini``, and copying the profile across a network share leaves
# AppleDouble ``._`` siblings. These are ignored rather than allowlisted —
# they carry nothing to validate, and treating one as "the profile contains
# configuration" used to delete a working ChatGPT login on the next Connect.
_OS_METADATA_ENTRY_NAMES: Final = frozenset(
    {
        ".ds_store",
        ".localized",
        ".spotlight-v100",
        ".trashes",
        ".fseventsd",
        "desktop.ini",
        "thumbs.db",
        "ehthumbs.db",
        "$recycle.bin",
        "system volume information",
    }
)


def _is_os_metadata_entry(name: str) -> bool:
    lowered = name.lower()
    return lowered in _OS_METADATA_ENTRY_NAMES or lowered.startswith("._")


_ARG0_RUNTIME_DIR_RE: Final = re.compile(r"^codex-arg0[A-Za-z0-9]{6}$")
_MAX_ARG0_RUNTIME_DIRS: Final = 32
_PERSONAL_CHATGPT_PLANS: Final = frozenset(
    {"free", "go", "plus", "pro", "prolite"}
)

_SUBSCRIPTION_ENV_ALLOWLIST: Final = frozenset(
    {
        # Executable/runtime discovery.
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "SYSTEMROOT",
        "WINDIR",
        # Temporary storage used by the CLI/runtime.
        "TEMP",
        "TMP",
        "TMPDIR",
        # Portable account/home selection. No provider or cloud variables are
        # inherited merely because they happen to exist in Jarvis's process.
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "CODEX_HOME",
        # Locale and terminal shape. These say how text is ENCODED and drawn,
        # never who anyone is: without them the child falls back to the C
        # locale and mangles every non-ASCII byte it handles. The interactive
        # login child already inherits exactly these
        # (``jarvis/codex_auth.py::_ISOLATED_CODEX_ENV_ALLOWLIST``); the
        # transport dropping them was an undocumented divergence.
        "LANG",
        "TERM",
        "NO_COLOR",
        # TLS trust roots. Not credentials — they name WHICH certificate
        # authorities to believe, and on hosts that carry their roots in the
        # environment (corporate images, Nix, conda, many container bases)
        # dropping them left the child with no trust store at all: `codex
        # login status` reads a local file and succeeds, so the card said
        # "connected" while every call died in the TLS handshake.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
)
# ``LC_*`` is an open family (LC_ALL, LC_CTYPE, LC_MESSAGES, ...), so it is
# matched by prefix. Same reasoning as LANG above: encoding, not identity.
_SUBSCRIPTION_ENV_ALLOWED_PREFIXES: Final = ("LC_",)


def _subscription_env_allowed(name: str) -> bool:
    """Whether one inherited environment variable may reach the child."""
    upper = name.upper()
    if upper in _OPENAI_BILLING_ENV_NAMES:
        # Belt and braces: the allowlist already excludes every billing token,
        # but a future entry added there must not silently re-open the
        # usage-billed path this transport exists to avoid.
        return False
    return upper in _SUBSCRIPTION_ENV_ALLOWLIST or upper.startswith(
        _SUBSCRIPTION_ENV_ALLOWED_PREFIXES
    )

_DISABLED_APP_SERVER_FEATURES: Final = (
    "shell_tool",
    "apps",
    "browser_use",
    "computer_use",
    "hooks",
    "image_generation",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "network_proxy",
    "remote_plugin",
    "remote_control",
    "respect_system_proxy",
    "skill_search",
    "workspace_dependencies",
    "web_search_request",
    "external_agent_memory_import",
    "chronicle",
)

# Codex 0.147 keeps the effective/session-layer value for ``multi_agent_v2``
# as the scalar supplied by ``--disable``, but config/read attributes that one
# value to the structured feature field ``features.multi_agent_v2.enabled``.
# Canonicalize only this audited release/path pair. The exact origin set,
# sessionFlags source, version, effective value, and allowed session surface
# are still checked below, so this does not admit an additional config field.
_CODEX_CONFIG_ORIGIN_PATH_ALIASES: Final = {
    _SUPPORTED_CODEX_VERSION: {
        "features.multi_agent_v2.enabled": "features.multi_agent_v2",
    },
}

_TRANSPORT_BASE_INSTRUCTIONS: Final = (
    "This ephemeral thread exists only to carry a realtime voice transport. "
    "Never use tools, inspect files, access the network, or perform actions. "
    "Never answer a realtime handoff as a Codex agent; the client-owned Jarvis "
    "supervisor handles every handoff."
)
_TRANSPORT_DEVELOPER_INSTRUCTIONS: Final = (
    "Transport-only boundary: do not call tools, shell commands, applications, "
    "plugins, skills, web search, MCP servers, or other agents. Do not read or "
    "write the filesystem. Yield all realtime handoffs to the client."
)
_TEXT_BASE_INSTRUCTIONS: Final = (
    "This ephemeral thread serves a conversational voice assistant. Answer the "
    "user directly in plain text. Never use tools, inspect files, access the "
    "workspace, run commands, browse, or perform actions."
)
_TEXT_DEVELOPER_INSTRUCTIONS: Final = (
    "Conversational text-only boundary: return only the assistant reply. Do not "
    "call tools, shell commands, applications, plugins, skills, web search, MCP "
    "servers, or other agents. Do not read or write the filesystem."
)
_TEXT_MODEL_PROVIDER: Final = "openai"

_OFFICIAL_OPENAI_API_BASE: Final = "https://api.openai.com/v1"
_OFFICIAL_OPENAI_REALTIME_BASE: Final = "https://api.openai.com/v1"
_OFFICIAL_CHATGPT_BASE: Final = "https://chatgpt.com/backend-api/"
_OFFICIAL_CHATGPT_CODEX_BASE: Final = "https://chatgpt.com/backend-api/codex"
_MISSING = object()

# A ChatGPT-authenticated Codex process must not inherit any OpenAI key that
# could make the CLI select a usage-billed path.  Include Jarvis's per-feature
# aliases as well as historical Codex aliases seen in existing installations.
_OPENAI_BILLING_ENV_NAMES: Final = frozenset(
    {
        "CODEX_API_KEY",
        "CODEX_OPENAI_API_KEY",
        "JARVIS_AGENT_OPENAI_API_KEY",
        "JARVIS_REALTIME_OPENAI_API_KEY",
        "LOCAL_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    }
)

_DECLINE_REQUEST_METHODS: Final = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)
_DYNAMIC_TOOL_REQUEST_METHODS: Final = frozenset({"item/tool/call"})

# Per-host memory of the WebRTC media path the LAST successful connect needed.
# In-process only and TTL-bounded on purpose: network topology is a property
# of this machine on this network right now, so persisting it would replay a
# stale environment. A host recorded here needed a server-reflexive (STUN)
# candidate, which means a host-candidates-only first attempt is known to be
# doomed and would cost a full second open (thread_start bundle included).
# Guarded by a lock because callers may record from executor threads.
_stun_media_path_lock = threading.Lock()
_stun_media_path_memory: dict[str, float] = {}


def _stun_media_path_key(host: str) -> str:
    return str(host or "").strip().lower()


def record_media_path_outcome(host: str, *, needed_stun: bool) -> None:
    """Remember whether the last successful connect to ``host`` needed STUN.

    Call this only after a connect actually succeeded: a successful
    host-candidate path clears the memory, a successful STUN path arms it so
    the next open for the same host starts with STUN directly instead of
    paying a doomed host-only attempt first.
    """
    key = _stun_media_path_key(host)
    if not key:
        return
    with _stun_media_path_lock:
        if needed_stun:
            _stun_media_path_memory[key] = time.monotonic()
        else:
            _stun_media_path_memory.pop(key, None)


def media_path_prefers_stun(host: str) -> bool:
    """Whether the last successful connect to ``host`` needed a STUN candidate.

    Returns ``False`` for unknown hosts and for stale entries — the memory is
    an optimization, never a gate, so forgetting only restores today's
    host-candidates-first behavior.
    """
    key = _stun_media_path_key(host)
    if not key:
        return False
    with _stun_media_path_lock:
        recorded_at = _stun_media_path_memory.get(key)
        if recorded_at is None:
            return False
        if time.monotonic() - recorded_at > _STUN_MEDIA_PATH_TTL_S:
            del _stun_media_path_memory[key]
            return False
        return True


class CodexAppServerError(RuntimeError):
    """Base error for the local app-server transport."""


class CodexSubscriptionUnavailable(CodexAppServerError):
    """The Codex CLI cannot currently use a ChatGPT subscription login."""


class CodexSubscriptionContainmentUnavailable(CodexSubscriptionUnavailable):
    """The host cannot safely bind the experimental child to Jarvis lifetime."""


class CodexSubscriptionProfileMissing(CodexSubscriptionUnavailable):
    """The dedicated subscription-voice profile does not exist yet."""


class CodexSubscriptionBinaryUnsupported(CodexSubscriptionUnavailable):
    """No installed Codex matches the pinned, hash-approved release.

    Maps to ``reason_code="not_installed"``: the actionable truth for a
    wrong-version or unknown build is "install the supported release", with
    the pinned install command — not "your profile needs attention".
    """


class CodexSubscriptionPlanUnsupported(CodexSubscriptionUnavailable):
    """The live account check refused this ChatGPT plan permanently.

    A transient startup failure must NOT carry this class: activation stores
    it as the sticky ``plan_unsupported`` diagnosis that outlives the toast.
    """


class CodexSubscriptionRuntimeStateInvalid(CodexSubscriptionUnavailable):
    """Codex's own ephemeral scratch under ``tmp/`` is not a shape we accept.

    Separate from a bad PROFILE because the recovery differs: this state is
    regenerated by the CLI on every run and contains no credential, so the
    honest repair is to clear ``tmp/`` — never to delete the profile, which
    would take a working ChatGPT login with it over a leftover scratch file.
    """


class CodexSubscriptionInspectionFailed(CodexSubscriptionUnavailable):
    """An OS read error prevented judging the profile — transiently unknown.

    Distinct from a genuinely invalid profile: an antivirus-locked directory
    or a slow disk must surface as ``busy`` ("checking"), never as "create a
    fresh voice-only login".
    """


class CodexAppServerDisconnected(CodexAppServerError):
    """The app-server process exited or its JSONL stream broke."""


class CodexAppServerTimeout(CodexAppServerError):
    """A bounded app-server request or notification wait expired."""


class CodexNotificationOverflow(CodexAppServerError):
    """A subscriber stopped consuming realtime notifications fast enough."""


#: A machine token is safe to forward; free text is not. Anything that is not
#: a short, lowercase, punctuation-free identifier stays redacted.
_RPC_ERROR_TOKEN_RE: Final = re.compile(r"^[a-z0-9_]{1,64}$")
_RPC_ERROR_STATUS_KEYS: Final = (
    "httpStatus",
    "http_status",
    "statusCode",
    "status_code",
    "status",
)
_RPC_ERROR_TYPE_KEYS: Final = ("type", "errorType", "error_type", "reason")


def _rpc_error_detail(error: Any) -> tuple[int | None, str | None]:
    """Extract ONLY an HTTP status and a bounded error-type token.

    Everything else in an app-server error — the message, the account label,
    the upstream request detail — stays redacted, which is why this transport
    used to surface a plan/quota refusal and a broken transport as the same
    opaque "rejected (code -32000)". Those two need opposite reactions from
    the user, so the two machine-readable fields that tell them apart are
    forwarded and nothing more.
    """
    if not isinstance(error, Mapping):
        return None, None
    scopes: list[Mapping[str, Any]] = [error]
    for key in ("data", "error"):
        nested = error.get(key)
        if isinstance(nested, Mapping):
            scopes.append(nested)
            deeper = nested.get("error")
            if isinstance(deeper, Mapping):
                scopes.append(deeper)
    status: int | None = None
    token: str | None = None
    for scope in scopes:
        if status is None:
            for key in _RPC_ERROR_STATUS_KEYS:
                candidate = scope.get(key)
                if isinstance(candidate, bool):
                    continue
                if isinstance(candidate, int) and 100 <= candidate <= 599:
                    status = candidate
                    break
        if token is None:
            for key in _RPC_ERROR_TYPE_KEYS:
                candidate = scope.get(key)
                if not isinstance(candidate, str):
                    continue
                normalized = candidate.strip().lower()
                if _RPC_ERROR_TOKEN_RE.fullmatch(normalized):
                    token = normalized
                    break
    return status, token


class CodexAppServerRPCError(CodexAppServerError):
    """A redacted JSON-RPC error returned by app-server."""

    def __init__(
        self,
        method: str,
        code: int | None,
        *,
        http_status: int | None = None,
        error_type: str | None = None,
    ) -> None:
        self.method = method
        self.code = code
        self.http_status = http_status
        self.error_type = error_type
        details = []
        if code is not None:
            details.append(f"code {code}")
        if http_status is not None:
            # Spelled as a bare status so the shared classifier
            # (jarvis/brain/provider_test.py::classify_provider_error) reads
            # 402/429 as an account state instead of "possible integration bug".
            details.append(f"http {http_status}")
        if error_type is not None:
            details.append(f"type {error_type}")
        suffix = f" ({'; '.join(details)})" if details else ""
        super().__init__(f"Codex app-server rejected {method}{suffix}.")


# SSOT for the subscription-status reason-code vocabulary. Mirrors (five-layer
# anti-drift guard, BUG-008 class): the runtime membership guard in
# ``jarvis/ui/web/provider_routes.py::_codex_subscription_status_payload``
# (unknown values degrade to busy with a warning), the TS union in
# ``useProviders.ts``, the exhaustive ``CODEX_STATUS_KEY_BY_REASON`` record in
# ``ProviderTierSection.tsx`` (a new member fails the TS build until mapped;
# the i18n parity test derives its key list from it), and one
# ``apikeys_codex.status_*`` i18n key per member in de/en/es. The Python side
# is pinned by ``test_reason_code_vocabulary_is_pinned``.
# "busy" is transient: the profile is briefly owned by another probe, a
# login/logout, or a starting voice transport. Not a setup defect; callers
# keep their last known state instead of asking the user to reconnect.
CodexSubscriptionReasonCode = Literal[
    "ready",
    "login_required",
    # An interactive login is running right now: the card must invite the
    # user to FINISH it in the browser, never to start a second one.
    "login_in_progress",
    "lifecycle_unavailable",
    "not_installed",
    "setup_invalid",
    # The connected ChatGPT account can never activate this provider (for
    # example a business/enterprise plan) — sticky until login/logout.
    "plan_unsupported",
    "busy",
]
CODEX_SUBSCRIPTION_REASON_CODES: Final = frozenset(
    get_args(CodexSubscriptionReasonCode)
)


@dataclass(frozen=True, slots=True)
class CodexAppServerCapability:
    """PII-free snapshot of whether the subscription transport may start."""

    available: bool
    chatgpt_authenticated: bool
    binary_path: str | None
    version: str | None
    reason: str
    reason_code: CodexSubscriptionReasonCode = "setup_invalid"


@dataclass(frozen=True, slots=True)
class CodexAppServerNotification:
    """One thread-scoped app-server notification."""

    method: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CodexRealtimeStartResult:
    """Realtime start acknowledgement plus an optional WebRTC answer SDP."""

    response: dict[str, Any]
    answer_sdp: str | None


_SUBSCRIPTION_END = object()
_SubscriptionItem = CodexAppServerNotification | BaseException | object


@dataclass(frozen=True, slots=True)
class _SafeTransportWorkspace:
    root: Path
    instructions: Path
    compact_prompt: Path
    model_catalog: Path
    sqlite_home: Path
    log_dir: Path
    child_home: Path
    child_appdata: Path
    child_local_appdata: Path
    child_tmp: Path


def codex_subscription_home() -> Path:
    """Jarvis-owned Codex identity used only by realtime subscription voice."""
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / _SUBSCRIPTION_HOME_DIRNAME


def _subscription_process_lock_path() -> Path:
    """Return an owner-only lock path outside the allowlisted CODEX_HOME."""
    from jarvis.core.paths import user_data_dir
    from jarvis.core.private_directory import ensure_owner_only_directory

    root = user_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        canonical_root = root.resolve(strict=True)
        lock_directory = canonical_root / _SUBSCRIPTION_LOCK_DIRNAME
        ensure_owner_only_directory(lock_directory, create=True)
        canonical_lock_directory = lock_directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CodexSubscriptionUnavailable(
            "The subscription-voice process lock could not be secured."
        ) from exc
    if canonical_lock_directory.parent != canonical_root:
        raise CodexSubscriptionUnavailable(
            "The subscription-voice process lock could not be secured."
        )
    return canonical_lock_directory / _SUBSCRIPTION_LOCK_FILENAME


def _acquire_subscription_process_lock():
    """Acquire the cross-process profile owner or fail without path disclosure."""
    from jarvis.core.exclusive_process_lock import (
        ExclusiveProcessLock,
        ExclusiveProcessLockError,
    )

    try:
        return ExclusiveProcessLock.acquire(
            _subscription_process_lock_path(),
            protected_directory=codex_subscription_home(),
        )
    except ExclusiveProcessLockError as exc:
        if exc.reason == "busy":
            message = "Another Jarvis process is using subscription voice."
        else:
            message = "The subscription-voice process lock could not be secured."
        raise CodexSubscriptionUnavailable(message) from exc


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _validate_current_owner(metadata: os.stat_result) -> None:
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid) and metadata.st_uid != geteuid():
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile belongs to another user."
        )


def _validate_regular_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        # An unreadable entry (antivirus hold, slow disk) is transiently
        # unknown, never proof of a broken profile.
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile could not be inspected."
        ) from exc
    if _is_link_or_reparse(path) or not stat.S_ISREG(metadata.st_mode):
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an unsafe filesystem entry."
        )
    if getattr(metadata, "st_nlink", 1) != 1:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains a linked credential file."
        )
    _validate_current_owner(metadata)
    if sys.platform == "win32":
        from jarvis.core.exclusive_process_lock import (  # noqa: PLC0415
            ExclusiveProcessLockError,
            _validate_windows_file_security,
        )

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            current = path.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if (
                stat.S_ISLNK(current.st_mode)
                or bool(getattr(current, "st_file_attributes", 0) & reparse_flag)
                or not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise CodexSubscriptionUnavailable(
                    "The dedicated Codex voice profile contains an unsafe filesystem entry."
                )
            _validate_windows_file_security(descriptor)
        except CodexSubscriptionUnavailable:
            raise
        except (ExclusiveProcessLockError, OSError, ValueError) as exc:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile contains a file that is not owner-only."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _validate_private_directory(
    path: Path,
    *,
    exact_posix_mode: int | None = None,
    require_owner_only: bool = False,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        # Transiently unreadable, not proven-broken (see the file variant).
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile could not be inspected."
        ) from exc
    if _is_link_or_reparse(path) or not stat.S_ISDIR(metadata.st_mode):
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an unsafe directory."
        )
    _validate_current_owner(metadata)
    if os.name == "posix":
        mode = stat.S_IMODE(metadata.st_mode)
        if exact_posix_mode is not None and mode != exact_posix_mode:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile has unsafe directory permissions."
            )
        if require_owner_only and exact_posix_mode is None and mode & 0o077:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile is not owner-only."
            )


def _is_trusted_codex_runtime_binary(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> bool:
    try:
        canonical = path.resolve(strict=True)
        if _is_link_or_reparse(canonical) or not canonical.is_file():
            return False
        if trusted_binary_path is not None:
            trusted = Path(trusted_binary_path).resolve(strict=True)
            return (
                not _is_link_or_reparse(trusted)
                and trusted.is_file()
                and os.path.normcase(str(canonical))
                == os.path.normcase(str(trusted))
            )
        return (
            _trusted_codex_target_for_digest(_sha256_file_cached(canonical))
            is not None
        )
    except OSError:  # An unreadable binary is reported as an unavailable capability.
        return False


def _validate_windows_arg0_wrapper(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    _validate_regular_private_file(path)
    try:
        if path.stat().st_size > 4096:
            raise ValueError("wrapper is too large")
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an invalid runtime wrapper."
        ) from exc
    match = re.fullmatch(
        r'@echo off\r?\n"([^"\r\n]+)" --codex-run-as-apply-patch %\*\r?\n',
        body,
    )
    if match is None or not _is_trusted_codex_runtime_binary(
        Path(match.group(1)),
        trusted_binary_path=trusted_binary_path,
    ):
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an untrusted runtime wrapper."
        )


def _validate_unix_arg0_alias(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        # Transiently unreadable, not proven-broken (see the sibling wraps).
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile runtime could not be inspected."
        ) from exc
    if not stat.S_ISLNK(metadata.st_mode):
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an invalid runtime alias."
        )
    _validate_current_owner(metadata)
    try:
        raw_target = Path(os.readlink(path))
        target = raw_target if raw_target.is_absolute() else path.parent / raw_target
    except OSError as exc:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an unreadable runtime alias."
        ) from exc
    if not _is_trusted_codex_runtime_binary(
        target,
        trusted_binary_path=trusted_binary_path,
    ):
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an untrusted runtime alias."
        )


def _validate_arg0_runtime_dir(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    """Judge one ``tmp/arg0/codex-arg0XXXXXX`` scratch directory.

    Membership is a NAME ALLOWLIST over a required ``.lock``, not set
    equality. The exact file set was only ever measured on Windows; the macOS
    and Linux sets were assumptions, and one file more or fewer (a Codex point
    release, a platform that ships no sandbox helper) turned a healthy install
    into ``setup_invalid``. The real security boundary is per entry and
    unchanged: every non-lock child must still resolve to the approved binary
    (``_validate_unix_arg0_alias`` / ``_validate_windows_arg0_wrapper``), so a
    name that merely went missing proves nothing and an extra name still has
    to survive that check.
    """
    _validate_private_directory(path)
    try:
        children = {
            entry.name: entry
            for entry in path.iterdir()
            if not _is_os_metadata_entry(entry.name)
        }
    except OSError as exc:
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile runtime could not be inspected."
        ) from exc
    if sys.platform == "win32":
        allowed = {"apply_patch.bat", "applypatch.bat"}
    elif sys.platform == "darwin" or sys.platform.startswith("linux"):
        allowed = {
            "apply_patch",
            "applypatch",
            "codex-execve-wrapper",
            "codex-linux-sandbox",
        }
    else:
        raise CodexSubscriptionUnavailable(
            "This platform's Codex runtime layout is not approved."
        )
    lock_file = children.get(".lock")
    if lock_file is None:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile runtime is missing its lock."
        )
    unknown = sorted(set(children) - allowed - {".lock"})
    if unknown:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains unexpected runtime files."
        )
    _validate_regular_private_file(lock_file)
    if lock_file.stat().st_size != 0:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains an invalid runtime lock."
        )
    for name, child in children.items():
        if name == ".lock":
            continue
        if sys.platform == "win32":
            _validate_windows_arg0_wrapper(
                child,
                trusted_binary_path=trusted_binary_path,
            )
        else:
            _validate_unix_arg0_alias(
                child,
                trusted_binary_path=trusted_binary_path,
            )


def _validate_codex_runtime_state(
    home: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    installation_id = home / "installation_id"
    if installation_id.exists() or installation_id.is_symlink():
        _validate_regular_private_file(installation_id)
        try:
            raw_installation_id = installation_id.read_text(encoding="utf-8")
            parsed = uuid.UUID(raw_installation_id)
        except (OSError, UnicodeError, ValueError) as exc:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile has an invalid installation id."
            ) from exc
        if raw_installation_id != str(parsed) or len(raw_installation_id) != 36:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile has a non-canonical installation id."
            )
        if os.name == "posix":
            mode = stat.S_IMODE(installation_id.stat().st_mode)
            if mode not in {0o600, 0o640, 0o644}:
                raise CodexSubscriptionUnavailable(
                    "The dedicated Codex voice installation id has unsafe permissions."
                )

    runtime_root = home / "tmp"
    if not runtime_root.exists() and not runtime_root.is_symlink():
        return
    trusted_runtime_binary: Path | None = None
    if trusted_binary_path is not None:
        try:
            trusted_runtime_binary = Path(trusted_binary_path).resolve(strict=True)
        except OSError as exc:
            raise CodexSubscriptionUnavailable(
                "The approved Codex runtime binary is unavailable."
            ) from exc
    _validate_private_directory(runtime_root)
    try:
        runtime_children = {
            entry.name: entry
            for entry in runtime_root.iterdir()
            if not _is_os_metadata_entry(entry.name)
        }
    except OSError as exc:
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice runtime could not be inspected."
        ) from exc
    if set(runtime_children) != {"arg0"}:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains unexpected runtime state."
        )
    arg0_root = runtime_children["arg0"]
    _validate_private_directory(
        arg0_root,
        exact_posix_mode=0o700 if os.name == "posix" else None,
    )
    try:
        process_dirs = tuple(
            entry
            for entry in arg0_root.iterdir()
            if not _is_os_metadata_entry(entry.name)
        )
    except OSError as exc:
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice runtime could not be inspected."
        ) from exc
    if len(process_dirs) > _MAX_ARG0_RUNTIME_DIRS:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile contains excessive runtime state."
        )
    for process_dir in process_dirs:
        if _ARG0_RUNTIME_DIR_RE.fullmatch(process_dir.name) is None:
            raise CodexSubscriptionRuntimeStateInvalid(
                "The dedicated Codex voice profile contains an unknown runtime directory."
            )
        _validate_arg0_runtime_dir(
            process_dir,
            trusted_binary_path=(
                str(trusted_runtime_binary)
                if trusted_runtime_binary is not None
                else None
            ),
        )
    if trusted_runtime_binary is not None and not _is_trusted_codex_runtime_binary(
        trusted_runtime_binary
    ):
        raise CodexSubscriptionRuntimeStateInvalid(
            "The dedicated Codex voice profile references an untrusted runtime binary."
        )


def _validated_subscription_home(
    *,
    create: bool,
    require_marker: bool,
    trusted_binary_path: str | None = None,
) -> Path:
    """Resolve the isolated profile and reject every non-login artifact."""
    from jarvis.core.paths import user_data_dir
    from jarvis.core.private_directory import ensure_owner_only_directory

    root = user_data_dir()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.exists() and not root.is_symlink():
        raise CodexSubscriptionProfileMissing(
            "The dedicated Codex voice profile has not been created yet."
        )
    try:
        canonical_root = root.resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionInspectionFailed(
            "Jarvis's private data directory could not be verified."
        ) from exc
    candidate = canonical_root / _SUBSCRIPTION_HOME_DIRNAME
    if not create and not candidate.exists() and not candidate.is_symlink():
        raise CodexSubscriptionProfileMissing(
            "The dedicated Codex voice profile has not been created yet."
        )
    try:
        ensure_owner_only_directory(candidate, create=create)
    except RuntimeError as exc:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile is not owner-only."
        ) from exc
    try:
        canonical_home = candidate.resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionProfileMissing(
            "The dedicated Codex voice profile has not been created yet."
        ) from exc
    if canonical_home.parent != canonical_root:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile escaped Jarvis's private data directory."
        )
    _validate_private_directory(canonical_home, require_owner_only=True)

    try:
        entries = tuple(canonical_home.iterdir())
    except OSError as exc:
        raise CodexSubscriptionInspectionFailed(
            "The dedicated Codex voice profile could not be inspected."
        ) from exc
    # Desktop-shell sidecars are skipped entirely: they are not Codex state,
    # and judging one as "the profile contains configuration" made merely
    # opening the folder in Finder or Explorer cost the user their login.
    entries = tuple(
        entry for entry in entries if not _is_os_metadata_entry(entry.name)
    )
    unexpected = [
        entry.name
        for entry in entries
        if entry.name not in _ALLOWED_SUBSCRIPTION_HOME_ENTRIES
    ]
    if unexpected:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains configuration or runtime state. "
            "Create a fresh voice-only login."
        )
    for entry in entries:
        if entry.name in {
            "auth.json",
            ".sandbox_migration",
            _SUBSCRIPTION_PROFILE_MARKER,
        }:
            _validate_regular_private_file(entry)
        if entry.name == ".sandbox_migration":
            # A tiny one-time CLI marker; anything larger is not that file.
            try:
                oversized = entry.stat().st_size > 4096
            except OSError as exc:
                raise CodexSubscriptionInspectionFailed(
                    "The dedicated Codex voice profile could not be inspected."
                ) from exc
            if oversized:
                raise CodexSubscriptionUnavailable(
                    "The dedicated Codex voice profile contains an unexpected "
                    "runtime file."
                )

    _validate_codex_runtime_state(
        canonical_home,
        trusted_binary_path=trusted_binary_path,
    )

    marker = canonical_home / _SUBSCRIPTION_PROFILE_MARKER
    if require_marker:
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CodexSubscriptionUnavailable(
                "Use Jarvis's subscription-voice login before starting voice mode."
            ) from exc
        if marker_data != {
            "schema": _SUBSCRIPTION_PROFILE_SCHEMA,
            "purpose": "realtime_transport",
        }:
            raise CodexSubscriptionUnavailable(
                "The dedicated Codex voice profile marker is invalid."
            )
    return canonical_home


def _rebuild_invalid_subscription_home() -> None:
    """Remove an invalid Jarvis-owned voice profile so login can rebuild it.

    Reached only from an explicit user action (Connect / Disconnect): the
    directory is Jarvis's own — never user documents — and its only meaningful
    content is the dedicated login the user is actively replacing or removing.
    Without this, ``setup_invalid`` was an in-app dead end whose card text
    demanded a reconnect that failed on the very same validation (CLAUDE.md
    §3: recoverable IN-APP). A symlinked profile is detached, never followed.
    """
    from jarvis.core.paths import user_data_dir

    try:
        root = user_data_dir().resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "Jarvis's private data directory could not be verified."
        ) from exc
    candidate = root / _SUBSCRIPTION_HOME_DIRNAME
    try:
        if not candidate.exists() and not candidate.is_symlink():
            return
        if _is_link_or_reparse(candidate):
            # Detach the link/junction itself; never follow it.
            candidate.unlink()
            return
        shutil.rmtree(candidate)
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "The invalid Codex voice profile could not be removed. Delete the "
            f"'{_SUBSCRIPTION_HOME_DIRNAME}' folder in Jarvis's data "
            "directory manually."
        ) from exc


def _clear_subscription_runtime_state() -> None:
    """Delete only Codex's ephemeral ``tmp/`` scratch inside the profile.

    The scratch is regenerated on the CLI's next run and holds no credential,
    so an unrecognised file in there must cost the user a directory Codex
    rebuilds by itself — never the ChatGPT login sitting next to it.
    """
    from jarvis.core.paths import user_data_dir

    try:
        root = user_data_dir().resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "Jarvis's private data directory could not be verified."
        ) from exc
    runtime_root = root / _SUBSCRIPTION_HOME_DIRNAME / "tmp"
    try:
        if not runtime_root.exists() and not runtime_root.is_symlink():
            return
        if _is_link_or_reparse(runtime_root):
            # Detach the link itself; never follow it out of the profile.
            runtime_root.unlink()
            return
        shutil.rmtree(runtime_root)
    except OSError as exc:
        raise CodexSubscriptionRuntimeStateInvalid(
            "The Codex voice runtime state could not be cleared. Close any "
            "running Codex process and try again."
        ) from exc


def _prepare_subscription_login_home() -> Path:
    try:
        home = _validated_subscription_home(create=True, require_marker=False)
    except CodexSubscriptionInspectionFailed:
        # Transiently unreadable is NOT license to delete anything.
        raise
    except CodexSubscriptionRuntimeStateInvalid:
        # Codex's own scratch, not the login. Clear ONLY that and re-validate;
        # the profile — and the ChatGPT login inside it — survives. If it still
        # fails, the honest error reaches the user instead of a silent wipe.
        _clear_subscription_runtime_state()
        home = _validated_subscription_home(create=True, require_marker=False)
    except CodexSubscriptionUnavailable:
        # The profile is invalid and the user explicitly asked for a fresh
        # login — rebuild the Jarvis-owned directory from scratch. The short
        # retry rides out Windows' delete-pending window (an indexer or AV
        # briefly holding the freshly removed directory open).
        _rebuild_invalid_subscription_home()
        last_error: CodexSubscriptionUnavailable | None = None
        for _attempt in range(3):
            try:
                home = _validated_subscription_home(
                    create=True, require_marker=False
                )
                break
            except CodexSubscriptionUnavailable as exc:
                # Windows delete-pending can briefly block the recreate.
                last_error = exc
                time.sleep(0.2)
        else:
            raise CodexSubscriptionUnavailable(
                "The Codex voice profile could not be rebuilt. "
                "Try Connect again in a moment."
            ) from last_error
    marker = home / _SUBSCRIPTION_PROFILE_MARKER
    auth_file = home / "auth.json"
    if not marker.exists() and auth_file.exists():
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile already contains credentials that were "
            "not created by Jarvis's direct login flow."
        )
    if not marker.exists():
        payload = json.dumps(
            {
                "schema": _SUBSCRIPTION_PROFILE_SCHEMA,
                "purpose": "realtime_transport",
            },
            separators=(",", ":"),
        )
        temporary = home / f"{_SUBSCRIPTION_PROFILE_MARKER}.{secrets.token_hex(4)}.tmp"
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, marker)
        except BaseException:
            with suppress(OSError):
                temporary.unlink()
            raise
    return _validated_subscription_home(create=False, require_marker=True)


def _normalized_machine() -> str:
    machine = platform.machine().strip().lower()
    return {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x64": "x86_64",
    }.get(machine, machine)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("Codex executable is not a regular file.")
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        completed = os.fstat(handle.fileno())
    final = path.stat()
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    # Python 3.12 on Windows exposes different ctime semantics for fstat() and
    # path stat(): the former may mirror mtime while the latter reports file
    # creation time. Comparing them rejects an unchanged executable. Device,
    # inode/file index, size, mtime, and the approved digest still bind the
    # opened handle to the final path. POSIX ctime remains a useful extra guard.
    if sys.platform != "win32":
        stable_fields += ("st_ctime_ns",)
    if any(
        getattr(opened, field, None) != getattr(completed, field, None)
        or getattr(opened, field, None) != getattr(final, field, None)
        for field in stable_fields
    ):
        raise OSError("Codex executable changed while it was being verified.")
    return digest.hexdigest()


_SHA256_CACHE_MAX_ENTRIES: Final = 16
_SHA256_IDENTITY = tuple[int, int, int, int, int]
_sha256_cache: dict[str, tuple[_SHA256_IDENTITY, str]] = {}
_sha256_cache_lock = threading.Lock()


def _file_identity_signature(identity: os.stat_result) -> _SHA256_IDENTITY:
    """Everything about a file that must be equal for its digest to be reused."""
    return (
        identity.st_dev,
        identity.st_ino,
        identity.st_size,
        identity.st_mtime_ns,
        # POSIX ctime changes on every write regardless of utimes-forgery; on
        # Windows this is creation time and merely harmless extra identity.
        identity.st_ctime_ns,
    )


def _forget_cached_digest(path: Path) -> None:
    """Drop a memoized digest so the next reader hashes the file again."""
    key = os.path.normcase(str(path))
    with _sha256_cache_lock:
        _sha256_cache.pop(key, None)


def _sha256_file_cached(path: Path) -> str:
    """Memoized ``_sha256_file`` keyed on the file's identity signature.

    Hashing the ~100 MB native Codex binary dominated the cost of every status
    probe, profile validation, and call start — and each of those runs several
    times per voice call. The signature (device, inode, size, mtime_ns,
    ctime_ns) is read fresh on EVERY call, so any ordinary change to the file
    re-hashes it; the digest is then kept for the process lifetime instead of
    expiring on a wall-clock timer, because a timer bounded nothing an
    attacker could not simply wait out while costing a full re-hash on every
    tick.

    The memo authorizes STATUS and profile-validation reads only. The copy
    that actually RUNS with the user's ChatGPT identity is hashed without the
    memo immediately before spawn (``_verify_spawn_binary``), and that
    un-memoized verdict overwrites — or purges — the memo, so the executing
    path can never be laundered by a cached digest.
    """
    try:
        identity = path.stat()
    except OSError:  # An unreadable identity cannot be memoized — hash fresh.
        return _sha256_file(path)
    key = os.path.normcase(str(path))
    signature = _file_identity_signature(identity)
    with _sha256_cache_lock:
        entry = _sha256_cache.get(key)
        if entry is not None and entry[0] == signature:
            return entry[1]
    digest = _sha256_file(path)
    with _sha256_cache_lock:
        if len(_sha256_cache) >= _SHA256_CACHE_MAX_ENTRIES:
            _sha256_cache.clear()
        _sha256_cache[key] = (signature, digest)
    return digest


def _verify_spawn_binary(binary_path: str) -> None:
    """Full, un-memoized hash check of the exact file about to be executed.

    The memoized hash is fine for status polling; the copy that runs with the
    user's ChatGPT identity gets one fresh verification right before spawn so
    a stale memo can never launder an in-place swap into execution. A
    microsecond rename-over between this check and exec remains possible on
    every platform (no portable fexecve); the guard binds the hash to the
    path at verification time, which is the strongest portable guarantee.

    This un-memoized verdict is also the memo's corrector: a match refreshes
    the entry, a mismatch purges it, so a digest the memo believes can never
    outlive the evidence that it is wrong.
    """
    path = Path(binary_path)
    try:
        identity: os.stat_result | None = path.stat()
    except OSError:  # Unreadable identity: verify anyway, just do not memoize.
        identity = None
    try:
        digest = _sha256_file(path)
    except OSError:
        # The file could not be hashed at all — a memo that still claims this
        # path is approved would outlive the only evidence we have.
        _forget_cached_digest(path)
        raise
    if _trusted_codex_target_for_digest(digest) is None:
        _forget_cached_digest(path)
        raise CodexSubscriptionUnavailable(
            "The Codex executable changed since it was verified."
        )
    if identity is not None:
        with _sha256_cache_lock:
            _sha256_cache[os.path.normcase(str(path))] = (
                _file_identity_signature(identity),
                digest,
            )


def _codex_package_roots(launcher: Path) -> list[Path]:
    """Return local npm package roots without invoking npm or trusting PATH order."""
    search_dirs = [launcher.parent, *list(launcher.parents)[:8]]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry.strip():
            search_dirs.append(Path(entry))
    try:
        from jarvis.core.path_augment import candidate_dirs

        search_dirs.extend(Path(entry) for entry in candidate_dirs())
    except Exception:  # noqa: BLE001 - optional discovery must stay fail-closed
        log.debug("Codex package-root discovery could not read augmented paths", exc_info=True)

    package_roots: list[Path] = []
    for directory in search_dirs:
        if directory.name == "codex" and directory.parent.name == "@openai":
            package_roots.append(directory)
        package_roots.extend(
            (
                directory / "node_modules" / "@openai" / "codex",
                directory.parent / "node_modules" / "@openai" / "codex",
                directory.parent / "lib" / "node_modules" / "@openai" / "codex",
            )
        )
    return package_roots


def _trusted_codex_targets_for_platform() -> tuple[
    tuple[str, tuple[str, str, str, str]], ...
]:
    """Return exact audited releases for this platform, newest first."""
    key = (sys.platform, _normalized_machine())
    releases = (
        (_SUPPORTED_CODEX_VERSION, _TRUSTED_CODEX_TARGETS),
        (_LEGACY_CODEX_VERSION, _LEGACY_TRUSTED_CODEX_TARGETS),
    )
    return tuple(
        (version, target)
        for version, targets in releases
        if (target := targets.get(key)) is not None
    )


def _trusted_codex_target_for_digest(
    digest: str,
) -> tuple[str, tuple[str, str, str, str]] | None:
    """Resolve an executable digest to its exact audited release."""
    for version, target in _trusted_codex_targets_for_platform():
        if digest == target[3]:
            return version, target
    return None


def _trusted_native_codex_binary(resolved_binary: str, version: str | None) -> str:
    """Locate a SHA-256-approved native executable for an audited release.

    The hash is the authority: a candidate matching an official artifact IS
    the pinned build, whatever the launcher's ``--version`` said. The version
    string is advisory only — the npm ``codex`` launcher needs a working
    ``node`` on PATH just to print it, and a service process without node used
    to fail the whole feature here although the native binary was intact.
    """
    release_targets = _trusted_codex_targets_for_platform()
    if not release_targets:
        # A platform gate, not a setup defect: maps to lifecycle_unavailable,
        # whose card text says the feature is not available on this OS yet.
        raise CodexSubscriptionContainmentUnavailable(
            "This operating-system architecture is not approved for the experimental "
            "Codex subscription voice transport."
        )
    try:
        launcher = Path(resolved_binary).resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionUnavailable("The Codex CLI binary is unavailable.") from exc

    candidates: list[Path] = [launcher]
    package_roots = _codex_package_roots(launcher)
    for _release, target in release_targets:
        variant, triple, executable_name, _expected_hash = target
        for package_root in package_roots:
            candidates.extend(
                (
                    package_root
                    / "node_modules"
                    / "@openai"
                    / f"codex-{variant}"
                    / "vendor"
                    / triple
                    / "bin"
                    / executable_name,
                    package_root.parent
                    / f"codex-{variant}"
                    / "vendor"
                    / triple
                    / "bin"
                    / executable_name,
                )
            )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            canonical = candidate.resolve(strict=True)
            key = os.path.normcase(str(canonical))
            if key in seen:
                continue
            seen.add(key)
            if _is_link_or_reparse(canonical) or not canonical.is_file():
                continue
            if _trusted_codex_target_for_digest(
                _sha256_file_cached(canonical)
            ) is not None:
                return str(canonical)
        except OSError:  # Unreadable installation candidates are skipped during discovery.
            continue
    if version is not None and version.startswith("codex-cli") and (
        version not in _SUPPORTED_CODEX_VERSIONS
    ):
        # The launcher answered with a real but different release — name the
        # required one instead of a generic hash complaint.
        raise CodexSubscriptionBinaryUnsupported(
            "Subscription voice supports Codex CLI "
            f"{_SUPPORTED_CODEX_VERSION.removeprefix('codex-cli ')} or "
            f"{_LEGACY_CODEX_VERSION.removeprefix('codex-cli ')}."
        )
    raise CodexSubscriptionBinaryUnsupported(
        "The installed Codex executable does not match an official approved build."
    )


_CLI_VERSION_RE: Final = re.compile(
    r"codex-cli \d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?"
)


_HEADLESS_LOGIN_REASON: Final = (
    "Interactive subscription-voice login is unavailable on headless Linux. "
    "Run Jarvis on a desktop to connect this dedicated profile."
)
_NO_LOGIN_TERMINAL_REASON: Final = (
    "Interactive subscription-voice login needs a terminal emulator this "
    "desktop does not provide. Install one (for example gnome-terminal, "
    "konsole, xfce4-terminal, kitty or alacritty) and connect again."
)
_PURE_WAYLAND_LOGIN_REASON: Final = (
    "This pure-Wayland session has no XWayland display, so the isolated "
    "subscription login cannot auto-open a browser. Connect opens a terminal; "
    "follow the device-login URL printed there."
)


def _headless_linux() -> bool:
    """A Linux host with no graphical session at all."""
    return (
        sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    )


def _pure_wayland_linux() -> bool:
    """A graphical Linux session with Wayland but no X/XWayland display."""
    return bool(
        sys.platform.startswith("linux")
        and os.environ.get("WAYLAND_DISPLAY")
        and not os.environ.get("DISPLAY")
    )


def _linux_login_terminal_missing() -> bool:
    """True on a graphical Linux host with no terminal able to host the login.

    Separate from the headless check: a screen exists, so the old probe said
    "login_required" and the card offered a Connect that could only ever fail.
    """
    if not sys.platform.startswith("linux") or _headless_linux():
        return False
    try:
        from jarvis.codex_auth import (  # noqa: PLC0415
            linux_login_terminal_available,
        )

        return not linux_login_terminal_available()
    except Exception:  # noqa: BLE001 - an unknown probe must not block a login
        log.debug("Linux login-terminal probe failed", exc_info=True)
        return False


def _login_required_reason_code() -> CodexSubscriptionReasonCode:
    """The honest "please log in" state for THIS host.

    On a headless Linux host the interactive browser login is impossible, so
    inviting it would only produce an error toast after the click — the
    pre-click truth there is ``lifecycle_unavailable`` (visible degradation,
    CLAUDE.md §3). The same holds for a graphical Linux desktop that ships no
    terminal able to host the login for its full lifetime. An EXISTING login
    still reports ready on such hosts.
    """
    if _headless_linux() or _linux_login_terminal_missing():
        return "lifecycle_unavailable"
    return "login_required"


def _login_required_state(reason: str) -> tuple[str, CodexSubscriptionReasonCode]:
    """Reason text and code for a missing login, coherent on every surface.

    When the code degrades to ``lifecycle_unavailable`` (headless host), the
    text degrades WITH it: otherwise the 409/400 details and the Test verdict
    would keep asking for the exact login the card refuses to offer.
    """
    code = _login_required_reason_code()
    if code == "lifecycle_unavailable":
        reason = (
            _HEADLESS_LOGIN_REASON
            if _headless_linux()
            else _NO_LOGIN_TERMINAL_REASON
        )
    elif _pure_wayland_linux():
        reason = _PURE_WAYLAND_LOGIN_REASON
    return reason, code


def _displayable_cli_version(raw: str | None) -> str | None:
    """Return the probe output only when it is an actual version string.

    The npm launcher prints a localized shell error instead of a version when
    ``node`` is missing; that text must never reach the UI's version chip or
    count as an installed version. The strict pattern also keeps ANSI noise
    and unbounded output off the chip.
    """
    text = " ".join(str(raw or "").split())
    return text if _CLI_VERSION_RE.fullmatch(text) else None


def _read_codex_capability(binary_path: str | None) -> CodexAppServerCapability:
    """Resolve the CLI; app-server itself authoritatively reads its auth store.

    Discovery uses ``codex login status``, whose audited output reports only
    the mode and never token contents or account PII. Billing authority comes
    only from the live ``account/read`` RPC below.
    """
    from jarvis.codex_auth import CodexAuthService  # lazy: off the boot path

    service = CodexAuthService(
        binary_path,
        codex_home=codex_subscription_home(),
        force_file_auth_store=True,
        isolate_openai_environment=True,
    )
    resolved = service._resolve_binary()
    if resolved is None:
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Codex CLI is not installed.",
            reason_code="not_installed",
        )
    version = _displayable_cli_version(service._probe_version(resolved))
    try:
        resolved_binary = _trusted_native_codex_binary(
            resolved,
            version,
        )
        # A hash match proves the exact official build even when the npm
        # launcher could not print its version (for example: no node on the
        # service PATH).
        try:
            approved = _trusted_codex_target_for_digest(
                _sha256_file_cached(Path(resolved_binary))
            )
        except OSError:
            # Test doubles may stand in for the already-authoritative native
            # discovery helper. A real discovery result is an existing file.
            approved = None
        if approved is not None:
            version = approved[0]
        elif version not in _SUPPORTED_CODEX_VERSIONS:
            version = _SUPPORTED_CODEX_VERSION
    except CodexSubscriptionContainmentUnavailable as exc:  # Expected platform gate becomes status.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved,
            version=version,
            reason=str(exc),
            reason_code="lifecycle_unavailable",
        )
    except CodexSubscriptionBinaryUnsupported as exc:
        # No audited release is present (wrong version or unknown build). The
        # actionable state is "install the supported release" — the card then
        # shows the preferred npm command instead of a profile warning.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved,
            version=version,
            reason=str(exc),
            reason_code="not_installed",
        )
    except CodexSubscriptionUnavailable as exc:
        # Convert setup failure into a safe status snapshot.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved,
            version=version,
            reason=str(exc),
            reason_code="setup_invalid",
        )
    try:
        home = _validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=resolved_binary,
        )
    except CodexSubscriptionProfileMissing as exc:  # Missing login becomes an actionable snapshot.
        reason, reason_code = _login_required_state(str(exc))
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=reason,
            reason_code=reason_code,
        )
    except CodexSubscriptionInspectionFailed as exc:
        # An OS read hiccup is transiently unknown, never a proven-broken
        # profile that tells the user to recreate their login.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=f"{exc} Retrying shortly.",
            reason_code="busy",
        )
    except CodexSubscriptionUnavailable as exc:  # Unsafe profile state becomes a status snapshot.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=str(exc),
            reason_code="setup_invalid",
        )
    status_log_dir = Path(tempfile.mkdtemp(prefix="jarvis-codex-status-"))
    try:
        logged_in, login_mode = CodexAuthService(
            resolved_binary,
            codex_home=home,
            force_file_auth_store=True,
            isolate_openai_environment=True,
            log_dir=status_log_dir,
        ).login_status()
    finally:
        shutil.rmtree(status_log_dir, ignore_errors=True)
    # Codex 0.146 creates only a persistent installation UUID plus its locked
    # tmp/arg0 aliases before parsing config. Validate that exact runtime
    # footprint after every status probe; any other state remains fail-closed.
    try:
        _validated_subscription_home(
            create=False,
            require_marker=True,
            trusted_binary_path=resolved_binary,
        )
    except CodexSubscriptionProfileMissing as exc:
        # Missing login remains a normal not-ready state.
        reason, reason_code = _login_required_state(str(exc))
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=reason,
            reason_code=reason_code,
        )
    except CodexSubscriptionInspectionFailed as exc:
        # Transiently unreadable, not proven-broken (see the first block).
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=f"{exc} Retrying shortly.",
            reason_code="busy",
        )
    except CodexSubscriptionUnavailable as exc:  # Profile validation failure is returned to the UI.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=str(exc),
            reason_code="setup_invalid",
        )
    if login_mode == "probe_failed":
        # The CLI could not be asked (spawn failure or timeout). That is a
        # transiently unknown state — publishing it as "login required" would
        # flip a connected card to "not connected" on one slow subprocess.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason="The Codex login status probe failed; retrying shortly.",
            reason_code="busy",
        )
    chatgpt_login = logged_in and login_mode == "chatgpt"
    if chatgpt_login:
        return CodexAppServerCapability(
            available=True,
            # Advisory only: the CLI emits a fixed PII-free mode string. The
            # live account/read RPC below proves ChatGPT mode and the plan.
            chatgpt_authenticated=True,
            binary_path=resolved_binary,
            version=version,
            reason="Dedicated ChatGPT login is available.",
            reason_code="ready",
        )
    reason, reason_code = _login_required_state(
        "Use Jarvis's subscription-voice login to connect ChatGPT."
    )
    return CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=False,
        binary_path=resolved_binary,
        version=version,
        reason=reason,
        reason_code=reason_code,
    )


def _subscription_environment(
    codex_home: Path,
    workspace: _SafeTransportWorkspace,
) -> dict[str, str]:
    """Minimal child environment containing no inherited provider tokens.

    ``CODEX_HOME`` is the fixed Jarvis-owned identity. Provider, cloud, proxy,
    keyring-session, and billing-token environment variables are not inherited.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if _subscription_env_allowed(name)
    }
    environment.update(
        {
            "APPDATA": str(workspace.child_appdata),
            "CODEX_HOME": str(codex_home),
            "HOME": str(workspace.child_home),
            "LOCALAPPDATA": str(workspace.child_local_appdata),
            "RUST_BACKTRACE": "0",
            "RUST_LOG": "warn",
            "TEMP": str(workspace.child_tmp),
            "TMP": str(workspace.child_tmp),
            "TMPDIR": str(workspace.child_tmp),
            "USERPROFILE": str(workspace.child_home),
        }
    )
    environment.pop("HOMEDRIVE", None)
    environment.pop("HOMEPATH", None)
    # Persisted Remote Control is resolved before initialize/config audit in
    # Codex 0.146. This one documented process guard disables that startup
    # path before app-server can make any outbound connection.
    environment["CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"] = "1"
    return environment


def _create_safe_transport_workspace(
    purpose: CodexAppServerPurpose = "realtime",
) -> _SafeTransportWorkspace:
    from jarvis.core.private_directory import (  # noqa: PLC0415
        ensure_owner_only_directory,
    )

    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        workspace_label = "voice" if purpose == "realtime" else "text"
        root = temporary_root / (
            f"jarvis-codex-{workspace_label}-{secrets.token_hex(16)}"
        )
        ensure_owner_only_directory(root, create=True)
        root = root.resolve(strict=True)
        if root.parent != temporary_root:
            raise RuntimeError("The private workspace escaped its temporary root.")
    except (OSError, RuntimeError) as exc:
        raise CodexSubscriptionUnavailable(
            "The private Codex transport workspace could not be secured."
        ) from exc
    instructions = root / "transport-instructions.md"
    compact_prompt = root / "transport-compact-prompt.md"
    model_catalog = root / "model-catalog.json"
    sqlite_home = root / "sqlite"
    log_dir = root / "logs"
    child_home = root / "home"
    child_appdata = root / "appdata"
    child_local_appdata = root / "local-appdata"
    child_tmp = root / "tmp"
    try:
        instruction_floor = (
            _TEXT_BASE_INSTRUCTIONS
            if purpose == "text"
            else _TRANSPORT_BASE_INSTRUCTIONS
        )
        instructions.write_text(instruction_floor, encoding="utf-8")
        compact_prompt.write_text(instruction_floor, encoding="utf-8")
        for directory in (
            sqlite_home,
            log_dir,
            child_home,
            child_appdata,
            child_local_appdata,
            child_tmp,
        ):
            directory.mkdir()
        model_catalog.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-5.1-codex",
                            "display_name": "Jarvis transport boundary",
                            "description": None,
                            "supported_reasoning_levels": [],
                            "shell_type": "disabled",
                            "visibility": "hide",
                            "supported_in_api": True,
                            "priority": 0,
                            "availability_nux": None,
                            "upgrade": None,
                            "base_instructions": instruction_floor,
                            "model_messages": None,
                            "support_verbosity": False,
                            "default_verbosity": None,
                            "apply_patch_tool_type": None,
                            "truncation_policy": {"mode": "tokens", "limit": 10_000},
                            "supports_parallel_tool_calls": False,
                            "experimental_supported_tools": [],
                        }
                    ]
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return _SafeTransportWorkspace(
        root=root,
        instructions=instructions,
        compact_prompt=compact_prompt,
        model_catalog=model_catalog,
        sqlite_home=sqlite_home,
        log_dir=log_dir,
        child_home=child_home,
        child_appdata=child_appdata,
        child_local_appdata=child_local_appdata,
        child_tmp=child_tmp,
    )


def _windows_creationflags(*, allow_breakaway: bool) -> int:
    flags = NO_WINDOW_CREATIONFLAGS
    if sys.platform == "win32" and allow_breakaway:
        flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    return flags


def _thread_id_from_params(params: Mapping[str, Any]) -> str | None:
    direct = params.get("threadId")
    if isinstance(direct, str) and direct:
        return direct
    for key in ("thread", "turn", "item"):
        nested = params.get(key)
        if not isinstance(nested, Mapping):
            continue
        value = nested.get("threadId")
        if isinstance(value, str) and value:
            return value
        if key == "thread":
            value = nested.get("id")
            if isinstance(value, str) and value:
                return value
    return None


def _result_dict(method: str, result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    raise CodexAppServerError(f"Codex app-server returned an invalid {method} result.")


def _validated_realtime_initial_items(
    initial_items: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    """Bound the exact role/text startup-history surface audited upstream."""
    if initial_items is None:
        return []
    if (
        not isinstance(initial_items, Sequence)
        or isinstance(initial_items, (str, bytes))
        or len(initial_items) > _MAX_REALTIME_INITIAL_ITEMS
    ):
        raise CodexSubscriptionUnavailable(
            "Codex realtime startup history is invalid or too large."
        )
    validated: list[dict[str, str]] = []
    total_bytes = 0
    for item in initial_items:
        if not isinstance(item, Mapping) or set(item) != {"role", "text"}:
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup history contains an invalid item."
            )
        role = item.get("role")
        text = item.get("text")
        if (
            not isinstance(role, str)
            or role not in _REALTIME_INITIAL_ROLES
            or not isinstance(text, str)
        ):
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup history contains an invalid role or text."
            )
        clean_text = text.strip()
        if not clean_text:
            continue
        total_bytes += len(clean_text.encode("utf-8"))
        if total_bytes > _MAX_REALTIME_INITIAL_TEXT_BYTES:
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup history is too large."
            )
        validated.append({"role": str(role), "text": clean_text})
    return validated


def _validated_realtime_audited_optional(
    *,
    realtime_start_instructions: str | None,
    realtime_end_instructions: str | None,
    codex_responses_as_items: bool | None,
    codex_response_item_prefix: str | None,
    codex_response_handoff_mode: str | None,
    codex_response_handoff_channel_prefixes: Sequence[str] | None,
) -> dict[str, Any]:
    """Validate the optional 0.147-audited realtime start fields.

    Field names mirror the upstream ``ThreadRealtimeStartParams`` schema
    byte-for-byte (verified against the codex-cli 0.147.0 binary's serde name
    table): ``realtimeStartInstructions``, ``realtimeEndInstructions``,
    ``codexResponsesAsItems``, ``codexResponseItemPrefix``,
    ``codexResponseHandoffMode``, ``codexResponseHandoffChannelPrefixes``.
    Only fields the caller actually provided are returned; empty strings and
    empty sequences count as not provided.
    """
    audited: dict[str, Any] = {}
    instruction_fields = (
        ("realtimeStartInstructions", realtime_start_instructions),
        ("realtimeEndInstructions", realtime_end_instructions),
    )
    for field_name, value in instruction_fields:
        if value is None:
            continue
        if not isinstance(value, str):
            raise CodexSubscriptionUnavailable("Codex realtime session instructions are invalid.")
        clean = value.strip()
        if len(clean.encode("utf-8")) > _MAX_REALTIME_START_PROMPT_BYTES:
            raise CodexSubscriptionUnavailable("Codex realtime session instructions are too large.")
        if clean:
            audited[field_name] = clean
    if codex_responses_as_items is not None:
        if not isinstance(codex_responses_as_items, bool):
            raise CodexSubscriptionUnavailable("Codex realtime handoff routing fields are invalid.")
        audited["codexResponsesAsItems"] = codex_responses_as_items
    string_fields = (
        ("codexResponseItemPrefix", codex_response_item_prefix),
        ("codexResponseHandoffMode", codex_response_handoff_mode),
    )
    for field_name, value in string_fields:
        if value is None:
            continue
        if not isinstance(value, str):
            raise CodexSubscriptionUnavailable("Codex realtime handoff routing fields are invalid.")
        clean = value.strip()
        if clean:
            audited[field_name] = clean
    if codex_response_handoff_channel_prefixes is not None:
        if isinstance(codex_response_handoff_channel_prefixes, (str, bytes)) or not isinstance(
            codex_response_handoff_channel_prefixes, Sequence
        ):
            raise CodexSubscriptionUnavailable("Codex realtime handoff routing fields are invalid.")
        prefixes: list[str] = []
        for prefix in codex_response_handoff_channel_prefixes:
            if not isinstance(prefix, str) or not prefix.strip():
                raise CodexSubscriptionUnavailable(
                    "Codex realtime handoff routing fields are invalid."
                )
            prefixes.append(prefix.strip())
        if prefixes:
            audited["codexResponseHandoffChannelPrefixes"] = prefixes
    return audited


def _config_layers(result: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_layers = result.get("layers")
    if not isinstance(raw_layers, list):
        raise CodexSubscriptionUnavailable(
            "Codex effective configuration could not be verified."
        )
    layers: list[Mapping[str, Any]] = []
    for layer in raw_layers:
        if not isinstance(layer, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration contained an invalid layer."
            )
        config = layer.get("config")
        if not isinstance(config, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration contained an unreadable layer."
            )
        layers.append(config)
    return tuple(layers)


def _config_layer_entries(
    result: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    raw_layers = result.get("layers")
    if not isinstance(raw_layers, list):
        raise CodexSubscriptionUnavailable(
            "Codex effective configuration could not be verified."
        )
    entries: list[tuple[str, Mapping[str, Any]]] = []
    for layer in raw_layers:
        if not isinstance(layer, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration contained an invalid layer."
            )
        raw_name = layer.get("name")
        if isinstance(raw_name, Mapping):
            raw_name = raw_name.get("type")
        if not isinstance(raw_name, str) or not raw_name:
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration contained an unnamed layer."
            )
        config = layer.get("config")
        if not isinstance(config, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration contained an unreadable layer."
            )
        entries.append((raw_name, config))
    return tuple(entries)


def _layer_value(
    layers: Sequence[Mapping[str, Any]], path: Sequence[str]
) -> Any:
    """Return the highest-precedence explicitly configured value for a path."""
    for layer in layers:  # config/read returns highest precedence first.
        value: Any = layer
        for part in path:
            if not isinstance(value, Mapping) or part not in value:
                value = _MISSING
                break
            value = value[part]
        if value is not _MISSING:
            return value
    return _MISSING


def _mcp_server_ids(layers: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    names: set[str] = set()
    for layer in layers:
        servers = layer.get("mcp_servers")
        if isinstance(servers, Mapping):
            names.update(str(name) for name in servers if str(name))
    return tuple(sorted(names))


class CodexNotificationSubscription:
    """A notification queue bound to exactly one app-server thread."""

    def __init__(
        self,
        client: CodexAppServerClient,
        thread_id: str,
        max_queue_size: int,
    ) -> None:
        self._client = client
        self.thread_id = thread_id
        self._queue: asyncio.Queue[_SubscriptionItem] = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False

    async def get(self, timeout_s: float | None = None) -> CodexAppServerNotification:
        """Return the next notification, with an optional bounded wait."""
        try:
            if timeout_s is None:
                item = await self._queue.get()
            else:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout_s)
        except TimeoutError as exc:
            raise CodexAppServerTimeout(
                f"Timed out waiting for a Codex notification on {self.thread_id}."
            ) from exc
        if item is _SUBSCRIPTION_END:
            raise CodexAppServerDisconnected("Codex notification subscription closed.")
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, CodexAppServerNotification):
            raise CodexAppServerDisconnected("Codex notification stream is invalid.")
        return item

    async def wait_for(
        self, method: str, timeout_s: float | None = None
    ) -> CodexAppServerNotification:
        """Wait for one method on this subscription.

        Non-matching items are consumed only from this subscription.  Callers
        that need the complete stream should keep their own parallel
        subscription; notifications are broadcast to every subscriber.
        """
        deadline = None
        if timeout_s is not None:
            deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = None
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise CodexAppServerTimeout(
                        f"Timed out waiting for Codex notification {method}."
                    )
            notification = await self.get(remaining)
            if notification.method == method:
                return notification

    def close(self) -> None:
        """Unregister this local subscriber.  Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        self._client._remove_subscription(self)
        self._replace_queue_with(_SUBSCRIPTION_END)

    def _publish(self, item: _SubscriptionItem) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # Continuing with missing audio/transcript frames would corrupt the
            # session invisibly.  Terminate this subscriber and replace every
            # queued frame with one explicit overflow error instead.
            self._closed = True
            self._client._remove_subscription(self)
            self._replace_queue_with(
                CodexNotificationOverflow(
                    "Codex realtime notification buffer overflowed; session closed."
                )
            )

    def _fail(self, error: BaseException) -> None:
        if self._closed:
            return
        self._closed = True
        self._client._remove_subscription(self)
        self._replace_queue_with(error)

    def _replace_queue_with(self, item: _SubscriptionItem) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # The terminal item can now fit without a drop.
                break
        self._queue.put_nowait(item)

    async def __aenter__(self) -> CodexNotificationSubscription:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.close()

    def __aiter__(self) -> AsyncIterator[CodexAppServerNotification]:
        return self

    async def __anext__(self) -> CodexAppServerNotification:
        try:
            return await self.get()
        except CodexAppServerDisconnected:
            raise StopAsyncIteration from None


class CodexAppServerClient:
    """Lazy JSONL client for one persistent ``codex app-server`` process."""

    def __init__(
        self,
        binary_path: str | None = None,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        *,
        purpose: CodexAppServerPurpose = "realtime",
    ) -> None:
        if purpose not in ("realtime", "text"):
            raise ValueError("purpose must be 'realtime' or 'text'")
        self._binary_path = (binary_path or "").strip() or None
        self._purpose: CodexAppServerPurpose = purpose
        self._request_timeout_s = max(0.1, float(request_timeout_s))
        self._child_environment: dict[str, str] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._process_tree: ProcessTree | None = None
        self._lifeline_write_fd: int | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._thread_start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, tuple[str, asyncio.Future[Any]]] = {}
        self._subscriptions: dict[str, set[CodexNotificationSubscription]] = {}
        self._next_request_id = 1
        self._workspace: _SafeTransportWorkspace | None = None
        self._sink_server: asyncio.Server | None = None
        self._sink_base_url: str | None = None
        self._trusted_binary_path: str | None = None
        self._trusted_binary_version: str | None = None
        # Ownership epoch of this client's profile reservation; None when the
        # client holds none. Releases quote it so stale teardown can never
        # touch a newer reservation.
        self._profile_transport_epoch: int | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._transport_provider_id = f"jarvis_voice_{secrets.token_hex(16)}"
        self._ready = False
        # Set once ``close()`` ran. A closed client has released its profile
        # reservation and deleted its workspace, so restarting it would fight
        # the successor for the single-owner profile; callers acquire a fresh
        # one from ``get_shared_codex_app_server`` instead.
        self._closed = False
        # The live process whose FULL startup audit (profile, live account,
        # config requirements, effective config) just passed. ``thread_start``
        # consumes this to skip repeating that identical audit microseconds
        # later; every WARM thread start still re-audits. The (process, stamp)
        # pair below extends the one-shot token by a short TTL for exactly one
        # scenario: a failed host-candidate media path retries with STUN and
        # pays a SECOND thread_start seconds after the audit passed — that
        # retry rides the same audit instead of re-running three RPCs and a
        # profile walk. Warm starts minutes later still re-audit in full, so
        # "re-read immediately before EVERY subscription thread" keeps its
        # meaning (the wake ride below is the one TTL-bounded exception, and
        # it too exists only because a full audit just passed).
        self._startup_audit_process: asyncio.subprocess.Process | None = None
        self._audited_process: asyncio.subprocess.Process | None = None
        self._startup_audit_at = 0.0
        # Wake ride minted by ``prime_startup_audit``: a warm/verify pass that
        # just COMPLETED the full startup audit stamps this (process, time)
        # pair so one wake-word thread start arriving within the same short
        # TTL can ride that audit instead of repeating it on the identical
        # process. One-shot — thread_start consumes it on first read — and
        # never minted without a full audit actually passing, so the audit
        # itself is not weakened, only de-duplicated.
        self._warm_audit_process: asyncio.subprocess.Process | None = None
        self._warm_audit_at = 0.0

    def _assert_owner_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = loop
        elif self._owner_loop is not loop:
            raise CodexSubscriptionUnavailable(
                "Codex app-server client ownership cannot cross event loops."
            )
        return loop

    async def capability_status(self) -> CodexAppServerCapability:
        """Return a bounded, PII-free ChatGPT-login capability snapshot.

        The caller must already own the profile (``ensure_started`` reserves
        the transport first): this probe runs the Codex CLI against
        ``CODEX_HOME`` and racing it with a login, logout, or another probe
        can corrupt the profile's auth and runtime files.
        """
        try:
            # The subprocesses inside this synchronous probe each have their own
            # hard timeout.  Do not wrap ``to_thread`` in ``wait_for``: cancelling
            # that await leaves the worker thread running against CODEX_HOME and
            # would release the profile mutation gate too early.
            binary_path = self._binary_path

            def _probe_and_publish() -> CodexAppServerCapability:
                # While this client reserves the transport, ordinary status
                # probes answer "busy". Publishing this authoritative result
                # keeps every concurrent status caller truthful during
                # app-server startup and wakes waiters parked on the busy
                # window. Probe AND publish stay in the worker thread: the
                # login mutex can be held across filesystem work, and the
                # event loop must never block on it.
                capability = _read_codex_capability(binary_path)
                with _subscription_login_lock:
                    _store_subscription_snapshot_locked(binary_path, capability)
                return capability

            return await asyncio.to_thread(_probe_and_publish)
        except TimeoutError:  # The returned status reports this bounded probe failure.
            # Transiently unknown — everywhere else a failed PROBE maps to
            # busy, never to a proven-broken setup.
            return CodexAppServerCapability(
                available=False,
                chatgpt_authenticated=False,
                binary_path=None,
                version=None,
                reason="Codex login status timed out; retrying shortly.",
                reason_code="busy",
            )
        except Exception as exc:  # noqa: BLE001 - status must degrade honestly
            log.warning(
                "Codex subscription status failed (%s); app-server stays disabled",
                type(exc).__name__,
                exc_info=True,
            )
            return CodexAppServerCapability(
                available=False,
                chatgpt_authenticated=False,
                binary_path=None,
                version=None,
                reason="Codex login status could not be verified; retrying shortly.",
                reason_code="busy",
            )

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None

    @property
    def ready(self) -> bool:
        """Whether handshake and live ChatGPT account verification completed."""
        return self.running and self._ready

    async def ensure_started(self) -> None:
        """Start and initialize app-server once, only when first needed."""
        self._assert_owner_loop()
        if self.ready:
            return
        if self._closed:
            # Restarting a closed client would race the successor for the
            # single-owner profile and wedge it behind an unactionable
            # "already owned" error. Name the recovery instead.
            raise CodexSubscriptionUnavailable(
                "This subscription-voice transport was closed. Start voice "
                "again to open a new one."
            )
        async with self._start_lock:
            if self.ready:
                return
            if self._closed:
                raise CodexSubscriptionUnavailable(
                    "This subscription-voice transport was closed. Start voice "
                    "again to open a new one."
                )
            if self._process is not None:
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server exited."),
                    expected=False,
                )

            # Off the event loop: reserving may wait out a running status
            # probe. Cancellation safety lives entirely in worker threads: a
            # cancel can interrupt any await here (even a shielded one, on a
            # second cancel), so no loop-side variable may decide whether the
            # reservation needs releasing. The reserve worker records its own
            # outcome; on cancellation a detached worker waits for it to
            # settle and releases the orphan, so the profile can never stay
            # parked on "voice is starting" forever.
            # Exactly-one-decider contract: the reserve worker and the
            # abandoner agree under a tiny lock who releases the epoch, so no
            # interleaving of cancellation, executor scheduling, or a second
            # ensure_started can leak the reservation OR release a newer one
            # (the epoch token makes stale releases no-ops). No timeouts: a
            # give-up path would turn executor saturation into a permanent
            # claim leak, because the worker would still claim later with
            # nobody left to release.
            # RT-SPAWN sub-spans: the cold bundle is the biggest single chunk
            # of a session open, and without per-step numbers "the handshake
            # is slow" is unattributable. Local stamps only; one summary INFO
            # on success.
            spans: dict[str, int] = {}
            bundle_started = time.monotonic()
            last_stamp = bundle_started

            def _stamp(name: str) -> None:
                nonlocal last_stamp
                now = time.monotonic()
                spans[name] = int((now - last_stamp) * 1000.0)
                last_stamp = now

            decision_lock = threading.Lock()
            abandoned = threading.Event()
            published_epoch: list[int] = []

            def _reserve() -> None:
                # A raising reserve holds nothing and propagates through the
                # awaited future; only a SUCCESSFUL reserve needs a decider.
                epoch = _reserve_subscription_transport(self)
                with decision_lock:
                    if not abandoned.is_set():
                        self._profile_transport_epoch = epoch
                        published_epoch.append(epoch)
                        return
                # The caller was cancelled before we finished: nobody will
                # ever use this reservation, so this worker releases it.
                _release_subscription_transport(self, epoch)

            reserve_future = asyncio.get_running_loop().run_in_executor(
                None, _reserve
            )
            # A cancelled awaiter leaves the worker's exception unretrieved;
            # consume it so asyncio does not log a spurious GC error.
            reserve_future.add_done_callback(
                lambda f: None if f.cancelled() else f.exception()
            )
            try:
                await asyncio.shield(reserve_future)
            except asyncio.CancelledError:
                with decision_lock:
                    abandoned.set()
                    epoch = published_epoch[0] if published_epoch else None
                    if epoch is not None and self._profile_transport_epoch == epoch:
                        self._profile_transport_epoch = None
                if epoch is not None:
                    # Already published before the cancel: release off-loop.
                    try:
                        threading.Thread(
                            target=_release_subscription_transport,
                            args=(self, epoch),
                            name="codex-reserve-abandon",
                            daemon=True,
                        ).start()
                    except RuntimeError:
                        # Thread exhaustion: a brief inline release beats
                        # leaking the reservation until restart.
                        _release_subscription_transport(self, epoch)
                raise

            _stamp("reserve")

            async def _release_after_failure() -> None:
                epoch = self._profile_transport_epoch
                self._profile_transport_epoch = None
                if epoch is not None:
                    await asyncio.shield(
                        asyncio.to_thread(
                            _release_subscription_transport, self, epoch
                        )
                    )

            try:
                capability = await self.capability_status()
            except BaseException:
                await _release_after_failure()
                raise
            _stamp("capability_probe")
            if not capability.available:
                await _release_after_failure()
                raise CodexSubscriptionUnavailable(capability.reason)
            if not capability.binary_path:
                await _release_after_failure()
                raise CodexSubscriptionUnavailable("Codex CLI binary is unavailable.")
            try:
                self._trusted_binary_path = capability.binary_path
                self._trusted_binary_version = capability.version
                codex_home = await asyncio.to_thread(
                    _validated_subscription_home,
                    create=False,
                    require_marker=True,
                    trusted_binary_path=self._trusted_binary_path,
                )
                if self._workspace is None:
                    self._workspace = await asyncio.to_thread(
                        _create_safe_transport_workspace, self._purpose
                    )
                self._child_environment = _subscription_environment(
                    codex_home,
                    self._workspace,
                )
                if self._purpose == "realtime":
                    await self._ensure_sink_started()
                _stamp("home_workspace_sink")
                await asyncio.to_thread(
                    _verify_spawn_binary, capability.binary_path
                )
                _stamp("binary_verify")
                await self._spawn(capability.binary_path)
                _stamp("spawn")
                await self._initialize_live_process()
                _stamp("initialize")
                await self._verify_live_chatgpt_account()
                _stamp("account_verify")
                self._audit_config_requirements(
                    await self._read_config_requirements()
                )
                final_config = await self._read_effective_config()
                self._audit_effective_config(final_config)
                await asyncio.to_thread(
                    _validated_subscription_home,
                    create=False,
                    require_marker=True,
                    trusted_binary_path=self._trusted_binary_path,
                )
                _stamp("config_audit")
                self._ready = True
                log.info(
                    "RT-SPAWN span=ensure_started ms=%d detail=%s",
                    int((time.monotonic() - bundle_started) * 1000.0),
                    ",".join(f"{name}={ms}" for name, ms in spans.items()),
                )
                # The audit that just passed is byte-for-byte the one
                # ``thread_start`` performs. Record which process it covers so
                # the very next thread start rides it instead of repeating
                # three RPC round trips and a profile walk on the SAME process
                # microseconds later.
                self._startup_audit_process = self._process
                self._audited_process = self._process
                self._startup_audit_at = time.monotonic()

                def _announce_ready() -> None:
                    # Status callers waiting out the "voice is starting"
                    # window can now read the ready transport state directly.
                    # Off-loop: the login mutex can be held across filesystem
                    # work by a probe thread. Shielded so a cancellation
                    # cannot drop the job from the executor queue and leave
                    # waiters running into their timeout.
                    with _subscription_login_lock:
                        _subscription_state_changed.notify_all()

                await asyncio.shield(asyncio.to_thread(_announce_ready))
            except BaseException as exc:
                if isinstance(exc, CodexSubscriptionPlanUnsupported):
                    # The COLD activation path discovers the refused plan
                    # here, inside startup — record the sticky diagnosis where
                    # the truth appears, or every surface keeps claiming ready
                    # after the one honest toast fades.
                    await asyncio.shield(
                        asyncio.to_thread(
                            set_codex_subscription_activation_block, str(exc)
                        )
                    )
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server initialization failed."),
                    expected=False,
                )
                raise

    async def _initialize_live_process(self) -> None:
        await self._request_live(
            "initialize",
            {
                "clientInfo": {
                    "name": "personal_jarvis",
                    "title": "Personal Jarvis",
                    "version": __version__,
                },
                "capabilities": {
                    "experimentalApi": self._purpose == "realtime",
                    "mcpServerOpenaiFormElicitation": False,
                    "requestAttestation": False,
                },
            },
            timeout_s=self._request_timeout_s,
        )
        await self._notify_live("initialized", None)

    async def _verify_live_chatgpt_account(self) -> None:
        account_result = _result_dict(
            "account/read",
            await self._request_live(
                "account/read",
                {"refreshToken": False},
                timeout_s=self._request_timeout_s,
            ),
        )
        account = account_result.get("account")
        account_type = account.get("type") if isinstance(account, Mapping) else None
        if account_type != "chatgpt" or account_result.get("requiresOpenaiAuth") is not True:
            raise CodexSubscriptionUnavailable(
                "Codex app-server is not using ChatGPT authentication."
            )
        plan_type = account.get("planType") if isinstance(account, Mapping) else None
        if plan_type not in _PERSONAL_CHATGPT_PLANS:
            raise CodexSubscriptionPlanUnsupported(
                "Subscription voice permits only personal ChatGPT accounts; workspace, "
                "enterprise, education, and unknown plans are refused."
            )

    async def require_chatgpt_login(self) -> None:
        """Verify the current app-server account through Codex's real store."""
        await self.ensure_started()
        try:
            await self._verify_live_chatgpt_account()
        except CodexSubscriptionUnavailable as exc:
            if isinstance(exc, CodexSubscriptionPlanUnsupported):
                # Recorded where the truth is discovered — this gate is also
                # the LIVE call path, so a plan that turns unsupported after
                # activation still flips every status surface to the sticky
                # diagnosis instead of leaving them all claiming "ready".
                # Off-loop and shielded like its cold-start and warm-path
                # siblings: an activation timeout must not drop the queued
                # recording job.
                await asyncio.shield(
                    asyncio.to_thread(
                        set_codex_subscription_activation_block, str(exc)
                    )
                )
            await self._close_process(
                CodexAppServerDisconnected(
                    "Codex app-server authentication is not ChatGPT."
                ),
                expected=False,
            )
            raise
        # The verify pass just proved the account; completing the FULL startup
        # audit here (off the wake critical path) lets one wake word inside
        # the ride TTL skip the byte-identical re-audit in ``thread_start``.
        # Priming is a pure wake-latency optimization on top of a login that
        # was JUST proven, so it is best-effort: any failure short of the
        # unambiguous entitlement loss below is logged and dropped — it never
        # fails the login result or closes the just-verified process, and the
        # next wake word simply pays the full audit in ``thread_start``.
        try:
            await self.prime_startup_audit(best_effort=True)
        except CodexSubscriptionPlanUnsupported:
            # The audit's own live account re-read refused the plan — a real
            # entitlement loss, recorded sticky where it was discovered. The
            # login result must report it, exactly like the gate above.
            raise
        except Exception:
            log.warning(
                "Codex warm-audit priming failed after a proven login; "
                "skipping the wake ride (the next wake word re-audits in full)",
                exc_info=True,
            )

    async def prime_startup_audit(self, *, best_effort: bool = False) -> None:
        """Complete the full startup audit now and mint the one-shot wake ride.

        Runs the byte-identical audit ``thread_start`` performs (profile walk,
        live ``account/read``, config requirements, effective config) under the
        same lock that serializes thread starts, then stamps the short-TTL
        one-shot ride token. A wake word arriving within the TTL rides this
        just-passed audit instead of paying it again on the same process; a
        wake word arriving later re-audits in full, so "re-read immediately
        before EVERY subscription thread" keeps its meaning. The token is only
        ever minted from an audit that actually completed — this method never
        skips or weakens any check, it only moves the identical work off the
        wake critical path.

        With ``best_effort=True`` (the ``require_chatgpt_login`` warm path) a
        refusal other than the permanently-recorded plan loss leaves the live
        process untouched and re-raises for the caller to log-and-drop: the
        account was proven moments earlier, so an ambiguous refusal must not
        tear down a just-verified process over a wake-latency optimization.
        """
        await self.ensure_started()
        async with self._thread_start_lock:
            live_process = self._process
            if live_process is None or live_process.returncode is not None:
                raise CodexAppServerDisconnected("Codex app-server is not running.")
            if (
                self._audited_process is live_process
                and time.monotonic() - self._startup_audit_at <= _STARTUP_AUDIT_TTL_S
            ):
                # A full audit of this very process passed within the TTL
                # (cold start or a warm thread_start re-audit). Re-mint the
                # wake ride from that stamp — its TTL still counts from the
                # moment the audit actually passed.
                self._warm_audit_process = live_process
                self._warm_audit_at = self._startup_audit_at
                return
            try:
                await asyncio.to_thread(
                    _validated_subscription_home,
                    create=False,
                    require_marker=True,
                    trusted_binary_path=self._trusted_binary_path,
                )
                await self._verify_live_chatgpt_account()
                self._audit_config_requirements(await self._read_config_requirements())
                self._audit_effective_config(await self._read_effective_config())
            except CodexSubscriptionUnavailable as exc:
                if isinstance(exc, CodexSubscriptionPlanUnsupported):
                    # Same recording rule as the cold-start and warm-call
                    # paths: the sticky diagnosis is written where the truth
                    # is discovered.
                    await asyncio.shield(
                        asyncio.to_thread(set_codex_subscription_activation_block, str(exc))
                    )
                elif best_effort:
                    # Ambiguous between a transient hiccup (an antivirus-locked
                    # profile directory, a config read racing a restart) and a
                    # real state change — and the account was proven moments
                    # ago. Keep the just-verified process alive and re-raise
                    # for the caller to log-and-drop; the next wake word
                    # re-audits in full.
                    raise
                error = type(exc)(str(exc))
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server safety state changed."),
                    expected=False,
                )
                raise error from None
            self._audited_process = live_process
            self._startup_audit_at = time.monotonic()
            self._warm_audit_process = live_process
            self._warm_audit_at = self._startup_audit_at

    async def _read_effective_config(self) -> dict[str, Any]:
        return _result_dict(
            "config/read",
            await self._request_live(
                "config/read",
                {"cwd": self._safe_thread_cwd(), "includeLayers": True},
                timeout_s=self._request_timeout_s,
            ),
        )

    async def _read_config_requirements(self) -> dict[str, Any]:
        return _result_dict(
            "configRequirements/read",
            await self._request_live(
                "configRequirements/read",
                {},
                timeout_s=self._request_timeout_s,
            ),
        )

    @staticmethod
    def _audit_config_requirements(result: Mapping[str, Any]) -> None:
        if set(result) != {"requirements"} or result.get("requirements") is not None:
            raise CodexSubscriptionUnavailable(
                "Codex subscription voice refuses managed configuration requirements."
            )

    async def _ensure_sink_started(self) -> None:
        if self._sink_server is not None:
            return

        async def reject_connection(
            _reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                await writer.drain()
            except (ConnectionError, OSError):  # Diagnostic peer may close before reply.
                pass
            finally:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

        try:
            server = await asyncio.start_server(reject_connection, "127.0.0.1", 0)
        except OSError as exc:
            raise CodexSubscriptionUnavailable(
                "The local Codex billing-isolation sink could not be started."
            ) from exc
        sockets = server.sockets or ()
        if len(sockets) != 1:
            server.close()
            await server.wait_closed()
            raise CodexSubscriptionUnavailable(
                "The local Codex billing-isolation sink is unavailable."
            )
        address = sockets[0].getsockname()
        if not isinstance(address, tuple) or len(address) < 2:
            server.close()
            await server.wait_closed()
            raise CodexSubscriptionUnavailable(
                "The local Codex billing-isolation sink returned an invalid address."
            )
        self._sink_server = server
        self._sink_base_url = f"http://127.0.0.1:{int(address[1])}/v1"

    def _audit_effective_config(
        self,
        result: Mapping[str, Any],
    ) -> None:
        entries = _config_layer_entries(result)
        layers = tuple(config for _name, config in entries)
        session_layers = [config for name, config in entries if name == "sessionFlags"]
        if len(session_layers) != 1:
            raise CodexSubscriptionUnavailable(
                "Codex session configuration provenance could not be verified."
            )
        for name, config in entries:
            if name != "sessionFlags" and config:
                raise CodexSubscriptionUnavailable(
                    "Codex subscription voice refuses non-empty user, project, system, "
                    "MDM, enterprise, profile, or legacy configuration layers."
                )

        expected_paths: dict[tuple[str, ...], Any] = {
            ("analytics", "enabled"): False,
            ("approval_policy",): "never",
            ("cli_auth_credentials_store",): "file",
            ("chatgpt_base_url",): _OFFICIAL_CHATGPT_BASE,
            ("history", "persistence"): "none",
            ("include_apps_instructions",): False,
            ("include_collaboration_mode_instructions",): False,
            ("include_environment_context",): False,
            ("include_permissions_instructions",): False,
            ("memories", "dedicated_tools"): False,
            ("memories", "generate_memories"): False,
            ("memories", "use_memories"): False,
            ("log_dir",): self._log_dir(),
            ("model_instructions_file",): self._safe_instructions_file(),
            ("notify",): [],
            ("openai_base_url",): _OFFICIAL_OPENAI_API_BASE,
            ("orchestrator", "mcp", "enabled"): False,
            ("orchestrator", "skills", "enabled"): False,
            ("otel", "exporter"): "none",
            ("otel", "log_user_prompt"): False,
            ("otel", "metrics_exporter"): "none",
            ("otel", "trace_exporter"): "none",
            ("project_doc_max_bytes",): 0,
            ("sandbox_mode",): "read-only",
            ("sqlite_home",): self._sqlite_home(),
            ("skills", "bundled", "enabled"): False,
            ("skills", "include_instructions"): False,
            ("tools", "web_search"): False,
        }
        for feature in _DISABLED_APP_SERVER_FEATURES:
            expected_paths[("features", feature)] = False
        if self._purpose == "realtime":
            expected_paths.update(
                {
                    ("experimental_realtime_webrtc_call_base_url",): (
                        _OFFICIAL_CHATGPT_CODEX_BASE
                    ),
                    ("experimental_realtime_start_instructions",): "",
                    ("experimental_realtime_ws_backend_prompt",): "",
                    ("experimental_realtime_ws_base_url",): (
                        _OFFICIAL_OPENAI_REALTIME_BASE
                    ),
                    ("model_provider",): self._transport_provider_id,
                    ("experimental_compact_prompt_file",): (
                        self._compact_prompt_file()
                    ),
                    ("model_catalog_json",): self._model_catalog_file(),
                    ("features", "realtime_conversation"): True,
                }
            )
            provider_prefix = ("model_providers", self._transport_provider_id)
            expected_paths.update(
                {
                    (*provider_prefix, "base_url"): self._provider_sink_base_url(),
                    (*provider_prefix, "name"): "OpenAI ChatGPT subscription voice",
                    (*provider_prefix, "requires_openai_auth"): True,
                    (*provider_prefix, "request_max_retries"): 0,
                    (*provider_prefix, "supports_standalone_web_search"): False,
                    (*provider_prefix, "supports_websockets"): False,
                    (*provider_prefix, "stream_max_retries"): 0,
                    (*provider_prefix, "wire_api"): "responses",
                }
            )
        else:
            expected_paths.update(
                {
                    ("model_provider",): _TEXT_MODEL_PROVIDER,
                    ("features", "realtime_conversation"): False,
                }
            )
        if sys.platform == "win32":
            expected_paths[("windows", "sandbox")] = "unelevated"
        for path, expected in expected_paths.items():
            if _layer_value(layers, path) != expected:
                raise CodexSubscriptionUnavailable(
                    "Codex effective configuration failed the transport safety audit."
                )

        allowed_paths = set(expected_paths)

        def walk(value: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
            if isinstance(value, Mapping):
                leaves: list[tuple[str, ...]] = []
                for key, child in value.items():
                    leaves.extend(walk(child, (*prefix, str(key))))
                return leaves
            return [prefix]

        if any(path not in allowed_paths for path in walk(session_layers[0])):
            raise CodexSubscriptionUnavailable(
                "Codex session flags exposed an unapproved configuration surface."
            )

        raw_layers = result.get("layers")
        if not isinstance(raw_layers, list):
            raise CodexSubscriptionUnavailable(
                "Codex configuration provenance could not be verified."
            )
        session_metadata = [
            layer
            for layer in raw_layers
            if isinstance(layer, Mapping)
            and isinstance(layer.get("name"), Mapping)
            and layer["name"].get("type") == "sessionFlags"
        ]
        if len(session_metadata) != 1:
            raise CodexSubscriptionUnavailable(
                "Codex session configuration provenance could not be verified."
            )
        session_version = session_metadata[0].get("version")
        if not isinstance(session_version, str) or not session_version:
            raise CodexSubscriptionUnavailable(
                "Codex session configuration version could not be verified."
            )
        origins = result.get("origins")
        expected_origin_keys = {
            ".".join(path)
            for path, expected in expected_paths.items()
            if expected not in ([], {})
        }
        if not isinstance(origins, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex configuration origins failed the transport safety audit."
            )
        origin_keys = set(origins)
        aliases = _CODEX_CONFIG_ORIGIN_PATH_ALIASES.get(
            self._trusted_binary_version or "", {}
        )
        canonical_origin_keys = {aliases.get(key, key) for key in origin_keys}
        if (
            len(canonical_origin_keys) != len(origin_keys)
            or canonical_origin_keys != expected_origin_keys
        ):
            raise CodexSubscriptionUnavailable(
                "Codex configuration origins failed the transport safety audit."
            )
        for metadata in origins.values():
            source = metadata.get("name") if isinstance(metadata, Mapping) else None
            if (
                not isinstance(source, Mapping)
                or source.get("type") != "sessionFlags"
                or metadata.get("version") != session_version
            ):
                raise CodexSubscriptionUnavailable(
                    "A Codex safety setting came from an unapproved configuration layer."
                )

        public_config = result.get("config")
        if not isinstance(public_config, Mapping):
            raise CodexSubscriptionUnavailable(
                "Codex effective configuration could not be verified."
            )
        public_expected = {
            "analytics": {"enabled": False},
            "approval_policy": "never",
            "model_provider": (
                self._transport_provider_id
                if self._purpose == "realtime"
                else _TEXT_MODEL_PROVIDER
            ),
            "sandbox_mode": "read-only",
        }
        for key, expected in public_expected.items():
            if public_config.get(key) != expected:
                raise CodexSubscriptionUnavailable(
                    "Codex managed configuration overrode a required safety setting."
                )

    async def _spawn(
        self,
        binary_path: str,
    ) -> None:
        command = [binary_path, "app-server", "--strict-config"]
        command.extend(
            (
                "--enable" if self._purpose == "realtime" else "--disable",
                "realtime_conversation",
            )
        )
        for feature in _DISABLED_APP_SERVER_FEATURES:
            command.extend(("--disable", feature))
        command.extend(
            (
                "-c",
                "notify=[]",
                "-c",
                'openai_base_url="https://api.openai.com/v1"',
                "-c",
                'chatgpt_base_url="https://chatgpt.com/backend-api/"',
                "-c",
                "tools.web_search=false",
                "-c",
                'history.persistence="none"',
                "-c",
                "include_apps_instructions=false",
                "-c",
                "include_collaboration_mode_instructions=false",
                "-c",
                "include_environment_context=false",
                "-c",
                "include_permissions_instructions=false",
                "-c",
                "memories.dedicated_tools=false",
                "-c",
                "memories.generate_memories=false",
                "-c",
                "memories.use_memories=false",
                "-c",
                "orchestrator.mcp.enabled=false",
                "-c",
                "orchestrator.skills.enabled=false",
                "-c",
                "skills.bundled.enabled=false",
                "-c",
                "skills.include_instructions=false",
                "-c",
                (
                "model_instructions_file="
                    f"{json.dumps(self._safe_instructions_file())}"
                ),
                "-c",
                f"log_dir={json.dumps(self._log_dir())}",
                "-c",
                f"sqlite_home={json.dumps(self._sqlite_home())}",
                "-c",
                "analytics.enabled=false",
                "-c",
                'cli_auth_credentials_store="file"',
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                'otel.exporter="none"',
                "-c",
                'otel.trace_exporter="none"',
                "-c",
                'otel.metrics_exporter="none"',
                "-c",
                "otel.log_user_prompt=false",
                "-c",
                'approval_policy="never"',
                "-c",
                'sandbox_mode="read-only"',
            )
        )
        if self._purpose == "realtime":
            command.extend(
                (
                    "-c",
                    'experimental_realtime_ws_base_url="https://api.openai.com/v1"',
                    "-c",
                    "experimental_realtime_webrtc_call_base_url="
                    '"https://chatgpt.com/backend-api/codex"',
                    "-c",
                    'experimental_realtime_start_instructions=""',
                    "-c",
                    'experimental_realtime_ws_backend_prompt=""',
                    "-c",
                    "experimental_compact_prompt_file="
                    f"{json.dumps(self._compact_prompt_file())}",
                    "-c",
                    f"model_catalog_json={json.dumps(self._model_catalog_file())}",
                )
            )
            provider = f"model_providers.{self._transport_provider_id}"
            command.extend(
                (
                "-c",
                f'{provider}.name="OpenAI ChatGPT subscription voice"',
                "-c",
                f"{provider}.base_url={json.dumps(self._provider_sink_base_url())}",
                "-c",
                f'{provider}.wire_api="responses"',
                "-c",
                f"{provider}.requires_openai_auth=true",
                "-c",
                f"{provider}.supports_websockets=false",
                "-c",
                f"{provider}.supports_standalone_web_search=false",
                "-c",
                f"{provider}.request_max_retries=0",
                "-c",
                f"{provider}.stream_max_retries=0",
                "-c",
                f'model_provider="{self._transport_provider_id}"',
                )
            )
        else:
            command.extend(("-c", f'model_provider="{_TEXT_MODEL_PROVIDER}"'))
        if sys.platform == "win32":
            command.extend(("-c", 'windows.sandbox="unelevated"'))
        kwargs: dict[str, Any] = {
            "env": dict(self._child_environment),
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": self._safe_thread_cwd(),
        }
        lifeline_read_fd: int | None = None
        lifeline_write_fd: int | None = None
        tree = make_process_tree("codex-app-server")
        if not bool(getattr(tree, "supports_containment", False)):
            tree.close()
            raise CodexSubscriptionUnavailable(
                "Codex app-server process-tree containment is unavailable."
            )
        try:
            if sys.platform != "win32":
                # Off-loop: the login mutex this helper takes can be held by a
                # probe thread across filesystem work; the audio loop must not
                # wait on it.
                profile_lock_fds = await asyncio.to_thread(
                    _subscription_transport_pass_fds, self
                )
                lifeline_read_fd, lifeline_write_fd = os.pipe()
                os.set_inheritable(lifeline_read_fd, True)
                os.set_inheritable(lifeline_write_fd, False)
                keep_fd_args: list[str] = []
                for descriptor in profile_lock_fds:
                    # The lifeline supervisor spawns the REAL child with
                    # close_fds, so every descriptor above stdio stopped here
                    # unless it is re-declared. Without these pairs the profile
                    # lock was held by the supervisor, not by the app-server
                    # the docstring promises holds it.
                    keep_fd_args.extend(("--keep-fd", str(descriptor)))
                command = [
                    sys.executable,
                    "-I",
                    _CHILD_LIFELINE_SCRIPT,
                    str(lifeline_read_fd),
                    *keep_fd_args,
                    "--",
                    *command,
                ]
                kwargs["start_new_session"] = True
                kwargs["pass_fds"] = (*profile_lock_fds, lifeline_read_fd)
        except BaseException:
            tree.close()
            for descriptor in (lifeline_read_fd, lifeline_write_fd):
                if descriptor is not None:
                    with suppress(OSError):
                        os.close(descriptor)
            raise
        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    creationflags=_windows_creationflags(allow_breakaway=True),
                    **kwargs,
                )
            except PermissionError:
                if sys.platform != "win32":
                    raise
                log.warning(
                    "Codex app-server breakaway was denied; retrying with no-window "
                    "process flags"
                )
                process = await asyncio.create_subprocess_exec(
                    *command,
                    creationflags=_windows_creationflags(allow_breakaway=False),
                    **kwargs,
                )
        except BaseException:
            tree.close()
            if lifeline_write_fd is not None:
                with suppress(OSError):
                    os.close(lifeline_write_fd)
            raise
        finally:
            if lifeline_read_fd is not None:
                with suppress(OSError):
                    os.close(lifeline_read_fd)

        self._process = process
        self._process_tree = tree
        self._lifeline_write_fd = lifeline_write_fd
        try:
            tree.assign(process.pid)
        except Exception as exc:  # noqa: BLE001 - containment is mandatory
            self._process = None
            self._process_tree = None
            self._close_lifeline()
            with suppress(ProcessLookupError, OSError):
                process.kill()
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_TIMEOUT_S)
            tree.close()
            raise CodexSubscriptionUnavailable(
                "Codex app-server process containment could not be established."
            ) from exc
        self._reader_task = asyncio.create_task(
            self._reader_loop(process), name="codex-app-server-stdout"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(process), name="codex-app-server-stderr"
        )
        log.info("Codex app-server process started; account verification pending")

    def _close_lifeline(self) -> None:
        descriptor = self._lifeline_write_fd
        self._lifeline_write_fd = None
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """Send one request and await only its own response future."""
        await self.ensure_started()
        return await self._request_live(method, params, timeout_s=timeout_s)

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        """Send a client notification after lazily starting the process."""
        await self.ensure_started()
        await self._notify_live(method, params)

    async def _request_live(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        *,
        timeout_s: float | None,
    ) -> Any:
        process = self._require_live_process()
        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        try:
            await self._write_frame(process, message)
            budget = self._request_timeout_s if timeout_s is None else timeout_s
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=budget)
            except TimeoutError as exc:
                raise CodexAppServerTimeout(
                    f"Codex app-server request {method} timed out."
                ) from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _notify_live(self, method: str, params: Mapping[str, Any] | None) -> None:
        process = self._require_live_process()
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._write_frame(process, message)

    def _require_live_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is None or process.returncode is not None:
            raise CodexAppServerDisconnected("Codex app-server is not running.")
        return process

    async def _write_frame(
        self, process: asyncio.subprocess.Process, message: Mapping[str, Any]
    ) -> None:
        try:
            payload = (
                json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise CodexAppServerError("Codex app-server request is not JSON-safe.") from exc

        async with self._write_lock:
            if process is not self._process or process.returncode is not None:
                raise CodexAppServerDisconnected("Codex app-server exited.")
            stdin = process.stdin
            if stdin is None:
                raise CodexAppServerDisconnected("Codex app-server stdin is unavailable.")
            try:
                stdin.write(payload)
                await stdin.drain()
            except (BrokenPipeError, ConnectionError, OSError) as exc:
                # Identity guard (mirror of _reader_loop's finally): a drain
                # that outlived a teardown-and-restart must not close the NEW
                # process or release the successor's reservation.
                if process is self._process:
                    await self._close_process(
                        CodexAppServerDisconnected("Codex app-server input closed."),
                        expected=False,
                    )
                raise CodexAppServerDisconnected("Codex app-server input closed.") from exc

    async def _reader_loop(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stdout
        if stream is None:
            if process is self._process:
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server stdout is unavailable."),
                    expected=False,
                )
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    log.warning(
                        "Codex app-server emitted an invalid JSONL frame (%d bytes; "
                        "content redacted)",
                        len(line),
                    )
                    continue
                if not isinstance(message, dict):
                    log.warning("Codex app-server emitted a non-object JSONL frame")
                    continue
                await self._handle_message(process, message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert transport failure
            log.warning("Codex app-server stdout reader failed (%s)", type(exc).__name__)
        finally:
            if process is self._process:
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server output closed."),
                    expected=False,
                )

    async def _stderr_loop(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                # Stderr can contain prompts, paths, account labels, or upstream
                # request details.  Record that diagnostics exist without
                # copying their contents into Jarvis's world-readable logs.
                log.debug(
                    "Codex app-server diagnostic received (%d bytes; content redacted)",
                    len(line),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - stderr is diagnostic only
            log.warning("Codex app-server stderr reader failed (%s)", type(exc).__name__)

    async def _handle_message(
        self, process: asyncio.subprocess.Process, message: dict[str, Any]
    ) -> None:
        if process is not self._process:
            return
        request_id = message.get("id")
        method = message.get("method")
        if request_id is not None and isinstance(method, str):
            await self._deny_server_request(process, request_id, method)
            return
        if request_id is not None and ("result" in message or "error" in message):
            if not isinstance(request_id, int):
                return
            pending = self._pending.get(request_id)
            if pending is None:
                log.debug("Codex app-server returned an unknown request id")
                return
            pending_method, future = pending
            if future.done():
                return
            if "error" in message:
                error = message.get("error")
                code = error.get("code") if isinstance(error, Mapping) else None
                http_status, error_type = _rpc_error_detail(error)
                future.set_exception(
                    CodexAppServerRPCError(
                        pending_method,
                        code if isinstance(code, int) else None,
                        http_status=http_status,
                        error_type=error_type,
                    )
                )
            else:
                future.set_result(message.get("result"))
            return
        if isinstance(method, str):
            params = message.get("params")
            safe_params = dict(params) if isinstance(params, Mapping) else {}
            thread_id = _thread_id_from_params(safe_params)
            if thread_id is None:
                return
            notification = CodexAppServerNotification(method, safe_params)
            for subscription in tuple(self._subscriptions.get(thread_id, ())):
                subscription._publish(notification)

    async def _deny_server_request(
        self, process: asyncio.subprocess.Process, request_id: Any, method: str
    ) -> None:
        """Fail closed for every server-initiated action request."""
        if method in _DECLINE_REQUEST_METHODS:
            response: dict[str, Any] = {
                "id": request_id,
                "result": {"decision": "decline"},
            }
        elif method in _DYNAMIC_TOOL_REQUEST_METHODS:
            response = {
                "id": request_id,
                "result": {"contentItems": [], "success": False},
            }
        else:
            response = {
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": "Personal Jarvis does not permit server-initiated actions.",
                },
            }
        log.warning("Denied Codex app-server request method %s", method)
        await self._write_frame(process, response)

    def subscribe(
        self,
        thread_id: str,
        *,
        max_queue_size: int = _DEFAULT_NOTIFICATION_QUEUE_SIZE,
    ) -> CodexNotificationSubscription:
        """Register a local broadcast subscription before starting a session."""
        self._assert_owner_loop()
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be at least one")
        subscription = CodexNotificationSubscription(self, thread_id, max_queue_size=max_queue_size)
        self._subscriptions.setdefault(thread_id, set()).add(subscription)
        return subscription

    def _remove_subscription(self, subscription: CodexNotificationSubscription) -> None:
        subscribers = self._subscriptions.get(subscription.thread_id)
        if not subscribers:
            return
        subscribers.discard(subscription)
        if not subscribers:
            self._subscriptions.pop(subscription.thread_id, None)

    def _safe_thread_cwd(self) -> str:
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport workspace is unavailable."
            )
        return str(self._workspace.root)

    def _safe_instructions_file(self) -> str:
        """Return the isolated transport-only model instructions file."""
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport instruction boundary is unavailable."
            )
        return str(self._workspace.instructions)

    def _compact_prompt_file(self) -> str:
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport compact-prompt boundary is unavailable."
            )
        return str(self._workspace.compact_prompt)

    def _model_catalog_file(self) -> str:
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport model catalog is unavailable."
            )
        return str(self._workspace.model_catalog)

    def _sqlite_home(self) -> str:
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport SQLite boundary is unavailable."
            )
        return str(self._workspace.sqlite_home)

    def _log_dir(self) -> str:
        if self._workspace is None:
            raise CodexSubscriptionUnavailable(
                "Codex transport log boundary is unavailable."
            )
        return str(self._workspace.log_dir)

    def _provider_sink_base_url(self) -> str:
        if not self._sink_base_url:
            raise CodexSubscriptionUnavailable(
                "Codex billing-isolation sink is unavailable."
            )
        return self._sink_base_url

    def _audit_thread_start_response(self, result: Mapping[str, Any]) -> None:
        thread = result.get("thread")
        sandbox = result.get("sandbox")
        expected_cwd = os.path.normcase(os.path.abspath(self._safe_thread_cwd()))
        response_cwd = result.get("cwd")
        thread_cwd = thread.get("cwd") if isinstance(thread, Mapping) else None
        instruction_sources = result.get("instructionSources", [])
        if (
            not isinstance(thread, Mapping)
            or not isinstance(thread.get("id"), str)
            or not thread["id"]
            or thread.get("ephemeral") is not True
            or thread.get("modelProvider") != self._transport_provider_id
            or not isinstance(response_cwd, str)
            or os.path.normcase(os.path.abspath(response_cwd)) != expected_cwd
            or not isinstance(thread_cwd, str)
            or os.path.normcase(os.path.abspath(thread_cwd)) != expected_cwd
            or result.get("approvalPolicy") != "never"
            or result.get("model") != "gpt-5.1-codex"
            or result.get("modelProvider") != self._transport_provider_id
            or sandbox != {"type": "readOnly", "networkAccess": False}
            or instruction_sources != []
        ):
            raise CodexSubscriptionUnavailable(
                "Codex returned an unsafe or unverifiable voice thread boundary."
            )

    def _audit_text_thread_start_response(
        self,
        result: Mapping[str, Any],
        *,
        expected_model: str | None,
    ) -> None:
        """Verify the stable text thread kept the subscription safety floor."""
        thread = result.get("thread")
        sandbox = result.get("sandbox")
        expected_cwd = os.path.normcase(os.path.abspath(self._safe_thread_cwd()))
        response_cwd = result.get("cwd")
        thread_cwd = thread.get("cwd") if isinstance(thread, Mapping) else None
        response_model = result.get("model")
        thread_model = thread.get("model") if isinstance(thread, Mapping) else None
        model_ok = (
            isinstance(response_model, str)
            and bool(response_model)
            and isinstance(thread_model, str)
            and bool(thread_model)
        )
        if expected_model:
            model_ok = (
                model_ok
                and response_model == expected_model
                and thread_model == expected_model
            )
        if (
            not isinstance(thread, Mapping)
            or not isinstance(thread.get("id"), str)
            or not thread["id"]
            or thread.get("ephemeral") is not True
            or thread.get("modelProvider") != _TEXT_MODEL_PROVIDER
            or not isinstance(response_cwd, str)
            or os.path.normcase(os.path.abspath(response_cwd)) != expected_cwd
            or not isinstance(thread_cwd, str)
            or os.path.normcase(os.path.abspath(thread_cwd)) != expected_cwd
            or result.get("approvalPolicy") != "never"
            or result.get("modelProvider") != _TEXT_MODEL_PROVIDER
            or not model_ok
            or sandbox != {"type": "readOnly", "networkAccess": False}
            or result.get("instructionSources", []) != []
        ):
            raise CodexSubscriptionUnavailable(
                "Codex returned an unsafe or unverifiable text thread boundary."
            )

    async def text_thread_start(
        self,
        *,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Start one isolated stable App Server text thread for subscription voice."""
        if self._purpose != "text":
            raise CodexSubscriptionUnavailable(
                "A realtime Codex transport cannot create text-generation threads."
            )
        selected_model = str(model or "").strip()
        if selected_model and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", selected_model
        ):
            raise CodexSubscriptionUnavailable(
                "Codex subscription model id contains unsupported characters."
            )
        await self.ensure_started()
        session_config: dict[str, Any] = {
            "analytics": {"enabled": False},
            "cli_auth_credentials_store": "file",
            "chatgpt_base_url": _OFFICIAL_CHATGPT_BASE,
            "features": {
                **{feature: False for feature in _DISABLED_APP_SERVER_FEATURES},
                "realtime_conversation": False,
            },
            "history": {"persistence": "none"},
            "include_apps_instructions": False,
            "include_collaboration_mode_instructions": False,
            "include_environment_context": False,
            "include_permissions_instructions": False,
            "memories": {
                "dedicated_tools": False,
                "generate_memories": False,
                "use_memories": False,
            },
            "log_dir": self._log_dir(),
            "model_instructions_file": self._safe_instructions_file(),
            "model_provider": _TEXT_MODEL_PROVIDER,
            "notify": [],
            "openai_base_url": _OFFICIAL_OPENAI_API_BASE,
            "orchestrator": {
                "mcp": {"enabled": False},
                "skills": {"enabled": False},
            },
            "otel": {
                "exporter": "none",
                "log_user_prompt": False,
                "metrics_exporter": "none",
                "trace_exporter": "none",
            },
            "project_doc_max_bytes": 0,
            "sandbox_mode": "read-only",
            "skills": {
                "bundled": {"enabled": False},
                "include_instructions": False,
            },
            "sqlite_home": self._sqlite_home(),
            "tools": {"web_search": False},
        }
        params: dict[str, Any] = {
            "allowProviderModelFallback": False,
            "approvalPolicy": "never",
            "baseInstructions": (
                str(base_instructions or "").strip() or _TEXT_BASE_INSTRUCTIONS
            ),
            "config": session_config,
            "cwd": self._safe_thread_cwd(),
            "developerInstructions": (
                str(developer_instructions or "").strip()
                or _TEXT_DEVELOPER_INSTRUCTIONS
            ),
            "dynamicTools": None,
            "environments": [],
            "ephemeral": True,
            "modelProvider": _TEXT_MODEL_PROVIDER,
            "runtimeWorkspaceRoots": [],
            "sandbox": "read-only",
            "selectedCapabilityRoots": [],
        }
        if selected_model:
            params["model"] = selected_model

        async with self._thread_start_lock:
            await self.ensure_started()
            live_process = self._process
            audit_is_fresh = (
                live_process is not None
                and self._startup_audit_process is live_process
            )
            self._startup_audit_process = None
            try:
                if not audit_is_fresh:
                    await asyncio.to_thread(
                        _validated_subscription_home,
                        create=False,
                        require_marker=True,
                        trusted_binary_path=self._trusted_binary_path,
                    )
                    await self._verify_live_chatgpt_account()
                    self._audit_config_requirements(
                        await self._read_config_requirements()
                    )
                    self._audit_effective_config(await self._read_effective_config())
            except CodexSubscriptionUnavailable as exc:
                if isinstance(exc, CodexSubscriptionPlanUnsupported):
                    await asyncio.shield(
                        asyncio.to_thread(
                            set_codex_subscription_activation_block, str(exc)
                        )
                    )
                error = type(exc)(str(exc))
                await self._close_process(
                    CodexAppServerDisconnected(
                        "Codex app-server safety state changed."
                    ),
                    expected=False,
                )
                raise error from None
            result = _result_dict(
                "thread/start",
                await self._request_live(
                    "thread/start", params, timeout_s=self._request_timeout_s
                ),
            )
            try:
                self._audit_text_thread_start_response(
                    result, expected_model=selected_model or None
                )
            except CodexSubscriptionUnavailable:
                await self._close_process(
                    CodexAppServerDisconnected(
                        "Codex app-server returned an unsafe text thread boundary."
                    ),
                    expected=False,
                )
                raise
            return result

    async def thread_start(
        self,
        *,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        cwd: str | Path | None = None,
        model: str | None = None,
        ephemeral: bool = True,
        extra: Mapping[str, Any] | None = None,
        # EXPLICIT opt-in for one scenario only: a failed host-candidate media
        # path retrying with STUN may ride an audit that passed within the TTL
        # instead of running a third identical copy. Every ordinary (warm)
        # thread start keeps the full re-audit — the account can switch under
        # a shared process between calls, and that doctrine stays intact.
        ride_recent_audit: bool = False,
    ) -> dict[str, Any]:
        """Start a verified ChatGPT-only transport thread outside the workspace."""
        if self._purpose != "realtime":
            raise CodexSubscriptionUnavailable(
                "A text Codex transport cannot create realtime voice threads."
            )
        # cwd/model/ephemeral stay caller-proof: the audit below pins them to
        # the safe values, so a caller cannot widen the boundary. Instructions
        # are different — they are the voice's identity, one-speaker rule and
        # language rule. Discarding them here (as an earlier revision did)
        # silently shipped the "dumb pipe" transport text instead, so the live
        # model never learned it may request handoffs and answered greetings
        # out of persona. The transport constants remain the fail-closed floor
        # whenever a caller passes nothing.
        del cwd, model, ephemeral
        base_text = (
            str(base_instructions or "").strip() or _TRANSPORT_BASE_INSTRUCTIONS
        )
        developer_text = (
            str(developer_instructions or "").strip()
            or _TRANSPORT_DEVELOPER_INSTRUCTIONS
        )
        await self.ensure_started()
        if extra:
            raise CodexSubscriptionUnavailable(
                "Custom Codex thread/start fields are disabled for subscription voice."
            )
        params: dict[str, Any] = {}
        params.update(
            {
                "allowProviderModelFallback": False,
                "approvalPolicy": "never",
                "baseInstructions": base_text,
                "config": {
                    "analytics": {"enabled": False},
                    "cli_auth_credentials_store": "file",
                    "chatgpt_base_url": _OFFICIAL_CHATGPT_BASE,
                    "experimental_realtime_webrtc_call_base_url": (
                        _OFFICIAL_CHATGPT_CODEX_BASE
                    ),
                    "experimental_realtime_start_instructions": "",
                    "experimental_realtime_ws_backend_prompt": (
                        ""
                    ),
                    "experimental_realtime_ws_base_url": (
                        _OFFICIAL_OPENAI_REALTIME_BASE
                    ),
                    "features": {
                        **{
                            feature: False
                            for feature in _DISABLED_APP_SERVER_FEATURES
                        },
                        "realtime_conversation": True,
                    },
                    "history": {"persistence": "none"},
                    "include_apps_instructions": False,
                    "include_collaboration_mode_instructions": False,
                    "include_environment_context": False,
                    "include_permissions_instructions": False,
                    "memories": {
                        "dedicated_tools": False,
                        "generate_memories": False,
                        "use_memories": False,
                    },
                    "experimental_compact_prompt_file": self._compact_prompt_file(),
                    "log_dir": self._log_dir(),
                    "model_catalog_json": self._model_catalog_file(),
                    "model_instructions_file": self._safe_instructions_file(),
                    "model_provider": self._transport_provider_id,
                    "model_providers": {
                        self._transport_provider_id: {
                            "base_url": self._provider_sink_base_url(),
                            "name": "OpenAI ChatGPT subscription voice",
                            "request_max_retries": 0,
                            "requires_openai_auth": True,
                            "stream_max_retries": 0,
                            "supports_standalone_web_search": False,
                            "supports_websockets": False,
                            "wire_api": "responses",
                        }
                    },
                    "notify": [],
                    "openai_base_url": _OFFICIAL_OPENAI_API_BASE,
                    "orchestrator": {
                        "mcp": {"enabled": False},
                        "skills": {"enabled": False},
                    },
                    "otel": {
                        "exporter": "none",
                        "log_user_prompt": False,
                        "metrics_exporter": "none",
                        "trace_exporter": "none",
                    },
                    "project_doc_max_bytes": 0,
                    "sandbox_mode": "read-only",
                    "sqlite_home": self._sqlite_home(),
                    "skills": {
                        "bundled": {"enabled": False},
                        "include_instructions": False,
                    },
                    "tools": {"web_search": False},
                },
                "cwd": self._safe_thread_cwd(),
                "developerInstructions": developer_text,
                "dynamicTools": None,
                "environments": [],
                "ephemeral": True,
                "model": "gpt-5.1-codex",
                "modelProvider": self._transport_provider_id,
                "runtimeWorkspaceRoots": [],
                "sandbox": "read-only",
                "selectedCapabilityRoots": [],
            }
        )
        # account/read at process initialization is not durable: `codex login
        # --with-api-key` may switch the shared process while it remains alive.
        # Re-read immediately before EVERY subscription thread and serialize
        # this check with its thread/start frame.
        async with self._thread_start_lock:
            await self.ensure_started()
            # ...with ONE exception: a cold start that finished inside this
            # very call already ran the identical audit against the identical
            # process, and nothing but this coroutine has touched it since.
            # Repeating it cost three RPC round trips plus a profile walk (and
            # a ~100 MB re-hash on a cold memo) on every first call. The token
            # is consumed here, under the same lock that serializes the
            # thread/start frame, so exactly one thread start can ride it and
            # every WARM start still re-audits.
            live_process = self._process
            # The wake ride minted by ``prime_startup_audit`` is one-shot:
            # reading it here consumes it whether or not it is still fresh,
            # so a second wake on the same warm process always re-audits.
            warm_ride_process = self._warm_audit_process
            warm_ride_at = self._warm_audit_at
            self._warm_audit_process = None
            self._warm_audit_at = 0.0
            audit_is_fresh = live_process is not None and (
                self._startup_audit_process is live_process
                or (
                    warm_ride_process is live_process
                    and time.monotonic() - warm_ride_at <= _STARTUP_AUDIT_TTL_S
                )
                or (
                    ride_recent_audit
                    and getattr(self, "_audited_process", None) is live_process
                    and time.monotonic()
                    - getattr(self, "_startup_audit_at", 0.0)
                    <= _STARTUP_AUDIT_TTL_S
                )
            )
            self._startup_audit_process = None
            try:
                if not audit_is_fresh:
                    await asyncio.to_thread(
                        _validated_subscription_home,
                        create=False,
                        require_marker=True,
                        trusted_binary_path=self._trusted_binary_path,
                    )
                    await self._verify_live_chatgpt_account()
                    self._audit_config_requirements(
                        await self._read_config_requirements()
                    )
                    self._audit_effective_config(
                        await self._read_effective_config()
                    )
                    # This full audit is byte-identical to the cold-start one;
                    # stamp it so a STUN media-path retry seconds from now can
                    # ride it instead of running a THIRD copy.
                    self._audited_process = live_process
                    self._startup_audit_at = time.monotonic()
            except CodexSubscriptionUnavailable as exc:
                if isinstance(exc, CodexSubscriptionPlanUnsupported):
                    # The WARM call path re-judges the live account before
                    # every thread — the same recording rule as the cold
                    # start applies, or a plan that changed between calls
                    # would fail every call while every surface says ready.
                    await asyncio.shield(
                        asyncio.to_thread(
                            set_codex_subscription_activation_block, str(exc)
                        )
                    )
                # Preserve the subclass: callers and future recorders may
                # discriminate on it.
                error = type(exc)(str(exc))
                await self._close_process(
                    CodexAppServerDisconnected(
                        "Codex app-server safety state changed."
                    ),
                    expected=False,
                )
                raise error from None
            result = _result_dict(
                "thread/start",
                await self._request_live(
                    "thread/start", params, timeout_s=self._request_timeout_s
                ),
            )
            try:
                self._audit_thread_start_response(result)
            except CodexSubscriptionUnavailable:
                await self._close_process(
                    CodexAppServerDisconnected(
                        "Codex app-server returned an unsafe thread boundary."
                    ),
                    expected=False,
                )
                raise
            return result

    async def turn_start(
        self,
        thread_id: str,
        input_items: Sequence[Mapping[str, Any]] | str,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a future text turn while retaining the thread safety floor."""
        if isinstance(input_items, str):
            rendered_input: list[dict[str, Any]] = [{"type": "text", "text": input_items}]
        else:
            rendered_input = [dict(item) for item in input_items]
        params = dict(extra or {})
        params.update(
            {
                "approvalPolicy": "never",
                "environments": [],
                "input": rendered_input,
                "threadId": thread_id,
            }
        )
        return _result_dict("turn/start", await self.request("turn/start", params))

    async def _teardown_request(
        self, method: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Send a teardown RPC, but never START a process just to send it.

        ``request`` lazily starts app-server, which is right for real work and
        wrong for cleanup: when the process is already gone, so is the
        ephemeral thread this call would tidy up, and the plain ``request``
        path paid a 15-25 s cold start only to have the caller's short cleanup
        budget expire and poison the client. Nothing is hidden — the skip is
        logged, and a LIVE process still gets the real RPC and its real error.
        """
        if not self.running:
            log.debug(
                "Skipping Codex %s: the app-server process is already gone",
                method,
            )
            return {}
        return _result_dict(
            method, await self._request_live(method, params, timeout_s=None)
        )

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        """Interrupt one exact app-server turn by its schema-defined ids."""
        return await self._teardown_request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    async def thread_unsubscribe(self, thread_id: str) -> dict[str, Any]:
        """Unload a completed ephemeral voice thread from app-server."""
        return await self._teardown_request(
            "thread/unsubscribe", {"threadId": thread_id}
        )

    def realtime_start_instructions_supported(self) -> bool:
        """Whether ``realtimeStartInstructions`` will actually be transmitted.

        Cheap, synchronous capability probe for the adapter layer, derived
        from the same trusted-binary gate ``realtime_start`` applies before
        sending any 0.147-audited field: ``True`` only when this client is
        bound to the audited 0.147.0 binary, whose schema accepts the field
        (per call, ``realtime_start`` additionally requires the audited v3
        protocol). This is the adapter's basis for deciding whether to blank
        its fallback prompt — when the field would be withheld, the fallback
        must stay in place instead.
        """
        return self._trusted_binary_version == _SUPPORTED_CODEX_VERSION

    async def realtime_start(
        self,
        thread_id: str,
        *,
        output_modality: str = "audio",
        offer_sdp: str | None = None,
        prompt: str | None = None,
        trusted_prompt: str | None = None,
        initial_items: Sequence[Mapping[str, Any]] | None = None,
        voice: str | None = None,
        version: str | None = None,
        model: str | None = None,
        include_startup_context: bool | None = None,
        client_managed_handoffs: bool | None = None,
        realtime_start_instructions: str | None = None,
        realtime_end_instructions: str | None = None,
        codex_responses_as_items: bool | None = None,
        codex_response_item_prefix: str | None = None,
        codex_response_handoff_mode: str | None = None,
        codex_response_handoff_channel_prefixes: Sequence[str] | None = None,
        extra: Mapping[str, Any] | None = None,
        sdp_timeout_s: float = _DEFAULT_SDP_TIMEOUT_S,
    ) -> CodexRealtimeStartResult:
        """Start realtime and capture WebRTC SDP without a notification race."""
        if extra:
            raise CodexSubscriptionUnavailable(
                "Custom Codex realtime fields are disabled for subscription voice."
            )
        del prompt, include_startup_context, client_managed_handoffs
        if trusted_prompt is not None and not isinstance(trusted_prompt, str):
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup instructions are invalid."
            )
        start_prompt = (trusted_prompt or "").strip()
        if len(start_prompt.encode("utf-8")) > _MAX_REALTIME_START_PROMPT_BYTES:
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup instructions are too large."
            )
        startup_items = _validated_realtime_initial_items(initial_items)
        if startup_items and version != "v3":
            raise CodexSubscriptionUnavailable(
                "Codex realtime startup history requires the audited v3 protocol."
            )
        audited_optional = _validated_realtime_audited_optional(
            realtime_start_instructions=realtime_start_instructions,
            realtime_end_instructions=realtime_end_instructions,
            codex_responses_as_items=codex_responses_as_items,
            codex_response_item_prefix=codex_response_item_prefix,
            codex_response_handoff_mode=codex_response_handoff_mode,
            codex_response_handoff_channel_prefixes=codex_response_handoff_channel_prefixes,
        )
        params = {
            "clientManagedHandoffs": True,
            "includeStartupContext": False,
            "outputModality": output_modality,
            "prompt": start_prompt,
            "threadId": thread_id,
        }
        # Added in audited 0.147.0. Older approved binaries must not receive an
        # unknown field, while 0.147 explicitly suppresses the automatic
        # delegation acknowledgement that otherwise speaks over Jarvis.
        trusted_current_binary = (
            version == "v3"
            and self._trusted_binary_version == _SUPPORTED_CODEX_VERSION
        )
        if startup_items:
            if trusted_current_binary:
                params["initialItems"] = startup_items
            else:
                # Same rule as the audited fields below: an older approved
                # binary must never receive an unknown field. A thread rebuild
                # on 0.146 restores the call without its history instead of
                # failing the whole start on serde.
                log.warning(
                    "Codex realtime start dropped initialItems (%d startup-history "
                    "items) for this binary: the field exists only in the audited "
                    "0.147.0 release.",
                    len(startup_items),
                )
        if trusted_current_binary:
            params["delegationAckFiller"] = False
            # Same 0.147/v3 gate for the caller-provided audited fields: the
            # start/end instruction slots and the codexResponse* handoff
            # routing controls. Outside the gate they are omitted (and named
            # in the log) rather than sent — an older approved binary must
            # never receive unknown fields.
            params.update(audited_optional)
        elif audited_optional:
            log.info(
                "Codex realtime start omitted 0.147-audited fields for this binary/protocol: %s",
                ", ".join(sorted(audited_optional)),
            )
        optional: dict[str, Any] = {
            "version": version,
            "voice": voice,
        }
        for key, value in optional.items():
            if value is not None:
                params[key] = value
        if model is not None and model.strip():
            params["model"] = model.strip()
        if offer_sdp is not None:
            # The upstream v3 SDP parser reads line-by-line and answers
            # "Failed to parse offer: failed to unmarshal SDP: EOF" for an
            # offer whose LAST line has no terminator — which is exactly what
            # Jarvis's ingress validation produces (it strips the offer).
            # Proven live 2026-08-01: the identical offer passes WITH a
            # trailing CRLF and fails without one.
            if not offer_sdp.endswith("\n"):
                offer_sdp = offer_sdp + "\r\n"
            params["transport"] = {"type": "webrtc", "sdp": offer_sdp}

        sdp_subscription = self.subscribe(thread_id) if offer_sdp is not None else None
        try:
            response = _result_dict(
                "thread/realtime/start",
                await self.request("thread/realtime/start", params),
            )
            answer_sdp: str | None = None
            if sdp_subscription is not None:
                # Wait for the answer, but FAIL FAST on a realtime error or
                # close: the upstream refusal (for example the 403 that ended
                # the experimental v1 protocol) used to hide behind a blind
                # 15s timeout instead of reaching the user as its honest text.
                deadline = asyncio.get_running_loop().time() + sdp_timeout_s
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise CodexAppServerTimeout(
                            "Timed out waiting for the Codex WebRTC answer."
                        )
                    notification = await sdp_subscription.get(remaining)
                    if notification.method == "thread/realtime/sdp":
                        candidate = notification.params.get("sdp")
                        if not isinstance(candidate, str) or not candidate:
                            raise CodexAppServerError(
                                "Codex app-server returned an invalid WebRTC answer."
                            )
                        answer_sdp = candidate
                        break
                    if notification.method == "thread/realtime/error":
                        message = " ".join(
                            str(notification.params.get("message", "") or "").split()
                        )[:300]
                        raise CodexAppServerError(
                            "Codex realtime start failed: "
                            + (message or "unspecified realtime error")
                        )
                    if notification.method == "thread/realtime/closed":
                        raise CodexAppServerError(
                            "Codex realtime transport closed before answering."
                        )
            return CodexRealtimeStartResult(response=response, answer_sdp=answer_sdp)
        finally:
            if sdp_subscription is not None:
                sdp_subscription.close()

    async def realtime_append_audio(
        self,
        thread_id: str,
        *,
        data: str,
        sample_rate: int,
        num_channels: int,
        samples_per_channel: int | None = None,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        audio: dict[str, Any] = {
            "data": data,
            "sampleRate": int(sample_rate),
            "numChannels": int(num_channels),
        }
        if samples_per_channel is not None:
            audio["samplesPerChannel"] = int(samples_per_channel)
        if item_id is not None:
            audio["itemId"] = item_id
        return _result_dict(
            "thread/realtime/appendAudio",
            await self.request(
                "thread/realtime/appendAudio",
                {"threadId": thread_id, "audio": audio},
            ),
        )

    async def realtime_append_text(
        self, thread_id: str, text: str, *, role: str = "user"
    ) -> dict[str, Any]:
        return _result_dict(
            "thread/realtime/appendText",
            await self.request(
                "thread/realtime/appendText",
                {"threadId": thread_id, "text": text, "role": role},
            ),
        )

    async def realtime_append_speech(self, thread_id: str, text: str) -> dict[str, Any]:
        return _result_dict(
            "thread/realtime/appendSpeech",
            await self.request(
                "thread/realtime/appendSpeech",
                {"threadId": thread_id, "text": text},
            ),
        )

    async def realtime_list_voices(self, thread_id: str) -> dict[str, Any]:
        """Read the server's realtime voice roster (``RealtimeVoicesList``).

        Same JSON-RPC plumbing as the other realtime methods. A server build
        without ``thread/realtime/listVoices`` answers with a JSON-RPC error
        that surfaces as :class:`CodexAppServerRPCError`, so callers can fall
        back to their audited static roster instead of failing the session.
        """
        return _result_dict(
            "thread/realtime/listVoices",
            await self.request("thread/realtime/listVoices", {"threadId": thread_id}),
        )

    async def realtime_stop(self, thread_id: str) -> dict[str, Any]:
        return await self._teardown_request(
            "thread/realtime/stop", {"threadId": thread_id}
        )

    async def _close_process(self, error: CodexAppServerDisconnected, *, expected: bool) -> None:
        process = self._process
        if process is None:
            self._ready = False
            self._startup_audit_process = None
            self._audited_process = None
            self._startup_audit_at = 0.0
            self._warm_audit_process = None
            self._warm_audit_at = 0.0
            self._close_lifeline()
            if self._profile_transport_epoch is not None:
                # Epoch first, release off-loop and shielded: a cancellation
                # must neither skip the release nor drop the queued job (a
                # queued to_thread future CAN be cancelled before its thread
                # starts — shield keeps it alive).
                epoch = self._profile_transport_epoch
                self._profile_transport_epoch = None
                await asyncio.shield(
                    asyncio.to_thread(_release_subscription_transport, self, epoch)
                )
            return
        self._process = None
        self._ready = False
        self._startup_audit_process = None
        self._audited_process = None
        self._startup_audit_at = 0.0
        self._warm_audit_process = None
        self._warm_audit_at = 0.0
        tree = self._process_tree
        self._process_tree = None
        reader_task = self._reader_task
        stderr_task = self._stderr_task
        self._reader_task = None
        self._stderr_task = None
        # Captured SYNCHRONOUSLY with self._process = None: the reaping below
        # awaits for seconds without _start_lock, and a parallel ensure_started
        # may re-reserve (new epoch) and re-spawn (new lifeline FD) meanwhile.
        # Reading these fields late in the finally would tear down the NEW
        # life's state — closing the fresh child's lifeline kills its process
        # group on POSIX, and releasing the fresh epoch strips a live call's
        # profile reservation.
        lifeline_fd = self._lifeline_write_fd
        self._lifeline_write_fd = None
        release_epoch = self._profile_transport_epoch
        self._profile_transport_epoch = None

        for _method, future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        for subscriptions in tuple(self._subscriptions.values()):
            for subscription in tuple(subscriptions):
                subscription._fail(error)
        self._subscriptions.clear()

        stdin = process.stdin
        if stdin is not None:
            with suppress(Exception):
                stdin.close()

        # Everything past stdin-close awaits repeatedly; a SECOND cancellation
        # landing on any of those awaits must still run the resource cleanup
        # (job-object handle, lifeline FD, reservation epoch) — hence the
        # try/finally around the whole reaping tail.
        try:
            current = asyncio.current_task()
            tasks = [
                task
                for task in (reader_task, stderr_task)
                if task is not None and task is not current and not task.done()
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if process.returncode is None:
                # EOF is Codex's graceful app-server shutdown path. Give its
                # Arg0PathEntryGuard time to remove the locked tmp/arg0
                # directory before terminating the contained tree.
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        process.wait(), timeout=_SHUTDOWN_TIMEOUT_S
                    )
            if process.returncode is None:
                with suppress(ProcessLookupError, OSError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_TIMEOUT_S)
                except TimeoutError:  # The grace expired, so hard-kill the contained tree.
                    with suppress(ProcessLookupError, OSError):
                        process.kill()
                with suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_TIMEOUT_S)
        finally:
            # Sync resource cleanup first (safe under any cancellation), the
            # awaited release last: its shield keeps the queued job alive even
            # if this await is interrupted again. ONLY the entry-time local
            # copies are used here — never re-read from self (see above).
            if tree is not None:
                tree.close()
            if lifeline_fd is not None:
                with suppress(OSError):
                    os.close(lifeline_fd)
            if release_epoch is not None:
                await asyncio.shield(
                    asyncio.to_thread(
                        _release_subscription_transport, self, release_epoch
                    )
                )
            if not expected:
                log.warning("Codex app-server connection reset")

    async def close(self) -> None:
        """Reap the app-server tree and release local temporary state.

        Closing also RETIRES this client from the shared registry. Without
        that, ``get_shared_codex_app_server`` kept handing the same corpse to
        the next caller — whose ``ensure_started`` then paid a full cold start
        against a client whose workspace had already been deleted. Eviction is
        idempotent and runs even when the teardown itself fails, because a
        client nobody can use must never be reachable.
        """
        self._assert_owner_loop()
        try:
            async with self._start_lock:
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server client closed."),
                    expected=True,
                )
                workspace = self._workspace
                self._workspace = None
                self._child_environment = {}
                sink_server = self._sink_server
                self._sink_server = None
                self._sink_base_url = None
                if sink_server is not None:
                    sink_server.close()
                    with suppress(Exception):
                        await sink_server.wait_closed()
                if workspace is not None:
                    with suppress(OSError):
                        await asyncio.to_thread(shutil.rmtree, workspace.root)
        finally:
            self._closed = True
            _evict_shared_codex_app_server(self)

    async def poison(self) -> None:
        """Fail closed after an uncertain remote cleanup."""
        await self.close()


# Keyed on the binary path ALONE, never on the acquiring event loop. The
# resource behind a client is process-global (one OS profile lock, one
# app-server child bound to it), so a per-loop client could only ever be a
# client that can never reserve the profile — the second loop got one and
# every call from it failed with "already owned by another client".
_shared_clients: dict[tuple[str, CodexAppServerPurpose], CodexAppServerClient] = {}
_shared_clients_lock = threading.Lock()
# Invariant: WORKER threads may hold this lock across filesystem work (the
# cross-process profile lock is acquired under it: mkdir, resolve, ACL
# validation — its OS locking call itself is non-blocking). Event-loop code
# must therefore NEVER take this lock directly — always go through
# asyncio.to_thread (shielded where a cancellation must not drop the job).
_subscription_login_lock = threading.Lock()
_subscription_login_in_flight = False
_subscription_login_process: Any | None = None
_subscription_profile_mutating = False
_subscription_active_transports: set[int] = set()
# The one client currently holding the reservation. Kept as an object (not
# just its id) so an off-loop reserve can tell a live owner from one whose
# event loop died, and so status reads do not depend on the client still
# being registered.
_subscription_transport_owner: CodexAppServerClient | None = None
_subscription_transport_process_lock: Any | None = None
# Monotonic ownership token for the transport reservation: bumped on every
# reserve (including same-client adoption), quoted by every release. A stale
# holder's release becomes a no-op instead of tearing down a live reservation.
_subscription_transport_epoch: int = 0
_subscription_mutation_process_lock: Any | None = None

# Status probes must never report a broken setup just because the profile is
# briefly owned by another probe or mutation (the UI fires several concurrent
# refreshes; each losing caller used to flap the card to "needs attention").
# Concurrent callers wait on this condition (it shares _subscription_login_lock)
# and share the owner's result. The last completed probe is cached briefly so a
# refresh burst costs one CLI probe instead of one per request.
# Slightly above the login flow's 5s status polling so a poll usually hits
# the cache instead of paying a fresh CLI probe on every tick.
_SUBSCRIPTION_SNAPSHOT_CACHE_TTL_S: Final = 8.0
_SUBSCRIPTION_SNAPSHOT_WAIT_TIMEOUT_S: Final = 10.0
# A snapshot served while the profile is briefly owned elsewhere may be stale,
# but never arbitrarily old: beyond this bound the caller reports busy instead
# of a status another process may have changed long ago.
_SUBSCRIPTION_SNAPSHOT_STALE_MAX_S: Final = 60.0
# How long user actions (login, logout, call start) wait for a read-only
# status probe to release the profile before failing honestly.
_SUBSCRIPTION_PROBE_WAIT_S: Final = 5.0
_subscription_state_changed = threading.Condition(_subscription_login_lock)
_subscription_snapshot_cache: CodexAppServerCapability | None = None
_subscription_snapshot_cache_key: str = ""
_subscription_snapshot_cache_at: float = 0.0
# True when the cached entry memoizes a RAISED probe (not a real status). A
# failure memo exists only to keep coalesced waiters off a broken CLI within
# the fresh TTL; it must never be served as "what was true before" on the
# stale path, or one transient hiccup would paint the card broken for the
# whole next busy window.
_subscription_snapshot_cache_is_failure = False
# True only while codex_subscription_auth_snapshot itself owns the profile.
# Distinguishes the read-only probe from real mutations (login/logout), so
# user actions can wait out a probe instead of failing with a message that
# blames a login that does not exist.
_subscription_status_probe_active = False


def _subscription_cache_key(binary_path: str | None) -> str:
    return (binary_path or "").strip() or "<default>"


def _cached_subscription_snapshot_locked(
    binary_path: str | None, *, allow_stale: bool
) -> CodexAppServerCapability | None:
    """Return the last completed probe result; caller holds the login lock.

    ``allow_stale`` serves the last known status while the profile is briefly
    owned elsewhere, bounded by ``_SUBSCRIPTION_SNAPSHOT_STALE_MAX_S``.
    In-process mutations invalidate the cache when they BEGIN and again when
    they finish; a probe that was already mid-flight when a mutation started
    may still store its (pre-mutation) result during the mutation window, so
    a stale read can briefly reflect the pre-mutation truth until the
    mutation's closing invalidation lands.
    """
    if _subscription_snapshot_cache is None:
        return None
    if _subscription_snapshot_cache_key != _subscription_cache_key(binary_path):
        return None
    if allow_stale and _subscription_snapshot_cache_is_failure:
        return None
    age = time.monotonic() - _subscription_snapshot_cache_at
    limit = (
        _SUBSCRIPTION_SNAPSHOT_STALE_MAX_S
        if allow_stale
        else _SUBSCRIPTION_SNAPSHOT_CACHE_TTL_S
    )
    if age <= limit:
        return _subscription_snapshot_cache
    return None


def _store_subscription_snapshot_locked(
    binary_path: str | None,
    snapshot: CodexAppServerCapability,
    *,
    is_failure: bool = False,
) -> None:
    global _subscription_snapshot_cache, _subscription_snapshot_cache_key
    global _subscription_snapshot_cache_at
    global _subscription_snapshot_cache_is_failure

    _subscription_snapshot_cache = snapshot
    _subscription_snapshot_cache_key = _subscription_cache_key(binary_path)
    _subscription_snapshot_cache_at = time.monotonic()
    _subscription_snapshot_cache_is_failure = is_failure
    # Waiters parked on a busy window get the fresh result immediately.
    _subscription_state_changed.notify_all()


def _invalidate_subscription_snapshot_locked() -> None:
    """Drop the cached status after anything that can change the profile."""
    global _subscription_snapshot_cache, _subscription_snapshot_cache_key
    global _subscription_snapshot_cache_at
    global _subscription_snapshot_cache_is_failure
    global _subscription_activation_block

    _subscription_snapshot_cache = None
    _subscription_snapshot_cache_key = ""
    _subscription_snapshot_cache_at = 0.0
    _subscription_snapshot_cache_is_failure = False
    # The sticky activation block is deliberately NOT cleared here: cache
    # invalidation happens on every mutation attempt, including an ABORTED
    # re-login, and a closed browser window is no evidence the refused plan
    # changed. The block clears only on explicit new evidence — a VERIFIED
    # fresh login, a logout, or a passed activation.


# Sticky reason the connected login cannot activate (for example a
# business/enterprise ChatGPT plan, refused by the live account check). The
# one honest 409 toast fades in seconds; without this, every surface keeps
# claiming "ready" for an account that can never work.
_subscription_activation_block: str | None = None


def _set_subscription_activation_block_locked(message: str | None) -> None:
    """Caller holds ``_subscription_login_lock``."""
    global _subscription_activation_block

    _subscription_activation_block = (message or "").strip() or None


def set_codex_subscription_activation_block(message: str | None) -> None:
    """Record (or clear with ``None``) why activation is impossible."""
    with _subscription_login_lock:
        _set_subscription_activation_block_locked(message)


def codex_subscription_activation_block() -> str | None:
    with _subscription_login_lock:
        return _subscription_activation_block


def _await_status_probe_completion_locked() -> None:
    """Wait briefly for a status probe to release the profile.

    Caller holds ``_subscription_login_lock``. A status probe is read-only and
    short; failing a user action because a background refresh happens to own
    the profile — with an error naming a login that does not exist — is worse
    than waiting the probe out.
    """
    deadline = time.monotonic() + _SUBSCRIPTION_PROBE_WAIT_S
    while _subscription_status_probe_active:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexSubscriptionUnavailable(
                "A subscription-voice status check is still running. "
                "Try again in a moment."
            )
        _subscription_state_changed.wait(timeout=remaining)


def _subscription_transport_pass_fds(
    client: CodexAppServerClient,
) -> tuple[int, ...]:
    """Return the owner lock FD inherited by a POSIX app-server child."""
    with _subscription_login_lock:
        if _subscription_active_transports != {id(client)}:
            raise CodexSubscriptionUnavailable(
                "The subscription-voice process lock has no unique owner."
            )
        process_lock = _subscription_transport_process_lock
        if process_lock is None:
            raise CodexSubscriptionUnavailable(
                "The subscription-voice process lock is unavailable."
            )
        try:
            descriptor = int(process_lock.fileno())
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise CodexSubscriptionUnavailable(
                "The subscription-voice process lock cannot be inherited."
            ) from exc
        if descriptor < 0:
            raise CodexSubscriptionUnavailable(
                "The subscription-voice process lock cannot be inherited."
            )
        return (descriptor,)


def _owner_loop_is_dead(client: CodexAppServerClient) -> bool:
    """True when this client's owning event loop can never run it again."""
    loop = client._owner_loop
    return loop is not None and loop.is_closed()


def _reap_dead_loop_transport_locked(client: CodexAppServerClient) -> None:
    """Kill an orphan whose owning loop died; caller holds the login lock.

    Mirrors the shutdown sweep's dead-loop branch: the loop that owns the
    child can never reap it, so the tree is killed and the lifeline closed
    BEFORE the reservation is handed on — an orphan must never keep running
    against ``CODEX_HOME`` while the profile reads as free.
    """
    orphan_process = client._process
    client._process = None
    orphan_tree = client._process_tree
    client._process_tree = None
    client._ready = False
    if orphan_process is not None and orphan_process.returncode is None:
        with suppress(ProcessLookupError, OSError):
            orphan_process.kill()
    if orphan_tree is not None:
        with suppress(Exception):
            orphan_tree.close()
    with suppress(Exception):
        client._close_lifeline()
    client._profile_transport_epoch = None


def _reserve_subscription_transport(client: CodexAppServerClient) -> int:
    """Reserve the profile for this client and return an ownership epoch.

    The epoch is the anti-stale-release token: every reservation (including a
    re-reservation by the same client after a crash or cancelled start) bumps
    it, and ``_release_subscription_transport`` only honors the CURRENT epoch.
    Without it, an abandon worker or a slow ``_close_process`` from a previous
    life could tear down a reservation a newer start legitimately owns.
    """
    global _subscription_transport_process_lock, _subscription_transport_epoch
    global _subscription_transport_owner

    with _subscription_login_lock:
        _await_status_probe_completion_locked()
        if _subscription_profile_mutating:
            raise CodexSubscriptionUnavailable(
                "The subscription-voice profile is being changed."
            )
        client_id = id(client)
        if _subscription_active_transports:
            if (
                _subscription_active_transports == {client_id}
                and _subscription_transport_process_lock is not None
            ):
                # Adoption: the new start supersedes any stale holder, whose
                # pending releases become no-ops via the epoch bump.
                _subscription_transport_owner = client
                _subscription_transport_epoch += 1
                return _subscription_transport_epoch
            owner = _subscription_transport_owner
            if (
                owner is not None
                and owner is not client
                and _subscription_active_transports == {id(owner)}
                and _subscription_transport_process_lock is not None
                and _owner_loop_is_dead(owner)
            ):
                # The holder's event loop is gone for good: it can never
                # release the reservation itself, and refusing forever would
                # make subscription voice unusable until a restart. Reap its
                # orphan and hand the SAME process lock to the new owner.
                log.warning(
                    "Reclaiming the subscription-voice reservation from a "
                    "client whose event loop is closed"
                )
                _reap_dead_loop_transport_locked(owner)
                _subscription_active_transports.discard(id(owner))
                _subscription_active_transports.add(client_id)
                _subscription_transport_owner = client
                _subscription_transport_epoch += 1
                return _subscription_transport_epoch
            raise CodexSubscriptionUnavailable(
                "Subscription voice is already running elsewhere in Jarvis. "
                "End the active voice session before starting another one."
            )
        _subscription_transport_process_lock = _acquire_subscription_process_lock()
        _subscription_active_transports.add(client_id)
        _subscription_transport_owner = client
        _subscription_transport_epoch += 1
        return _subscription_transport_epoch


def _force_drop_subscription_transport(client: CodexAppServerClient) -> None:
    """Unconditionally drop a dead client's reservation (no epoch check).

    Safe ONLY for a client whose owner loop is closed: it can never reserve
    again, and no other client can hold the reservation while its id is in
    the set. Without this, a close that nulled the epoch before its loop died
    left the profile permanently "starting" with login/logout refused.
    """
    global _subscription_transport_process_lock, _subscription_transport_owner

    with _subscription_login_lock:
        _subscription_active_transports.discard(id(client))
        if _subscription_transport_owner is client:
            _subscription_transport_owner = None
        if not _subscription_active_transports:
            process_lock = _subscription_transport_process_lock
            _subscription_transport_process_lock = None
            if process_lock is not None:
                process_lock.close()
            _subscription_state_changed.notify_all()


def _release_subscription_transport(
    client: CodexAppServerClient, epoch: int
) -> None:
    """Release the reservation, but only when ``epoch`` is still current."""
    global _subscription_transport_process_lock, _subscription_transport_owner

    with _subscription_login_lock:
        if epoch != _subscription_transport_epoch:
            # A newer reservation owns the profile now; this release belongs
            # to a superseded holder and must not touch the live state.
            return
        _subscription_active_transports.discard(id(client))
        if _subscription_transport_owner is client:
            _subscription_transport_owner = None
        if not _subscription_active_transports:
            process_lock = _subscription_transport_process_lock
            _subscription_transport_process_lock = None
            if process_lock is not None:
                process_lock.close()
            _subscription_state_changed.notify_all()


def _release_subscription_mutation_process_lock_locked() -> None:
    """Release the mutation owner while ``_subscription_login_lock`` is held."""
    global _subscription_mutation_process_lock

    process_lock = _subscription_mutation_process_lock
    _subscription_mutation_process_lock = None
    if process_lock is not None:
        process_lock.close()


def _handoff_subscription_mutation_lock_to_login_guard() -> None:
    """Release the parent lock only after the guardian has signalled waiting."""
    global _subscription_mutation_process_lock

    with _subscription_login_lock:
        process_lock = _subscription_mutation_process_lock
        if not _subscription_profile_mutating or process_lock is None:
            raise CodexSubscriptionUnavailable(
                "The subscription-login lock handoff is unavailable."
            )
        _subscription_mutation_process_lock = None
    process_lock.close()


def _launch_subscription_login_reaper(target: Any) -> None:
    threading.Thread(
        target=target,
        name="codex-subscription-login-reaper",
        daemon=True,
    ).start()


def _evict_shared_codex_app_server(client: CodexAppServerClient) -> None:
    """Retire a client from the shared registry. Safe to call repeatedly."""
    with _shared_clients_lock:
        for key in [
            key for key, entry in _shared_clients.items() if entry is client
        ]:
            _shared_clients.pop(key, None)


def get_shared_codex_app_server(
    binary_path: str | None = None,
    *,
    purpose: CodexAppServerPurpose = "realtime",
) -> CodexAppServerClient:
    """Return a shared client for Jarvis's fixed subscription identity.

    One client per binary and purpose, process-wide. The dedicated profile still
    permits only one live child at a time. A caller on a different event loop is
    refused HERE with an actionable message rather than being handed a client
    that cannot reserve the single-owner profile and fails deep inside a call.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise CodexSubscriptionUnavailable(
            "The shared Codex app-server must be acquired from its owning event loop."
        ) from exc
    if purpose not in ("realtime", "text"):
        raise ValueError("purpose must be 'realtime' or 'text'")
    key = ((binary_path or "").strip() or "<default>", purpose)
    with _shared_clients_lock:
        client = _shared_clients.get(key)
        if client is not None:
            owner = client._owner_loop
            if owner is loop:
                return client
            if owner is not None and not owner.is_closed():
                raise CodexSubscriptionUnavailable(
                    "Subscription voice is already running elsewhere in "
                    "Jarvis. End the active voice session before starting "
                    "another one."
                )
            # The owning loop is gone for good; this entry can never be used
            # or closed again. Retire it here — the reservation it may still
            # hold is reclaimed off-loop by the next reserve, which can kill
            # the orphan safely.
            log.warning(
                "Replacing a Codex app-server client whose owner loop is closed"
            )
            _shared_clients.pop(key, None)
        client = CodexAppServerClient(binary_path=binary_path, purpose=purpose)
        client._owner_loop = loop
        _shared_clients[key] = client
    return client


def _local_subscription_auth_snapshot_locked(
    binary_path: str | None = None,
) -> CodexAppServerCapability | None:
    """Return an in-memory status while the dedicated profile is already owned.

    The caller holds ``_subscription_login_lock``.  Starting another Codex CLI
    against the same profile during login, logout, startup, or a live transport
    can race its auth and runtime files, so these states must never be probed.
    ``binary_path`` only fills cosmetic fields (the version chip) from the
    cache; ownership decisions never depend on it.
    """
    if _subscription_login_in_flight:
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated ChatGPT subscription login is in progress.",
            # Its own state, not login_required: the card must invite the
            # user to FINISH the running browser login, never to start a
            # second one.
            reason_code="login_in_progress",
        )
    if _subscription_profile_mutating:
        # Checked before the transport branch: during disconnect-and-logout
        # both are set, and reporting the closing transport as still ready
        # would be a lie for the whole teardown window.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated subscription voice status is being checked or changed.",
            reason_code="busy",
        )
    if _subscription_active_transports:
        # The reservation holder is tracked directly: it stays authoritative
        # even after the client was retired from the shared registry (a closed
        # owner loop), where a registry scan would have found nothing and
        # reported a live call as "starting".
        owner = _subscription_transport_owner
        ready_client = (
            owner
            if owner is not None
            and id(owner) in _subscription_active_transports
            and owner.ready
            else None
        )
        if ready_client is not None:
            # The bounded cache read applies the age ceiling and the
            # failure-memo exclusion — never read the raw cache here.
            cached = _cached_subscription_snapshot_locked(
                binary_path, allow_stale=True
            )
            cached_version = cached.version if cached is not None else None
            return CodexAppServerCapability(
                available=True,
                chatgpt_authenticated=True,
                binary_path=(
                    ready_client._trusted_binary_path or ready_client._binary_path
                ),
                # Cosmetic: keep the version chip alive during a call instead
                # of blanking it while the profile cannot be probed.
                version=cached_version,
                reason="Dedicated ChatGPT subscription voice is active.",
                reason_code="ready",
            )
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated ChatGPT subscription voice is starting.",
            reason_code="busy",
        )
    return None


def codex_subscription_auth_snapshot(
    binary_path: str | None = None,
) -> CodexAppServerCapability:
    """Return a lightweight snapshot without starting ``codex app-server``.

    Concurrent callers never see a fake broken setup: while another probe or a
    profile mutation owns the profile, this serves the last completed result,
    or waits briefly for the owner and re-evaluates. Only a cold start that
    stays contended past the bounded wait reports the transient ``busy`` state.
    """
    global _subscription_mutation_process_lock, _subscription_profile_mutating
    global _subscription_status_probe_active

    deadline = time.monotonic() + _SUBSCRIPTION_SNAPSHOT_WAIT_TIMEOUT_S
    with _subscription_state_changed:
        while True:
            local_snapshot = _local_subscription_auth_snapshot_locked(binary_path)
            if local_snapshot is not None:
                if local_snapshot.reason_code != "busy":
                    return local_snapshot
                cached = _cached_subscription_snapshot_locked(
                    binary_path, allow_stale=True
                )
                if cached is not None:
                    return cached
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return local_snapshot
                _subscription_state_changed.wait(timeout=remaining)
                continue
            cached = _cached_subscription_snapshot_locked(
                binary_path, allow_stale=False
            )
            if cached is not None:
                return cached
            try:
                _subscription_mutation_process_lock = (
                    _acquire_subscription_process_lock()
                )
            except CodexSubscriptionUnavailable as exc:
                # Another process (a second Jarvis instance or CLI session)
                # briefly owns the profile. Serve the last known status; a
                # transient lock is not a setup defect.
                stale = _cached_subscription_snapshot_locked(
                    binary_path, allow_stale=True
                )
                if stale is not None:
                    log.debug(
                        "Subscription profile owned by another process (%s); "
                        "serving the last known status.",
                        exc,
                    )
                    return stale
                return CodexAppServerCapability(
                    available=False,
                    chatgpt_authenticated=False,
                    binary_path=None,
                    version=None,
                    reason=str(exc),
                    reason_code="busy",
                )
            _subscription_profile_mutating = True
            _subscription_status_probe_active = True
            break
    try:
        snapshot = _read_codex_capability(binary_path)
    except Exception as exc:
        # Cache the failure briefly so waiters coalesced behind this probe do
        # not each retry the broken CLI serially; the owner still raises so
        # its caller sees the real error. A RAISED probe is "transiently
        # unknown" (busy), never a proven-broken setup: presenting one
        # antivirus-slowed hash or tempdir hiccup as "reconnect your account"
        # is the exact lie this pipeline exists to prevent.
        with _subscription_login_lock:
            _store_subscription_snapshot_locked(
                binary_path,
                CodexAppServerCapability(
                    available=False,
                    chatgpt_authenticated=False,
                    binary_path=None,
                    version=None,
                    reason=(
                        "The Codex status probe failed "
                        f"({type(exc).__name__}); retrying shortly."
                    ),
                    reason_code="busy",
                ),
                # Flagged so the stale path never serves this memo as "what
                # was true before" — it exists only to keep coalesced waiters
                # off a broken CLI within the fresh TTL.
                is_failure=True,
            )
        raise
    else:
        with _subscription_login_lock:
            _store_subscription_snapshot_locked(binary_path, snapshot)
        return snapshot
    finally:
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False
            _subscription_status_probe_active = False
            _subscription_state_changed.notify_all()


def start_codex_subscription_login(
    binary_path: str | None = None,
) -> subprocess.Popen[bytes]:
    """Start a fresh direct ChatGPT login in the dedicated voice profile."""
    global _subscription_login_in_flight, _subscription_login_process
    global _subscription_mutation_process_lock, _subscription_profile_mutating

    from jarvis.codex_auth import CodexAuthService

    if _headless_linux():
        raise CodexSubscriptionUnavailable(_HEADLESS_LOGIN_REASON)
    if _linux_login_terminal_missing():
        # Same pre-click truth the card shows, so the click and the card can
        # never disagree about why this desktop cannot host the login.
        raise CodexSubscriptionUnavailable(_NO_LOGIN_TERMINAL_REASON)
    with _subscription_login_lock:
        _await_status_probe_completion_locked()
        if _subscription_profile_mutating or _subscription_login_in_flight:
            raise CodexSubscriptionUnavailable(
                "A subscription-voice login is already in progress."
            )
        if _subscription_active_transports:
            raise CodexSubscriptionUnavailable(
                "Disconnect active subscription voice before starting login."
            )
        _subscription_mutation_process_lock = (
            _acquire_subscription_process_lock()
        )
        _subscription_profile_mutating = True
        _subscription_login_in_flight = True
        _invalidate_subscription_snapshot_locked()

    log_dir: Path | None = None
    try:
        home = _prepare_subscription_login_home()
        capability = _read_codex_capability(binary_path)
        if not capability.available or not capability.binary_path:
            raise CodexSubscriptionUnavailable(capability.reason)
        approved = next(
            (
                (release, target)
                for release, target in _trusted_codex_targets_for_platform()
                if release == capability.version
            ),
            None,
        )
        if approved is None:
            raise CodexSubscriptionUnavailable(
                "This platform has no approved Codex subscription runtime."
            )
        _release, target = approved
        lock_path = _subscription_process_lock_path()
        from jarvis.core.private_directory import (  # noqa: PLC0415
            ensure_owner_only_directory,
        )

        log_dir = lock_path.parent / f"login-{secrets.token_hex(16)}"
        ensure_owner_only_directory(log_dir, create=True)
        process = CodexAuthService(
            capability.binary_path,
            codex_home=home,
            force_file_auth_store=True,
            isolate_openai_environment=True,
            log_dir=log_dir,
            visible_login=True,
            lifetime_lock_path=lock_path,
            login_guard_directory=log_dir,
            login_guard_handoff=(
                _handoff_subscription_mutation_lock_to_login_guard
            ),
            trusted_binary_sha256=target[3],
        ).start_login()
    except RuntimeError as exc:
        if log_dir is not None:
            shutil.rmtree(log_dir, ignore_errors=True)
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False
            _subscription_login_in_flight = False
            _invalidate_subscription_snapshot_locked()
            _subscription_state_changed.notify_all()
        raise CodexSubscriptionUnavailable(str(exc)) from exc
    except BaseException:
        if log_dir is not None:
            shutil.rmtree(log_dir, ignore_errors=True)
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False
            _subscription_login_in_flight = False
            _invalidate_subscription_snapshot_locked()
            _subscription_state_changed.notify_all()
        raise

    with _subscription_login_lock:
        _subscription_login_process = process

    def cleanup_login() -> None:
        global _subscription_login_in_flight, _subscription_login_process
        global _subscription_profile_mutating

        login_verified = False
        try:
            process.wait()
            try:
                post_login = _read_codex_capability(capability.binary_path)
                login_verified = bool(
                    post_login.available
                    and post_login.chatgpt_authenticated
                    and post_login.reason_code == "ready"
                )
                if not login_verified:
                    log.warning(
                        "Dedicated subscription login did not produce a verified "
                        "ChatGPT login (%s)",
                        post_login.reason_code,
                    )
            except (CodexAppServerError, OSError) as exc:
                log.warning(
                    "Dedicated subscription login post-check failed (%s)",
                    type(exc).__name__,
                )
        finally:
            release_guard = getattr(process, "release_profile_lock", None)
            if callable(release_guard):
                try:
                    release_guard()
                except (OSError, RuntimeError) as exc:
                    log.warning(
                        "Dedicated subscription login guard release failed (%s)",
                        type(exc).__name__,
                    )
            if log_dir is not None:
                shutil.rmtree(log_dir, ignore_errors=True)
            with _subscription_login_lock:
                if _subscription_login_process is process:
                    _subscription_login_process = None
                    _subscription_login_in_flight = False
                    _subscription_profile_mutating = False
                    _release_subscription_mutation_process_lock_locked()
                    _invalidate_subscription_snapshot_locked()
                    if login_verified:
                        # A VERIFIED fresh login is new evidence — the next
                        # activation re-judges the plan. An aborted login
                        # window keeps the recorded verdict.
                        _set_subscription_activation_block_locked(None)
                    _subscription_state_changed.notify_all()

    try:
        _launch_subscription_login_reaper(cleanup_login)
    except BaseException:
        # Without a reaper the login flags would stay set until restart and
        # the profile would wedge as "login in progress". The guarded process
        # has no kill(); its guardian owns the OS profile lock, so ask it to
        # reap and release (the documented guardian path), then restore a
        # clean not-logged-in state.
        release_guard = getattr(process, "release_profile_lock", None)
        if callable(release_guard):
            try:
                release_guard()
            except (OSError, RuntimeError) as exc:
                log.warning(
                    "Login guard release failed after reaper launch failure (%s)",
                    type(exc).__name__,
                )
        if log_dir is not None:
            shutil.rmtree(log_dir, ignore_errors=True)
        with _subscription_login_lock:
            if _subscription_login_process is process:
                _subscription_login_process = None
            _subscription_login_in_flight = False
            _subscription_profile_mutating = False
            _release_subscription_mutation_process_lock_locked()
            _invalidate_subscription_snapshot_locked()
            _subscription_state_changed.notify_all()
        raise
    return process


def _delete_codex_subscription_auth_locked() -> tuple[bool, str | None]:
    """Delete the dedicated auth file while the caller owns the mutation lock."""
    try:
        home = _validated_subscription_home(create=False, require_marker=True)
    except CodexSubscriptionProfileMissing:  # Logging out an absent profile is idempotent success.
        return True, None
    except CodexSubscriptionInspectionFailed as exc:
        # Transiently unreadable — refuse honestly instead of deleting blind.
        return False, f"{exc} Try again in a moment."
    except CodexSubscriptionUnavailable:
        # Disconnecting must work IN-APP even when the profile is invalid:
        # removing the whole Jarvis-owned directory deletes the credential
        # AND clears the invalid state in one explicit user action.
        try:
            _rebuild_invalid_subscription_home()
        except CodexSubscriptionUnavailable as exc:
            # The route returns this honest failure as its 409 detail.
            return False, str(exc)
        return True, None
    auth_file = home / "auth.json"
    if not auth_file.exists() and not auth_file.is_symlink():
        return True, None
    try:
        _validate_regular_private_file(auth_file)
    except CodexSubscriptionInspectionFailed as exc:
        # Same contract as the profile-level branch above: transiently
        # unreadable refuses honestly instead of escaping as a raw error.
        return False, f"{exc} Try again in a moment."
    try:
        auth_file.unlink()
    except OSError as exc:  # Return a path-free deletion error to the authenticated caller.
        return (
            False,
            "Dedicated subscription credentials could not be removed "
            f"({type(exc).__name__}).",
        )
    try:
        _validated_subscription_home(create=False, require_marker=True)
    except CodexSubscriptionUnavailable:  # noqa: S110 - the logout already succeeded; this recheck is advisory.
        # The credential IS deleted — reporting failure here would show a
        # 409 for a logout that actually happened.
        log.warning(
            "Post-logout profile revalidation failed; the login file is gone",
            exc_info=True,
        )
    return True, None


def logout_codex_subscription(
    binary_path: str | None = None,
) -> tuple[bool, str | None]:
    """Delete only the dedicated file-backed login; missing is success.

    Synchronous and potentially slow (it can wait out a status probe and does
    filesystem work under the profile lock) — call it from a worker thread,
    never on the event loop. The HTTP route uses the async
    ``disconnect_and_logout_codex_subscription`` wrapper instead.
    """
    global _subscription_mutation_process_lock, _subscription_profile_mutating

    del binary_path  # Kept for the stable route/helper signature.

    with _subscription_login_lock:
        _await_status_probe_completion_locked()
        if _subscription_profile_mutating or _subscription_login_in_flight:
            raise CodexSubscriptionUnavailable(
                "Subscription voice cannot log out while login is in progress."
            )
        if _subscription_active_transports:
            raise CodexSubscriptionUnavailable(
                "Subscription voice is still active; disconnect it before logout."
            )
        _subscription_mutation_process_lock = (
            _acquire_subscription_process_lock()
        )
        _subscription_profile_mutating = True
        _invalidate_subscription_snapshot_locked()
    try:
        return _delete_codex_subscription_auth_locked()
    finally:
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False
            _invalidate_subscription_snapshot_locked()
            # The judged login is gone; the plan verdict goes with it.
            _set_subscription_activation_block_locked(None)
            _subscription_state_changed.notify_all()


def _begin_subscription_disconnect_mutation() -> None:
    """Claim the profile for disconnect; runs off-loop (it may wait briefly)."""
    global _subscription_mutation_process_lock, _subscription_profile_mutating
    global _subscription_transport_process_lock

    with _subscription_login_lock:
        _await_status_probe_completion_locked()
        if _subscription_profile_mutating or _subscription_login_in_flight:
            raise CodexSubscriptionUnavailable(
                "Subscription voice cannot log out while login is in progress."
            )
        if _subscription_active_transports:
            process_lock = _subscription_transport_process_lock
            if process_lock is None:
                raise CodexSubscriptionUnavailable(
                    "The active subscription-voice process lock is unavailable."
                )
            _subscription_mutation_process_lock = process_lock
            _subscription_transport_process_lock = None
        else:
            _subscription_mutation_process_lock = (
                _acquire_subscription_process_lock()
            )
        _subscription_profile_mutating = True
        _invalidate_subscription_snapshot_locked()


def _finish_subscription_disconnect_mutation() -> None:
    """Release the disconnect claim; runs off-loop (the mutex may be busy)."""
    global _subscription_mutation_process_lock, _subscription_profile_mutating
    global _subscription_transport_process_lock

    with _subscription_login_lock:
        if _subscription_active_transports:
            if _subscription_transport_process_lock is not None:
                log.error(
                    "Subscription voice has two process-lock owners during logout"
                )
                _release_subscription_mutation_process_lock_locked()
            else:
                _subscription_transport_process_lock = (
                    _subscription_mutation_process_lock
                )
                _subscription_mutation_process_lock = None
        else:
            _release_subscription_mutation_process_lock_locked()
        _subscription_profile_mutating = False
        _invalidate_subscription_snapshot_locked()
        # The judged login is gone; the plan verdict goes with it.
        _set_subscription_activation_block_locked(None)
        _subscription_state_changed.notify_all()


async def disconnect_and_logout_codex_subscription(
    binary_path: str | None = None,
) -> tuple[bool, str | None]:
    """Atomically block starts, close the transport, and delete its login."""
    del binary_path  # Kept for the stable route/helper signature.

    # Cleanup binds to OUR claim only: _begin... raising because a FOREIGN
    # owner is mutating must not clear that owner's state. The whole
    # decide-and-release lives in worker threads synchronized by a
    # threading.Event — never in a loop-side variable — because a SECOND
    # task cancellation interrupts even a shielded await, and a decision
    # read on the loop at that moment would miss a claim the worker is
    # about to complete (the permanent-wedge class).
    claim_settled = threading.Event()
    claim_failed: list[BaseException] = []

    def _claim() -> None:
        try:
            _begin_subscription_disconnect_mutation()
        except BaseException as exc:
            claim_failed.append(exc)
            raise
        finally:
            claim_settled.set()

    def _settle() -> None:
        # Off-loop: waits for the claim thread to settle, then releases our
        # claim. Once this thread starts it always finishes, whatever happens
        # to the coroutine that spawned it. The wait is unbounded on purpose:
        # a give-up timeout would turn executor saturation into a permanent
        # claim leak (the claim thread still runs later, with nobody left to
        # release it), while the claim itself is bounded by construction
        # (probe wait <= 5s plus lock acquisition).
        claim_settled.wait()
        if not claim_failed:
            _finish_subscription_disconnect_mutation()

    claim_future = asyncio.get_running_loop().run_in_executor(None, _claim)
    # A cancelled awaiter leaves the worker's exception unretrieved; consume
    # it so asyncio does not log a spurious GC error.
    claim_future.add_done_callback(
        lambda f: None if f.cancelled() else f.exception()
    )
    try:
        await asyncio.shield(claim_future)
        await asyncio.shield(close_shared_codex_app_servers())
        return await asyncio.to_thread(_delete_codex_subscription_auth_locked)
    finally:
        # Shield keeps the settle job alive even if this await is interrupted
        # by another cancellation; the job itself cannot be skipped because
        # nothing can cancel an executor future the loop never cancels.
        await asyncio.shield(asyncio.to_thread(_settle))


async def codex_subscription_login_ready(binary_path: str | None = None) -> bool:
    """Return the lightweight dedicated-profile login snapshot.

    Transient ``busy`` fails OPEN, mirroring the realtime provider's
    ``external_login_ready``: a caller acting on this answer performs the
    authoritative live account verification anyway, while failing closed here
    turns a healthy install into "connect this provider first" for the busy
    window.
    """
    if await asyncio.to_thread(codex_subscription_activation_block):
        # The live account gate refused this login permanently; readiness
        # surfaces must stop advertising a provider that can never start.
        return False
    try:
        snapshot = await asyncio.to_thread(
            codex_subscription_auth_snapshot,
            binary_path,
        )
    except (CodexAppServerError, OSError):
        # Readiness probes fail closed without starting transport.
        return False
    if snapshot.reason_code == "busy":
        return True
    return bool(snapshot.available and snapshot.chatgpt_authenticated)


async def close_shared_codex_app_servers() -> None:
    """Close all shared transports, primarily for application shutdown/tests."""
    current_loop = asyncio.get_running_loop()
    with _shared_clients_lock:
        entries = tuple(_shared_clients.items())
    failures: list[BaseException] = []
    for key, client in entries:
        owner_loop = client._owner_loop or current_loop
        try:
            if owner_loop is current_loop:
                await client.close()
            elif owner_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(client.close(), owner_loop)
                await asyncio.wrap_future(future)
            elif owner_loop.is_closed():
                # The owner loop is gone for good; this entry can never be
                # closed properly again. Escalating forever would make every
                # later logout answer 409 for the process lifetime — drop the
                # entry and free its reservation best-effort instead.
                log.warning(
                    "Dropping Codex app-server entry whose owner loop is closed"
                )
                # The dead loop can never reap its child: kill the tree and
                # close the lifeline best-effort BEFORE freeing the profile,
                # so no orphan keeps running against CODEX_HOME while the
                # reservation reads as free.
                orphan_process = client._process
                client._process = None
                orphan_tree = client._process_tree
                client._process_tree = None
                if orphan_process is not None and orphan_process.returncode is None:
                    with suppress(ProcessLookupError, OSError):
                        orphan_process.kill()
                if orphan_tree is not None:
                    with suppress(Exception):
                        orphan_tree.close()
                with suppress(Exception):
                    client._close_lifeline()
                # Unconditional: a close that nulled the epoch before its
                # loop died would otherwise leave the reservation forever.
                client._profile_transport_epoch = None
                await asyncio.shield(
                    asyncio.to_thread(
                        _force_drop_subscription_transport, client
                    )
                )
            else:
                raise CodexSubscriptionUnavailable(
                    "A Codex app-server owner loop is unavailable for safe shutdown."
                )
        except BaseException as exc:  # noqa: BLE001 - collect every owner-loop failure
            failures.append(exc)
            continue
        with _shared_clients_lock:
            if _shared_clients.get(key) is client:
                _shared_clients.pop(key, None)
    if failures:
        raise CodexSubscriptionUnavailable(
            "Not every Codex app-server could be closed on its owning event loop."
        ) from failures[0]


__all__ = [
    "CodexAppServerPurpose",
    "CodexAppServerCapability",
    "CodexAppServerClient",
    "CodexAppServerDisconnected",
    "CodexAppServerError",
    "CodexAppServerNotification",
    "CodexAppServerRPCError",
    "CodexAppServerTimeout",
    "CodexNotificationOverflow",
    "CodexNotificationSubscription",
    "CodexRealtimeStartResult",
    "CodexSubscriptionProfileMissing",
    "CodexSubscriptionContainmentUnavailable",
    "CodexSubscriptionBinaryUnsupported",
    "CodexSubscriptionInspectionFailed",
    "CodexSubscriptionPlanUnsupported",
    "CodexSubscriptionRuntimeStateInvalid",
    "CodexSubscriptionUnavailable",
    "CODEX_SUBSCRIPTION_REASON_CODES",
    "codex_subscription_activation_block",
    "set_codex_subscription_activation_block",
    "codex_subscription_auth_snapshot",
    "codex_subscription_home",
    "codex_subscription_login_ready",
    "close_shared_codex_app_servers",
    "disconnect_and_logout_codex_subscription",
    "get_shared_codex_app_server",
    "logout_codex_subscription",
    "start_codex_subscription_login",
]
