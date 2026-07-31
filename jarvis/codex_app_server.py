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
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from jarvis import __version__
from jarvis.core.process_tree import ProcessTree, make_process_tree
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT_S: Final = 20.0
_SHUTDOWN_TIMEOUT_S: Final = 2.0
_DEFAULT_SDP_TIMEOUT_S: Final = 15.0
_DEFAULT_NOTIFICATION_QUEUE_SIZE: Final = 512
_SUPPORTED_CODEX_VERSION: Final = "codex-cli 0.146.0"

# SHA-256 of the native executables in OpenAI's six official npm artifacts for
# @openai/codex 0.146.0. The app-server protocol used below is experimental;
# an unknown build is refused instead of assuming its safety semantics match.
_TRUSTED_CODEX_TARGETS: Final = {
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
        "tmp",
        _SUBSCRIPTION_PROFILE_MARKER,
    }
)
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
    }
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


class CodexAppServerError(RuntimeError):
    """Base error for the local app-server transport."""


class CodexSubscriptionUnavailable(CodexAppServerError):
    """The Codex CLI cannot currently use a ChatGPT subscription login."""


class CodexSubscriptionContainmentUnavailable(CodexSubscriptionUnavailable):
    """The host cannot safely bind the experimental child to Jarvis lifetime."""


class CodexSubscriptionProfileMissing(CodexSubscriptionUnavailable):
    """The dedicated subscription-voice profile does not exist yet."""


class CodexAppServerDisconnected(CodexAppServerError):
    """The app-server process exited or its JSONL stream broke."""


class CodexAppServerTimeout(CodexAppServerError):
    """A bounded app-server request or notification wait expired."""


class CodexNotificationOverflow(CodexAppServerError):
    """A subscriber stopped consuming realtime notifications fast enough."""


class CodexAppServerRPCError(CodexAppServerError):
    """A redacted JSON-RPC error returned by app-server."""

    def __init__(self, method: str, code: int | None) -> None:
        self.method = method
        self.code = code
        suffix = f" (code {code})" if code is not None else ""
        super().__init__(f"Codex app-server rejected {method}{suffix}.")


@dataclass(frozen=True, slots=True)
class CodexAppServerCapability:
    """PII-free snapshot of whether the subscription transport may start."""

    available: bool
    chatgpt_authenticated: bool
    binary_path: str | None
    version: str | None
    reason: str
    reason_code: Literal[
        "ready",
        "login_required",
        "lifecycle_unavailable",
        "not_installed",
        "setup_invalid",
    ] = "setup_invalid"


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
    metadata = path.lstat()
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
    metadata = path.lstat()
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
        target = _TRUSTED_CODEX_TARGETS.get(
            (sys.platform, _normalized_machine())
        )
        return target is not None and _sha256_file(canonical) == target[3]
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
        raise CodexSubscriptionUnavailable(
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
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an untrusted runtime wrapper."
        )


def _validate_unix_arg0_alias(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    metadata = path.lstat()
    if not stat.S_ISLNK(metadata.st_mode):
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an invalid runtime alias."
        )
    _validate_current_owner(metadata)
    try:
        raw_target = Path(os.readlink(path))
        target = raw_target if raw_target.is_absolute() else path.parent / raw_target
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an unreadable runtime alias."
        ) from exc
    if not _is_trusted_codex_runtime_binary(
        target,
        trusted_binary_path=trusted_binary_path,
    ):
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains an untrusted runtime alias."
        )


def _validate_arg0_runtime_dir(
    path: Path,
    *,
    trusted_binary_path: str | None = None,
) -> None:
    _validate_private_directory(path)
    try:
        children = {entry.name: entry for entry in path.iterdir()}
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile runtime could not be inspected."
        ) from exc
    expected = {".lock"}
    if sys.platform == "win32":
        expected.update({"apply_patch.bat", "applypatch.bat"})
    elif sys.platform == "darwin":
        expected.update({"apply_patch", "applypatch", "codex-execve-wrapper"})
    elif sys.platform.startswith("linux"):
        expected.update(
            {
                "apply_patch",
                "applypatch",
                "codex-execve-wrapper",
                "codex-linux-sandbox",
            }
        )
    else:
        raise CodexSubscriptionUnavailable(
            "This platform's Codex runtime layout is not approved."
        )
    if set(children) != expected:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains unexpected runtime files."
        )
    lock_file = children[".lock"]
    _validate_regular_private_file(lock_file)
    if lock_file.stat().st_size != 0:
        raise CodexSubscriptionUnavailable(
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
        runtime_children = {entry.name: entry for entry in runtime_root.iterdir()}
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice runtime could not be inspected."
        ) from exc
    if set(runtime_children) != {"arg0"}:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains unexpected runtime state."
        )
    arg0_root = runtime_children["arg0"]
    _validate_private_directory(
        arg0_root,
        exact_posix_mode=0o700 if os.name == "posix" else None,
    )
    try:
        process_dirs = tuple(arg0_root.iterdir())
    except OSError as exc:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice runtime could not be inspected."
        ) from exc
    if len(process_dirs) > _MAX_ARG0_RUNTIME_DIRS:
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile contains excessive runtime state."
        )
    for process_dir in process_dirs:
        if _ARG0_RUNTIME_DIR_RE.fullmatch(process_dir.name) is None:
            raise CodexSubscriptionUnavailable(
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
        raise CodexSubscriptionUnavailable(
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
        raise CodexSubscriptionUnavailable(
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
        raise CodexSubscriptionUnavailable(
            "The dedicated Codex voice profile could not be inspected."
        ) from exc
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
        if entry.name in {"auth.json", _SUBSCRIPTION_PROFILE_MARKER}:
            _validate_regular_private_file(entry)

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


def _prepare_subscription_login_home() -> Path:
    home = _validated_subscription_home(create=True, require_marker=False)
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
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(
        getattr(opened, field, None) != getattr(completed, field, None)
        or getattr(opened, field, None) != getattr(final, field, None)
        for field in stable_fields
    ):
        raise OSError("Codex executable changed while it was being verified.")
    return digest.hexdigest()


def _trusted_native_codex_binary(resolved_binary: str, version: str | None) -> str:
    if version != _SUPPORTED_CODEX_VERSION:
        raise CodexSubscriptionUnavailable(
            f"Subscription voice requires Codex CLI {_SUPPORTED_CODEX_VERSION}."
        )
    if sys.platform != "win32":
        raise CodexSubscriptionContainmentUnavailable(
            "Experimental Codex subscription voice is unavailable on this platform "
            "until kill-on-parent-exit process containment is available."
        )
    target = _TRUSTED_CODEX_TARGETS.get((sys.platform, _normalized_machine()))
    if target is None:
        raise CodexSubscriptionUnavailable(
            "This operating-system architecture is not approved for the experimental "
            "Codex subscription voice transport."
        )
    variant, triple, executable_name, expected_hash = target
    try:
        launcher = Path(resolved_binary).resolve(strict=True)
    except OSError as exc:
        raise CodexSubscriptionUnavailable("The Codex CLI binary is unavailable.") from exc

    candidates: list[Path] = [launcher]
    roots = [launcher.parent, *list(launcher.parents)[:8]]
    package_roots: list[Path] = []
    for root in roots:
        if root.name == "codex" and root.parent.name == "@openai":
            package_roots.append(root)
        package_roots.append(root / "node_modules" / "@openai" / "codex")
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
            if _sha256_file(canonical) == expected_hash:
                return str(canonical)
        except OSError:  # Unreadable installation candidates are skipped during discovery.
            continue
    raise CodexSubscriptionUnavailable(
        "The installed Codex 0.146 executable does not match an official approved build."
    )


def _read_codex_capability(binary_path: str | None) -> CodexAppServerCapability:
    """Resolve the CLI; app-server itself authoritatively reads its auth store.

    Discovery uses ``codex login status``, whose exact 0.146 output reports only
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
    version = service._probe_version(resolved)
    try:
        resolved_binary = _trusted_native_codex_binary(
            resolved,
            version,
        )
    except CodexSubscriptionContainmentUnavailable as exc:  # Expected platform gate becomes status.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=version,
            reason=str(exc),
            reason_code="lifecycle_unavailable",
        )
    except CodexSubscriptionUnavailable as exc:
        # Convert setup failure into a safe status snapshot.
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
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
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=str(exc),
            reason_code="login_required",
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
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=resolved_binary,
            version=version,
            reason=str(exc),
            reason_code="login_required",
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
    chatgpt_login = logged_in and login_mode == "chatgpt"
    return CodexAppServerCapability(
        available=True,
        # Advisory only: the CLI emits a fixed PII-free mode string. The live
        # account/read RPC below proves ChatGPT mode and the plan.
        chatgpt_authenticated=chatgpt_login,
        binary_path=resolved_binary,
        version=version,
        reason=(
            "Dedicated ChatGPT login is available."
            if chatgpt_login
            else "Use Jarvis's subscription-voice login to connect ChatGPT."
        ),
        reason_code="ready" if chatgpt_login else "login_required",
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
        if name.upper() in _SUBSCRIPTION_ENV_ALLOWLIST
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


def _create_safe_transport_workspace() -> _SafeTransportWorkspace:
    from jarvis.core.private_directory import (  # noqa: PLC0415
        ensure_owner_only_directory,
    )

    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        root = temporary_root / f"jarvis-codex-voice-{secrets.token_hex(16)}"
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
        instructions.write_text(_TRANSPORT_BASE_INSTRUCTIONS, encoding="utf-8")
        compact_prompt.write_text(_TRANSPORT_BASE_INSTRUCTIONS, encoding="utf-8")
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
                            "base_instructions": _TRANSPORT_BASE_INSTRUCTIONS,
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
    ) -> None:
        self._binary_path = (binary_path or "").strip() or None
        self._request_timeout_s = max(0.1, float(request_timeout_s))
        self._child_environment: dict[str, str] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._process_tree: ProcessTree | None = None
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
        self._profile_transport_reserved = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._transport_provider_id = f"jarvis_voice_{secrets.token_hex(16)}"
        self._ready = False

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
        """Return a bounded, PII-free ChatGPT-login capability snapshot."""
        try:
            # The subprocesses inside this synchronous probe each have their own
            # hard timeout.  Do not wrap ``to_thread`` in ``wait_for``: cancelling
            # that await leaves the worker thread running against CODEX_HOME and
            # would release the profile mutation gate too early.
            return await asyncio.to_thread(_read_codex_capability, self._binary_path)
        except TimeoutError:  # The returned status reports this bounded probe failure.
            return CodexAppServerCapability(
                available=False,
                chatgpt_authenticated=False,
                binary_path=None,
                version=None,
                reason="Codex login status timed out.",
                reason_code="setup_invalid",
            )
        except Exception as exc:  # noqa: BLE001 - status must degrade honestly
            log.warning(
                "Codex subscription status failed (%s); app-server stays disabled",
                type(exc).__name__,
            )
            return CodexAppServerCapability(
                available=False,
                chatgpt_authenticated=False,
                binary_path=None,
                version=None,
                reason="Codex login status could not be verified.",
                reason_code="setup_invalid",
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
        async with self._start_lock:
            if self.ready:
                return
            if self._process is not None:
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server exited."),
                    expected=False,
                )

            _reserve_subscription_transport(self)
            self._profile_transport_reserved = True

            try:
                capability = await self.capability_status()
            except BaseException:
                _release_subscription_transport(self)
                self._profile_transport_reserved = False
                raise
            if not capability.available:
                _release_subscription_transport(self)
                self._profile_transport_reserved = False
                raise CodexSubscriptionUnavailable(capability.reason)
            if not capability.binary_path:
                _release_subscription_transport(self)
                self._profile_transport_reserved = False
                raise CodexSubscriptionUnavailable("Codex CLI binary is unavailable.")
            try:
                self._trusted_binary_path = capability.binary_path
                codex_home = await asyncio.to_thread(
                    _validated_subscription_home,
                    create=False,
                    require_marker=True,
                    trusted_binary_path=self._trusted_binary_path,
                )
                if self._workspace is None:
                    self._workspace = await asyncio.to_thread(
                        _create_safe_transport_workspace
                    )
                self._child_environment = _subscription_environment(
                    codex_home,
                    self._workspace,
                )
                await self._ensure_sink_started()
                await self._spawn(capability.binary_path)
                await self._initialize_live_process()
                await self._verify_live_chatgpt_account()
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
                self._ready = True
            except BaseException:
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
                    "experimentalApi": True,
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
            raise CodexSubscriptionUnavailable(
                "Subscription voice permits only personal ChatGPT accounts; workspace, "
                "enterprise, education, and unknown plans are refused."
            )

    async def require_chatgpt_login(self) -> None:
        """Verify the current app-server account through Codex's real store."""
        await self.ensure_started()
        try:
            await self._verify_live_chatgpt_account()
        except CodexSubscriptionUnavailable:
            await self._close_process(
                CodexAppServerDisconnected(
                    "Codex app-server authentication is not ChatGPT."
                ),
                expected=False,
            )
            raise

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
            except (ConnectionError, OSError):
                # The diagnostic sink peer may close before its reply.
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
            ("experimental_realtime_webrtc_call_base_url",): (
                _OFFICIAL_CHATGPT_CODEX_BASE
            ),
            ("experimental_realtime_start_instructions",): "",
            ("experimental_realtime_ws_backend_prompt",): (
                ""
            ),
            ("experimental_realtime_ws_base_url",): (
                _OFFICIAL_OPENAI_REALTIME_BASE
            ),
            ("history", "persistence"): "none",
            ("include_apps_instructions",): False,
            ("include_collaboration_mode_instructions",): False,
            ("include_environment_context",): False,
            ("include_permissions_instructions",): False,
            ("memories", "dedicated_tools"): False,
            ("memories", "generate_memories"): False,
            ("memories", "use_memories"): False,
            ("model_provider",): self._transport_provider_id,
            ("experimental_compact_prompt_file",): self._compact_prompt_file(),
            ("log_dir",): self._log_dir(),
            ("model_catalog_json",): self._model_catalog_file(),
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
        expected_paths[("features", "realtime_conversation")] = True
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
        if not isinstance(origins, Mapping) or set(origins) != expected_origin_keys:
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
            "model_provider": self._transport_provider_id,
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
        command = [
            binary_path,
            "app-server",
            "--strict-config",
            "--enable",
            "realtime_conversation",
        ]
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
                (
                    'experimental_realtime_ws_base_url='
                    '"https://api.openai.com/v1"'
                ),
                "-c",
                (
                    'experimental_realtime_webrtc_call_base_url='
                    '"https://chatgpt.com/backend-api/codex"'
                ),
                "-c",
                'experimental_realtime_start_instructions=""',
                "-c",
                (
                    "experimental_realtime_ws_backend_prompt="
                    '""'
                ),
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
                (
                    "experimental_compact_prompt_file="
                    f"{json.dumps(self._compact_prompt_file())}"
                ),
                "-c",
                f"log_dir={json.dumps(self._log_dir())}",
                "-c",
                f"model_catalog_json={json.dumps(self._model_catalog_file())}",
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
        if sys.platform == "win32":
            command.extend(("-c", 'windows.sandbox="unelevated"'))
        kwargs: dict[str, Any] = {
            "env": dict(self._child_environment),
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": self._safe_thread_cwd(),
        }
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            kwargs["pass_fds"] = _subscription_transport_pass_fds(self)

        tree = make_process_tree("codex-app-server")
        if not bool(getattr(tree, "supports_containment", False)):
            tree.close()
            raise CodexSubscriptionUnavailable(
                "Codex app-server process-tree containment is unavailable."
            )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                creationflags=_windows_creationflags(allow_breakaway=True),
                **kwargs,
            )
        except PermissionError:
            if sys.platform != "win32":
                tree.close()
                raise
            log.warning(
                "Codex app-server breakaway was denied; retrying with no-window process flags"
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    creationflags=_windows_creationflags(allow_breakaway=False),
                    **kwargs,
                )
            except BaseException:
                tree.close()
                raise
        except BaseException:
            tree.close()
            raise

        self._process = process
        self._process_tree = tree
        try:
            tree.assign(process.pid)
        except Exception as exc:  # noqa: BLE001 - containment is mandatory
            self._process = None
            self._process_tree = None
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
                await self._close_process(
                    CodexAppServerDisconnected("Codex app-server input closed."),
                    expected=False,
                )
                raise CodexAppServerDisconnected("Codex app-server input closed.") from exc

    async def _reader_loop(self, process: asyncio.subprocess.Process) -> None:
        stream = process.stdout
        if stream is None:
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
                future.set_exception(
                    CodexAppServerRPCError(pending_method, code if isinstance(code, int) else None)
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

    async def thread_start(
        self,
        *,
        base_instructions: str | None = None,
        developer_instructions: str | None = None,
        cwd: str | Path | None = None,
        model: str | None = None,
        ephemeral: bool = True,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a verified ChatGPT-only transport thread outside the workspace."""
        del base_instructions, developer_instructions, cwd, model, ephemeral
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
                "baseInstructions": _TRANSPORT_BASE_INSTRUCTIONS,
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
                "developerInstructions": _TRANSPORT_DEVELOPER_INSTRUCTIONS,
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
            try:
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
                error = CodexSubscriptionUnavailable(str(exc))
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

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        """Interrupt one exact app-server turn by its schema-defined ids."""
        return _result_dict(
            "turn/interrupt",
            await self.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            ),
        )

    async def thread_unsubscribe(self, thread_id: str) -> dict[str, Any]:
        """Unload a completed ephemeral voice thread from app-server."""
        return _result_dict(
            "thread/unsubscribe",
            await self.request("thread/unsubscribe", {"threadId": thread_id}),
        )

    async def realtime_start(
        self,
        thread_id: str,
        *,
        output_modality: str = "audio",
        offer_sdp: str | None = None,
        prompt: str | None = None,
        voice: str | None = None,
        version: str | None = None,
        model: str | None = None,
        include_startup_context: bool | None = None,
        client_managed_handoffs: bool | None = None,
        extra: Mapping[str, Any] | None = None,
        sdp_timeout_s: float = _DEFAULT_SDP_TIMEOUT_S,
    ) -> CodexRealtimeStartResult:
        """Start realtime and capture WebRTC SDP without a notification race."""
        if extra:
            raise CodexSubscriptionUnavailable(
                "Custom Codex realtime fields are disabled for subscription voice."
            )
        del prompt, include_startup_context, client_managed_handoffs
        params = {
            "clientManagedHandoffs": True,
            "includeStartupContext": False,
            "outputModality": output_modality,
            "prompt": "",
            "threadId": thread_id,
        }
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
            params["transport"] = {"type": "webrtc", "sdp": offer_sdp}

        sdp_subscription = self.subscribe(thread_id) if offer_sdp is not None else None
        try:
            response = _result_dict(
                "thread/realtime/start",
                await self.request("thread/realtime/start", params),
            )
            answer_sdp: str | None = None
            if sdp_subscription is not None:
                notification = await sdp_subscription.wait_for(
                    "thread/realtime/sdp", timeout_s=sdp_timeout_s
                )
                candidate = notification.params.get("sdp")
                if not isinstance(candidate, str) or not candidate:
                    raise CodexAppServerError("Codex app-server returned an invalid WebRTC answer.")
                answer_sdp = candidate
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

    async def realtime_stop(self, thread_id: str) -> dict[str, Any]:
        return _result_dict(
            "thread/realtime/stop",
            await self.request("thread/realtime/stop", {"threadId": thread_id}),
        )

    async def _close_process(self, error: CodexAppServerDisconnected, *, expected: bool) -> None:
        process = self._process
        if process is None:
            self._ready = False
            if self._profile_transport_reserved:
                _release_subscription_transport(self)
                self._profile_transport_reserved = False
            return
        self._process = None
        self._ready = False
        tree = self._process_tree
        self._process_tree = None
        reader_task = self._reader_task
        stderr_task = self._stderr_task
        self._reader_task = None
        self._stderr_task = None

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
            # Arg0PathEntryGuard time to remove the locked tmp/arg0 directory
            # before terminating the contained tree.
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
        if tree is not None:
            tree.close()
        if self._profile_transport_reserved:
            _release_subscription_transport(self)
            self._profile_transport_reserved = False
        if not expected:
            log.warning("Codex app-server connection reset")

    async def close(self) -> None:
        """Reap the app-server tree and release local temporary state."""
        self._assert_owner_loop()
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

    async def poison(self) -> None:
        """Fail closed after an uncertain remote cleanup."""
        await self.close()


_shared_clients: dict[
    tuple[str, asyncio.AbstractEventLoop], CodexAppServerClient
] = {}
_shared_clients_lock = threading.Lock()
_subscription_login_lock = threading.Lock()
_subscription_login_in_flight = False
_subscription_login_process: Any | None = None
_subscription_profile_mutating = False
_subscription_active_transports: set[int] = set()
_subscription_transport_process_lock: Any | None = None
_subscription_mutation_process_lock: Any | None = None


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


def _reserve_subscription_transport(client: CodexAppServerClient) -> None:
    global _subscription_transport_process_lock

    with _subscription_login_lock:
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
                return
            raise CodexSubscriptionUnavailable(
                "Subscription voice is already owned by another client or event loop."
            )
        _subscription_transport_process_lock = _acquire_subscription_process_lock()
        _subscription_active_transports.add(client_id)


def _release_subscription_transport(client: CodexAppServerClient) -> None:
    global _subscription_transport_process_lock

    with _subscription_login_lock:
        _subscription_active_transports.discard(id(client))
        if not _subscription_active_transports:
            process_lock = _subscription_transport_process_lock
            _subscription_transport_process_lock = None
            if process_lock is not None:
                process_lock.close()


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


def get_shared_codex_app_server(
    binary_path: str | None = None,
) -> CodexAppServerClient:
    """Return the shared client for Jarvis's fixed subscription-voice identity."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise CodexSubscriptionUnavailable(
            "The shared Codex app-server must be acquired from its owning event loop."
        ) from exc
    binary_key = (binary_path or "").strip() or "<default>"
    key = (binary_key, loop)
    with _shared_clients_lock:
        client = _shared_clients.get(key)
        if client is None:
            client = CodexAppServerClient(binary_path=binary_path)
            client._owner_loop = loop
            _shared_clients[key] = client
    return client


def _local_subscription_auth_snapshot_locked() -> CodexAppServerCapability | None:
    """Return an in-memory status while the dedicated profile is already owned.

    The caller holds ``_subscription_login_lock``.  Starting another Codex CLI
    against the same profile during login, logout, startup, or a live transport
    can race its auth and runtime files, so these states must never be probed.
    """
    if _subscription_login_in_flight:
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated ChatGPT subscription login is in progress.",
            reason_code="login_required",
        )
    if _subscription_active_transports:
        with _shared_clients_lock:
            active_clients = tuple(
                client
                for client in _shared_clients.values()
                if id(client) in _subscription_active_transports
            )
        ready_client = next((client for client in active_clients if client.ready), None)
        if ready_client is not None:
            return CodexAppServerCapability(
                available=True,
                chatgpt_authenticated=True,
                binary_path=(
                    ready_client._trusted_binary_path or ready_client._binary_path
                ),
                version=None,
                reason="Dedicated ChatGPT subscription voice is active.",
                reason_code="ready",
            )
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated ChatGPT subscription voice is starting.",
            reason_code="setup_invalid",
        )
    if _subscription_profile_mutating:
        return CodexAppServerCapability(
            available=False,
            chatgpt_authenticated=False,
            binary_path=None,
            version=None,
            reason="Dedicated subscription voice status is being checked or changed.",
            reason_code="setup_invalid",
        )
    return None


def codex_subscription_auth_snapshot(
    binary_path: str | None = None,
) -> CodexAppServerCapability:
    """Return a lightweight snapshot without starting ``codex app-server``."""
    global _subscription_mutation_process_lock, _subscription_profile_mutating

    with _subscription_login_lock:
        local_snapshot = _local_subscription_auth_snapshot_locked()
        if local_snapshot is not None:
            return local_snapshot
        try:
            _subscription_mutation_process_lock = (
                _acquire_subscription_process_lock()
            )
        except CodexSubscriptionUnavailable as exc:  # Expected lock failure becomes status.
            return CodexAppServerCapability(
                available=False,
                chatgpt_authenticated=False,
                binary_path=None,
                version=None,
                reason=str(exc),
                reason_code="setup_invalid",
            )
        _subscription_profile_mutating = True
    try:
        return _read_codex_capability(binary_path)
    finally:
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False


def start_codex_subscription_login(
    binary_path: str | None = None,
) -> subprocess.Popen[bytes]:
    """Start a fresh direct ChatGPT login in the dedicated voice profile."""
    global _subscription_login_in_flight, _subscription_login_process
    global _subscription_mutation_process_lock, _subscription_profile_mutating

    from jarvis.codex_auth import CodexAuthService

    if (
        sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    ):
        raise CodexSubscriptionUnavailable(
            "Interactive subscription-voice login is unavailable on headless Linux. "
            "Run Jarvis on a desktop to connect this dedicated profile."
        )
    with _subscription_login_lock:
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

    log_dir: Path | None = None
    try:
        home = _prepare_subscription_login_home()
        capability = _read_codex_capability(binary_path)
        if not capability.available or not capability.binary_path:
            raise CodexSubscriptionUnavailable(capability.reason)
        target = _TRUSTED_CODEX_TARGETS.get(
            (sys.platform, _normalized_machine())
        )
        if target is None:
            raise CodexSubscriptionUnavailable(
                "This platform has no approved Codex subscription runtime."
            )
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
        raise CodexSubscriptionUnavailable(str(exc)) from exc
    except BaseException:
        if log_dir is not None:
            shutil.rmtree(log_dir, ignore_errors=True)
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False
            _subscription_login_in_flight = False
        raise

    with _subscription_login_lock:
        _subscription_login_process = process

    def cleanup_login() -> None:
        global _subscription_login_in_flight, _subscription_login_process
        global _subscription_profile_mutating

        try:
            process.wait()
            try:
                post_login = _read_codex_capability(capability.binary_path)
                if not (
                    post_login.available
                    and post_login.chatgpt_authenticated
                    and post_login.reason_code == "ready"
                ):
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

    _launch_subscription_login_reaper(cleanup_login)
    return process


def _delete_codex_subscription_auth_locked() -> tuple[bool, str | None]:
    """Delete the dedicated auth file while the caller owns the mutation lock."""
    try:
        home = _validated_subscription_home(create=False, require_marker=True)
    except CodexSubscriptionProfileMissing:  # Logging out an absent profile is idempotent success.
        return True, None
    auth_file = home / "auth.json"
    if not auth_file.exists() and not auth_file.is_symlink():
        return True, None
    _validate_regular_private_file(auth_file)
    try:
        auth_file.unlink()
    except OSError as exc:  # Return a path-free deletion error to the authenticated caller.
        return (
            False,
            "Dedicated subscription credentials could not be removed "
            f"({type(exc).__name__}).",
        )
    _validated_subscription_home(create=False, require_marker=True)
    return True, None


def logout_codex_subscription(
    binary_path: str | None = None,
) -> tuple[bool, str | None]:
    """Delete only the dedicated file-backed login; missing is success."""
    global _subscription_mutation_process_lock, _subscription_profile_mutating

    del binary_path  # Kept for the stable route/helper signature.

    with _subscription_login_lock:
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
    try:
        return _delete_codex_subscription_auth_locked()
    finally:
        with _subscription_login_lock:
            _release_subscription_mutation_process_lock_locked()
            _subscription_profile_mutating = False


async def disconnect_and_logout_codex_subscription(
    binary_path: str | None = None,
) -> tuple[bool, str | None]:
    """Atomically block starts, close the transport, and delete its login."""
    global _subscription_mutation_process_lock, _subscription_profile_mutating
    global _subscription_transport_process_lock

    del binary_path  # Kept for the stable route/helper signature.

    with _subscription_login_lock:
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
    try:
        await close_shared_codex_app_servers()
        return await asyncio.to_thread(_delete_codex_subscription_auth_locked)
    finally:
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


async def codex_subscription_login_ready(binary_path: str | None = None) -> bool:
    """Return the lightweight dedicated-profile login snapshot."""
    try:
        snapshot = await asyncio.to_thread(
            codex_subscription_auth_snapshot,
            binary_path,
        )
    except (CodexAppServerError, OSError):
        # Readiness probes fail closed without starting transport.
        return False
    return bool(snapshot.available and snapshot.chatgpt_authenticated)


async def close_shared_codex_app_servers() -> None:
    """Close all shared transports, primarily for application shutdown/tests."""
    current_loop = asyncio.get_running_loop()
    with _shared_clients_lock:
        entries = tuple(_shared_clients.items())
    failures: list[BaseException] = []
    for key, client in entries:
        owner_loop = key[1]
        try:
            if owner_loop is current_loop:
                await client.close()
            elif owner_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(client.close(), owner_loop)
                await asyncio.wrap_future(future)
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
    "CodexSubscriptionUnavailable",
    "codex_subscription_auth_snapshot",
    "codex_subscription_home",
    "codex_subscription_login_ready",
    "close_shared_codex_app_servers",
    "disconnect_and_logout_codex_subscription",
    "get_shared_codex_app_server",
    "logout_codex_subscription",
    "start_codex_subscription_login",
]
