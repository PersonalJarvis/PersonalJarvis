"""This PC's ~/.ssh/config and known_hosts, and its Tailscale network."""

from __future__ import annotations

import json
from pathlib import Path

import asyncssh
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.computers import identity, ssh, ssh_config, tailscale
from jarvis.computers.models import Computer, ComputerFacts, ComputerRoute
from jarvis.computers.service import ComputerService
from jarvis.ui.web import computers_routes
from tests.fakes.fake_ssh_server import FakeSshServer


def _home(tmp_path: Path, config: str, known_hosts: str = "") -> Path:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    (ssh_dir / "config").write_text(config, encoding="utf-8")
    if known_hosts:
        (ssh_dir / "known_hosts").write_text(known_hosts, encoding="utf-8")
    return tmp_path


# -- ~/.ssh/config ---------------------------------------------------------------


def test_first_value_wins_and_wildcard_blocks_fill_the_gaps(tmp_path: Path) -> None:
    home = _home(
        tmp_path,
        """
# a comment
Host vps
    HostName 203.0.113.7
    Port 2222

Host vps other
    User deploy
    Port 9999

Host *
    User root
    ServerAliveInterval 30
""",
    )

    hosts = {h.alias: h for h in ssh_config.discover(home)}

    assert set(hosts) == {"vps", "other"}
    assert (hosts["vps"].host, hosts["vps"].port, hosts["vps"].username) == (
        "203.0.113.7",
        2222,
        "deploy",
    )
    assert (hosts["other"].host, hosts["other"].port, hosts["other"].username) == (
        "other",
        9999,
        "deploy",
    )


def test_patterns_negations_and_match_blocks_are_never_listed(tmp_path: Path) -> None:
    home = _home(
        tmp_path,
        """
Host *.internal !secret.internal web?
    User ops
Match host foo
    User nobody
Host real
    HostName real.example.com
""",
    )

    aliases = [h.alias for h in ssh_config.discover(home)]

    assert aliases == ["real"]


def test_include_globs_relative_to_dot_ssh_are_followed(tmp_path: Path) -> None:
    home = _home(tmp_path, "Include conf.d/*.conf\nHost main\n  HostName 10.0.0.1\n")
    conf_d = home / ".ssh" / "conf.d"
    conf_d.mkdir()
    (conf_d / "a.conf").write_text("Host alpha\n  HostName 10.0.0.2\n", encoding="utf-8")
    (conf_d / "b.conf").write_text("Host beta\n  HostName 10.0.0.3\n", encoding="utf-8")
    (conf_d / "skip.txt").write_text("Host never\n", encoding="utf-8")

    assert sorted(h.alias for h in ssh_config.discover(home)) == ["alpha", "beta", "main"]


def test_an_include_loop_ends(tmp_path: Path) -> None:
    home = _home(tmp_path, "Include ~/.ssh/config\nHost loop\n  HostName 10.0.0.9\n")

    assert [h.alias for h in ssh_config.discover(home)] == ["loop"]


def test_identity_files_accumulate_and_only_existing_ones_count(tmp_path: Path) -> None:
    home = _home(
        tmp_path,
        """
Host box
    HostName %h.example.com
    IdentityFile ~/.ssh/box_key
    IdentityFile ~/.ssh/missing_key
Host *
    IdentityFile ~/.ssh/fallback_key
""",
    )
    (home / ".ssh" / "box_key").write_text("x", encoding="utf-8")
    (home / ".ssh" / "fallback_key").write_text("x", encoding="utf-8")

    (box,) = ssh_config.discover(home)

    assert box.host == "box.example.com"
    assert [Path(p).name for p in box.identity_files] == ["box_key", "fallback_key"]


def test_a_jump_host_is_marked(tmp_path: Path) -> None:
    home = _home(tmp_path, "Host inner\n  ProxyJump bastion\nHost bastion\n  HostName 1.2.3.4\n")

    hosts = {h.alias: h for h in ssh_config.discover(home)}

    assert hosts["inner"].needs_proxy is True
    assert hosts["bastion"].needs_proxy is False


def test_key_equals_value_and_quoted_values(tmp_path: Path) -> None:
    home = _home(tmp_path, 'Host=eq\n  HostName=10.1.1.1\n  User "my user"\n')

    (host,) = ssh_config.discover(home)

    assert (host.host, host.username) == ("10.1.1.1", "my user")


def test_no_config_and_no_known_hosts_is_simply_nothing(tmp_path: Path) -> None:
    assert ssh_config.discover(tmp_path) == []


def test_a_bad_port_falls_back_to_22(tmp_path: Path) -> None:
    home = _home(tmp_path, "Host p\n  Port seventy\nHost q\n  Port 70000\n")

    assert {h.alias: h.port for h in ssh_config.discover(home)} == {"p": 22, "q": 22}


# -- known_hosts -----------------------------------------------------------------


def test_known_hosts_formats() -> None:
    text = """
# comment
203.0.113.1 ssh-ed25519 AAAA
[203.0.113.2]:2200,[alias.example]:2200 ssh-ed25519 AAAA
|1|hashed=|salt= ssh-ed25519 AAAA
@cert-authority *.example.com ssh-ed25519 AAAA
host-a,host-b ecdsa-sha2-nistp256 AAAA
2001:db8::1 ssh-ed25519 AAAA
*.wild ssh-ed25519 AAAA
"""
    assert ssh_config.parse_known_hosts(text) == [
        ("203.0.113.1", 22),
        ("203.0.113.2", 2200),
        ("alias.example", 2200),
        ("host-a", 22),
        ("host-b", 22),
        ("2001:db8::1", 22),
    ]


def test_known_hosts_skips_forges_localhost_and_config_hosts(tmp_path: Path) -> None:
    home = _home(
        tmp_path,
        "Host vps\n  HostName 203.0.113.7\n",
        "github.com ssh-ed25519 A\nlocalhost ssh-ed25519 A\n203.0.113.7 ssh-ed25519 A\n"
        "198.51.100.4 ssh-ed25519 A\n",
    )

    hosts = ssh_config.discover(home)

    assert [(h.alias, h.source) for h in hosts] == [
        ("vps", "ssh_config"),
        ("198.51.100.4", "known_hosts"),
    ]
    assert hosts[1].username is None


# -- the auto login offers the config's IdentityFile ------------------------------


async def test_auto_login_uses_the_identity_file_the_config_names(
    computer_service: ComputerService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = asyncssh.generate_private_key("ssh-ed25519")
    key_path = tmp_path / ".ssh" / "hetzner"
    home = _home(tmp_path, f"Host hz\n  HostName 127.0.0.1\n  IdentityFile {key_path}\n")
    key.write_private_key(str(key_path))
    monkeypatch.setattr(ssh_config, "home_dir", lambda: home)
    monkeypatch.setattr(ssh, "this_pc_keys", lambda: [])
    monkeypatch.setattr(ssh, "THIS_PC_AGENT", None)
    server = FakeSshServer()
    await server.start()
    try:
        server.state.authorized.add(key.export_public_key("openssh").decode().strip())

        tested = await computer_service.test_connection(
            host="127.0.0.1", port=server.port, auth="auto", ssh_alias="hz"
        )
        assert tested["ok"] is True, tested
        computer = await computer_service.add_server(
            name="hz", host="127.0.0.1", port=server.port, auth="auto", ssh_alias="hz"
        )
    finally:
        await server.stop()

    assert computer.auth == "key"
    assert any(identity.public_key_line().split()[1] in k for k in server.state.authorized)


async def test_without_the_alias_the_same_server_refuses(
    computer_service: ComputerService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = asyncssh.generate_private_key("ssh-ed25519")
    monkeypatch.setattr(ssh, "this_pc_keys", lambda: [])
    monkeypatch.setattr(ssh, "THIS_PC_AGENT", None)
    server = FakeSshServer()
    await server.start()
    try:
        server.state.authorized.add(key.export_public_key("openssh").decode().strip())
        tested = await computer_service.test_connection(
            host="127.0.0.1", port=server.port, auth="auto"
        )
    finally:
        await server.stop()

    assert tested["ok"] is False


def test_an_encrypted_or_broken_key_file_is_skipped(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(
        str(locked),
        passphrase="secret",  # noqa: S106 — a throwaway test key
    )
    broken = tmp_path / "broken"
    broken.write_text("not a key", encoding="utf-8")
    good = tmp_path / "good"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(good))

    keys = ssh.key_files((str(locked), str(broken), str(good), str(tmp_path / "gone")))

    assert len(keys) == 1


# -- Tailscale ---------------------------------------------------------------------

STATUS = {
    "Self": {"HostName": "this-pc"},
    "Peer": {
        "k1": {
            "HostName": "vps-01",
            "DNSName": "vps-01.tail1234.ts.net.",
            "TailscaleIPs": ["100.64.0.5", "fd7a::5"],
            "Online": True,
            "OS": "linux",
        },
        "k2": {
            "HostName": "laptop",
            "DNSName": "",
            "TailscaleIPs": ["100.64.0.9"],
            "Online": False,
            "OS": "windows",
        },
        "k3": {"HostName": "noaddr", "DNSName": "", "TailscaleIPs": []},
    },
}


def test_status_json_is_read_into_peers() -> None:
    peers = tailscale.parse_status(json.dumps(STATUS))

    assert [(p.host_name, p.address, p.online) for p in peers] == [
        ("vps-01", "vps-01.tail1234.ts.net", True),
        ("laptop", "100.64.0.9", False),
    ]
    assert tailscale.parse_status("not json") == []
    assert tailscale.parse_status("[]") == []


def _computer(cid: str, host: str, *, hostname: str | None = None, port: int = 22) -> Computer:
    return Computer(
        id=cid,
        name=cid,
        host=host,
        port=port,
        created_at=1.0,
        facts=ComputerFacts(hostname=hostname) if hostname else None,
    )


def test_suggestions_match_by_reported_hostname_or_known_address() -> None:
    peers = tailscale.parse_status(json.dumps(STATUS))
    by_name = _computer("c_a", "192.168.1.20", hostname="VPS-01.fritz.box", port=2222)
    by_ip = _computer("c_b", "100.64.0.9")
    stranger = _computer("c_c", "10.0.0.1", hostname="other")
    already = _computer("c_d", "192.168.1.30", hostname="vps-01").model_copy(
        update={
            "routes": [
                ComputerRoute(id="r_main", host="192.168.1.30"),
                ComputerRoute(id="r_ts", host="vps-01.tail1234.ts.net"),
            ]
        }
    )
    vm = _computer("c_e", "100.64.0.5").model_copy(update={"kind": "local_vm"})

    found = tailscale.suggestions([by_name, by_ip, stranger, already, vm], peers)

    assert found == {
        "c_a": [
            {"host": "vps-01.tail1234.ts.net", "port": 2222, "label": "Tailscale", "online": True}
        ]
    }


async def test_no_tailscale_cli_means_no_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale, "binary", lambda: None)

    assert await tailscale.peers() == []


# -- REST ------------------------------------------------------------------------------


@pytest.fixture
def client(computer_service: ComputerService, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(computers_routes, "get_service", lambda: computer_service)
    app = FastAPI()
    app.include_router(computers_routes.router)
    return TestClient(app)


def test_ssh_hosts_route_is_not_swallowed_by_the_id_route(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path, "Host vps\n  HostName 203.0.113.7\n  User deploy\n")
    monkeypatch.setattr(ssh_config, "home_dir", lambda: home)

    response = client.get("/api/computers/ssh-hosts")

    assert response.status_code == 200
    (host,) = response.json()["hosts"]
    assert host["alias"] == "vps" and host["username"] == "deploy" and host["added_as"] is None


def test_tailscale_route_without_tailscale(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tailscale, "binary", lambda: None)

    response = client.get("/api/computers/tailscale")

    assert response.json() == {"available": False, "peers": [], "suggestions": {}}


def test_patch_switches_a_computer_off_and_the_row_carries_routes(
    client: TestClient, computer_service: ComputerService
) -> None:
    computer_service._store.add(_computer("c_x", "203.0.113.3"))  # noqa: SLF001

    off = client.patch("/api/computers/c_x", json={"enabled": False}).json()

    assert off["enabled"] is False
    assert off["routes"] == [
        {
            "id": "r_main",
            "host": "203.0.113.3",
            "port": 22,
            "label": None,
            "source": "manual",
            "last_ok_at": None,
        }
    ]
    assert off["active_route_id"] == "r_main"
    assert off["health"]["trace_id"] is None


def test_route_endpoints_refuse_unknown_and_last_routes(
    client: TestClient, computer_service: ComputerService
) -> None:
    computer_service._store.add(_computer("c_y", "203.0.113.4"))  # noqa: SLF001

    assert client.delete("/api/computers/c_y/routes/r_main").status_code == 409
    assert client.delete("/api/computers/c_y/routes/r_nope").status_code == 404
    assert (
        client.put("/api/computers/c_y/routes/order", json={"route_ids": ["r_x"]}).status_code
        == 400
    )
    assert client.post("/api/computers/c_y/routes", json={"host": "10.0.0.5"}).status_code == 409
    assert client.post("/api/computers/c_nope/routes", json={"host": "10.0.0.5"}).status_code == 404
