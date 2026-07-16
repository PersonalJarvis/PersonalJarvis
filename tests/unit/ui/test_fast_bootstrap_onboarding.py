"""While warming, the bootstrap must answer /api/onboarding/* itself."""
import json
from http.cookies import SimpleCookie

from jarvis.ui.web.fast_bootstrap import FastBootstrap
from jarvis.ui.web.surface_security import COOKIE_NAME

_TOKEN = "fast-bootstrap-onboarding-session"  # noqa: S105


def _collector():
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return sent, send


async def _receive_empty():
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope(path: str, method: str = "GET", *, cookie: str | None = None) -> dict:
    headers = [
        (b"host", b"127.0.0.1:47821"),
        (b"origin", b"http://127.0.0.1:47821"),
    ]
    if cookie is not None:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie}".encode("ascii")))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "client": ("127.0.0.1", 50000),
        "headers": headers,
    }


async def _exchange(boot: FastBootstrap) -> str:
    body = json.dumps({"session_token": _TOKEN}).encode("utf-8")
    sent, send = _collector()

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    await boot.app(_scope("/api/ui/session", "POST"), receive, send)
    assert sent[0]["status"] == 204
    raw_cookie = next(value for key, value in sent[0]["headers"] if key == b"set-cookie")
    parsed = SimpleCookie()
    parsed.load(raw_cookie.decode("latin-1"))
    return parsed[COOKIE_NAME].value


async def test_onboarding_state_answers_while_warming(tmp_path, monkeypatch) -> None:
    from jarvis.setup import onboarding_fastpath as fp

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    boot = FastBootstrap(dist_dir=tmp_path / "no-dist", session_token=_TOKEN)
    cookie = await _exchange(boot)
    sent, send = _collector()
    await boot.app(
        _scope("/api/onboarding/state", cookie=cookie),
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["completed"] is False


async def test_onboarding_complete_persists_while_warming(tmp_path, monkeypatch) -> None:
    from jarvis.setup import onboarding_fastpath as fp
    from jarvis.setup import state as st

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    boot = FastBootstrap(dist_dir=tmp_path / "no-dist", session_token=_TOKEN)
    cookie = await _exchange(boot)
    sent, send = _collector()
    await boot.app(
        _scope("/api/onboarding/complete", "POST", cookie=cookie),
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 200
    assert st.is_onboarding_complete(tmp_path / "setup_state.json") is True


async def test_other_api_routes_still_held(tmp_path) -> None:
    boot = FastBootstrap(
        hold_timeout=0.05, dist_dir=tmp_path / "no-dist", session_token=_TOKEN
    )
    cookie = await _exchange(boot)
    sent, send = _collector()
    await boot.app(
        _scope("/api/settings", cookie=cookie),
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

    boot = FastBootstrap(dist_dir=tmp_path / "no-dist", session_token=_TOKEN)
    boot.set_app(real_app)
    sent, send = _collector()
    await boot.app(
        _scope("/api/onboarding/state"),
        _receive_empty,
        send,
    )
    assert seen == ["/api/onboarding/state"]
    assert sent[1]["body"] == b"real"
