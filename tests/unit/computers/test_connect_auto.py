"""Connecting with nothing but an address.

"auto" first offers the app's own key and the keys this PC's ``ssh`` already
uses; one that opens the server is used ONCE to plant the app's key. When none
does, the answer says what would: the password (``needs_password``) or a key
the server already knows (``key_only``) — so the form asks for exactly that.
"""

from __future__ import annotations

from typing import Any

import asyncssh
import pytest

from jarvis.computers import identity, ssh
from jarvis.computers.service import ComputerError, ComputerService
from tests.fakes.fake_ssh_server import FakeSshServer


@pytest.fixture
async def ssh_server():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def pc_keys(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """This PC's own SSH keys, in memory; no real agent or ~/.ssh is touched."""
    keys: list[Any] = []
    monkeypatch.setattr(ssh, "this_pc_keys", lambda: list(keys))
    monkeypatch.setattr(ssh, "THIS_PC_AGENT", None)
    return keys


def _public(key: Any) -> str:
    return key.export_public_key("openssh").decode().strip()


async def test_a_key_this_pc_already_uses_connects_and_plants_the_apps_key(
    computer_service: ComputerService, ssh_server: FakeSshServer, pc_keys: list[Any]
) -> None:
    mine = asyncssh.generate_private_key("ssh-ed25519")
    pc_keys.append(mine)
    ssh_server.state.authorized.add(_public(mine))

    test = await computer_service.test_connection(
        host="127.0.0.1", port=ssh_server.port, auth="auto"
    )
    computer = await computer_service.add_server(
        name="vps", host="127.0.0.1", port=ssh_server.port, auth="auto"
    )

    assert test["ok"] is True
    assert computer.auth == "key", "stored as the app's own key, not the PC's"
    assert computer.health.status == "online"
    assert any(identity.public_key_line().split()[1] in k for k in ssh_server.state.authorized)


async def test_the_apps_key_alone_is_enough_when_it_is_already_there(
    computer_service: ComputerService, ssh_server: FakeSshServer, pc_keys: list[Any]
) -> None:
    ssh_server.state.authorized.add(identity.public_key_line())

    computer = await computer_service.add_server(
        name="vps", host="127.0.0.1", port=ssh_server.port, auth="auto"
    )

    assert computer.auth == "key" and computer.health.status == "online"


async def test_without_a_working_key_it_asks_for_the_password(
    computer_service: ComputerService, ssh_server: FakeSshServer, pc_keys: list[Any]
) -> None:
    pc_keys.append(asyncssh.generate_private_key("ssh-ed25519"))  # not on the server

    test = await computer_service.test_connection(
        host="127.0.0.1", port=ssh_server.port, auth="auto"
    )
    with pytest.raises(ComputerError) as refused:
        await computer_service.add_server(
            name="vps", host="127.0.0.1", port=ssh_server.port, auth="auto"
        )

    assert test["ok"] is False and test["kind"] == "needs_password"
    assert "password" in test["message"]
    assert refused.value.kind == "needs_password"
    assert computer_service.all() == [], "nothing is saved on a refused login"


async def test_a_keys_only_server_says_which_line_to_run(
    computer_service: ComputerService, ssh_server: FakeSshServer, pc_keys: list[Any]
) -> None:
    ssh_server.state.password_login = False

    test = await computer_service.test_connection(
        host="127.0.0.1", port=ssh_server.port, auth="auto"
    )

    assert test["ok"] is False and test["kind"] == "key_only"
    command = identity.describe()["install_command"]
    assert identity.public_key_line().split()[1] in command
    assert "authorized_keys" in command


def test_this_pc_keys_reads_the_default_files_and_skips_encrypted_ones(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    plain = asyncssh.generate_private_key("ssh-ed25519")
    plain.write_private_key(str(home / ".ssh" / "id_ed25519"))
    locked = asyncssh.generate_private_key("ecdsa-sha2-nistp256")
    locked.write_private_key(  # a passphrase-protected key: needs a person, so skipped
        str(home / ".ssh" / "id_ecdsa"),
        format_name="pkcs8-pem",
        passphrase="fixture-only",  # noqa: S106 - a fixture, not a credential
        cipher_name="aes256-cbc",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    found = ssh.this_pc_keys()

    assert [k.public_data for k in found] == [plain.public_data]
