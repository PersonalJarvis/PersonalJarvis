"""The whole SSH path against a real in-process server.

Password once -> Jarvis key planted -> key login proven -> identity pinned ->
vitals read; then a changed server identity is refused until confirmed.
"""

from __future__ import annotations

import pytest

from jarvis.computers import identity
from jarvis.computers.service import ComputerError, ComputerService
from tests.fakes.fake_ssh_server import TEST_PASSWORD, FakeSshServer


@pytest.fixture
async def ssh_server():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def test_password_once_plants_the_key_and_forgets_the_password(
    computer_service: ComputerService, ssh_server: FakeSshServer, secret_box
) -> None:  # noqa: ANN001
    computer = await computer_service.add_server(
        name="Hostinger VPS",
        host="127.0.0.1",
        port=ssh_server.port,
        username="root",
        auth="password",
        password=TEST_PASSWORD,
    )

    assert computer.auth == "key"
    assert computer.health.status == "online", computer.health.message
    assert computer.host_key and computer.host_fingerprint.startswith("SHA256:")
    assert computer.facts is not None
    assert computer.facts.os_name == "Ubuntu 24.04.1 LTS"
    assert computer.facts.cpu_count == 4
    assert computer.health.disk_used_pct == 25.0
    # The public key reached authorized_keys; no password was kept anywhere.
    assert any(identity.public_key_line().split()[1] in k for k in ssh_server.state.authorized)
    assert not any(k.startswith("computer_password_") for k in secret_box.values)


async def test_run_returns_output_and_exit_status(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    ssh_server.state.authorized.add(identity.public_key_line())
    computer = await computer_service.add_server(name="box", host="127.0.0.1", port=ssh_server.port)

    ok = await computer_service.run(computer.id, "uptime")
    failed = await computer_service.run(computer.id, "fail")

    assert ok.stdout == "ran: uptime\n" and ok.exit_status == 0
    assert failed.exit_status == 3 and failed.stderr == "boom\n"


async def test_key_not_installed_reports_auth_failed(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await computer_service.add_server(name="box", host="127.0.0.1", port=ssh_server.port)

    assert computer.health.status == "auth_failed"

    fixed = await computer_service.install_key(computer.id, TEST_PASSWORD)
    assert fixed.health.status == "online"


async def test_wrong_password_is_refused_before_anything_is_saved(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    with pytest.raises(ComputerError) as caught:
        await computer_service.add_server(
            name="box",
            host="127.0.0.1",
            port=ssh_server.port,
            auth="password",
            password=TEST_PASSWORD[::-1],
        )

    assert caught.value.kind == "auth"
    assert computer_service.all() == []


async def test_changed_server_identity_is_refused_until_trusted(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    ssh_server.state.authorized.add(identity.public_key_line())
    computer = await computer_service.add_server(name="box", host="127.0.0.1", port=ssh_server.port)
    pinned = computer.host_fingerprint

    # The "server was reinstalled": same address, new identity.
    port = ssh_server.port
    await ssh_server.stop()
    impostor = FakeSshServer(ssh_server.state)
    await impostor.start(port)
    try:
        refused = await computer_service.check(computer.id)
        assert refused.health.status == "host_key_changed"
        assert refused.host_fingerprint == pinned
        commands_before = len(impostor.state.commands)
        with pytest.raises(ComputerError):
            await computer_service.run(computer.id, "whoami")
        assert len(impostor.state.commands) == commands_before

        trusted = await computer_service.trust_new_host_key(computer.id)
        assert trusted.health.status == "online"
        assert trusted.host_fingerprint != pinned
    finally:
        await impostor.stop()


async def test_unreachable_host_is_offline(computer_service: ComputerService) -> None:
    computer = await computer_service.add_server(name="gone", host="127.0.0.1", port=1)

    assert computer.health.status == "offline"
    assert computer.health.message
