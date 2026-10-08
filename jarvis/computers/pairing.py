"""Pair independent Jarvis servers without exchanging their control keys."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import platform
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from jarvis.computers.pairing_errors import PairingError
from jarvis.core.config import delete_secret, get_secret, set_secret
from jarvis.core.http_pool import HttpClientPool
from jarvis.core.paths import user_data_dir

PROTOCOL = "jarvis-pairing-v1"
CODE_TTL = 300
TICKET_TTL = 60
_LOCK = threading.RLock()


class ServerIdentity(BaseModel):
    protocol: Literal["jarvis-pairing-v1"]
    name: str = Field(min_length=1, max_length=120)
    platform: str = Field(max_length=120)


class PairedServer(BaseModel):
    id: str
    url: str
    name: str
    platform: str
    created_at: float
    checked_at: float
    online: bool


class PairingGrant(BaseModel):
    id: str
    name: str = Field(max_length=120)
    digest: str
    created_at: float


class PairingState(BaseModel):
    servers: list[PairedServer] = Field(default_factory=list)
    grants: list[PairingGrant] = Field(default_factory=list)


class Redemption(ServerIdentity):
    credential: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _slot(server_id: str) -> str:
    return f"paired_server_{server_id}"


def normalize_server(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        url = urlsplit(value)
        if (
            any(char.isspace() for char in value)
            or "\\" in value
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Use a server origin such as https://jarvis.example.com.")
        port = url.port
        if port is not None and port < 1:
            raise ValueError("The server port must be between 1 and 65535.")
        host = url.hostname.encode("idna").decode("ascii")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None  # A DNS name is valid too; only localhost permits cleartext HTTP.
        if url.scheme == "http" and host != "localhost" and not (address and address.is_loopback):
            raise ValueError("Remote servers require HTTPS. An SSH tunnel may use local HTTP.")
        authority = f"[{host}]" if address and address.version == 6 else host
        if port is not None:
            authority += f":{port}"
        return urlunsplit((url.scheme, authority, "", "", ""))
    except ValueError as exc:
        raise PairingError(str(exc)) from exc


class ServerPairing:
    """Durable public metadata, keyring credentials, and one-use in-memory codes."""

    def __init__(self, path: Path | None = None, pool: HttpClientPool | None = None) -> None:
        self.path = path or user_data_dir() / "computers" / "paired-servers.json"
        self.pool = pool or HttpClientPool(
            timeout_s=15,
            client_kwargs={"follow_redirects": False, "trust_env": False},
        )
        self._codes: dict[str, float] = {}
        self._tickets: dict[str, tuple[float, str]] = {}
        self._operation_loop: asyncio.AbstractEventLoop | None = None
        self._operations: dict[str, asyncio.Lock] = {}

    def _operation(self, url: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._operation_loop is not loop:
            self._operations = {}
            self._operation_loop = loop
        return self._operations.setdefault(url, asyncio.Lock())

    def _read(self) -> PairingState:
        try:
            return PairingState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return PairingState()
        except (ValueError, OSError) as exc:
            raise PairingError("The saved server connections could not be read.", 500) from exc

    def _write(self, state: PairingState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".pairing-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(state.model_dump_json(indent=2))
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def identity(self) -> ServerIdentity:
        return ServerIdentity(
            protocol=PROTOCOL, name=platform.node()[:120] or "Jarvis", platform=platform.system()
        )

    def issue_code(self) -> dict[str, Any]:
        with _LOCK:
            now = time.time()
            self._codes = {code: expiry for code, expiry in self._codes.items() if expiry > now}
            if len(self._codes) >= 16:
                raise PairingError("Wait for an existing pairing code to expire.", 429)
            code = secrets.token_urlsafe(24)
            self._codes[_digest(code)] = now + CODE_TTL
            return {"code": code, "expires_at": now + CODE_TTL}

    def redeem(self, code: str, name: str) -> Redemption:
        with _LOCK:
            digest = _digest(code.strip())
            if self._codes.get(digest, 0) <= time.time():
                raise PairingError("The pairing code is invalid, expired, or already used.", 401)
            state = self._read()
            if len(state.grants) >= 64:
                raise PairingError("Remove an existing paired client before adding another.", 409)
            credential = secrets.token_urlsafe(32)
            state.grants.append(
                PairingGrant(
                    id="g_" + secrets.token_hex(8),
                    name=name.strip() or "Jarvis client",
                    digest=_digest(credential),
                    created_at=time.time(),
                )
            )
            self._write(state)
            self._codes.pop(digest)
            return Redemption(**self.identity().model_dump(), credential=credential)

    def authenticate(self, credential: str) -> PairingGrant:
        with _LOCK:
            digest = _digest(credential)
            grant = next(
                (g for g in self._read().grants if secrets.compare_digest(g.digest, digest)), None
            )
            if not credential or grant is None:
                raise PairingError(
                    "This server connection is no longer authorized. Pair again.", 401
                )
            return grant

    def clients(self) -> list[dict[str, Any]]:
        with _LOCK:
            return [grant.model_dump(exclude={"digest"}) for grant in self._read().grants]

    def revoke(self, grant_id: str) -> None:
        with _LOCK:
            state = self._read()
            state.grants = [g for g in state.grants if g.id != grant_id]
            self._write(state)
            self._tickets = {
                key: value for key, value in self._tickets.items() if value[1] != grant_id
            }

    def issue_ticket(self, credential: str) -> str:
        with _LOCK:
            grant = self.authenticate(credential)
            now = time.time()
            self._tickets = {key: value for key, value in self._tickets.items() if value[0] > now}
            if len(self._tickets) >= 64:
                raise PairingError("Too many pending server windows. Try again in one minute.", 429)
            ticket = secrets.token_urlsafe(32)
            self._tickets[_digest(ticket)] = (now + TICKET_TTL, grant.id)
            return ticket

    def consume_ticket(self, ticket: str) -> None:
        with _LOCK:
            expiry, grant_id = self._tickets.pop(_digest(ticket), (0, ""))
            if expiry <= time.time() or not any(g.id == grant_id for g in self._read().grants):
                raise PairingError(
                    "This server link expired or was already used. Open it again.", 401
                )

    def servers(self) -> list[PairedServer]:
        with _LOCK:
            return self._read().servers

    def server(self, server_id: str) -> PairedServer:
        server = next((s for s in self.servers() if s.id == server_id), None)
        if server is None:
            raise PairingError("This server connection does not exist.", 404)
        return server

    async def _request(
        self, url: str, path: str, *, credential: str = "", body: dict | None = None
    ) -> dict:
        # Revalidate stored origins too; credentials never follow redirects or environment proxies.
        origin = normalize_server(url)
        try:
            response = await self.pool.client().request(
                "POST" if body is not None else "GET",
                origin + "/api/computers/pairing/" + path,
                headers={"Authorization": "Bearer " + credential} if credential else {},
                json=body,
            )
            if response.status_code in {401, 403}:
                raise PairingError(
                    "The server rejected this pairing code or saved connection. Pair again.", 401
                )
            if response.status_code != 200:
                raise PairingError(
                    "The server did not accept the connection. Check its address and version.", 502
                )
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            # Never echo response bodies or submitted credentials into user errors or logs.
            raise PairingError("Could not reach a compatible Jarvis server securely.", 502) from exc

    async def add(self, host: str, code: str) -> PairedServer:
        url = normalize_server(host)
        async with self._operation(url):
            return await self._add(url, code)

    async def _add(self, url: str, code: str) -> PairedServer:
        try:
            reply = Redemption.model_validate(
                await self._request(
                    url,
                    "redeem",
                    body={
                        "code": code.strip(),
                        "name": self.identity().name,
                    },
                )
            )
        except ValidationError as exc:
            raise PairingError(
                "The server returned an incompatible pairing response.", 502
            ) from exc
        return await asyncio.to_thread(self._save_server, url, reply)

    def _save_server(self, url: str, reply: Redemption) -> PairedServer:
        now = time.time()
        with _LOCK:
            state = self._read()
            previous = next((s for s in state.servers if s.url == url), None)
            server = PairedServer(
                id=previous.id if previous else "s_" + secrets.token_hex(8),
                url=url,
                name=reply.name,
                platform=reply.platform,
                created_at=previous.created_at if previous else now,
                checked_at=now,
                online=True,
            )
            old_credential = get_secret(_slot(server.id), env_fallback="")
            if not set_secret(_slot(server.id), reply.credential):
                raise PairingError(
                    "The server credential could not be saved. Generate a new code and try again.",
                    500,
                )
            state.servers = [s for s in state.servers if s.id != server.id] + [server]
            try:
                self._write(state)
            except OSError as exc:
                if old_credential:
                    set_secret(_slot(server.id), old_credential)
                else:
                    delete_secret(_slot(server.id))
                raise PairingError("The server connection could not be saved.", 500) from exc
        return server

    def _credential(self, server_id: str) -> str:
        value = get_secret(_slot(server_id), env_fallback="")
        if not value:
            raise PairingError("The saved server credential is missing. Pair again.", 409)
        return value

    async def check(self, server_id: str) -> PairedServer:
        server = self.server(server_id)
        async with self._operation(server.url):
            return await self._check(server_id)

    async def _check(self, server_id: str) -> PairedServer:
        server = self.server(server_id)
        online = False
        try:
            reply = ServerIdentity.model_validate(
                await self._request(
                    server.url,
                    "status",
                    credential=self._credential(server_id),
                )
            )
            online = True
        except (PairingError, ValidationError):
            # A failed health check is represented explicitly in the saved status.
            reply = ServerIdentity(protocol=PROTOCOL, name=server.name, platform=server.platform)
        with _LOCK:
            state = self._read()
            current = next((s for s in state.servers if s.id == server_id), None)
            if current is None:
                raise PairingError("This server connection was removed.", 404)
            updated = current.model_copy(
                update={
                    "online": online,
                    "checked_at": time.time(),
                    "name": reply.name,
                    "platform": reply.platform,
                }
            )
            state.servers = [updated if s.id == server_id else s for s in state.servers]
            self._write(state)
            return updated

    async def launch(self, server_id: str) -> str:
        server = self.server(server_id)
        async with self._operation(server.url):
            return await self._launch(server_id)

    async def _launch(self, server_id: str) -> str:
        server = self.server(server_id)
        result = await self._request(
            server.url, "launch", credential=self._credential(server_id), body={}
        )
        ticket = result.get("ticket")
        import re

        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            raise PairingError("The server returned an invalid opening link.", 502)
        return server.url + "/api/computers/pairing/enter#" + ticket

    async def remove(self, server_id: str) -> None:
        server = self.server(server_id)
        async with self._operation(server.url):
            await self._remove(server_id)

    async def _remove(self, server_id: str) -> None:
        server = self.server(server_id)
        # Refuse to claim the remote grant was revoked when its server is unreachable.
        await self._request(
            server.url, "disconnect", credential=self._credential(server_id), body={}
        )
        with _LOCK:
            state = self._read()
            state.servers = [s for s in state.servers if s.id != server_id]
            self._write(state)
            delete_secret(_slot(server_id))


_service: ServerPairing | None = None


def get_pairing() -> ServerPairing:
    global _service
    with _LOCK:
        if _service is None:
            _service = ServerPairing()
        return _service
