"""``POST /api/workspace/agents/{name}/recheck`` — "did that install work?".

The route an install dialog polls while a package manager runs, and the reason
it is not simply ``GET /agents``: that read answers from a 30-second cache and
probes every registered CLI at once, so it can neither be polled nor tell the
truth about a binary that appeared two seconds ago.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.clis.spec import CliStatus
from jarvis.workspace import agents as registry


class OneProber:
    """Answers a single probe and refuses to be used as a sweep."""

    def __init__(self, status: CliStatus) -> None:
        self.status = status
        self.probed: list[str] = []

    async def probe(self, spec) -> CliStatus:  # noqa: ANN001
        self.probed.append(spec.name)
        return self.status

    async def probe_all(self, specs) -> dict[str, CliStatus]:  # noqa: ANN001
        raise AssertionError("the recheck route must not run the full sweep")


@pytest.fixture
def client():
    from jarvis.ui.web import workspace_routes

    app = FastAPI()
    app.include_router(workspace_routes.router)
    registry.invalidate_agent_detection()
    yield TestClient(app)
    registry.invalidate_agent_detection()


def test_a_freshly_installed_cli_is_reported_as_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    prober = OneProber(CliStatus(installed=True, version="0.1.1"))
    monkeypatch.setattr(registry, "CliStatusProber", lambda: prober)

    body = client.post("/api/workspace/agents/deepseek-harness/recheck").json()

    assert body == {
        "name": "deepseek-harness",
        "display_name": "DeepSeek Harness",
        "installed": True,
        "version": "0.1.1",
    }
    assert prober.probed == ["deepseek-harness"]


def test_a_failed_install_is_reported_as_still_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route reports what it found, never what the user was hoping for."""
    monkeypatch.setattr(registry, "CliStatusProber", lambda: OneProber(CliStatus(installed=False)))

    body = client.post("/api/workspace/agents/deepseek-harness/recheck").json()

    assert body["installed"] is False
    assert body["version"] is None


def test_an_unregistered_name_is_a_404_and_not_an_invented_row(client: TestClient) -> None:
    response = client.post("/api/workspace/agents/no-such-cli/recheck")
    assert response.status_code == 404
