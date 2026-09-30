"""REST API for the Computers section (Settings -> Computers).

    GET    /api/computers                         list + Jarvis's public key
    POST   /api/computers                         add an SSH server
    POST   /api/computers/check-all               check every computer
    GET    /api/computers/identity                Jarvis's public key + fingerprint
    GET    /api/computers/providers               provider catalog (SSH guides, API)
    POST   /api/computers/test                    try a login without saving
    PUT    /api/computers/{id}/credentials        switch the login method
    GET    /api/computers/cloud                   hosting providers + token state
    PUT    /api/computers/cloud/{provider}/token  save an API token (keyring)
    DELETE /api/computers/cloud/{provider}/token  forget it
    GET    /api/computers/cloud/{provider}/servers  the account's servers
    POST   /api/computers/cloud/{provider}/import   connect one of them
    GET    /api/computers/local                   Multipass state + instances
    POST   /api/computers/local                   create a local VM (background)
    GET    /api/computers/{id}                    one computer
    PATCH  /api/computers/{id}                    rename / edit address
    DELETE /api/computers/{id}                    remove (optionally destroy the VM)
    POST   /api/computers/{id}/check              connect and read vitals
    POST   /api/computers/{id}/run                run one shell command
    POST   /api/computers/{id}/install-key        plant Jarvis's key with a password
    POST   /api/computers/{id}/trust-host-key     accept a changed server identity
    POST   /api/computers/{id}/power              start / stop a local VM
    GET    /api/computers/{id}/readiness          tmux / git / agent CLIs / logins
    POST   /api/computers/{id}/install            install what is missing (job)
    GET    /api/computers/{id}/install            that job's state and log
    POST   /api/computers/{id}/copy-login         copy this computer's CLI login there

Static paths are registered before ``/{computer_id}`` so the dynamic route
cannot swallow them. Store reads are plain ``def`` (threadpool); anything that
talks to a network or a subprocess is ``async`` and awaits it. Passwords and
tokens arrive in request bodies and go straight to the keyring or the SSH
handshake — they are never logged or echoed back.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.computers import cloud, identity, local_vm, providers, toolbox
from jarvis.computers.models import Computer
from jarvis.computers.service import ComputerError, get_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/computers", tags=["computers"])


def _fail(exc: ComputerError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"message": exc.message, "kind": exc.kind})


def _row(computer: Computer) -> dict[str, Any]:
    data = computer.model_dump(mode="json")
    data["busy"] = get_service().is_busy(computer.id)
    data["provider_name"] = providers.display_name(computer.provider)
    return data


# -- request bodies ----------------------------------------------------------


AuthLiteral = Literal["key", "password", "private_key"]
#: What a connect form may ask for; "auto" tries this PC's own SSH keys.
LoginLiteral = Literal["key", "password", "private_key", "auto"]


class AddServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", min_length=1, max_length=64)
    auth: LoginLiteral = "key"
    password: str | None = Field(default=None, max_length=1024)
    keep_password: bool = False
    private_key: str | None = Field(default=None, max_length=32_000)
    passphrase: str | None = Field(default=None, max_length=1024)
    provider: str = Field(default="generic", max_length=40)


class TestBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", min_length=1, max_length=64)
    auth: LoginLiteral = "key"
    password: str | None = Field(default=None, max_length=1024)
    keep_password: bool = False
    private_key: str | None = Field(default=None, max_length=32_000)
    passphrase: str | None = Field(default=None, max_length=1024)
    provider: str = Field(default="generic", max_length=40)


class CredentialsBody(BaseModel):
    auth: AuthLiteral
    password: str | None = Field(default=None, max_length=1024)
    keep_password: bool = False
    private_key: str | None = Field(default=None, max_length=32_000)
    passphrase: str | None = Field(default=None, max_length=1024)


class UpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=64)


class RunBody(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    timeout_s: float = Field(default=60.0, ge=1, le=300)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class PowerBody(BaseModel):
    action: Literal["start", "stop"]


class TokenBody(BaseModel):
    token: str = Field(min_length=8, max_length=4096)


class ImportBody(BaseModel):
    server_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    username: str = Field(default="root", max_length=64)
    password: str | None = Field(default=None, max_length=1024)


class InstallBody(BaseModel):
    items: list[Literal["tmux", "git", "node", "claude", "codex"]] = Field(min_length=1)


class CopyLoginBody(BaseModel):
    agent: Literal["claude", "codex"]


class LocalVmBody(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    cpus: int = Field(default=2, ge=1, le=64)
    memory_gb: int = Field(default=4, ge=1, le=256)
    disk_gb: int = Field(default=20, ge=5, le=2048)
    image: str = Field(default="24.04", max_length=40, pattern=r"^[A-Za-z0-9.:\-]+$")


# -- collection ----------------------------------------------------------------


@router.get("")
def list_computers() -> dict[str, Any]:
    return {"computers": [_row(c) for c in get_service().all()]}


@router.post("", status_code=201)
async def add_computer(body: AddServerBody) -> dict[str, Any]:
    try:
        computer = await get_service().add_server(
            name=body.name,
            host=body.host,
            port=body.port,
            username=body.username,
            auth=body.auth,
            password=body.password,
            keep_password=body.keep_password,
            private_key=body.private_key,
            passphrase=body.passphrase,
            provider=body.provider,
        )
    except ComputerError as exc:
        raise _fail(exc) from exc
    return _row(computer)


@router.post("/test")
async def test_computer(body: TestBody) -> dict[str, Any]:
    """Try the login a form describes, saving nothing and planting nothing."""
    return await get_service().test_connection(
        host=body.host,
        port=body.port,
        username=body.username,
        auth=body.auth,
        password=body.password,
        private_key=body.private_key,
        passphrase=body.passphrase,
    )


@router.get("/providers")
def list_providers() -> dict[str, Any]:
    """Every place a computer can come from, with how to reach it there."""
    return {"providers": providers.catalog_rows()}


@router.post("/check-all")
async def check_all() -> dict[str, Any]:
    computers = await get_service().check_all()
    return {"computers": [_row(c) for c in computers]}


@router.get("/identity")
def get_identity() -> dict[str, Any]:
    try:
        return identity.describe()
    except identity.IdentityError as exc:
        raise HTTPException(status_code=500, detail={"message": str(exc), "kind": None}) from exc


# -- hosting providers ---------------------------------------------------------


@router.get("/cloud")
def list_cloud_providers() -> dict[str, Any]:
    rows = []
    for spec in cloud.PROVIDERS.values():
        rows.append(
            {
                "id": spec.id,
                "name": spec.name,
                "connected": bool(cloud.token(spec.id)),
                "console_url": spec.console_url,
                "token_label": spec.credential_label,
                "setup_hint": spec.setup_hint,
                "attaches_keys": spec.attaches_keys,
            }
        )
    return {"providers": rows}


def _known_provider(provider_id: str) -> None:
    if provider_id not in cloud.PROVIDERS:
        raise HTTPException(status_code=404, detail={"message": "Unknown provider.", "kind": None})


@router.put("/cloud/{provider_id}/token")
async def save_cloud_token(provider_id: str, body: TokenBody) -> dict[str, Any]:
    _known_provider(provider_id)
    previous = cloud.token(provider_id)
    try:
        cloud.save_token(provider_id, body.token)
        # Prove the token before calling it connected; a typo should say so now.
        servers = await get_service().cloud_servers(provider_id)
    except cloud.CloudError as exc:
        raise HTTPException(status_code=500, detail={"message": exc.message, "kind": None}) from exc
    except ComputerError as exc:
        # Keep a working token rather than replace it with one that failed.
        if previous:
            cloud.save_token(provider_id, previous)
        else:
            try:
                cloud.forget_token(provider_id)
            except cloud.CloudError:
                log.warning("computers: a refused %s token could not be removed", provider_id)
        raise _fail(exc) from exc
    return {"connected": True, "servers": servers}


@router.delete("/cloud/{provider_id}/token")
def forget_cloud_token(provider_id: str) -> dict[str, Any]:
    _known_provider(provider_id)
    try:
        cloud.forget_token(provider_id)
    except cloud.CloudError as exc:
        raise HTTPException(status_code=500, detail={"message": exc.message, "kind": None}) from exc
    return {"connected": False}


@router.get("/cloud/{provider_id}/servers")
async def list_cloud_servers(provider_id: str) -> dict[str, Any]:
    _known_provider(provider_id)
    try:
        return {"servers": await get_service().cloud_servers(provider_id)}
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.post("/cloud/{provider_id}/import", status_code=201)
async def import_cloud_server(provider_id: str, body: ImportBody) -> dict[str, Any]:
    _known_provider(provider_id)
    try:
        computer = await get_service().import_cloud_server(
            provider_id,
            body.server_id,
            name=body.name,
            username=body.username,
            password=body.password,
        )
    except ComputerError as exc:
        raise _fail(exc) from exc
    return _row(computer)


# -- local virtual machines ------------------------------------------------------


@router.get("/local")
async def local_status() -> dict[str, Any]:
    return await local_vm.status()


@router.post("/local", status_code=202)
async def create_local_vm(body: LocalVmBody) -> dict[str, Any]:
    try:
        computer = await get_service().create_local_vm(
            name=body.name,
            cpus=body.cpus,
            memory_gb=body.memory_gb,
            disk_gb=body.disk_gb,
            image=body.image,
        )
    except ComputerError as exc:
        raise _fail(exc) from exc
    return _row(computer)


# -- one computer ------------------------------------------------------------------


@router.get("/{computer_id}")
def get_computer(computer_id: str) -> dict[str, Any]:
    try:
        return _row(get_service().get(computer_id))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.patch("/{computer_id}")
def update_computer(computer_id: str, body: UpdateBody) -> dict[str, Any]:
    try:
        return _row(get_service().update(computer_id, **body.model_dump(exclude_none=True)))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.delete("/{computer_id}")
async def remove_computer(computer_id: str, destroy_vm: bool = False) -> dict[str, Any]:
    try:
        removed = await get_service().remove(computer_id, destroy_vm=destroy_vm)
    except ComputerError as exc:
        raise _fail(exc) from exc
    # Its IDE panes' connection goes with it (the agents on it are not killed).
    from jarvis.computers.remote_terminal import forget_pool

    forget_pool(computer_id)
    return {"removed": removed}


@router.post("/{computer_id}/check")
async def check_computer(computer_id: str) -> dict[str, Any]:
    try:
        return _row(await get_service().check(computer_id))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.post("/{computer_id}/run", openapi_extra={"x-jarvis-dangerous": True})
async def run_on_computer(computer_id: str, body: RunBody) -> dict[str, Any]:
    try:
        result = await get_service().run(computer_id, body.command, timeout_s=body.timeout_s)
    except ComputerError as exc:
        raise _fail(exc) from exc
    return {
        "exit_status": result.exit_status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }


@router.put("/{computer_id}/credentials")
async def set_credentials(computer_id: str, body: CredentialsBody) -> dict[str, Any]:
    """Switch the login method (a password alone plants the app's key once)."""
    try:
        computer = await get_service().set_credentials(
            computer_id,
            auth=body.auth,
            password=body.password,
            keep_password=body.keep_password,
            private_key=body.private_key,
            passphrase=body.passphrase,
        )
    except ComputerError as exc:
        raise _fail(exc) from exc
    return _row(computer)


@router.post("/{computer_id}/install-key")
async def install_key(computer_id: str, body: PasswordBody) -> dict[str, Any]:
    try:
        return _row(await get_service().install_key(computer_id, body.password))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.post("/{computer_id}/trust-host-key")
async def trust_host_key(computer_id: str) -> dict[str, Any]:
    try:
        return _row(await get_service().trust_new_host_key(computer_id))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.post("/{computer_id}/power", openapi_extra={"x-jarvis-dangerous": True})
async def power_computer(computer_id: str, body: PowerBody) -> dict[str, Any]:
    try:
        return _row(await get_service().power(computer_id, body.action))
    except ComputerError as exc:
        raise _fail(exc) from exc


@router.get("/{computer_id}/readiness")
async def computer_readiness(computer_id: str) -> dict[str, Any]:
    """What coding agents need on this computer, and what is there."""
    try:
        readiness = await toolbox.inspect(computer_id)
    except ComputerError as exc:
        raise _fail(exc) from exc
    job = toolbox.job(computer_id)
    return {**readiness.to_dict(), "install": job.to_dict() if job else None}


@router.post("/{computer_id}/install", status_code=202, openapi_extra={"x-jarvis-dangerous": True})
async def install_tools(computer_id: str, body: InstallBody) -> dict[str, Any]:
    try:
        job = await toolbox.start_install(computer_id, list(body.items))
    except ComputerError as exc:
        raise _fail(exc) from exc
    return job.to_dict()


@router.get("/{computer_id}/install")
def install_state(computer_id: str) -> dict[str, Any]:
    job = toolbox.job(computer_id)
    return {"install": job.to_dict() if job else None}


@router.post("/{computer_id}/copy-login")
async def copy_login(computer_id: str, body: CopyLoginBody) -> dict[str, Any]:
    """Copy this computer's CLI login to the server — only ever on this request."""
    try:
        await toolbox.copy_login(computer_id, body.agent)
    except ComputerError as exc:
        raise _fail(exc) from exc
    return {"copied": body.agent}
