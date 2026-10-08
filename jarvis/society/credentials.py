"""Credentials an agent asked the person for: stored safely, never shown to the agent.

An agent that needs a token (a GitHub token, a Discord bot token, an API key
for a service it scripts against) asks with ``society_request_credential``.
The person pastes the value into a secure field in the chat; it goes straight
from that field to the OS credential store through ``set_secret`` and never
enters the chat log, the event stream, a prompt or a tool result.

What the agent gets is the NAME: the value is set as the environment variable
``<ENV>`` only in the commands it runs through ``society_shell``, which reads
the store on every call (a credential saved mid-turn works in the very next
command) and masks the value in the command's output BEFORE the model reads
it. A CLI seat's own process never receives a credential: its own shell tool
would hand ``printenv`` straight to the model, and a provider key there could
move a subscription seat onto paid billing.

Masking (``redact``) covers the plain value and its common encodings (base64,
URL encoding). It protects against an accidental print, not an agent set on
exfiltrating the value; every chat event of the agent is masked the same way.

The index file per agent holds names, labels and timestamps only; the values
live in the credential store under ``society_credential.<agent>.<ENV>``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote

log = logging.getLogger(__name__)

__all__ = [
    "SecretStore",
    "CredentialError",
    "CredentialInfo",
    "CredentialVault",
    "MAX_VALUE_CHARS",
    "validate_env_name",
]

#: A pasted secret longer than this is a file, not a token.
MAX_VALUE_CHARS: Final[int] = 16_384

#: Shorter values are never scrubbed from text: "1234" would eat every number.
_MIN_REDACT_CHARS: Final[int] = 6

_ENV_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

#: Names the shell or the app itself depends on: refused outright.
_RESERVED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "APPDATA", "COMSPEC", "HOME", "HOMEDRIVE", "HOMEPATH", "LANG", "LD_LIBRARY_PATH",
        "LD_PRELOAD", "LOCALAPPDATA", "NODE_OPTIONS", "PATH", "PATHEXT", "PROGRAMDATA",
        "PROGRAMFILES", "PSMODULEPATH", "PWD", "PYTHONHOME", "PYTHONIOENCODING", "PYTHONPATH",
        "SHELL", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TERM", "TMP", "TMPDIR", "USER",
        "USERNAME", "USERPROFILE", "WINDIR",
    }
)
_RESERVED_PREFIXES: Final[tuple[str, ...]] = ("JARVIS", "DYLD_")

_SLOT_PREFIX: Final[str] = "society_credential"
_SAFE_ID: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.-]")


class SecretStore(Protocol):
    """Where the values live: the OS credential store in the app, a dict in tests."""

    def get(self, slot: str) -> str | None: ...

    def set(self, slot: str, value: str) -> bool: ...

    def delete(self, slot: str) -> bool: ...


class _OsSecrets:
    """``jarvis.core.config``'s keyring → file-fallback store (AGENTS.md: get_secret only)."""

    def get(self, slot: str) -> str | None:
        from jarvis.core.config import get_secret

        # "" turns off the ENV/.env fallback: only this slot's own value counts.
        return get_secret(slot, "")

    def set(self, slot: str, value: str) -> bool:
        from jarvis.core.config import set_secret

        return set_secret(slot, value)

    def delete(self, slot: str) -> bool:
        from jarvis.core.config import delete_secret

        return delete_secret(slot)


class CredentialError(ValueError):
    """A safe, user-facing reason for a failed credential submission."""

    def __init__(self, message: str, *, code: str = "invalid_token") -> None:
        super().__init__(message)
        self.code = code


def normalize_value(value: str) -> str:
    """Reject malformed input before validation or storage, without echoing it."""
    value = value.strip()
    if not value:
        raise CredentialError("Enter the credential before saving.")
    if len(value) > MAX_VALUE_CHARS:
        raise CredentialError("The credential is too long.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise CredentialError("A credential must be a single line without control characters.")
    return value


async def validate_token(env: str, value: str, *, label: str = "") -> None:
    """Check identifiable Discord tokens only at the fixed, official endpoint.

    Other credentials receive local input validation; arbitrary service names
    and descriptions can never select a URL or send credentials elsewhere.
    """
    import httpx

    value = normalize_value(value)
    discord = ("DISCORD" in env.split("_") and "TOKEN" in env.split("_")) or (
        "discord" in label.casefold() and "token" in label.casefold()
    )
    if not discord:
        return
    if not value.isascii() or any(ch.isspace() for ch in value):
        raise CredentialError("Paste only the Discord bot token, without an authorization prefix.")
    try:
        # One user-triggered validation, no retry, redirect, paid generation or
        # token in the URL. A context-managed client closes its resources.
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {value}"},
                )
    except (TimeoutError, httpx.TimeoutException):
        raise CredentialError(
            "Discord did not respond in time. Please try saving again.",
            code="validation_timeout",
        ) from None
    except httpx.RequestError:
        raise CredentialError(
            "Discord could not be reached. Check your connection and try again.",
            code="network_error",
        ) from None
    if response.status_code in {401, 403}:
        raise CredentialError("Discord rejected this bot token. Check it or create a new one.")
    if response.status_code != 200:
        raise CredentialError(
            "Discord could not validate the token right now. Please try again later.",
            code="network_error",
        )
    try:
        user = response.json()
    except ValueError:
        raise CredentialError(
            "Discord returned an unreadable response. Please try again.", code="network_error",
        ) from None
    if not isinstance(user, dict) or not user.get("id") or user.get("bot") is not True:
        raise CredentialError("Use a Discord bot token from the Bot page in the Developer Portal.")


async def save_requested_credential(
    agent_id: str, env: str, value: str, *, label: str = "",
) -> None:
    """The shared validation and storage path for new and restored fields."""
    validate_env_name(env)
    value = normalize_value(value)
    vault = current_vault()
    if vault is None:
        raise CredentialError(
            "The credential store is unavailable. Please try again shortly.", code="storage_failed",
        )
    await validate_token(env, value, label=label)
    await asyncio.to_thread(vault.store, agent_id, env, value, label=label)


def validate_env_name(raw: Any) -> str:
    """The environment variable name ``raw`` asks for, or ``CredentialError``."""
    name = str(raw or "").strip()
    if not _ENV_NAME.fullmatch(name):
        raise CredentialError(
            "env must be an UPPER_SNAKE_CASE environment variable name such as GITHUB_TOKEN"
        )
    if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
        raise CredentialError(f"{name} is reserved for the system and cannot hold a credential")
    return name


def _encodings(value: str) -> list[str]:
    """The forms a printed value commonly takes: plain, base64 (also with the
    newline ``echo`` adds), URL-encoded."""
    raw = value.encode("utf-8")
    forms = {value, quote(value, safe="")}
    for data in (raw, raw + b"\n"):
        forms.add(base64.b64encode(data).decode("ascii"))
        forms.add(base64.b64encode(data).decode("ascii").rstrip("="))
        forms.add(base64.urlsafe_b64encode(data).decode("ascii").rstrip("="))
    return [form for form in forms if len(form) >= _MIN_REDACT_CHARS]


@dataclass(frozen=True, slots=True)
class CredentialInfo:
    """What the index knows about one stored credential: never its value."""

    env: str
    label: str
    created_ms: int
    updated_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "label": self.label,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
        }


class CredentialVault:
    """Per-agent credentials: names in ``<root>/<agent>.json``, values in the store."""

    def __init__(self, root: Path, secrets: SecretStore | None = None) -> None:
        self._root = Path(root)
        self._secrets: SecretStore = secrets or _OsSecrets()
        self._lock = threading.Lock()
        #: Values by agent, loaded once per process for scrubbing and env building.
        self._values: dict[str, dict[str, str]] = {}
        #: (needle, env) per agent, longest first, so a longer secret that
        #: contains a shorter one is masked whole.
        self._masks: dict[str, list[tuple[str, str]]] = {}
        #: Bumped by every store/delete: a load that raced one is discarded.
        self._generation = 0

    # ------------------------------------------------------------- index

    def _index_path(self, agent_id: str) -> Path:
        return self._root / f"{_SAFE_ID.sub('_', agent_id)}.json"

    def _read_index(self, agent_id: str) -> dict[str, CredentialInfo]:
        path = self._index_path(agent_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # An agent without saved credentials has no index yet.
            return {}
        except (OSError, ValueError):
            log.warning("society credentials: unreadable index %s", path, exc_info=True)
            return {}
        rows: dict[str, CredentialInfo] = {}
        for entry in raw.get("credentials", []) if isinstance(raw, dict) else []:
            if not isinstance(entry, dict):
                continue
            env = str(entry.get("env") or "")
            if not _ENV_NAME.fullmatch(env):
                continue
            rows[env] = CredentialInfo(
                env=env,
                label=str(entry.get("label") or "")[:120],
                created_ms=int(entry.get("created_ms") or 0),
                updated_ms=int(entry.get("updated_ms") or 0),
            )
        return rows

    def _write_index(self, agent_id: str, rows: Mapping[str, CredentialInfo]) -> None:
        path = self._index_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"credentials": [rows[name].to_dict() for name in sorted(rows)]}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _slot(agent_id: str, env: str) -> str:
        return f"{_SLOT_PREFIX}.{_SAFE_ID.sub('_', agent_id)}.{env}"

    # ------------------------------------------------------------- public

    def list(self, agent_id: str) -> list[CredentialInfo]:
        with self._lock:
            rows = self._read_index(agent_id)
        return [rows[name] for name in sorted(rows)]

    def has(self, agent_id: str, env: str) -> bool:
        """The value is really there: an index row whose store entry vanished is not."""
        return env in self.values(agent_id)

    def store(self, agent_id: str, env: str, value: str, *, label: str = "") -> CredentialInfo:
        """Save ``value`` for ``agent_id`` as ``env``; raises ``CredentialError``."""
        env = validate_env_name(env)
        value = normalize_value(value)
        now = int(time.time() * 1000)
        with self._lock:
            rows = self._read_index(agent_id)
            previous = rows.get(env)
            info = CredentialInfo(
                env=env,
                label=(label.strip() or (previous.label if previous else ""))[:120],
                created_ms=previous.created_ms if previous else now,
                updated_ms=now,
            )
            slot = self._slot(agent_id, env)
            previous_value: str | None = None
            attempted = False
            try:
                previous_value = self._secrets.get(slot)
                attempted = True
                if not self._secrets.set(slot, value) or self._secrets.get(slot) != value:
                    raise CredentialError(
                        "The credential store could not verify the saved value. Please try again.",
                        code="storage_failed",
                    )
                rows[env] = info
                self._write_index(agent_id, rows)
            except Exception:
                # Neither keyring errors nor their tracebacks are safe: a
                # backend may include the submitted value in its exception.
                if attempted:
                    try:
                        restored = (
                            self._secrets.set(slot, previous_value)
                            if previous_value is not None else self._secrets.delete(slot)
                        )
                        if not restored:
                            log.error("society credentials: rollback failed for %s", env)
                    except Exception:
                        log.error("society credentials: rollback failed for %s", env)
                raise CredentialError(
                    "The credential could not be stored. Please try saving again.",
                    code="storage_failed",
                ) from None
            finally:
                self._forget(agent_id)
        log.info("society credentials: stored %s for %s", env, agent_id)
        return info

    def delete(self, agent_id: str, env: str) -> bool:
        """Forget ``env`` for ``agent_id``; False when it was never stored."""
        with self._lock:
            rows = self._read_index(agent_id)
            if env not in rows:
                return False
            if not self._secrets.delete(self._slot(agent_id, env)):
                raise CredentialError("the credential store could not delete the value")
            rows.pop(env)
            self._write_index(agent_id, rows)
            self._forget(agent_id)
        log.info("society credentials: deleted %s for %s", env, agent_id)
        return True

    def _forget(self, agent_id: str) -> None:
        """Drop the cached values (caller holds the lock); the next read reloads."""
        self._generation += 1
        self._values.pop(agent_id, None)
        self._masks.pop(agent_id, None)

    def values(self, agent_id: str) -> dict[str, str]:
        """Every stored credential of ``agent_id`` by name (reads the store once)."""
        while True:
            with self._lock:
                cached = self._values.get(agent_id)
                if cached is not None:
                    return dict(cached)
                rows = self._read_index(agent_id)
                generation = self._generation
            loaded = self._load(agent_id, rows)
            with self._lock:
                if generation != self._generation:
                    continue  # a store or delete ran meanwhile: load again
                self._values[agent_id] = loaded
                self._masks[agent_id] = sorted(
                    ((form, env) for env, value in loaded.items() for form in _encodings(value)),
                    key=lambda pair: len(pair[0]),
                    reverse=True,
                )
                return dict(loaded)

    def _load(self, agent_id: str, rows: Mapping[str, CredentialInfo]) -> dict[str, str]:
        loaded: dict[str, str] = {}
        for env in rows:
            value = self._secrets.get(self._slot(agent_id, env))
            if value:
                loaded[env] = value
            else:
                log.warning(
                    "society credentials: %s for %s is missing from the store", env, agent_id
                )
        return loaded

    def is_loaded(self, agent_id: str) -> bool:
        return agent_id in self._values

    def shell_env(self, agent_id: str) -> dict[str, str]:
        """The variables an agent's own ``society_shell`` commands run with."""
        return self.values(agent_id)

    def redact(self, agent_id: str, text: str) -> str:
        """``text`` with every credential value of ``agent_id`` masked (may read the store)."""
        if not text or not self.values(agent_id):
            return text
        for needle, env in self._masks.get(agent_id, ()):
            if needle in text:
                text = text.replace(needle, f"[credential {env}]")
        return text

    def redact_payload(self, agent_id: str, payload: Any) -> Any:
        """``payload`` with every string in it redacted; unchanged objects are reused."""
        if not self.values(agent_id):
            return payload
        if isinstance(payload, str):
            return self.redact(agent_id, payload)
        if isinstance(payload, dict):
            out: dict[Any, Any] = {}
            changed = False
            for key, item in payload.items():
                new = self.redact_payload(agent_id, item)
                changed = changed or new is not item
                out[key] = new
            return out if changed else payload
        if isinstance(payload, list):
            items = [self.redact_payload(agent_id, item) for item in payload]
            changed = any(a is not b for a, b in zip(items, payload, strict=True))
            return items if changed else payload
        return payload


_VAULT: CredentialVault | None = None
_VAULT_ROOT: Path | None = None
_VAULT_LOCK = threading.Lock()


def vault_for(data_dir: Path) -> CredentialVault:
    """The process's vault for the society data folder ``data_dir``."""
    global _VAULT, _VAULT_ROOT
    root = Path(data_dir) / "credentials"
    with _VAULT_LOCK:
        if _VAULT is None or _VAULT_ROOT != root:
            _VAULT = CredentialVault(root)
            _VAULT_ROOT = root
        return _VAULT


def current_vault() -> CredentialVault | None:
    """The vault of the running society, or ``None`` before it started."""
    from .runtime import current_runtime

    rt = current_runtime()
    data_dir = getattr(rt, "data_dir", None) if rt is not None else None
    return vault_for(data_dir) if data_dir is not None else None
