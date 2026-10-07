"""Public profile API and the narrow extension transport boundary."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.browser.session import BrowserJobs
from jarvis.ui.web.browser_profile_routes import router
from jarvis.ui.web.surface_security import chrome_transport_allowed
from tests.fakes.fake_browser_profiles import ProfileRoster, already_started

pytestmark = pytest.mark.no_auto_web_auth


def client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.society = SimpleNamespace(
        roster=ProfileRoster(), browser=BrowserJobs(tmp_path), ensure_started=already_started
    )
    return TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 40000))


def test_api_selected_all_override_and_restart(tmp_path):
    c = client(tmp_path)
    root = "/api/society/browser/profiles"
    response = c.post(root, json={"name": "Team", "kind": "managed"})
    assert response.status_code == 200
    pid = response.json()["id"]
    response = c.put(f"{root}/{pid}/sharing", json={"scope": "selected", "agent_ids": ["scout"]})
    assert response.json()["bindings"]["lead"]["effective_profile_id"] is None
    assert response.json()["bindings"]["scout"]["effective_profile_id"] == pid
    response = c.put(f"{root}/{pid}/sharing", json={"scope": "all", "agent_ids": []})
    assert response.json()["default_profile_id"] == pid
    response = c.put("/api/society/agents/scout/browser/profile", json={"mode": "own"})
    assert response.json()["bindings"]["scout"]["effective_profile_id"] is None
    assert client(tmp_path).get(root).json() == response.json()
    assert (
        c.put(
            f"{root}/{pid}/sharing", json={"scope": "selected", "agent_ids": ["missing"]}
        ).status_code
        == 400
    )


def test_extension_pairing_refuses_web_origin_and_invalid_code(tmp_path):
    c = client(tmp_path)
    root = "/api/society/browser"
    pid = c.post(
        f"{root}/profiles", json={"name": "Chrome", "kind": "chrome", "allowed_domains": ["x.com"]}
    ).json()["id"]
    code = c.post(f"{root}/profiles/{pid}/pair").json()["pairing_code"]
    body = {"code": code, "installation_id": "instance"}
    assert (
        c.post(
            f"{root}/chrome/pair", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    response = c.post(
        f"{root}/chrome/pair", json=body, headers={"Origin": "chrome-extension://" + "a" * 32}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"profile_id", "token"}
    assert c.post(f"{root}/chrome/pair", json=body).status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        {"path": "/api/society/browser/profiles"},
        {"method": "GET"},
        {"client": ("192.168.1.9", 30)},
        {"headers": [(b"host", b"127.0.0.1"), (b"origin", b"https://evil.example")]},
        {"headers": [(b"host", b"evil.example")]},
        {"headers": [(b"host", b"127.0.0.1"), (b"x-forwarded-for", b"1.2.3.4")]},
    ],
)
def test_extension_transport_exception_is_narrow(change):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/society/browser/chrome/pair",
        "client": ("127.0.0.1", 30),
        "headers": [(b"host", b"127.0.0.1")],
    }
    assert chrome_transport_allowed(scope)
    scope.update(change)
    assert not chrome_transport_allowed(scope)


def test_extension_download_is_complete_and_does_not_contain_credentials(tmp_path):
    import io
    import json
    import zipfile

    response = client(tmp_path).get("/api/society/browser/extension.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["manifest_version"] == 3
        assert "cookies" not in manifest["permissions"]
        assert manifest["background"]["service_worker"] in archive.namelist()
