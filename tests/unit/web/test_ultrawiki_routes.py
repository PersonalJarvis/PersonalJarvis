"""Full-app tests for the /api/ultrawiki REST surface.

Pattern: a real ``WebServer`` (so the router mount, SurfaceSecurity, and the
OpenAPI metadata are all the production ones) with a hand-wired
``UltraWikiService`` on ``app.state.ultrawiki`` — the same manual wiring the
task-stack integration tests use, because ``WebServer.start()`` never runs
under ``TestClient``.

Offline discipline: the service's own pipeline gets an UNCONFIGURED embedding
factory (claims no embed work), and the tests drive a separate
``PipelineWorker`` inline with a fake backend + fake distiller — deterministic,
no sleeps, no network, no credentials. Provider readiness probes are
monkeypatched at the module seams the routes import through.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer
from jarvis.ultrawiki import service as uw_service_mod
from jarvis.ultrawiki.service import UltraWikiService

#: Routes that MUST carry the x-jarvis-dangerous OpenAPI extra.
DANGEROUS_ROUTES = (
    ("/api/ultrawiki/activate", "post"),
    ("/api/ultrawiki/deactivate", "post"),
    ("/api/ultrawiki/settings", "put"),
    ("/api/ultrawiki/test/{slot}", "post"),
    ("/api/ultrawiki/sources/{source_id}/approve", "post"),
    ("/api/ultrawiki/sources/{source_id}/sync", "post"),
    ("/api/ultrawiki/jobs/{job_id}/cancel", "post"),
)


class FakeEmbeddingBackend:
    """Offline 3-dimensional embedding backend for the inline pipeline."""

    name = "fake"

    def ready(self) -> tuple[bool, str]:
        return True, ""

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


async def fake_distill(cfg, *, title, body, source_kind):
    """Offline distiller returning a DistillResult-shaped namespace."""
    return SimpleNamespace(
        question=f"What is {title or 'this'} about?",
        summary=(body or "")[:80],
        resolution="",
        entities=[],
        refs=[],
        raw_json="",
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Full WebServer app + hand-wired UltraWikiService, tmp config + data dir."""
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text("", encoding="utf-8")
    # config_writer resolves through resolve_config_path(), which honours this.
    monkeypatch.setenv("JARVIS_CONFIG", str(toml_path))
    # Deterministic, offline provider probes at the seams the routes/service
    # import through (no keyring walks, no localhost Ollama probe).
    monkeypatch.setattr(
        "jarvis.ultrawiki.embeddings.available_backends",
        lambda cfg: [
            {
                "name": "gemini",
                "ready": True,
                "reason": "",
                "default_model": "gemini-embedding-001",
            },
            {
                "name": "openai",
                "ready": False,
                "reason": "no key",
                "default_model": "text-embedding-3-small",
            },
        ],
    )
    monkeypatch.setattr(
        "jarvis.ultrawiki.rerank.available_rerankers", lambda cfg: []
    )
    monkeypatch.setattr(
        "jarvis.memory.wiki.provider_chain.credential_ready_wiki_providers",
        lambda **_kw: [],
    )

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.memory.data_dir = str(tmp_path / "data")
    server = WebServer(cfg, bus=EventBus())
    # ensure_started() only starts the pipeline once the mode is enabled; the
    # unconfigured factory (None) makes that background pipeline claim no
    # embed/distill work, so the inline driver below stays deterministic.
    service = UltraWikiService(
        cfg, embedding_backend_factory=lambda: None, distill_fn=fake_distill
    )
    server.app.state.ultrawiki = service
    uw_service_mod.clear_jobs()
    with TestClient(server.app) as client:
        yield SimpleNamespace(
            client=client,
            service=service,
            server=server,
            cfg=cfg,
            toml=toml_path,
            tmp=tmp_path,
        )
        client.portal.call(service.shutdown)
    uw_service_mod.clear_jobs()


def _activate(env) -> dict:
    # The explicit model matters: the inline PipelineWorker resolves the model
    # from cfg (its fake backend has no DEFAULT_MODELS entry).
    response = env.client.post(
        "/api/ultrawiki/activate",
        json={"embedding_provider": "gemini", "embedding_model": "fake-embed"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_and_sync_folder(env) -> tuple[str, str]:
    """Register + approve + sync a local-folder source; returns (source_id, job_id)."""
    docs = env.tmp / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "alpha.md").write_text(
        "# Alpha\n\nThe quarterly ledger reconciliation lives here.",
        encoding="utf-8",
    )
    (docs / "beta.md").write_text(
        "# Beta\n\nNotes about the telescope maintenance schedule.",
        encoding="utf-8",
    )
    created = env.client.post(
        "/api/ultrawiki/sources",
        json={"connector": "local-folder", "label": "Docs", "config": {"root": str(docs)}},
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["consent"] == "pending"
    source_id = source["id"]

    approved = env.client.post(f"/api/ultrawiki/sources/{source_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["consent"] == "approved"

    synced = env.client.post(f"/api/ultrawiki/sources/{source_id}/sync")
    assert synced.status_code == 201, synced.text
    return source_id, synced.json()["job_id"]


def _wait_for_job(env, job_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = env.client.get(f"/api/ultrawiki/jobs/{job_id}")
        assert response.status_code == 200, response.text
        snapshot = response.json()
        if snapshot["status"] in ("done", "failed", "cancelled"):
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout_s}s")


def _drive_pipeline(env) -> None:
    """Advance every item through the staged ladder inline (no sleeps)."""
    from jarvis.ultrawiki.pipeline import PipelineWorker

    async def _run() -> None:
        store = env.service._store  # noqa: SLF001 — deliberate test seam
        assert store is not None
        worker = PipelineWorker(
            store,
            env.cfg,
            embedding_backend_factory=lambda: FakeEmbeddingBackend(),
            distill_fn=fake_distill,
        )
        for _ in range(8):
            if await worker.run_once() == 0:
                break

    env.client.portal.call(_run)


# ---------------------------------------------------------------------------
# Status / activation
# ---------------------------------------------------------------------------


def test_status_answers_while_disabled(env) -> None:
    response = env.client.get("/api/ultrawiki/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["started"] is False
    assert body["db_backend"] == "sqlite"
    assert "search_legs" in body
    assert body["search_legs"]["keyword"] == {"available": True}
    assert body["search_legs"]["vector"]["available"] is False


def test_activate_flips_mode_and_creates_pending_sources(env) -> None:
    body = _activate(env)
    assert body["enabled"] is True
    assert body["persisted"] is True
    assert sorted(body["sources_created"]) == ["jarvis-conversations", "normal-wiki"]
    assert body["next_steps"]
    assert env.cfg.ultrawiki.enabled is True
    assert env.cfg.ultrawiki.embedding_provider == "gemini"
    toml_text = env.toml.read_text(encoding="utf-8")
    assert "enabled = true" in toml_text
    assert 'embedding_provider = "gemini"' in toml_text

    listed = env.client.get("/api/ultrawiki/sources").json()
    by_id = {row["id"]: row for row in listed["sources"]}
    assert by_id["normal-wiki"]["consent"] == "pending"
    assert by_id["jarvis-conversations"]["consent"] == "pending"


def test_activate_unready_backend_is_409(env) -> None:
    response = env.client.post(
        "/api/ultrawiki/activate", json={"embedding_provider": "openai"}
    )
    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Consent gate + sync jobs
# ---------------------------------------------------------------------------


def test_sync_on_pending_source_is_409(env) -> None:
    _activate(env)
    response = env.client.post("/api/ultrawiki/sources/normal-wiki/sync")
    assert response.status_code == 409
    assert "not approved" in response.json()["detail"]


def test_approve_then_sync_completes_against_local_folder(env) -> None:
    _activate(env)
    source_id, job_id = _approve_and_sync_folder(env)
    snapshot = _wait_for_job(env, job_id)
    assert snapshot["status"] == "done", snapshot
    assert snapshot["new"] == 2
    assert snapshot["source_id"] == source_id

    listed = env.client.get("/api/ultrawiki/sources").json()
    row = next(r for r in listed["sources"] if r["id"] == source_id)
    assert row["counts"]["total"] == 2


def test_jobs_list_get_cancel_shapes(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    listed = env.client.get("/api/ultrawiki/jobs")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert any(job["job_id"] == job_id for job in body["jobs"])

    assert env.client.get("/api/ultrawiki/jobs/no-such-job").status_code == 404
    assert env.client.post("/api/ultrawiki/jobs/no-such-job/cancel").status_code == 404

    # Terminal job — cancel refuses with 409, honestly.
    response = env.client.post(f"/api/ultrawiki/jobs/{job_id}/cancel")
    assert response.status_code == 409
    assert "terminal" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_disabled_is_409_with_honest_message(env) -> None:
    response = env.client.get("/api/ultrawiki/search", params={"q": "ledger"})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "UltraWiki mode is off — the normal wiki answers today."
    )


def test_search_returns_fused_hits_after_inline_pipeline(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    response = env.client.get("/api/ultrawiki/search", params={"q": "ledger"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    hit = body["results"][0]
    assert "alpha" in hit["title"].lower()
    assert hit["permalink"]
    assert "keyword" in hit["matched_by"]
    assert hit["score"] > 0


# ---------------------------------------------------------------------------
# Settings guard (D-3: embedding change re-embeds the corpus)
# ---------------------------------------------------------------------------


def test_embedding_change_without_confirm_is_409(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    response = env.client.put(
        "/api/ultrawiki/settings", json={"embedding_provider": "openai"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["vector_items"] == 2
    assert "confirm_reembed" in detail["message"]

    confirmed = env.client.put(
        "/api/ultrawiki/settings",
        json={"embedding_provider": "openai", "confirm_reembed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["changed"] == ["embedding_provider"]
    assert body["reembed_started"] is True
    assert env.cfg.ultrawiki.embedding_provider == "openai"
    assert 'embedding_provider = "openai"' in env.toml.read_text(encoding="utf-8")

    # The vectors were dropped: items fell back to keyword_indexed and wait
    # for the (deliberately unconfigured) background embed stage.
    counts = env.client.get("/api/ultrawiki/status").json()["counts"]
    assert counts["embedded"] == 0
    assert counts["distilled"] == 0
    assert counts["keyword_indexed"] == 2


def test_update_settings_without_changes_is_noop(env) -> None:
    _activate(env)
    response = env.client.put("/api/ultrawiki/settings", json={})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "changed": [],
        "persisted": True,
        "reembed_started": False,
    }


# ---------------------------------------------------------------------------
# Areas + providers + deactivation
# ---------------------------------------------------------------------------


def test_areas_list_and_create(env) -> None:
    _activate(env)
    listed = env.client.get("/api/ultrawiki/areas").json()
    assert any(area["is_default"] for area in listed["areas"])

    created = env.client.post("/api/ultrawiki/areas", json={"name": "Work Stuff"})
    assert created.status_code == 201
    assert created.json() == {"id": "work-stuff", "name": "Work Stuff"}


def test_list_providers_reports_slots(env, monkeypatch) -> None:
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda name: None)
    response = env.client.get("/api/ultrawiki/providers")
    assert response.status_code == 200
    body = response.json()
    assert {row["name"] for row in body["embedding"]} == {"gemini", "openai"}
    backends = {row["name"]: row for row in body["db_backends"]}
    assert backends["sqlite"]["ready"] is True
    assert backends["postgres"]["ready"] is False
    assert backends["postgres"]["secret_present"] is False


def test_deactivate_is_non_destructive(env) -> None:
    _activate(env)
    response = env.client.post("/api/ultrawiki/deactivate")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["non_destructive"] is True
    assert env.cfg.ultrawiki.enabled is False
    assert "enabled = false" in env.toml.read_text(encoding="utf-8")
    # Search now refuses with the mode-off message; status still answers.
    assert env.client.get("/api/ultrawiki/search", params={"q": "x"}).status_code == 409
    assert env.client.get("/api/ultrawiki/status").status_code == 200


# ---------------------------------------------------------------------------
# Contract guards (CLI-first + danger metadata + mount)
# ---------------------------------------------------------------------------


def test_dangerous_routes_carry_the_flag(env) -> None:
    spec = env.server.app.openapi()
    for path, method in DANGEROUS_ROUTES:
        operation = spec["paths"][path][method]
        assert operation.get("x-jarvis-dangerous") is True, (path, method)


def test_router_import_line_exists_in_server_source() -> None:
    import jarvis.ui.web.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "from .ultrawiki_routes import router as ultrawiki_router" in source
    assert "app.include_router(ultrawiki_router)" in source
