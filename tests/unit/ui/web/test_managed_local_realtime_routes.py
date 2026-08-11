"""Routes for the one-click managed local realtime server install.

Pins: the preflight surfaces the honest blocker payload untouched, install
returns immediately with the poll snapshot, status pairs progress with the
fail-closed on-disk probe, uninstall refuses while a run is live (409), and
the local-realtime card carries the managed_server payload while cloud
cards carry None.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.realtime.local_server import brain_link, install, preflight
from jarvis.ui.web import provider_routes
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
        lambda **kwargs: brain_link.BrainResolution(
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
        "ready": False,
        "available": False,
        "pool": None,
        "port": 8765,
        "pid": None,
        "owned": False,
        "stale": False,
        # Boot forensics (crash-loop verdicts, 2026-08-10): a fresh status
        # reports no boot in flight and a clean failure streak.
        "boot": {"failed_streak": 0, "starting": False},
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


def test_start_spawns_and_schedules_the_brain_warm(
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
        provider_routes,
        "_schedule_managed_server_warm",
        lambda *args: calls.append("schedule"),
    )
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    monkeypatch.setattr(
        supervisor,
        "probe_runtime",
        lambda *args, **kwargs: {
            "size": 1,
            "in_use": 0,
            "available": 1,
            "stuck": 0,
        },
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["outcome"] == "spawned"
    assert calls == ["start", "schedule"]
    assert body["runtime"]["reachable"] is True
    assert body["runtime"]["ready"] is True


def test_start_does_not_compete_for_gpu_while_speech_is_still_loading(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig
    from jarvis.realtime.local_server import supervisor

    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command="serve --flag"
    )
    monkeypatch.setattr(supervisor, "ensure_running", lambda **kwargs: "spawned")
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    scheduled: list[str] = []
    monkeypatch.setattr(
        provider_routes,
        "_schedule_managed_server_warm",
        lambda *args: scheduled.append("warm-worker"),
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/start")
    assert resp.status_code == 200
    assert resp.json()["runtime"]["ready"] is False
    assert scheduled == ["warm-worker"]


async def test_background_warm_waits_for_managed_pool_before_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.realtime.local_server import supervisor

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(supervisor, "is_managed_launch_command", lambda command: True)

    def ready(*args, **kwargs) -> bool:
        calls.append(("ready", kwargs))
        return True

    monkeypatch.setattr(supervisor, "wait_until_ready", ready)
    monkeypatch.setattr(
        install,
        "repair_smoke_marker_from_live_runtime",
        lambda base_url: calls.append(("marker", base_url)) or True,
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append(("monitor", kwargs)) or True,
    )
    monkeypatch.setattr(
        supervisor,
        "warm_brain",
        lambda **kwargs: calls.append(("brain", kwargs)) or True,
    )

    await provider_routes._finish_managed_server_warm(
        "http://127.0.0.1:8765",
        "managed-server --mode realtime",
        threading.Event(),
    )

    assert [name for name, _payload in calls] == [
        "ready",
        "marker",
        "monitor",
        "brain",
    ]
    ready_kwargs = calls[0][1]
    assert isinstance(ready_kwargs, dict)
    assert ready_kwargs["cleanup_on_timeout"] is True


def test_brain_route_voice_tests_before_adopting_the_model(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig
    from jarvis.realtime.local_server import configure

    entrypoint = (install.install_root() / "venv" / "s.exe").as_posix()
    old_command = f"'{entrypoint}' --mode realtime --model_name qwen2.5:7b"
    new_command = f"'{entrypoint}' --mode realtime --model_name llama3.1:8b"
    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        base_url="http://127.0.0.1:8765",
        launch_command=old_command,
    )
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(
        brain_link, "_ollama_models", lambda base, timeout: [("llama3.1:8b", 4.9)]
    )
    seen: dict[str, str] = {}

    async def tested(**kwargs):
        seen.update({key: str(value) for key, value in kwargs.items()})
        return {
            "ok": True,
            "changed": True,
            "smoke": {"audio_bytes": 4800},
            "launch_command": new_command,
        }

    monkeypatch.setattr(configure, "apply_and_test_stack", tested)
    with TestClient(server.app) as client:
        resp = client.post(
            "/api/providers/local-realtime/managed-server/brain",
            json={"model": "llama3.1:8b"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["changed"] is True
    assert body["brain"]["model"] == "llama3.1:8b"
    assert seen["brain_model"] == "llama3.1:8b"
    assert seen["voice_model"] == "qwen3-tts-1.7b"
    assert (
        server.app.state.config.brain.providers["local-realtime"].launch_command
        == new_command
    )


def test_brain_models_route_lists_annotated_choices(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core.config import BrainProviderConfig

    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command="serve --model_name qwen2.5:7b"
    )
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(
        brain_link, "_ollama_models", lambda base, timeout: [("qwen2.5:7b", 4.7)]
    )
    with TestClient(server.app) as client:
        resp = client.get(
            "/api/providers/local-realtime/managed-server/brain-models"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is True
    assert body["current"] == "qwen2.5:7b"
    by_id = {entry["id"]: entry for entry in body["models"]}
    assert by_id["qwen2.5:7b"]["current"] is True


def test_brain_route_refuses_to_swap_an_explicit_choice(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named model that does not fit gets an honest 409 — never a silent
    substitution (user autonomy)."""
    from jarvis.core.config import BrainProviderConfig

    entrypoint = (install.install_root() / "venv" / "s.exe").as_posix()
    server.app.state.config.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command=f"'{entrypoint}' --mode realtime --model_name qwen2.5:7b"
    )
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(
        brain_link,
        "_ollama_models",
        lambda base, timeout: [
            ("nemotron-cascade-2:latest", 24.0),
            ("qwen2.5:7b", 4.7),
        ],
    )
    with TestClient(server.app) as client:
        resp = client.post(
            "/api/providers/local-realtime/managed-server/brain",
            json={"model": "nemotron-cascade-2:latest"},
        )
    assert resp.status_code == 409
    assert "does not fit" in resp.json()["detail"]


def test_brain_route_answers_409_without_a_local_brain(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
    monkeypatch.setattr(brain_link, "_ollama_models", lambda base, timeout: None)
    monkeypatch.setattr(brain_link, "_openai_key", lambda: "")
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/brain")
    assert resp.status_code == 409


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

    calls: list[str] = []

    async def cancel_warm(_request) -> None:
        calls.append("cancel")

    monkeypatch.setattr(provider_routes, "_cancel_managed_server_warm", cancel_warm)
    monkeypatch.setattr(
        supervisor,
        "stop",
        lambda **kwargs: calls.append("stop") or (True, "stopped pid 4711"),
    )
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/local-realtime/managed-server/stop")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls == ["cancel", "stop"]


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
