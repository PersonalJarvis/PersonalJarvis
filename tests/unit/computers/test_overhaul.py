"""Provider catalog, the user's own SSH key, try-before-save, and switching logins."""

from __future__ import annotations

import asyncssh
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.computers import cloud, identity, providers
from jarvis.computers.service import ComputerError, ComputerService
from jarvis.ui.web import computers_routes
from tests.fakes.fake_ssh_server import TEST_PASSWORD, FakeSshServer


@pytest.fixture
async def ssh_server():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def own_key() -> asyncssh.SSHKey:
    return asyncssh.generate_private_key("ssh-ed25519", comment="me@laptop")


def _pem(key: asyncssh.SSHKey, passphrase: str | None = None) -> str:
    return key.export_private_key("openssh", passphrase=passphrase).decode()


def _public(key: asyncssh.SSHKey) -> str:
    return key.export_public_key("openssh").decode().strip()


# -- catalog -------------------------------------------------------------------


def test_catalog_has_the_promised_providers_and_shape(secret_box) -> None:  # noqa: ANN001
    rows = providers.catalog_rows()
    ids = [row["id"] for row in rows]
    for wanted in (
        "hostinger",
        "hetzner",
        "digitalocean",
        "vultr",
        "linode",
        "aws_lightsail",
        "contabo",
        "ionos",
        "strato",
        "ovhcloud",
        "oracle_cloud",
        "raspberry_pi",
        "home_server",
        "generic",
    ):
        assert wanted in ids
    assert ids[-1] == "generic"
    with_api = {row["id"] for row in rows if row["api"] is not None}
    assert with_api == {"hostinger", "hetzner", "digitalocean", "vultr", "linode"}
    pi = next(row for row in rows if row["id"] == "raspberry_pi")
    assert pi["category"] == "home" and pi["ssh"]["default_username"] == "pi"
    for row in rows:
        assert set(row["ssh"]) == {
            "default_username",
            "ip_hint",
            "key_hint",
            "key_url",
            "password_hint",
        }
        if row["api"]:
            assert set(row["api"]) == {
                "connected",
                "console_url",
                "setup_hint",
                "attaches_keys",
                "token_label",
            }


# -- the user's own key ------------------------------------------------------------


async def test_add_with_own_private_key_logs_in_and_keeps_the_key_out_of_json(
    computer_service: ComputerService,
    ssh_server: FakeSshServer,
    own_key,
    secret_box,  # noqa: ANN001
) -> None:
    ssh_server.state.authorized.add(_public(own_key))

    computer = await computer_service.add_server(
        name="lightsail",
        host="127.0.0.1",
        port=ssh_server.port,
        auth="private_key",
        private_key=_pem(own_key, "pw1"),
        passphrase="pw1",  # noqa: S106 — test key
        provider="aws_lightsail",
    )

    assert computer.auth == "private_key" and computer.provider == "aws_lightsail"
    assert computer.health.status == "online", computer.health.message
    assert secret_box.values[f"computer_private_key_{computer.id}"].startswith("-----BEGIN")
    assert "BEGIN" not in computer.model_dump_json()

    await computer_service.remove(computer.id)
    assert not any(k.endswith(computer.id) for k in secret_box.values)


async def test_bad_key_and_wrong_passphrase_are_sentences(
    computer_service: ComputerService,
    own_key,  # noqa: ANN001
) -> None:
    for pem, phrase in (
        ("nonsense", None),
        (_pem(own_key, "right"), "wrong"),
        (_pem(own_key, "right"), None),
        (_public(own_key), None),
    ):
        with pytest.raises(ComputerError) as caught:
            await computer_service.add_server(
                name="x",
                host="127.0.0.1",
                port=1,
                auth="private_key",
                private_key=pem,
                passphrase=phrase,
            )
        assert caught.value.kind == "bad_key" and caught.value.status == 400
    assert computer_service.all() == []


def test_protected_keys_can_be_unlocked_on_this_install() -> None:
    """``asyncssh[bcrypt]`` is a declared dependency: ssh-keygen's default
    format needs bcrypt's KDF, and without it no protected key can be added."""
    from jarvis.computers import service

    assert service._bcrypt_kdf_available()  # noqa: SLF001


def test_a_missing_bcrypt_is_named_instead_of_blaming_the_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.computers import service

    def _locked(*_args: object, **_kwargs: object) -> object:
        raise asyncssh.KeyEncryptionError(
            "OpenSSH private key encryption requires bcrypt with KDF support"
        )

    monkeypatch.setattr(asyncssh, "import_private_key", _locked)
    monkeypatch.setattr(service, "_bcrypt_kdf_available", lambda: False)
    with pytest.raises(ComputerError) as caught:
        # A header with a dummy body, split so secret scanners do not read it as a key.
        service.import_private_key("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nx", "pw")
    assert caught.value.kind == "bad_key"
    assert "bcrypt" in str(caught.value)
    assert "passphrase does not unlock" not in str(caught.value)


async def test_unknown_provider_is_refused(computer_service: ComputerService) -> None:
    with pytest.raises(ComputerError):
        await computer_service.add_server(name="x", host="127.0.0.1", provider="nope")


# -- try before save ------------------------------------------------------------------


async def test_test_connection_for_all_three_logins(
    computer_service: ComputerService,
    ssh_server: FakeSshServer,
    own_key,  # noqa: ANN001
) -> None:
    common = {"host": "127.0.0.1", "port": ssh_server.port, "username": "root"}

    by_password = await computer_service.test_connection(
        **common, auth="password", password=TEST_PASSWORD
    )
    assert by_password["ok"] and by_password["facts"]["os_id"] == "ubuntu"
    assert by_password["host_fingerprint"].startswith("SHA256:")
    assert not ssh_server.state.authorized, "a test plants nothing"

    jarvis_key = await computer_service.test_connection(**common, auth="key")
    assert jarvis_key["ok"] is False and jarvis_key["kind"] == "auth"
    ssh_server.state.authorized.add(identity.public_key_line())
    assert (await computer_service.test_connection(**common, auth="key"))["ok"]

    ssh_server.state.authorized.add(_public(own_key))
    mine = await computer_service.test_connection(
        **common, auth="private_key", private_key=_pem(own_key)
    )
    assert mine["ok"] and mine["latency_ms"] is not None

    broken = await computer_service.test_connection(
        **common, auth="private_key", private_key="not a key"
    )
    assert broken == {**broken, "ok": False, "kind": "bad_key"}
    assert computer_service.all() == [], "nothing was saved"


# -- switching logins -------------------------------------------------------------------


async def test_switch_from_jarvis_key_to_own_key_and_to_kept_password(
    computer_service: ComputerService,
    ssh_server: FakeSshServer,
    own_key,
    secret_box,  # noqa: ANN001
) -> None:
    ssh_server.state.authorized.add(identity.public_key_line())
    computer = await computer_service.add_server(name="box", host="127.0.0.1", port=ssh_server.port)
    ssh_server.state.authorized.add(_public(own_key))

    switched = await computer_service.set_credentials(
        computer.id, auth="private_key", private_key=_pem(own_key)
    )
    assert switched.auth == "private_key" and switched.health.status == "online"

    kept = await computer_service.set_credentials(
        computer.id, auth="password", password=TEST_PASSWORD, keep_password=True
    )
    assert kept.auth == "password" and kept.health.status == "online"
    assert f"computer_private_key_{computer.id}" not in secret_box.values

    planted = await computer_service.set_credentials(
        computer.id, auth="password", password=TEST_PASSWORD
    )
    assert planted.auth == "key" and f"computer_password_{computer.id}" not in secret_box.values


# -- routes -------------------------------------------------------------------------------


@pytest.fixture
def client(computer_service: ComputerService, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(computers_routes, "get_service", lambda: computer_service)
    app = FastAPI()
    app.include_router(computers_routes.router)
    return TestClient(app)


def test_routes_providers_test_and_bad_key(client: TestClient) -> None:
    body = client.get("/api/computers/providers").json()
    assert body["providers"][0]["id"] == "hostinger"

    tried = client.post(
        "/api/computers/test", json={"host": "127.0.0.1", "port": 1, "auth": "key"}
    ).json()
    assert tried["ok"] is False and tried["kind"] in {"unreachable", "timeout"}

    refused = client.post(
        "/api/computers",
        json={"name": "x", "host": "127.0.0.1", "auth": "private_key", "private_key": "junk"},
    )
    assert refused.status_code == 400 and refused.json()["detail"]["kind"] == "bad_key"

    listed = client.post(
        "/api/computers", json={"name": "y", "host": "127.0.0.1", "port": 1, "provider": "strato"}
    ).json()
    assert listed["provider_name"] == "Strato"


# -- Vultr and Linode --------------------------------------------------------------------


async def test_vultr_follows_the_cursor(secret_box) -> None:  # noqa: ANN001
    secret_box.set("vultr_api_token", "vultr-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/instances"
        first = "cursor" not in request.url.params
        instance = {
            "id": "a1" if first else "b2",
            "label": "web" if first else "db",
            "main_ip": "198.51.100.7" if first else "0.0.0.0",  # noqa: S104
            "status": "active",
            "power_status": "running",
            "os": "Ubuntu 24.04 LTS x64",
            "plan": "vc2-1c-1gb",
            "region": "fra",
            "vcpu_count": 1,
            "ram": 1024,
            "disk": 25,
        }
        meta = {"total": 2, "links": {"next": "abc" if first else "", "prev": ""}}
        return httpx.Response(200, json={"instances": [instance], "meta": meta})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        servers = await cloud.list_servers("vultr", client=http)

    assert [s.name for s in servers] == ["web", "db"]
    assert servers[0].host == "198.51.100.7" and servers[0].running
    assert servers[1].host is None, "0.0.0.0 means no IP yet"
    assert servers[0].memory_mb == 1024 and servers[0].disk_gb == 25.0


async def test_linode_pages_and_public_ip(secret_box) -> None:  # noqa: ANN001
    secret_box.set("linode_api_token", "linode-token")

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        row = {
            "id": page,
            "label": f"li{page}",
            "status": "running",
            "ipv4": ["192.168.130.2", f"203.0.113.{page}"],
            "type": "g6-standard-1",
            "region": "eu-central",
            "image": "linode/ubuntu24.04",
            "specs": {"vcpus": 1, "memory": 2048, "disk": 51200},
        }
        return httpx.Response(200, json={"data": [row], "page": page, "pages": 2, "results": 2})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        servers = await cloud.list_servers("linode", client=http)

    assert [s.host for s in servers] == ["203.0.113.1", "203.0.113.2"]
    assert servers[0].os == "ubuntu24.04" and servers[0].disk_gb == 50.0
