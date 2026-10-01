"""The orchestration behind the Computers section.

Every REST route is a thin call into :class:`ComputerService`. It owns the
rules a user never has to learn:

* Jarvis logs in with its OWN key by default. A password is used once to plant
  that key and is then forgotten, unless the user explicitly keeps password
  login (it then lives in the keyring, never in the JSON file).
* The first contact pins the server's identity; later connections refuse a
  changed identity until the user confirms it (:meth:`trust_new_host_key`).
* A local VM is a record that starts in ``provisioning`` and turns into an
  ordinary SSH computer when Multipass reports it running.

Later features (deploying Jarvis to a VPS, agents working on a remote box) use
:meth:`run` and :meth:`session` rather than opening their own connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib.util
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.computers import cloud, identity, local_vm, providers, remote_os
from jarvis.computers.models import (
    AuthMethod,
    Computer,
    ComputerHealth,
    LoginMode,
    ProviderId,
)
from jarvis.computers.probe import PROBE_SCRIPT, WINDOWS_PROBE_SCRIPT, parse_probe
from jarvis.computers.ssh import (
    MAX_COMMAND_TIMEOUT_S,
    CommandResult,
    Session,
    SshError,
    SshTarget,
    authorize_key_command,
    authorize_key_script_windows,
    close,
    open_session,
    run_command,
)
from jarvis.computers.store import ComputerStore
from jarvis.core.config import delete_secret, get_secret, set_secret

log = logging.getLogger(__name__)

_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,253}$")
_USER_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_PROBE_TIMEOUT_S = 25.0


async def _probe(opened: Session, host: remote_os.RemoteHost) -> CommandResult:
    """The health script in the language the computer speaks."""
    if host.windows:
        return await remote_os.run_powershell(
            # PowerShell alone took 6 s to start on a busy Windows VM.
            opened,
            WINDOWS_PROBE_SCRIPT,
            timeout_s=_PROBE_TIMEOUT_S * 2,
        )
    return await run_command(opened, PROBE_SCRIPT, timeout_s=_PROBE_TIMEOUT_S)


class ComputerError(Exception):
    """A request the service refuses; ``status`` maps to the HTTP code."""

    def __init__(self, message: str, *, status: int = 400, kind: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind


def _password_slot(computer_id: str) -> str:
    return f"computer_password_{computer_id}"


def _private_key_slot(computer_id: str) -> str:
    return f"computer_private_key_{computer_id}"


def _passphrase_slot(computer_id: str) -> str:
    return f"computer_key_passphrase_{computer_id}"


def _forget_secrets(computer_id: str, *, keep: str | None = None) -> None:
    """Drop every stored credential of a computer except the ``keep`` kind."""
    if keep != "password":
        delete_secret(_password_slot(computer_id))
    if keep != "private_key":
        delete_secret(_private_key_slot(computer_id))
        delete_secret(_passphrase_slot(computer_id))


def _bcrypt_kdf_available() -> bool:
    """Whether a passphrase-protected OpenSSH key can be unlocked here.

    ssh-keygen's default key format derives the encryption key with bcrypt's
    KDF. It is a declared dependency (``asyncssh[bcrypt]``), but an install
    that lost it must say THAT rather than blame the user's passphrase.
    """
    try:
        import bcrypt
    except ImportError:
        # The answer IS the missing module; the caller turns it into a sentence.
        return False
    return hasattr(bcrypt, "kdf")


def import_private_key(pem: str | None, passphrase: str | None) -> Any:
    """The user's own SSH key as an asyncssh key; a 400 sentence when unusable."""
    import asyncssh

    text = (pem or "").strip()
    if not text:
        raise ComputerError("Paste your private SSH key.", kind="bad_key")
    if text.startswith(("ssh-", "ecdsa-")):
        raise ComputerError(
            "That is a public key. Paste the PRIVATE key (it starts with -----BEGIN).",
            kind="bad_key",
        )
    try:
        return asyncssh.import_private_key(text + "\n", passphrase or None)
    except asyncssh.KeyEncryptionError as exc:
        if passphrase and not _bcrypt_kdf_available():
            raise ComputerError(
                "This protected key cannot be unlocked on this install: the "
                "bcrypt package is missing. Reinstall Personal Jarvis, or add "
                "the key without a passphrase.",
                kind="bad_key",
            ) from exc
        raise ComputerError(
            "The passphrase does not unlock this key."
            if passphrase
            else "This key is protected. Enter its passphrase.",
            kind="bad_key",
        ) from exc
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise ComputerError(
            "This does not look like a private SSH key (OpenSSH or PEM format).",
            kind="bad_key",
        ) from exc


def _check_provider(provider_id: str) -> str:
    if not providers.is_known(provider_id):
        raise ComputerError(f"Unknown provider: {provider_id}.")
    return provider_id


def _test_result(ok: bool, kind: str | None, message: str | None) -> dict[str, Any]:
    return {
        "ok": ok,
        "kind": kind,
        "message": message,
        "host_fingerprint": None,
        "facts": None,
        "latency_ms": None,
    }


def _new_id() -> str:
    return f"c_{secrets.token_hex(6)}"


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())[:80]
    if not cleaned:
        raise ComputerError("Give the computer a name.")
    return cleaned


def _check_host(host: str) -> str:
    host = host.strip()
    if not _HOST_RE.match(host):
        raise ComputerError("Enter an IP address or host name, for example 203.0.113.10.")
    return host.strip("[]")


def _check_user(username: str) -> str:
    username = username.strip()
    if not _USER_RE.match(username):
        raise ComputerError("Enter the login name, for example root or ubuntu.")
    return username


_ERROR_STATUS = {
    "auth": "auth_failed",
    "host_key_changed": "host_key_changed",
    "unreachable": "offline",
    "timeout": "offline",
}


def _login_error(exc: SshError, target: SshTarget) -> ComputerError:
    """A refused login as the sentence (and kind) the connect form acts on."""
    if exc.kind == "auth" and target.use_this_pc:
        if exc.password_offered:
            return ComputerError(
                "None of this PC's SSH keys opens this server. Enter its password once: "
                "the app then sets up its own key and forgets the password.",
                status=502,
                kind="needs_password",
            )
        return ComputerError(
            "This server only accepts keys it already knows. Add the app's key there "
            "once (one line, shown below), then try again.",
            status=502,
            kind="key_only",
        )
    return ComputerError(exc.message, status=502, kind=exc.kind)


class ComputerService:
    def __init__(self, store: ComputerStore | None = None) -> None:
        self._store = store or ComputerStore()
        #: Running background jobs (VM creation) per computer id.
        self._jobs: dict[str, asyncio.Task[None]] = {}

    # -- reading ------------------------------------------------------------

    def all(self) -> list[Computer]:
        return self._store.all()

    def get(self, computer_id: str) -> Computer:
        computer = self._store.get(computer_id)
        if computer is None:
            raise ComputerError("This computer does not exist.", status=404)
        return computer

    def is_busy(self, computer_id: str) -> bool:
        task = self._jobs.get(computer_id)
        return task is not None and not task.done()

    # -- connecting ---------------------------------------------------------

    def _target(self, computer: Computer, *, password: str | None = None) -> SshTarget:
        if password is not None:
            return SshTarget(
                host=computer.host,
                port=computer.port,
                username=computer.username,
                host_key=computer.host_key,
                password=password,
            )
        if computer.auth == "private_key":
            pem = get_secret(_private_key_slot(computer.id), env_fallback="")
            if not pem:
                raise ComputerError(
                    "The saved SSH key is missing. Paste it again.", status=409, kind="auth"
                )
            phrase = get_secret(_passphrase_slot(computer.id), env_fallback="")
            return SshTarget(
                host=computer.host,
                port=computer.port,
                username=computer.username,
                host_key=computer.host_key,
                client_key=import_private_key(pem, phrase),
            )
        if computer.auth == "password":
            stored = get_secret(_password_slot(computer.id), env_fallback="")
            if not stored:
                raise ComputerError(
                    "The saved password is missing. Enter it again.", status=409, kind="auth"
                )
            return SshTarget(
                host=computer.host,
                port=computer.port,
                username=computer.username,
                host_key=computer.host_key,
                password=stored,
            )
        try:
            key = identity.private_key()
        except identity.IdentityError as exc:
            raise ComputerError(str(exc), status=500) from exc
        return SshTarget(
            host=computer.host,
            port=computer.port,
            username=computer.username,
            host_key=computer.host_key,
            client_key=key,
        )

    def _pin(self, computer_id: str, session: Session) -> None:
        def change(row: Computer) -> Computer:
            if row.host_key:
                return row
            return row.model_copy(
                update={"host_key": session.host_key, "host_fingerprint": session.host_fingerprint}
            )

        self._store.update(computer_id, change)

    async def connect(self, computer_id: str) -> Session:
        """A long-lived authenticated connection; the caller closes it.

        For callers that keep a connection open across many channels (the IDE's
        remote terminals). Pins the host key on first contact like
        :meth:`session`.
        """
        computer = self.get(computer_id)
        try:
            opened = await open_session(self._target(computer))
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        self._pin(computer_id, opened)
        return opened

    @contextlib.asynccontextmanager
    async def session(self, computer_id: str) -> AsyncIterator[Session]:
        """An authenticated connection; pins the host key on first contact."""
        computer = self.get(computer_id)
        target = self._target(computer)
        try:
            opened = await open_session(target)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        self._pin(computer_id, opened)
        try:
            yield opened
        finally:
            close(opened)

    # -- checks -------------------------------------------------------------

    def _set_health(self, computer_id: str, health: ComputerHealth, **fields: Any) -> Computer:
        updated = self._store.update(
            computer_id, lambda row: row.model_copy(update={"health": health, **fields})
        )
        if updated is None:
            raise ComputerError("This computer does not exist.", status=404)
        return updated

    async def check(self, computer_id: str) -> Computer:
        """Connect, read the machine's facts and vitals, store both."""
        computer = self.get(computer_id)
        if computer.health.status == "provisioning" and self.is_busy(computer_id):
            return computer
        now = time.time()
        try:
            async with self.session(computer_id) as opened:
                try:
                    host = await remote_os.remote_host(computer_id, opened)
                    result = await _probe(opened, host)
                except SshError as exc:
                    # A probe that times out or loses the channel is a health
                    # reading like a refused login, not a crash of the check.
                    raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
                latency = opened.latency_ms
        except ComputerError as exc:
            status = _ERROR_STATUS.get(exc.kind or "", "error")
            if computer.kind == "local_vm" and status == "offline":
                status = await self._local_vm_state(computer) or status
            return self._set_health(
                computer_id,
                ComputerHealth(status=status, checked_at=now, message=exc.message),  # type: ignore[arg-type]
            )
        reading = parse_probe(result.stdout)
        return self._set_health(
            computer_id,
            ComputerHealth(
                status="online",
                checked_at=now,
                latency_ms=latency,
                load_1m=reading.load_1m,
                mem_used_pct=reading.mem_used_pct,
                disk_used_pct=reading.disk_used_pct,
                uptime_s=reading.uptime_s,
            ),
            facts=reading.facts,
        )

    async def check_all(self) -> list[Computer]:
        ids = [c.id for c in self.all()]
        results = await asyncio.gather(*(self.check(cid) for cid in ids), return_exceptions=True)
        for cid, outcome in zip(ids, results, strict=True):
            if isinstance(outcome, BaseException):
                log.warning("computers: check of %s failed: %r", cid, outcome)
        return self.all()

    async def _local_vm_state(self, computer: Computer) -> str | None:
        if not computer.provider_ref:
            return None
        try:
            instance = await local_vm.info(computer.provider_ref)
        except local_vm.LocalVmError as exc:
            log.info("computers: multipass state of %s unknown: %s", computer.id, exc.message)
            return None
        if instance is not None and instance.state.lower() == "stopped":
            return "stopped"
        return None

    async def run(
        self, computer_id: str, command: str, *, timeout_s: float = 60.0
    ) -> CommandResult:
        """Run one shell command on the computer and return its output."""
        if not command.strip():
            raise ComputerError("Type a command to run.")
        timeout_s = max(1.0, min(timeout_s, MAX_COMMAND_TIMEOUT_S))
        async with self.session(computer_id) as opened:
            try:
                return await run_command(opened, command, timeout_s=timeout_s)
            except SshError as exc:
                raise ComputerError(exc.message, status=502, kind=exc.kind) from exc

    # -- adding -------------------------------------------------------------

    async def _plant_key(self, target: SshTarget) -> Session:
        """Log in (password or this PC's keys), add Jarvis's key, prove it works."""
        try:
            opened = await open_session(target)
        except SshError as exc:
            raise _login_error(exc, target) from exc
        windows = False
        try:
            host = await remote_os.detect(opened)
            windows = host.windows
            if windows:
                result = await remote_os.run_powershell(
                    opened, authorize_key_script_windows(identity.public_key_line()), timeout_s=30
                )
            else:
                result = await run_command(
                    opened, authorize_key_command(identity.public_key_line()), timeout_s=20
                )
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        finally:
            close(opened)
        if result.exit_status not in (0, None):
            raise ComputerError(
                "The login worked, but Windows refused to save the key. Administrator "
                "accounts need an elevated login to change the SSH key file."
                if windows
                else "The login worked, but the key could not be saved on the server "
                "(is the home folder writable?).",
                status=502,
                kind="install_key",
            )
        verify = SshTarget(
            host=target.host,
            port=target.port,
            username=target.username,
            host_key=opened.host_key,
            client_key=identity.private_key(),
        )
        try:
            proof = await open_session(verify)
        except SshError as exc:
            raise ComputerError(
                "The key was saved, but the server still refuses key logins. "
                "Check that PubkeyAuthentication is enabled.",
                status=502,
                kind="install_key",
            ) from exc
        return proof

    async def add_server(
        self,
        *,
        name: str,
        host: str,
        port: int = 22,
        username: str = "root",
        auth: LoginMode = "key",
        password: str | None = None,
        keep_password: bool = False,
        private_key: str | None = None,
        passphrase: str | None = None,
        provider: ProviderId = "generic",
        provider_ref: str | None = None,
        region: str | None = None,
        plan: str | None = None,
    ) -> Computer:
        """Register a server. With a password, plant the key first (default).

        ``auth="auto"`` needs nothing but the address: the app's own key or any
        key this PC's ``ssh`` uses logs in once, the app's key is planted, and
        the computer is stored with key login. When none opens the server the
        error says whether a password would (``needs_password``) or only a key
        (``key_only``).
        """
        computer = Computer(
            id=_new_id(),
            name=_clean_name(name),
            kind="server",
            provider=_check_provider(provider),
            host=_check_host(host),
            port=port,
            username=_check_user(username),
            auth="key",
            provider_ref=provider_ref,
            region=region,
            plan=plan,
            created_at=time.time(),
        )
        if auth == "password":
            if not password:
                raise ComputerError("Enter the password for this login.")
            if keep_password:
                if not set_secret(_password_slot(computer.id), password):
                    raise ComputerError(
                        "The password could not be saved to the keyring.", status=500
                    )
                computer = computer.model_copy(update={"auth": "password"})
            else:
                proof = await self._plant_key(self._target(computer, password=password))
                close(proof)
                computer = computer.model_copy(
                    update={"host_key": proof.host_key, "host_fingerprint": proof.host_fingerprint}
                )
        elif auth == "private_key":
            import_private_key(private_key, passphrase)
            self._save_private_key(computer.id, private_key or "", passphrase)
            computer = computer.model_copy(update={"auth": "private_key"})
        elif auth == "auto":
            proof = await self._plant_key(self._this_pc_target(computer))
            close(proof)
            computer = computer.model_copy(
                update={"host_key": proof.host_key, "host_fingerprint": proof.host_fingerprint}
            )
        self._store.add(computer)
        return await self.check(computer.id)

    def _save_private_key(self, computer_id: str, pem: str, passphrase: str | None) -> None:
        if not set_secret(_private_key_slot(computer_id), pem.strip()):
            raise ComputerError("The SSH key could not be saved to the keyring.", status=500)
        if passphrase:
            if not set_secret(_passphrase_slot(computer_id), passphrase):
                raise ComputerError("The passphrase could not be saved to the keyring.", status=500)
        else:
            delete_secret(_passphrase_slot(computer_id))

    def _this_pc_target(self, computer: Computer) -> SshTarget:
        """The app's key plus this PC's own SSH keys, for one planting login."""
        try:
            key = identity.private_key()
        except identity.IdentityError as exc:
            raise ComputerError(str(exc), status=500) from exc
        return SshTarget(
            host=computer.host,
            port=computer.port,
            username=computer.username,
            host_key=computer.host_key,
            client_key=key,
            use_this_pc=True,
        )

    async def test_connection(
        self,
        *,
        host: str,
        port: int = 22,
        username: str = "root",
        auth: LoginMode = "key",
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None,
    ) -> dict[str, Any]:
        """Try a login WITHOUT saving anything; nothing is planted on the server."""
        if importlib.util.find_spec("asyncssh") is None:
            # A base dependency missing from an older install: say so in the
            # form instead of failing the request with a bare 500.
            return _test_result(
                False,
                "protocol",
                "SSH support is missing from this installation. Update the app to install it.",
            )
        try:
            base = SshTarget(host=_check_host(host), port=port, username=_check_user(username))
            if auth == "password":
                if not password:
                    raise ComputerError("Enter the password for this login.", kind="auth")
                target = dataclasses.replace(base, password=password)
            elif auth == "private_key":
                key = import_private_key(private_key, passphrase)
                target = dataclasses.replace(base, client_key=key)
            elif auth == "auto":
                target = dataclasses.replace(
                    base, client_key=identity.private_key(), use_this_pc=True
                )
            else:
                target = dataclasses.replace(base, client_key=identity.private_key())
        except ComputerError as exc:  # the answer IS the result: shown in the form
            return _test_result(False, exc.kind or "protocol", exc.message)
        except identity.IdentityError as exc:  # reported in the result, not raised
            return _test_result(False, "protocol", str(exc))
        try:
            opened = await open_session(target)
        except SshError as exc:  # a failed login is the test's answer, not an error
            if target.use_this_pc:
                refused = _login_error(exc, target)
                return _test_result(False, refused.kind or exc.kind, refused.message)
            message = exc.message
            if exc.kind == "auth" and auth == "key":
                message = (
                    "The server does not accept the app's key yet. Add the public key on "
                    "the server, or log in once with the password."
                )
            return _test_result(False, exc.kind, message)
        facts: dict[str, Any] | None = None
        try:
            probe = await _probe(opened, await remote_os.detect(opened))
            facts = parse_probe(probe.stdout).facts.model_dump(mode="json")
        except SshError as exc:
            log.info("computers: test probe failed after login: %s", exc.message)
        finally:
            close(opened)
        return {
            "ok": True,
            "kind": None,
            "message": None,
            "host_fingerprint": opened.host_fingerprint,
            "facts": facts,
            "latency_ms": opened.latency_ms,
        }

    async def set_credentials(
        self,
        computer_id: str,
        *,
        auth: AuthMethod,
        password: str | None = None,
        keep_password: bool = False,
        private_key: str | None = None,
        passphrase: str | None = None,
    ) -> Computer:
        """Switch how the app logs in to an existing computer."""
        self.get(computer_id)
        if auth == "password":
            if not password:
                raise ComputerError("Enter the password for this login.")
            if not keep_password:
                return await self.install_key(computer_id, password)
            if not set_secret(_password_slot(computer_id), password):
                raise ComputerError("The password could not be saved to the keyring.", status=500)
            _forget_secrets(computer_id, keep="password")
        elif auth == "private_key":
            import_private_key(private_key, passphrase)
            self._save_private_key(computer_id, private_key or "", passphrase)
            _forget_secrets(computer_id, keep="private_key")
        else:
            _forget_secrets(computer_id)
        self._store.update(computer_id, lambda row: row.model_copy(update={"auth": auth}))
        return await self.check(computer_id)

    async def install_key(self, computer_id: str, password: str) -> Computer:
        """Plant Jarvis's key with a one-time password; switch to key login."""
        computer = self.get(computer_id)
        if not password:
            raise ComputerError("Enter the password for this login.")
        proof = await self._plant_key(self._target(computer, password=password))
        close(proof)
        _forget_secrets(computer_id)
        self._store.update(
            computer_id,
            lambda row: row.model_copy(
                update={
                    "auth": "key",
                    "host_key": row.host_key or proof.host_key,
                    "host_fingerprint": row.host_fingerprint or proof.host_fingerprint,
                }
            ),
        )
        return await self.check(computer_id)

    # -- editing ------------------------------------------------------------

    def update(self, computer_id: str, **fields: Any) -> Computer:
        changes: dict[str, Any] = {}
        if fields.get("name") is not None:
            changes["name"] = _clean_name(fields["name"])
        if fields.get("host") is not None:
            changes["host"] = _check_host(fields["host"])
        if fields.get("username") is not None:
            changes["username"] = _check_user(fields["username"])
        if fields.get("port") is not None:
            changes["port"] = int(fields["port"])
        current = self.get(computer_id)
        if "host" in changes and changes["host"] != current.host:
            # A different address is a different machine until proven otherwise.
            changes.update(host_key=None, host_fingerprint=None)
        updated = self._store.update(computer_id, lambda row: row.model_copy(update=changes))
        if updated is None:
            raise ComputerError("This computer does not exist.", status=404)
        return updated

    async def trust_new_host_key(self, computer_id: str) -> Computer:
        """Forget the pinned identity and pin whatever answers next."""
        self.get(computer_id)
        self._store.update(
            computer_id,
            lambda row: row.model_copy(update={"host_key": None, "host_fingerprint": None}),
        )
        return await self.check(computer_id)

    async def remove(self, computer_id: str, *, destroy_vm: bool = False) -> bool:
        computer = self.get(computer_id)
        task = self._jobs.pop(computer_id, None)
        if task is not None and not task.done():
            task.cancel()
        if destroy_vm and computer.kind == "local_vm" and computer.provider_ref:
            try:
                await local_vm.delete(computer.provider_ref)
            except local_vm.LocalVmError as exc:
                raise ComputerError(exc.message, status=502) from exc
        _forget_secrets(computer_id)
        remote_os.forget(computer_id)
        return self._store.remove(computer_id)

    # -- cloud import ---------------------------------------------------------

    async def cloud_servers(self, provider_id: str) -> list[dict[str, Any]]:
        try:
            servers = await cloud.list_servers(provider_id)
        except cloud.CloudError as exc:
            raise ComputerError(exc.message, status=502) from exc
        added = {
            c.provider_ref: c.id for c in self.all() if c.provider == provider_id and c.provider_ref
        }
        return [{**s.to_dict(), "added_as": added.get(s.id)} for s in servers]

    async def import_cloud_server(
        self,
        provider_id: str,
        server_id: str,
        *,
        name: str | None = None,
        username: str = "root",
        password: str | None = None,
    ) -> Computer:
        servers = await self.cloud_servers(provider_id)
        match = next((s for s in servers if s["id"] == server_id), None)
        if match is None:
            raise ComputerError("This server is no longer in the account.", status=404)
        if match["added_as"]:
            raise ComputerError("This server is already connected.", status=409)
        if not match["host"]:
            raise ComputerError("This server has no public IPv4 address yet.")
        spec = cloud.provider(provider_id)
        attached = False
        if spec.attaches_keys and not password:
            try:
                await cloud.attach_public_key(provider_id, server_id, identity.public_key_line())
                attached = True
            except cloud.CloudError as exc:
                raise ComputerError(exc.message, status=502) from exc
        computer = await self.add_server(
            name=name or match["name"],
            host=match["host"],
            username=username,
            auth="password" if password else "key",
            password=password,
            provider=provider_id,  # type: ignore[arg-type]
            provider_ref=server_id,
            region=match.get("region"),
            plan=match.get("plan"),
        )
        if attached and computer.health.status == "auth_failed":
            # The provider applies the key asynchronously; say so instead of
            # leaving a bare "login refused" on a key that is still on its way.
            computer = self._set_health(
                computer.id,
                computer.health.model_copy(
                    update={
                        "message": f"{spec.name} is still adding the key. Check again in a minute."
                    }
                ),
            )
        return computer

    # -- local VMs ------------------------------------------------------------

    async def create_local_vm(
        self, *, name: str, cpus: int, memory_gb: int, disk_gb: int, image: str
    ) -> Computer:
        if local_vm.binary() is None:
            raise ComputerError(
                "Install Multipass first to create local virtual machines.", status=409
            )
        if not local_vm.valid_name(name):
            raise ComputerError(
                "Use 2-40 lowercase letters, digits or hyphens, starting with a letter."
            )
        if any(c.kind == "local_vm" and c.provider_ref == name for c in self.all()):
            raise ComputerError("A virtual machine with this name already exists.", status=409)
        try:
            public_key = identity.public_key_line()
        except identity.IdentityError as exc:
            raise ComputerError(str(exc), status=500) from exc
        computer = Computer(
            id=_new_id(),
            name=name,
            kind="local_vm",
            provider="multipass",
            host="0.0.0.0",  # noqa: S104 — placeholder until Multipass reports the VM's address
            username=local_vm.DEFAULT_USER,
            provider_ref=name,
            region="This computer",
            plan=f"{cpus} CPU · {memory_gb} GB · {disk_gb} GB",
            created_at=time.time(),
            health=ComputerHealth(
                status="provisioning",
                checked_at=time.time(),
                message=(
                    "Creating the virtual machine. The first one downloads Ubuntu "
                    "and takes a few minutes."
                ),
            ),
        )
        self._store.add(computer)
        self._jobs[computer.id] = asyncio.create_task(
            self._provision(computer.id, name, cpus, memory_gb, disk_gb, image, public_key),
            name=f"computers-provision-{name}",
        )
        return computer

    async def _provision(
        self,
        computer_id: str,
        name: str,
        cpus: int,
        memory_gb: int,
        disk_gb: int,
        image: str,
        public_key: str,
    ) -> None:
        try:
            instance = await local_vm.launch(
                name,
                cpus=cpus,
                memory_gb=memory_gb,
                disk_gb=disk_gb,
                image=image,
                public_key=public_key,
            )
        except local_vm.LocalVmError as exc:
            log.warning("computers: creating VM %s failed: %s", name, exc.message)
            with contextlib.suppress(ComputerError):
                self._set_health(
                    computer_id,
                    ComputerHealth(status="error", checked_at=time.time(), message=exc.message),
                )
            return
        self._store.update(
            computer_id,
            lambda row: row.model_copy(
                update={
                    "host": instance.ipv4 or row.host,
                    "health": ComputerHealth(status="unknown", checked_at=time.time()),
                }
            ),
        )
        # cloud-init may still be writing the key for a few seconds after boot.
        for attempt in range(6):
            refreshed = await self.check(computer_id)
            if refreshed.health.status == "online":
                return
            await asyncio.sleep(5 * (attempt + 1))

    async def power(self, computer_id: str, action: str) -> Computer:
        computer = self.get(computer_id)
        if computer.kind != "local_vm" or not computer.provider_ref:
            raise ComputerError("Only local virtual machines can be started or stopped here.")
        try:
            if action == "start":
                await local_vm.start(computer.provider_ref)
                instance = await local_vm.info(computer.provider_ref)
                if instance is not None and instance.ipv4 and instance.ipv4 != computer.host:
                    # A restarted VM may come back on a new address; same machine.
                    self._store.update(
                        computer_id, lambda row: row.model_copy(update={"host": instance.ipv4})
                    )
            elif action == "stop":
                await local_vm.stop(computer.provider_ref)
                return self._set_health(
                    computer_id, ComputerHealth(status="stopped", checked_at=time.time())
                )
            else:
                raise ComputerError("Choose start or stop.")
        except local_vm.LocalVmError as exc:
            raise ComputerError(exc.message, status=502) from exc
        return await self.check(computer_id)


_SERVICE: ComputerService | None = None


def get_service() -> ComputerService:
    """The process-wide service (routes, CLI and later features share it)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ComputerService()
    return _SERVICE
