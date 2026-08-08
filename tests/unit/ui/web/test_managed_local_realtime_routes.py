"""Routes for the one-click managed local realtime server install.

Pins: the preflight surfaces the honest blocker payload untouched, install
returns immediately with the poll snapshot, status pairs progress with the
fail-closed on-disk probe, uninstall refuses while a run is live (409), and
the local-realtime card carries the managed_server payload while cloud
cards carry None.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.realtime.local_server import brain_link, install, preflight
from jarvis.ui.web.server import WebServer


@pytest.fixture
def server(tmp_path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    return srv


def test_preflight_reports_the_floor_blocker(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (4.0, "nvidia-smi"))
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/local-realtime/managed-server/preflight")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "minimum" in body["blocker"]
    assert body["tier"] is None
    assert body["actions"]


def test_preflight_reports_tier_and_brain_when_ok(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(preflight, "_disk_free_gb", lambda root: 200.0)
    monkeypatch.setattr(
        preflight,
        "resolve_brain",
        lambda: brain_link.BrainResolution(
            kind="ollama", base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b"
        ),
    )
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/local-realtime/managed-server/preflight")
    body = resp.json()
    assert body["ok"] is True
    assert body["tier"]["key"] == "t1-16gb"
    assert body["tier"]["download_gb"] > 0
    assert body["brain"]["kind"] == "ollama"


def test_install_returns_immediately_and_forwards_confirmed_brain(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def _fake_start(*, confirmed_brain: str = "") -> tuple[bool, str]:
        seen["confirmed_brain"] = confirmed_brain
        return True, "install started"

    monkeypatch.setattr(install, "start_install", _fake_start)
    with TestClient(server.app) as client:
        resp = client.post(
            "/api/providers/local-realtime/managed-server/install",
            json={"confirmed_brain": "ollama"},
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body["started"] is True
    assert body["message"] == "install started"
    assert "phase" in body and "percent" in body
    assert seen["confirmed_brain"] == "ollama"


def test_install_accepts_an_empty_body(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        install, "start_install", lambda confirmed_brain="": (True, "install started")
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/install")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_status_pairs_progress_with_failclosed_probe(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/local-realtime/managed-server/status")
    body = resp.json()
    assert set(body) == {"progress", "server", "runtime"}
    # Nothing is installed under the tmp data dir: readiness must be False
    # with the exact not-installed sentence, never a guess.
    assert body["server"]["ready"] is False
    assert body["server"]["sentence"] == "Managed server not installed."
    # The live half says what the disk cannot: nothing serves, nothing owned.
    assert body["runtime"] == {
        "reachable": False,
        "port": 8765,
        "pid": None,
        "owned": False,
        "stale": False,
    }


def test_start_without_a_launch_command_answers_409(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/start")
    assert resp.status_code == 409
    assert "launch command" in resp.json()["detail"]


def test_start_forwards_the_supervisor_refusal(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig
    from jarvis.realtime.local_server import supervisor

    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command="serve --flag"
    )
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: "refused:rate-limited"
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/start")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "refused:rate-limited"


def test_start_spawns_and_warms_the_brain(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig
    from jarvis.realtime.local_server import supervisor

    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command="serve --flag"
    )
    calls: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append("start") or "spawned",
    )
    monkeypatch.setattr(
        supervisor, "warm_brain", lambda **kwargs: calls.append("warm") or True
    )
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["outcome"] == "spawned"
    assert calls == ["start", "warm"]
    assert body["runtime"]["reachable"] is True


def test_stop_answers_409_when_nothing_is_owned(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(
        supervisor, "stop", lambda **kwargs: (False, "no owned server process found")
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/stop")
    assert resp.status_code == 409


def test_stop_reports_success(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(supervisor, "stop", lambda **kwargs: (True, "stopped pid 4711"))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/stop")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_uninstall_refuses_while_running(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install, "uninstall", lambda: (False, "an install is running"))
    with TestClient(server.app) as client:
        resp = client.delete("/api/providers/local-realtime/managed-server")
    assert resp.status_code == 409


def test_uninstall_reports_success(server: WebServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "uninstall", lambda: (True, "nothing installed"))
    with TestClient(server.app) as client:
        resp = client.delete("/api/providers/local-realtime/managed-server")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_card_payload_carries_managed_server_only_on_local_realtime(
    server: WebServer,
) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/providers")
    assert resp.status_code == 200
    providers = {p["id"]: p for p in resp.json()["providers"]}
    assert providers["local-realtime"]["managed_server"] is not None
    assert "sentence" in providers["local-realtime"]["managed_server"]
    cloud = next(p for pid, p in providers.items() if pid != "local-realtime")
    assert cloud["managed_server"] is None
