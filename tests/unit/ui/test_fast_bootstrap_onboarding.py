"""While warming, the bootstrap must answer /api/onboarding/* itself."""
import json

from jarvis.ui.web.fast_bootstrap import FastBootstrap


def _collector():
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return sent, send


async def _receive_empty():
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_onboarding_state_answers_while_warming(tmp_path, monkeypatch) -> None:
    from jarvis.setup import onboarding_fastpath as fp

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    boot = FastBootstrap(dist_dir=tmp_path / "no-dist")
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "GET", "path": "/api/onboarding/state"},
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["completed"] is False


async def test_onboarding_complete_persists_while_warming(tmp_path, monkeypatch) -> None:
    from jarvis.setup import onboarding_fastpath as fp
    from jarvis.setup import state as st

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    boot = FastBootstrap(dist_dir=tmp_path / "no-dist")
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "POST", "path": "/api/onboarding/complete"},
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 200
    assert st.is_onboarding_complete(tmp_path / "setup_state.json") is True


async def test_other_api_routes_still_held(tmp_path) -> None:
    boot = FastBootstrap(hold_timeout=0.05, dist_dir=tmp_path / "no-dist")
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "GET", "path": "/api/settings"},
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 503  # warming hold unchanged


async def test_real_app_owns_onboarding_after_set_app(tmp_path, monkeypatch) -> None:
    """Once set_app runs, delegation wins — the fast path must not shadow it."""
    from jarvis.setup import onboarding_fastpath as fp

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    seen: list[str] = []

    async def real_app(scope, receive, send) -> None:
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"real"})

    boot = FastBootstrap(dist_dir=tmp_path / "no-dist")
    boot.set_app(real_app)
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "GET", "path": "/api/onboarding/state"},
        _receive_empty,
        send,
    )
    assert seen == ["/api/onboarding/state"]
    assert sent[1]["body"] == b"real"
