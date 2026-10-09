""" "Automatic" placement over this PC and the computers, and GitHub sharing."""

from __future__ import annotations

import os
import random
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.computers import github_access, identity, placement, remote_os, service
from jarvis.computers.models import Computer, ComputerHealth
from jarvis.computers.service import ComputerError, ComputerService
from jarvis.computers.store import ComputerStore
from jarvis.ui.web import computers_routes
from tests.fakes.fake_ssh_server import FakeSshServer

# -- placement ---------------------------------------------------------------------


def _machine(
    cid: str, *, weight: int = 2, status: str = "online", enabled: bool = True
) -> Computer:
    return Computer(
        id=cid,
        name=cid,
        host="203.0.113.1",
        created_at=1.0,
        placement_weight=weight,
        enabled=enabled,
        health=ComputerHealth(status=status),  # type: ignore[arg-type]
    )


def test_only_switched_on_online_computers_with_a_share_are_candidates() -> None:
    computers = [
        _machine("c_ok"),
        _machine("c_more", weight=3),
        _machine("c_never", weight=0),
        _machine("c_off", enabled=False),
        _machine("c_down", status="offline"),
    ]

    pool = placement.candidates(
        placement.PlacementSettings(enabled=True, local_weight=1), computers
    )

    assert pool == [(None, 1), ("c_ok", 2), ("c_more", 4)]


def test_pick_follows_the_shares() -> None:
    computers = [_machine("c_a", weight=3), _machine("c_b", weight=1)]
    settings = placement.PlacementSettings(enabled=True, local_weight=0)
    rng = random.Random(7)  # noqa: S311 — a seeded test, not a secret

    picks = [placement.pick(settings, computers, rng=rng) for _ in range(2000)]

    share_a = picks.count("c_a") / len(picks)
    assert 0.75 < share_a < 0.85  # 4 : 1
    assert None not in picks


def test_with_nothing_else_this_pc_takes_the_work() -> None:
    settings = placement.PlacementSettings(enabled=True, local_weight=0)

    assert placement.pick(settings, [_machine("c_x", status="offline")]) is None
    assert placement.pick(settings, []) is None


def test_settings_round_trip_and_survive_garbage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "computers" / "placement.json"
    monkeypatch.setattr(placement, "_path", lambda: path)

    assert placement.load() == placement.PlacementSettings()
    placement.save(placement.PlacementSettings(enabled=True, local_weight=3))
    assert placement.load() == placement.PlacementSettings(enabled=True, local_weight=3)
    path.write_text('{"enabled": true, "local_weight": 9}', encoding="utf-8")
    assert placement.load() == placement.PlacementSettings(enabled=True, local_weight=2)
    path.write_text("not json", encoding="utf-8")
    assert placement.load() == placement.PlacementSettings()


def test_a_share_outside_the_scale_is_refused(computer_service: ComputerService) -> None:
    computer_service._store.add(_machine("c_w"))  # noqa: SLF001

    with pytest.raises(ComputerError):
        computer_service.update("c_w", placement_weight=5)
    assert computer_service.update("c_w", placement_weight=0).placement_weight == 0


@pytest.fixture
def client(
    computer_service: ComputerService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setattr(computers_routes, "get_service", lambda: computer_service)
    monkeypatch.setattr(placement, "_path", lambda: tmp_path / "placement.json")
    app = FastAPI()
    app.include_router(computers_routes.router)
    return TestClient(app)


def test_placement_routes(client: TestClient, computer_service: ComputerService) -> None:
    assert client.get("/api/computers/placement").json() == {"enabled": False, "local_weight": 2}
    assert client.put("/api/computers/placement", json={"enabled": True}).json() == {
        "enabled": True,
        "local_weight": 2,
    }
    assert client.put("/api/computers/placement", json={"local_weight": 0}).json() == {
        "enabled": True,
        "local_weight": 0,
    }
    assert client.put("/api/computers/placement", json={"local_weight": 7}).status_code == 422
    computer_service._store.add(_machine("c_only"))  # noqa: SLF001

    assert client.post("/api/computers/placement/pick").json() == {"computer_id": "c_only"}
    assert (
        client.patch("/api/computers/c_only", json={"placement_weight": 0}).json()[
            "placement_weight"
        ]
        == 0
    )
    assert client.post("/api/computers/placement/pick").json() == {"computer_id": None}


# -- GitHub sharing -----------------------------------------------------------------


@pytest.fixture
def computers(tmp_path: Path, secret_box, monkeypatch: pytest.MonkeyPatch) -> ComputerService:  # noqa: ANN001
    path = tmp_path / "computers.json"
    monkeypatch.setattr("jarvis.computers.store.default_path", lambda: path)
    svc = ComputerService(ComputerStore(path))
    monkeypatch.setattr(service, "_SERVICE", svc)
    return svc


@pytest.fixture
async def ssh_server(tmp_path: Path):  # noqa: ANN201
    home = tmp_path / "remote-home"
    home.mkdir()
    server = FakeSshServer(sftp_root=home)
    server.state.authorized.add(identity.public_key_line())
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def box(computers: ComputerService, ssh_server: FakeSshServer):  # noqa: ANN201
    return await computers.add_server(name="Box", host="127.0.0.1", port=ssh_server.port)


TOKEN = "gho_" + "x" * 36  # noqa: S105 — a fixture, not a credential


def _token(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    from jarvis.agentic_ide import github_link

    monkeypatch.setattr(
        github_link,
        "credential",
        lambda: github_link.Credential(token=value, source="gh") if value else None,
    )


async def test_sharing_writes_an_owner_only_env_file_and_the_helper(
    box,  # noqa: ANN001
    ssh_server: FakeSshServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token(monkeypatch, TOKEN)

    shared = await github_access.share(box.id)

    saved = ssh_server.sftp_root / ".config" / "jarvis" / "github.env"
    text = saved.read_text(encoding="utf-8")
    assert f"export GH_TOKEN={TOKEN}\n" in text and f"export GITHUB_TOKEN={TOKEN}\n" in text
    if sys.platform != "win32":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert shared.github_shared_at is not None
    # The token travelled only in the file, never on a command line or in a script.
    assert not any(TOKEN in command for command in ssh_server.state.commands)
    assert any(github_access.HELPER in command for command in ssh_server.state.commands)

    removed = await github_access.unshare(box.id)
    assert removed.github_shared_at is None
    assert any(remote_os.GITHUB_ENV_FILE in command for command in ssh_server.state.commands)


async def test_sharing_without_a_github_login_says_how_to_connect(
    box,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _token(monkeypatch, None)

    with pytest.raises(ComputerError) as caught:
        await github_access.share(box.id)

    assert caught.value.kind == "github_missing"


def test_every_launcher_sources_the_github_file_after_the_agent_file() -> None:
    lines = remote_os._source_agent_env().splitlines()  # noqa: SLF001

    assert remote_os.AGENT_ENV_FILE in lines[0]
    assert remote_os.GITHUB_ENV_FILE in lines[1]


def _real_bash() -> str | None:
    found = shutil.which("bash")
    if found is None or "system32" in found.lower():  # WSL's launcher, not a POSIX bash
        return None
    return found if shutil.which("git") else None


@pytest.mark.skipif(_real_bash() is None, reason="needs bash and git")
def test_the_helper_hands_git_the_token_and_is_removed_cleanly(tmp_path: Path) -> None:
    bash = _real_bash()
    assert bash is not None
    home = tmp_path / "home"
    (home / ".config" / "jarvis").mkdir(parents=True)
    (home / ".config" / "jarvis" / "github.env").write_text(
        github_access.env_file_body(TOKEN), encoding="utf-8"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_TOKEN": "",
    }
    env.pop("XDG_CONFIG_HOME", None)

    def bash_run(script: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — a fixed local bash in a temp HOME
            [bash, "-c", script],
            capture_output=True,
            text=True,
            env={**env, **extra},
            check=False,
            timeout=60,
        )

    assert bash_run(github_access.helper_script(True)).returncode == 0
    fill = bash_run(
        '. "$HOME/.config/jarvis/github.env"; '
        "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill"
    )
    assert f"password={TOKEN}" in fill.stdout, fill.stderr
    assert "username=x-access-token" in fill.stdout

    assert bash_run(github_access.helper_script(False)).returncode == 0
    left = bash_run("git config --global --get-all credential.https://github.com.helper")
    assert left.stdout.strip() == ""
    assert not (home / ".config" / "jarvis" / "github.env").exists()


def test_unsharing_keeps_a_helper_the_user_set_themselves(tmp_path: Path) -> None:
    bash = _real_bash()
    if bash is None:
        pytest.skip("needs bash and git")
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}
    env.pop("XDG_CONFIG_HOME", None)
    key = "credential.https://github.com.helper"
    subprocess.run(  # noqa: S603
        [bash, "-c", f"git config --global {key} store"], env=env, check=True, timeout=60
    )

    subprocess.run(  # noqa: S603
        [bash, "-c", github_access.helper_script(False)], env=env, check=True, timeout=60
    )

    kept = subprocess.run(  # noqa: S603
        [bash, "-c", f"git config --global --get {key}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert kept.stdout.strip() == "store"
