"""The Passwords section's REST endpoints.

Contract (see jarvis/ui/web/logins_routes.py):
- GET    /api/logins                      → {"logins": [summary, ...]}
- POST   /api/logins                      → create/replace; 201
- PATCH  /api/logins/{service_id}         → partial edit; 404 if unknown
- DELETE /api/logins/{service_id}         → idempotent; {"removed": bool}
- POST   /api/logins/{service_id}/reveal  → the password, for an explicit click

The vault is redirected to an in-memory backend through the single
``default_store`` seam, so no test ever touches the real keychain — which also
makes these run unchanged on a headless Linux box with no keyring service.

The rule these tests exist to hold: only ``/reveal`` may ever return a
password, and it is a POST so a secret cannot land in an access log or a
browser history.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.logins import store as store_module
from jarvis.logins.store import CredentialStore, LoginStatus
from jarvis.marketplace.token_store import InMemoryBackend

_PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    store = CredentialStore(backend=InMemoryBackend())
    monkeypatch.setattr(store_module, "default_store", lambda: store)
    return store


@pytest.fixture
def client(vault: CredentialStore) -> TestClient:
    from jarvis.ui.web.logins_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _github(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "label": "GitHub",
        "domains": ["github.com"],
        "username": "someone@example.com",
        "password": _PASSWORD,
        "notes": "# GitHub\n\nDeveloper account.",
    }
    payload.update(overrides)
    return payload


class TestCreateAndList:
    def test_an_empty_vault_lists_nothing(self, client: TestClient) -> None:
        response = client.get("/api/logins")

        assert response.status_code == 200
        assert response.json() == {"logins": []}

    def test_creating_returns_201_and_the_summary(self, client: TestClient) -> None:
        response = client.post("/api/logins", json=_github())

        assert response.status_code == 201
        body = response.json()
        assert body["service_id"] == "github"
        assert body["username"] == "someone@example.com"
        assert body["has_password"] is True

    def test_the_service_id_is_derived_from_the_label(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/logins", json=_github(label="My Bank"))

        assert response.json()["service_id"] == "my-bank"

    def test_a_created_login_appears_in_the_list(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        logins = client.get("/api/logins").json()["logins"]

        assert [entry["label"] for entry in logins] == ["GitHub"]

    def test_a_new_entry_starts_unproven(self, client: TestClient) -> None:
        """It has produced no successful login yet, so the browser tool asks."""
        response = client.post("/api/logins", json=_github())

        assert response.json()["status"] == LoginStatus.UNKNOWN.value


class TestNoPasswordEverLeaks:
    def test_the_create_response_carries_no_password(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/logins", json=_github())

        assert _PASSWORD not in response.text
        assert "password" not in response.json()

    def test_the_list_response_carries_no_password(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        response = client.get("/api/logins")

        assert _PASSWORD not in response.text

    def test_the_patch_response_carries_no_password(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        response = client.patch("/api/logins/github", json={"label": "GitHub Work"})

        assert _PASSWORD not in response.text


class TestReveal:
    def test_reveal_returns_the_password(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        response = client.post("/api/logins/github/reveal")

        assert response.status_code == 200
        assert response.json()["password"] == _PASSWORD

    def test_reveal_is_not_reachable_by_get(self, client: TestClient) -> None:
        """A secret in a URL lands in access logs, history and every proxy."""
        client.post("/api/logins", json=_github())

        response = client.get("/api/logins/github/reveal")

        assert response.status_code == 405

    def test_revealing_an_unknown_login_is_404(self, client: TestClient) -> None:
        assert client.post("/api/logins/nope/reveal").status_code == 404

    def test_reveal_is_flagged_dangerous_for_the_generated_cli(self) -> None:
        from jarvis.ui.web.logins_routes import router

        route = next(
            r for r in router.routes if getattr(r, "path", "").endswith("/reveal")
        )
        assert route.openapi_extra == {"x-jarvis-dangerous": True}


class TestUpdate:
    def test_a_partial_edit_keeps_untouched_fields(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        client.patch("/api/logins/github", json={"label": "GitHub Work"})

        body = client.post("/api/logins/github/reveal").json()
        assert body["password"] == _PASSWORD
        assert body["username"] == "someone@example.com"

    def test_editing_an_unknown_login_is_404(self, client: TestClient) -> None:
        assert client.patch("/api/logins/nope", json={"label": "x"}).status_code == 404

    def test_a_changed_password_becomes_unproven_again(
        self, client: TestClient, vault: CredentialStore
    ) -> None:
        """Otherwise a silent edit inherits the OLD value's confirmed status and
        the next unattended login runs without asking."""
        client.post("/api/logins", json=_github())
        vault.mark_used("github", LoginStatus.OK)

        response = client.patch("/api/logins/github", json={"password": "new-one"})

        assert response.json()["status"] == LoginStatus.UNKNOWN.value

    def test_an_unchanged_password_keeps_the_proven_status(
        self, client: TestClient, vault: CredentialStore
    ) -> None:
        client.post("/api/logins", json=_github())
        vault.mark_used("github", LoginStatus.OK)

        response = client.patch("/api/logins/github", json={"label": "GitHub Work"})

        assert response.json()["status"] == LoginStatus.OK.value


class TestDelete:
    def test_deleting_removes_the_entry(self, client: TestClient) -> None:
        client.post("/api/logins", json=_github())

        response = client.delete("/api/logins/github")

        assert response.json() == {"removed": True}
        assert client.get("/api/logins").json()["logins"] == []

    def test_deleting_an_absent_entry_is_idempotent(self, client: TestClient) -> None:
        response = client.delete("/api/logins/github")

        assert response.status_code == 200
        assert response.json() == {"removed": False}


class TestValidation:
    def test_an_empty_label_is_rejected(self, client: TestClient) -> None:
        assert client.post("/api/logins", json=_github(label="   ")).status_code == 400

    def test_a_missing_label_is_rejected(self, client: TestClient) -> None:
        payload = _github()
        del payload["label"]

        assert client.post("/api/logins", json=payload).status_code == 422

    def test_an_oversized_note_is_rejected(self, client: TestClient) -> None:
        assert (
            client.post("/api/logins", json=_github(notes="x" * 16_001)).status_code
            == 400
        )

    def test_too_many_domains_are_rejected(self, client: TestClient) -> None:
        payload = _github(domains=[f"d{i}.example" for i in range(26)])

        assert client.post("/api/logins", json=payload).status_code == 400


class TestDegradation:
    def test_an_unopenable_vault_reports_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A headless Linux box with no keyring service must say what is wrong."""

        def _boom() -> CredentialStore:
            raise RuntimeError("no keyring backend")

        monkeypatch.setattr(store_module, "default_store", _boom)
        from jarvis.ui.web.logins_routes import router

        app = FastAPI()
        app.include_router(router)

        response = TestClient(app).get("/api/logins")

        assert response.status_code == 503
        assert "keyring" in response.json()["detail"].lower()
