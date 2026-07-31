"""PUT /api/providers/{id}/base-url + the base-url card payload fields.

The server-URL field is what makes a local provider reconfigurable IN-APP
(§3: never by hand-editing jarvis.toml). Pins: normalization to a bare server
root, honest 400/404/422 refusals, the payload triple
(supports_base_url / default_base_url / base_url), and the atomic TOML write.
"""

from __future__ import annotations

import pytest
import tomlkit
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture
def server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    return srv


@pytest.fixture
def recorded_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    def _record(provider: str, base_url: str | None, **kwargs) -> None:
        calls.append((provider, base_url))

    monkeypatch.setattr("jarvis.core.config_writer.set_provider_base_url", _record)
    return calls


def test_put_normalizes_to_server_root(
    server: WebServer, recorded_writes: list[tuple[str, str | None]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/ollama/base-url",
            json={"base_url": "http://gpu.lan:11434/v1/"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_url"] == "http://gpu.lan:11434"
    assert body["default_base_url"] == "http://localhost:11434"
    assert recorded_writes == [("ollama", "http://gpu.lan:11434")]


def test_put_clear_resets_to_vendor_default(
    server: WebServer, recorded_writes: list[tuple[str, str | None]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/providers/ollama/base-url", json={"base_url": None})
    assert resp.status_code == 200
    assert resp.json()["base_url"] is None
    assert recorded_writes == [("ollama", None)]


def test_put_rejects_non_http_scheme(
    server: WebServer, recorded_writes: list[tuple[str, str | None]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/providers/local-openai/base-url", json={"base_url": "ftp://nope"})
    assert resp.status_code == 422
    assert recorded_writes == []


def test_put_refuses_cloud_provider(
    server: WebServer, recorded_writes: list[tuple[str, str | None]]
) -> None:
    """Cloud cards keep their advanced [brain.providers.<id>].base_url override
    (team proxy), but the CARD field is local-provider-only by design."""
    with TestClient(server.app) as client:
        resp = client.put("/api/providers/gemini/base-url", json={"base_url": "http://x:1"})
    assert resp.status_code == 400
    assert recorded_writes == []


def test_put_unknown_provider_is_404(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/providers/does-not-exist/base-url", json={"base_url": "http://x:1"})
    assert resp.status_code == 404


def test_provider_payload_carries_base_url_fields(server: WebServer) -> None:
    with TestClient(server.app) as client:
        data = client.get("/api/providers").json()
    items = data["providers"] if isinstance(data, dict) else data
    by_id = {p["id"]: p for p in items}

    ollama = by_id["ollama"]
    assert ollama["supports_base_url"] is True
    assert ollama["default_base_url"] == "http://localhost:11434"
    assert ollama["base_url"] is None  # no override stored in a fresh config

    local = by_id["local-openai"]
    assert local["supports_base_url"] is True
    assert local["default_base_url"] is None  # the user must set one

    gemini = by_id["gemini"]
    assert gemini["supports_base_url"] is False


# ── The TOML writer itself (AP-7 discipline) ─────────────────────────────
def test_set_provider_base_url_writes_quoted_table(tmp_path, monkeypatch) -> None:
    from jarvis.core import config_writer

    # Keep the best-effort drift-guard baseline sync away from the real file.
    monkeypatch.setattr(config_writer, "_update_config_soll_section", lambda *a, **k: None)  # i18n-allow: identifier, named after config-soll.json
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")

    config_writer.set_provider_base_url("local-openai", "http://localhost:8000", path=toml_path)
    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    # Round-trip through the parser proves the hyphenated id serialized to a
    # VALID table key (TOML bare keys allow dashes).
    assert doc["brain"]["providers"]["local-openai"]["base_url"] == "http://localhost:8000"

    # Clearing writes an empty string (falsy everywhere the override is read)
    # instead of deleting the key, so TOML and the baseline stay in agreement.
    config_writer.set_provider_base_url("local-openai", None, path=toml_path)
    doc = tomlkit.parse(toml_path.read_text(encoding="utf-8"))
    assert doc["brain"]["providers"]["local-openai"]["base_url"] == ""


def test_set_provider_base_url_preserves_bom(tmp_path, monkeypatch) -> None:
    """BOM-safe like every writer here (AP-7 / BUG-018)."""
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "_update_config_soll_section", lambda *a, **k: None)  # i18n-allow: identifier, named after config-soll.json
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text('﻿[brain]\nprimary = "gemini"\n', encoding="utf-8")

    config_writer.set_provider_base_url("ollama", "http://gpu.lan:11434", path=toml_path)
    raw = toml_path.read_text(encoding="utf-8")
    assert raw.startswith("﻿")
    doc = tomlkit.parse(raw.lstrip("﻿"))
    assert doc["brain"]["providers"]["ollama"]["base_url"] == "http://gpu.lan:11434"
