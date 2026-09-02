"""Per-client credentials for the Agent MCP surface.

One control key for everything is fine while one person drives one machine. It
stops being fine the moment several clients connect: a laptop, a phone's Claude
app, a teammate, a cloud session. They all hold the SAME secret, nothing records
which of them did what, and revoking one means rotating the key and re-pairing
every other client.

An MCP token fixes each of those. It is named ("Claude Desktop – MacBook"),
scoped, individually revocable, and its use is stamped — so "who has access?"
and "cut that one off" are both one call.

**Only the hash is stored.** The secret is returned exactly once, at issue, and
never again; a stolen token file yields nothing to replay. That is also why
re-issuing is the recovery path for a lost token, not "show it to me again".

Storage is a ``0600`` JSON file beside the control key, not ``jarvis.toml``: a
credential in the config file is a credential in every backup and every support
screenshot (AP-12).

Format of the wire value::

    jarvis_mcp_<token_id>_<secret>

The id travels in the clear so verification is a single dictionary lookup plus
one constant-time compare, rather than hashing the presented secret against
every stored token in turn.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

_FILE_NAME: Final[str] = "mcp_tokens.json"
_PREFIX: Final[str] = "jarvis_mcp"
_ID_BYTES: Final[int] = 6
_SECRET_BYTES: Final[int] = 32

#: What a token may do. Deliberately three, not a per-tool matrix: a permission
#: model nobody can hold in their head is one nobody sets correctly.
SCOPES: Final[tuple[str, ...]] = ("read", "work", "full")

#: Tools a ``work`` token may call on top of every read-only one. Governance
#: (approvals, the kill switch) and roster edits stay with ``full``, because
#: they decide what the ecosystem is allowed to do rather than doing work.
_WORK_EXTRA: Final[frozenset[str]] = frozenset(
    {
        "agent_chat",
        "agent_message",
        "agent_assign",
        "quest_post",
        "quest_retry",
        "quest_cancel",
        "room_say",
        "room_settle",
        "agent_create",
    }
)


class TokenError(ValueError):
    """A token could not be issued or found. The message is user-facing."""


@dataclass
class McpToken:
    """One issued credential. ``secret_hash`` is all that is ever persisted."""

    token_id: str
    name: str
    scope: str
    secret_hash: str
    created_ms: int
    last_used_ms: int = 0
    revoked_ms: int = 0
    note: str = ""
    #: Free-form label for where it was installed ("claude-desktop", "cursor").
    client: str = ""

    @property
    def active(self) -> bool:
        return self.revoked_ms == 0

    def to_public(self) -> dict[str, Any]:
        """What may be shown or logged — never the hash."""
        return {
            "token_id": self.token_id,
            "name": self.name,
            "scope": self.scope,
            "client": self.client,
            "created_ms": self.created_ms,
            "last_used_ms": self.last_used_ms,
            "revoked": not self.active,
            "revoked_ms": self.revoked_ms,
            "note": self.note,
        }


@dataclass
class TokenStore:
    """The token file, read and written whole. It is small by nature."""

    path: Path
    _tokens: dict[str, McpToken] = field(default_factory=dict)
    _loaded: bool = False

    def load(self) -> TokenStore:
        if self._loaded:
            return self
        self._tokens = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {"tokens": []}
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt file must not lock the owner out of their own app: the
            # control key still works, and re-issuing a token is one click.
            log.warning("agent MCP tokens: %s is unreadable (%s) — starting empty", self.path, exc)
            raw = {"tokens": []}
        for row in raw.get("tokens") or []:
            try:
                token = McpToken(**row)
            except TypeError:
                log.warning("agent MCP tokens: skipping a malformed row")
                continue
            self._tokens[token.token_id] = token
        self._loaded = True
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "tokens": [asdict(t) for t in self._tokens.values()]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Tighten BEFORE the rename so the file is never briefly world-readable.
        _restrict(tmp)
        tmp.replace(self.path)
        _restrict(self.path)

    # ------------------------------------------------------------ commands

    def issue(
        self, *, name: str, scope: str = "work", client: str = "", note: str = ""
    ) -> tuple[McpToken, str]:
        """Mint a token. Returns it plus the secret, which is shown ONCE."""
        name = name.strip()
        if not name:
            raise TokenError("a token needs a name — you will read it in the list later")
        if scope not in SCOPES:
            raise TokenError(f"scope must be one of {', '.join(SCOPES)}")
        self.load()
        token_id = secrets.token_hex(_ID_BYTES)
        while token_id in self._tokens:
            token_id = secrets.token_hex(_ID_BYTES)
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        token = McpToken(
            token_id=token_id,
            name=name[:120],
            scope=scope,
            secret_hash=_hash(secret),
            created_ms=int(time.time() * 1000),
            client=client[:60],
            note=note[:400],
        )
        self._tokens[token_id] = token
        self.save()
        return token, f"{_PREFIX}_{token_id}_{secret}"

    def list(self, *, include_revoked: bool = False) -> list[McpToken]:
        self.load()
        rows = sorted(self._tokens.values(), key=lambda t: t.created_ms, reverse=True)
        return rows if include_revoked else [t for t in rows if t.active]

    def get(self, token_id: str) -> McpToken | None:
        self.load()
        return self._tokens.get(token_id)

    def revoke(self, token_id: str) -> McpToken:
        self.load()
        token = self._tokens.get(token_id)
        if token is None:
            raise TokenError(f"no token {token_id!r}")
        if token.active:
            token.revoked_ms = int(time.time() * 1000)
            self.save()
        return token

    def delete(self, token_id: str) -> bool:
        """Forget a token entirely. Revoking is usually better — it keeps the record."""
        self.load()
        if self._tokens.pop(token_id, None) is None:
            return False
        self.save()
        return True

    def verify(self, presented: str | None) -> McpToken | None:
        """The live token a wire value names, or ``None``.

        Stamps ``last_used_ms`` on success, so the owner can see which clients
        are actually in use and which they can safely cut off.
        """
        parsed = _parse(presented)
        if parsed is None:
            return None
        token_id, secret = parsed
        self.load()
        token = self._tokens.get(token_id)
        if token is None or not token.active:
            return None
        if not hmac.compare_digest(token.secret_hash, _hash(secret)):
            return None
        now = int(time.time() * 1000)
        # One write per minute at most: a busy client would otherwise rewrite
        # the file on every single tool call.
        if now - token.last_used_ms > 60_000:
            token.last_used_ms = now
            try:
                self.save()
            except OSError:  # noqa: BLE001 — a read-only disk must not break auth
                log.debug("agent MCP tokens: could not stamp last use", exc_info=True)
        return token


def allows(scope: str, tool_name: str, *, dangerous: bool) -> bool:
    """Whether a scope may call one tool."""
    if scope == "full":
        return True
    if not dangerous:
        return True
    if scope == "work":
        return tool_name in _WORK_EXTRA
    return False  # read


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse(value: str | None) -> tuple[str, str] | None:
    """``jarvis_mcp_<id>_<secret>`` → ``(id, secret)``, or ``None``."""
    if not value or not value.startswith(f"{_PREFIX}_"):
        return None
    rest = value[len(_PREFIX) + 1 :]
    token_id, _, secret = rest.partition("_")
    if not token_id or not secret:
        return None
    return token_id, secret


def looks_like_token(value: str | None) -> bool:
    """Cheap check so the auth path can tell a token from a control key."""
    return _parse(value) is not None


def _restrict(path: Path) -> None:
    """Owner-only permissions where the OS has them; a quiet no-op elsewhere.

    POSIX gets ``0600``. Windows inherits the parent directory's ACL, which for
    a per-user data directory is already owner-only — there is no portable
    chmod there, and pretending otherwise would be worse than saying so.
    """
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        log.debug("agent MCP tokens: no POSIX permissions on %s", path, exc_info=True)


def token_file() -> Path:
    """Where tokens live: beside the control key, in the instance's data dir."""
    from jarvis.core import control_key as ck

    return ck.control_key_file().with_name(_FILE_NAME)


_store: TokenStore | None = None


def store() -> TokenStore:
    """The process-wide store, built on first use."""
    global _store
    if _store is None:
        _store = TokenStore(token_file())
    return _store


def _reset_for_tests(path: Path | None = None) -> None:
    global _store
    _store = TokenStore(path) if path is not None else None


__all__ = [
    "SCOPES",
    "McpToken",
    "TokenError",
    "TokenStore",
    "allows",
    "looks_like_token",
    "store",
    "token_file",
]
