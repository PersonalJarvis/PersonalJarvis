"""SyncClient — pushes the board aggregate to the federation backend (phase C).

Responsibilities:

1. On first start: fetch (or generate) a keypair and register the identity
   with the backend using the admin-token header.
2. Periodically (every ``sync_interval_s``): build the signed sync payload
   from ``BoardAggregator.export_all_for_federation()`` plus locally managed
   achievements and the locally managed bio.
3. On backend outage: fail silently; the next tick will try again.

Security requirements:
- Private key via Credential Manager (``keyring``), never in config files.
- Sync body is canonicalised and Ed25519-signed as expected by the server.
- Before pushing: a whitelist filter passes only aggregate fields through
  (second layer; the server PII wall is the first).
"""
from __future__ import annotations

import asyncio
import logging
import platform
import sqlite3
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from jarvis.board.aggregator import BoardAggregator

log = logging.getLogger(__name__)

# Fields the local filter passes through — everything else is dropped
# before the body enters the signature. Mirrors board_backend.schemas.SyncPayload.
_ALLOWED_DAILY_KEYS = frozenset({
    "date", "tasks_completed", "tasks_failed", "tools_used",
    "unique_tools_count", "voice_commands_count",
    "voice_first_try_rate", "hours_saved_estimate",
})
_ALLOWED_ACHIEVEMENT_KEYS = frozenset({"id", "unlocked_at", "tier"})


# ----------------------------------------------------------------------
# Keypair storage
# ----------------------------------------------------------------------

class SecretsBackend(Protocol):
    """Minimal interface for private-key storage. ``keyring`` satisfies this."""
    def get_password(self, service: str, key: str) -> str | None: ...
    def set_password(self, service: str, key: str, value: str) -> None: ...


_KEYRING_SERVICE = "jarvis-board"
_PRIVKEY_KEY = "sync_privkey_hex"
_ADMIN_TOKEN_KEY = "admin_token"


def _load_or_create_privkey(secrets: SecretsBackend) -> tuple[str, str]:
    """Returns (privkey_hex, pubkey_hex). Generates a pair if none is stored.

    Persists the private key in the Credential Manager. The public key is
    derived from it on every start, so the public key stays the same across
    device switches as long as the private key is copied over.
    """
    from board_backend.crypto import generate_keypair
    from nacl.signing import SigningKey

    existing = secrets.get_password(_KEYRING_SERVICE, _PRIVKEY_KEY)
    if existing:
        try:
            sk = SigningKey(bytes.fromhex(existing))
            return existing, sk.verify_key.encode().hex()
        except (ValueError, Exception):  # noqa: BLE001
            log.warning("stored sync privkey corrupt — generating fresh")

    priv, pub = generate_keypair()
    try:
        secrets.set_password(_KEYRING_SERVICE, _PRIVKEY_KEY, priv)
    except Exception:  # noqa: BLE001
        log.exception("could not persist sync privkey to keyring; in-memory only")
    return priv, pub


def _resolve_admin_token(secrets: SecretsBackend) -> str | None:
    return secrets.get_password(_KEYRING_SERVICE, _ADMIN_TOKEN_KEY)


# ----------------------------------------------------------------------
# Default secrets (keyring)
# ----------------------------------------------------------------------

class _KeyringBackend:
    """Thin wrapper around the ``keyring`` package. Tests inject an
    in-memory stub instead.
    """

    def __init__(self) -> None:
        try:
            # Route through the central backend setup first: on macOS this
            # installs the single-vault-item wrapper (BUG-103), so board
            # secrets never become per-item Keychain entries that each pop
            # their own permission dialog.
            from jarvis.core.config import _ensure_keyring_backend

            _ensure_keyring_backend()
        except Exception:  # noqa: BLE001, S110 -- keyring stays best-effort here
            pass
        try:
            import keyring
            self._kr = keyring
            self._available = True
        except ImportError:
            self._kr = None
            self._available = False

    def get_password(self, service: str, key: str) -> str | None:
        if not self._available:
            return None
        try:
            return self._kr.get_password(service, key)
        except Exception:  # noqa: BLE001
            log.exception("keyring.get_password failed")
            return None

    def set_password(self, service: str, key: str, value: str) -> None:
        if not self._available:
            return
        try:
            self._kr.set_password(service, key, value)
        except Exception:  # noqa: BLE001
            log.exception("keyring.set_password failed")


# ----------------------------------------------------------------------
# Board DB reader for achievements and bio
# ----------------------------------------------------------------------

def _read_achievements(db_path: Path) -> list[dict[str, Any]]:
    """Returns unlocked achievements formatted for the server schema."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, unlocked_at FROM achievements WHERE unlocked_at IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    from jarvis.board.achievements import ACHIEVEMENTS_BY_ID
    for row in rows:
        spec = ACHIEVEMENTS_BY_ID.get(row["id"])
        if spec is None:
            continue
        out.append({
            "id": row["id"],
            "unlocked_at": row["unlocked_at"],
            "tier": spec.tier,
        })
    return out


def _read_latest_bio_text(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT text FROM bio ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return row["text"] if row is not None else None


# ----------------------------------------------------------------------
# SyncClient
# ----------------------------------------------------------------------

class SyncClient:
    """Pushes aggregated board data to the backend.

    Tests inject ``http_client`` (httpx.AsyncClient with a ``transport``)
    and a ``secrets`` stub so the class has no real network or keyring
    dependency.
    """

    def __init__(
        self,
        *,
        backend_url: str,
        aggregator: BoardAggregator,
        board_db_path: Path,
        sync_interval_s: int = 60,
        display_name: str = "",
        secrets: SecretsBackend | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = backend_url.rstrip("/")
        self._aggregator = aggregator
        self._board_db_path = Path(board_db_path)
        self._interval_s = max(10, int(sync_interval_s))
        self._display_name = display_name or platform.node()
        self._secrets = secrets if secrets is not None else _KeyringBackend()
        self._http = http_client
        self._owns_http = http_client is None
        self._privkey_hex: str | None = None
        self._pubkey_hex: str | None = None
        self._registered = False
        self._task: asyncio.Task[None] | None = None

    @property
    def pubkey(self) -> str | None:
        return self._pubkey_hex

    # --------------- Lifecycle ---------------

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        self._privkey_hex, self._pubkey_hex = _load_or_create_privkey(self._secrets)
        self._task = asyncio.create_task(self._loop(), name="board-sync")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # --------------- Loop ---------------

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("SyncClient.tick crashed")
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise

    async def tick(self) -> bool:
        """One sync attempt. Returns True on success, False otherwise.

        Idempotent — multiple ticks within the same second are allowed
        (replay-window drift would cover them, but we avoid it via
        ``_interval_s``).
        """
        if not self._registered:
            ok = await self._register_if_needed()
            if not ok:
                return False
        return await self._push()

    # --------------- Register ---------------

    async def _register_if_needed(self) -> bool:
        token = _resolve_admin_token(self._secrets)
        if not token:
            log.info("board sync: no admin_token in keyring — skip register")
            return False
        assert self._http is not None and self._pubkey_hex is not None
        try:
            resp = await self._http.post(
                f"{self._url}/api/v1/identity/register",
                json={"pubkey": self._pubkey_hex, "display_name": self._display_name},
                headers={"X-Admin-Token": token},
            )
        except httpx.HTTPError:
            log.exception("board sync: register-call failed")
            return False
        if resp.status_code == 200:
            self._registered = True
            log.info("board sync: registered as %s...", self._pubkey_hex[:8])
            return True
        if resp.status_code in (401, 403):
            log.warning(
                "board sync: register rejected (%s) — is admin_token correct?",
                resp.status_code,
            )
            return False
        log.warning("board sync: register failed status=%s body=%s",
                    resp.status_code, resp.text[:200])
        return False

    # --------------- Push ---------------

    def _build_payload(self) -> dict[str, Any]:
        """Pulls aggregator.export_all_for_federation() + achievements + bio
        and filters to whitelist fields.
        """
        export = self._aggregator.export_all_for_federation()
        daily = [
            {k: v for k, v in row.items() if k in _ALLOWED_DAILY_KEYS}
            for row in export.get("daily_stats", [])
        ]
        achievements = _read_achievements(self._board_db_path)
        # whitelist again (defensive copy)
        achievements = [
            {k: v for k, v in a.items() if k in _ALLOWED_ACHIEVEMENT_KEYS}
            for a in achievements
        ]
        payload: dict[str, Any] = {
            "ts_ms": int(time.time() * 1000),
            "display_name": self._display_name,
            "daily_stats": daily,
            "achievements": achievements,
        }
        bio = _read_latest_bio_text(self._board_db_path)
        if bio:
            payload["bio"] = bio[:1000]
        return payload

    async def _push(self) -> bool:
        from board_backend.crypto import canonical_json, sign

        assert self._http is not None
        assert self._privkey_hex is not None and self._pubkey_hex is not None
        payload = self._build_payload()
        body = canonical_json(payload)
        sig = sign(payload, privkey_hex=self._privkey_hex)
        try:
            resp = await self._http.post(
                f"{self._url}/api/v1/sync",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Pubkey": self._pubkey_hex,
                    "X-Jarvis-Sig": sig,
                },
            )
        except httpx.HTTPError:
            log.exception("board sync: push failed")
            return False
        if resp.status_code == 200:
            return True
        if resp.status_code == 401:
            # Likely the identity was deleted or the pubkey does not match
            self._registered = False
            log.warning("board sync: push rejected 401 — re-register on next tick")
            return False
        log.warning(
            "board sync: push failed status=%s body=%s",
            resp.status_code, resp.text[:200],
        )
        return False
