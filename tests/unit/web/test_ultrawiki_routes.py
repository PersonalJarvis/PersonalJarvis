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

import asyncio
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
            # The injected distiller brings its own provider: the production
            # credential-chain gate must not run, or this test would pass or
            # fail depending on which keys the host happens to hold (AP-23).
            distill_ready_fn=lambda: (True, ""),
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
    # Five-layer parity (AP-4): the SearchResult dataclass fields reach the
    # payload verbatim. The rerank stage is off here, so the absolute grade is
    # honestly null rather than a fabricated number.
    assert hit["rerank_score"] is None
    assert isinstance(hit["context"], list)


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
# Ranking settings (rerank slot + the knobs it governs)
# ---------------------------------------------------------------------------


def test_llm_rerank_provider_is_accepted_and_persisted(env) -> None:
    """The universal backend must be selectable without any vendor key."""
    _activate(env)

    response = env.client.put(
        "/api/ultrawiki/settings",
        json={"rerank_provider": "llm", "rerank_model": "some-cheap-model"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == ["rerank_model", "rerank_provider"]
    assert env.cfg.ultrawiki.rerank_provider == "llm"
    assert env.cfg.ultrawiki.rerank_model == "some-cheap-model"
    toml = env.toml.read_text(encoding="utf-8")
    assert 'rerank_provider = "llm"' in toml
    assert 'rerank_model = "some-cheap-model"' in toml


def test_unknown_rerank_provider_is_refused(env) -> None:
    _activate(env)
    response = env.client.put(
        "/api/ultrawiki/settings", json={"rerank_provider": "not-a-backend"}
    )
    assert response.status_code == 400
    assert "not-a-backend" in response.json()["detail"]


def test_ranking_knobs_persist_as_numbers(env) -> None:
    _activate(env)

    response = env.client.put(
        "/api/ultrawiki/settings",
        json={
            "rerank_min_score": 6.5,
            "rrf_keyword_weight": 2,
            "recency_half_life_days": 0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] == [
        "recency_half_life_days",
        "rerank_min_score",
        "rrf_keyword_weight",
    ]
    assert env.cfg.ultrawiki.rerank_min_score == 6.5
    assert env.cfg.ultrawiki.recency_half_life_days == 0
    toml = env.toml.read_text(encoding="utf-8")
    # Numbers, not quoted strings that merely happen to parse.
    assert "rerank_min_score = 6.5" in toml
    assert "rrf_keyword_weight = 2.0" in toml


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"rerank_min_score": 11}, "between 0.0 and 10.0"),
        ({"rerank_min_score": -1}, "between 0.0 and 10.0"),
        ({"rrf_vector_weight": 999}, "between 0.0 and 10.0"),
    ],
)
def test_out_of_range_ranking_knobs_are_refused(env, payload, needle) -> None:
    """Refused, never clamped: a silently corrected value would leave the UI
    showing a number the ranking does not actually use."""
    _activate(env)
    response = env.client.put("/api/ultrawiki/settings", json=payload)

    assert response.status_code == 400
    assert needle in response.json()["detail"]
    # Nothing was written on the way to the rejection.
    assert "rerank_min_score" not in env.toml.read_text(encoding="utf-8")


def test_status_reports_the_rerank_slot_with_its_ranking_knobs(env) -> None:
    """The knobs ride along with the slot they govern, so the settings card
    can show what the ranking actually does. (That the `llm` backend is
    OFFERED is a rerank-registry property, covered in
    tests/unit/ultrawiki/test_rerank.py — this fixture stubs the backend
    probe out to stay offline.)"""
    _activate(env)
    slot = env.client.get("/api/ultrawiki/status").json()["slots"]["rerank"]

    assert slot["ranking"]["rerank_min_score"] == 4.0
    assert slot["ranking"]["keyword_weight"] == 1.0
    assert slot["ranking"]["vector_weight"] == 1.0
    assert slot["ranking"]["recency_half_life_days"] == 180.0
    assert slot["model"] == ""  # honest empty, not a fabricated default


def test_status_reflects_a_changed_relevance_floor(env) -> None:
    _activate(env)
    env.client.put("/api/ultrawiki/settings", json={"rerank_min_score": 7})

    slot = env.client.get("/api/ultrawiki/status").json()["slots"]["rerank"]

    assert slot["ranking"]["rerank_min_score"] == 7.0


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


def test_slot_test_route_declares_a_long_cli_timeout(env) -> None:
    """A real provider call outlives the CLI's default client timeout."""
    spec = env.server.app.openapi()
    operation = spec["paths"]["/api/ultrawiki/test/{slot}"]["post"]
    assert operation.get("x-jarvis-timeout-seconds") == 120


# ---------------------------------------------------------------------------
# Unwired service — every route stays honest instead of crashing
# ---------------------------------------------------------------------------


def test_routes_are_honest_while_the_service_is_unwired(env) -> None:
    env.server.app.state.ultrawiki = None
    try:
        sources = env.client.get("/api/ultrawiki/sources")
        assert sources.status_code == 503
        assert "not wired" in sources.json()["detail"]

        # /status is the honesty surface: it ALWAYS answers, degraded.
        status = env.client.get("/api/ultrawiki/status")
        assert status.status_code == 200
        body = status.json()
        assert body["started"] is False
        assert body["slots"] == {}
        assert body["sources"] == []
        assert body["pipeline"]["running"] is False
        assert body["pipeline"]["state"] == "paused"
        assert body["pipeline"]["reason"]
        assert any("not wired" in line for line in body["degradations"])
        assert "search_legs" in body
    finally:
        env.server.app.state.ultrawiki = env.service


# ---------------------------------------------------------------------------
# Sync: one at a time, and the full refresh
# ---------------------------------------------------------------------------


def test_second_sync_of_one_source_is_409_with_the_active_job(env) -> None:
    _activate(env)
    source_id, job_id = _approve_and_sync_folder(env)
    # The first job may already be done on a fast machine — assert on whichever
    # of the two honest answers applies, never on a race.
    second = env.client.post(f"/api/ultrawiki/sources/{source_id}/sync")
    if second.status_code == 409:
        detail = second.json()["detail"]
        assert detail["job_id"] == job_id
        assert detail["source_id"] == source_id
        assert "already running" in detail["message"]
    else:
        assert second.status_code == 201, second.text
    _wait_for_job(env, job_id)


def test_full_refresh_is_requested_through_the_body(env) -> None:
    _activate(env)
    source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    plain = env.client.post(f"/api/ultrawiki/sources/{source_id}/sync")
    assert plain.status_code == 201, plain.text
    assert plain.json()["full"] is False
    _wait_for_job(env, plain.json()["job_id"])

    full = env.client.post(
        f"/api/ultrawiki/sources/{source_id}/sync", json={"full": True}
    )
    assert full.status_code == 201, full.text
    assert full.json()["full"] is True
    snapshot = _wait_for_job(env, full.json()["job_id"])
    assert snapshot["status"] == "done"
    assert snapshot["mode"] == "backfill"


# ---------------------------------------------------------------------------
# Cancelling a live job
# ---------------------------------------------------------------------------


def test_cancel_of_a_running_job_succeeds(env, monkeypatch) -> None:
    """A live job cancels; a job without a live task answers 409."""
    _activate(env)

    import jarvis.ultrawiki.connectors as connectors_mod
    from jarvis.ultrawiki.types import (
        AuthKind,
        ConnectorCapabilities,
        IncrementalMode,
        RawItem,
    )

    class SlowConnector:
        id = "slow-conn"
        label = "Slow Connector"
        auth = AuthKind.NONE
        capabilities = ConnectorCapabilities(
            backfill=True, incremental=IncrementalMode.NONE, deletes=False
        )

        async def backfill(self, ctx, checkpoint=None):
            yield RawItem(
                external_id="slow-1",
                body="first item",
                permalink="fake://slow/1",
                timestamp_utc="2026-01-01T00:00:00Z",
                title="Slow 1",
            )
            await asyncio.sleep(30)  # cancelled long before this returns

        async def incremental(self, ctx, cursor=None):
            return
            yield  # pragma: no cover — makes this an async generator

    registry = dict(connectors_mod.discover_connectors())
    registry["slow-conn"] = SlowConnector
    monkeypatch.setattr(connectors_mod, "discover_connectors", lambda: registry)

    created = env.client.post(
        "/api/ultrawiki/sources", json={"connector": "slow-conn", "label": "Slow"}
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    env.client.post(f"/api/ultrawiki/sources/{source_id}/approve")
    job_id = env.client.post(
        f"/api/ultrawiki/sources/{source_id}/sync"
    ).json()["job_id"]

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if env.client.get(f"/api/ultrawiki/jobs/{job_id}").json()["status"] == "running":
            break
        time.sleep(0.02)

    cancelled = env.client.post(f"/api/ultrawiki/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {"job_id": job_id, "cancel_requested": True}

    snapshot = _wait_for_job(env, job_id)
    assert snapshot["status"] == "cancelled"
    # Terminal now: a second cancel is refused honestly.
    assert env.client.post(f"/api/ultrawiki/jobs/{job_id}/cancel").status_code == 409


def test_cancel_of_a_job_without_a_live_task_is_409(env) -> None:
    """The narrow window where a job is registered but has no task yet."""
    job = uw_service_mod.SyncJob(
        job_id="pending-job", source_id="whatever", mode="backfill"
    )
    job.status = "queued"
    job.task = None
    uw_service_mod._register_job(job)  # noqa: SLF001 — the registry is module state
    try:
        response = env.client.post("/api/ultrawiki/jobs/pending-job/cancel")
        assert response.status_code == 409
        assert "no live task" in response.json()["detail"]
    finally:
        uw_service_mod.clear_jobs()


# ---------------------------------------------------------------------------
# Dead-letter recovery
# ---------------------------------------------------------------------------


def test_requeue_failed_returns_dead_lettered_items(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    async def _fail_everything() -> None:
        store = env.service._store  # noqa: SLF001 — deliberate test seam
        for item in await store.claim_batch("keyword_indexed", limit=10):
            await store.mark_failed(item["id"], "the distill provider was dead")

    env.client.portal.call(_fail_everything)
    assert env.client.get("/api/ultrawiki/status").json()["counts"]["failed"] == 2

    response = env.client.post("/api/ultrawiki/pipeline/requeue-failed")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requeued"] == 2
    assert body["detail"]
    counts = env.client.get("/api/ultrawiki/status").json()["counts"]
    assert counts["failed"] == 0
    assert counts["captured"] == 2  # nothing was indexed yet, so they restart

    # Nothing left to requeue is an honest zero, not an error.
    again = env.client.post("/api/ultrawiki/pipeline/requeue-failed")
    assert again.status_code == 200
    assert again.json()["requeued"] == 0


def test_requeue_failed_is_dangerous_and_scoped(env) -> None:
    spec = env.server.app.openapi()
    operation = spec["paths"]["/api/ultrawiki/pipeline/requeue-failed"]["post"]
    assert operation.get("x-jarvis-dangerous") is True

    _activate(env)
    response = env.client.post(
        "/api/ultrawiki/pipeline/requeue-failed", json={"source_id": "no-such-source"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Honest pipeline state on the status surface
# ---------------------------------------------------------------------------


def test_status_reports_waiting_for_sources_after_a_fresh_activation(env) -> None:
    """The maintainer report: a fresh activation must not claim to be working."""
    _activate(env)
    pipeline = env.client.get("/api/ultrawiki/status").json()["pipeline"]
    assert pipeline["state"] == "waiting_for_sources"
    assert "approve" in pipeline["reason"].lower()


def test_status_reports_processing_once_items_are_queued(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    pipeline = env.client.get("/api/ultrawiki/status").json()["pipeline"]
    assert pipeline["state"] in ("processing", "paused")
    assert "2" in pipeline["reason"]


def test_status_reports_idle_once_everything_is_processed(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)
    pipeline = env.client.get("/api/ultrawiki/status").json()["pipeline"]
    assert pipeline["state"] == "idle"


# ---------------------------------------------------------------------------
# Activation ordering (a failed activation must not leave the mode on)
# ---------------------------------------------------------------------------


def test_failed_activation_leaves_the_mode_off(env, monkeypatch) -> None:
    async def _explode(_payload=None):
        raise RuntimeError("the store could not be opened")

    monkeypatch.setattr(env.service, "activate", _explode)
    response = env.client.post(
        "/api/ultrawiki/activate",
        json={"embedding_provider": "gemini", "embedding_model": "fake-embed"},
    )
    assert response.status_code == 500
    assert "could not be activated" in response.json()["detail"]
    # The mode switch never flipped — neither live nor on disk.
    assert env.cfg.ultrawiki.enabled is False
    assert "enabled = true" not in env.toml.read_text(encoding="utf-8")
    # Search still answers with the mode-off message, not a broken Ultra view.
    assert env.client.get("/api/ultrawiki/search", params={"q": "x"}).status_code == 409


def test_router_import_line_exists_in_server_source() -> None:
    import jarvis.ui.web.server as server_mod

    source = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "from .ultrawiki_routes import router as ultrawiki_router" in source
    assert "app.include_router(ultrawiki_router)" in source


# ---------------------------------------------------------------------------
# Provider catalog + the guided Supabase link
# ---------------------------------------------------------------------------


def test_catalog_lists_every_slot_with_a_connectable_credential_field(env) -> None:
    """The regression guard for the defect this surface was built to fix.

    Before the catalog existed the settings cards offered providers with no way
    to enter their credential, and pointed at an API-Keys view that had no field
    for them either. Every row must now name the secret slot the UI can write,
    and that slot must be one the secrets API actually accepts.
    """
    from jarvis.ui.web.provider_routes import ALLOWED_SECRET_KEYS

    response = env.client.get("/api/ultrawiki/catalog")
    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert set(slots) == {"storage", "embedding", "distill", "rerank"}
    for slot, rows in slots.items():
        assert rows, f"slot {slot} rendered no providers"
        for row in rows:
            for key in row["secret_keys"]:
                assert key in ALLOWED_SECRET_KEYS, (slot, row["id"], key)
                assert key in row["secrets_set"]
                assert key in row["secret_shared_with"]


def test_catalog_marks_the_configured_provider_as_selected(env) -> None:
    _activate(env)
    body = env.client.get("/api/ultrawiki/catalog").json()
    assert body["selected"]["embedding"] == "gemini"
    embedding = {row["id"]: row for row in body["slots"]["embedding"]}
    assert embedding["gemini"]["selected"] is True
    assert embedding["openai"]["selected"] is False
    # Readiness comes from the provider's own probe, not from being selected.
    assert embedding["gemini"]["ready"] is True
    assert embedding["openai"]["ready"] is False
    assert embedding["openai"]["reason"]
    assert body["models"]["embedding"] == "fake-embed"


def test_catalog_storage_defaults_to_the_local_floor(env) -> None:
    body = env.client.get("/api/ultrawiki/catalog").json()
    storage = {row["id"]: row for row in body["slots"]["storage"]}
    assert body["selected"]["storage"] == "sqlite"
    assert storage["sqlite"]["ready"] is True
    # A cloud preset with no saved connection string is honest about it and
    # says the local store keeps answering — never a bare failure.
    assert storage["supabase"]["ready"] is False
    assert "connection string" in storage["supabase"]["reason"]


def test_selecting_a_storage_preset_derives_the_functional_backend(env) -> None:
    """The UI picks a NAME; the two-value backend enum is derived server-side."""
    _activate(env)
    response = env.client.put(
        "/api/ultrawiki/settings", json={"storage_provider": "neon"}
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["changed"]) == {"storage_provider", "db_backend"}
    assert env.cfg.ultrawiki.db_backend == "postgres"
    assert env.cfg.ultrawiki.storage_provider == "neon"
    persisted = env.toml.read_text(encoding="utf-8")
    assert 'storage_provider = "neon"' in persisted
    assert 'db_backend = "postgres"' in persisted


def test_switching_back_to_sqlite_restores_the_local_backend(env) -> None:
    _activate(env)
    env.client.put("/api/ultrawiki/settings", json={"storage_provider": "neon"})
    response = env.client.put(
        "/api/ultrawiki/settings", json={"storage_provider": "sqlite"}
    )
    assert response.status_code == 200, response.text
    assert env.cfg.ultrawiki.db_backend == "sqlite"


def test_an_unknown_storage_preset_is_refused(env) -> None:
    response = env.client.put(
        "/api/ultrawiki/settings", json={"storage_provider": "dropbox"}
    )
    assert response.status_code == 400
    assert "dropbox" in response.json()["detail"]


def test_supabase_projects_need_a_token_first(env, monkeypatch) -> None:
    """Unlinked is a 409 with an instruction, never a 500 or an empty list.

    The empty keyring is stubbed rather than assumed: a developer machine that
    happens to hold a real Supabase token would otherwise turn this unit test
    into a live API call against that person's own account.
    """
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *_a, **_kw: None)
    response = env.client.get("/api/ultrawiki/storage/supabase/projects")
    assert response.status_code == 409
    assert "token" in response.json()["detail"].lower()


def _stub_supabase(monkeypatch, *, probe_ok: bool, probe_detail: str) -> dict[str, str]:
    """Wire an offline Supabase link: saved token, fixed endpoint, fixed probe.

    Returns the dict that captures every secret write, so a test can assert
    that a refused link wrote nothing at all.
    """
    from jarvis.ultrawiki import supabase_link

    monkeypatch.setattr(
        "jarvis.core.config.get_secret",
        lambda key, **_kw: "sbp_token" if key == "supabase_access_token" else None,
    )

    async def fake_resolve(token, ref, *, mode="transaction", transport=None):
        return (
            supabase_link.PoolerEndpoint(
                host="aws-1-eu-central-2.pooler.supabase.com",
                port=6543,
                user=f"postgres.{ref}",
                database="postgres",
                mode="transaction",
            ),
            "Using the Supabase transaction pooler.",
        )

    async def fake_connect_test(conn_str):
        return probe_ok, probe_detail

    monkeypatch.setattr(supabase_link, "resolve_endpoint", fake_resolve)
    monkeypatch.setattr(
        "jarvis.ultrawiki.store.PostgresStore.connect_test",
        staticmethod(fake_connect_test),
    )
    written: dict[str, str] = {}

    def fake_set_secret(key, value):
        written[key] = value
        return True

    monkeypatch.setattr("jarvis.core.config.set_secret", fake_set_secret)
    return written


def test_supabase_link_saves_nothing_when_the_connection_fails(env, monkeypatch) -> None:
    """A string that cannot connect must not become the configured store.

    Saving it anyway would flip db_backend to postgres and then degrade back to
    SQLite on every boot — a silent downgrade the user never asked for and
    cannot see.
    """
    written = _stub_supabase(
        monkeypatch, probe_ok=False, probe_detail="Connection failed: timeout"
    )
    response = env.client.post(
        "/api/ultrawiki/storage/supabase/link",
        json={"project_ref": "abcdefghijklmnopqrst", "db_password": "hunter2"},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["can_save_anyway"] is True
    assert "timeout" in detail["probe_detail"]
    assert written == {}
    assert env.cfg.ultrawiki.db_backend == "sqlite"


def test_supabase_link_can_be_forced_past_an_unreachable_probe(env, monkeypatch) -> None:
    """A database only reachable over the user's VPN must still be linkable."""
    written = _stub_supabase(
        monkeypatch, probe_ok=False, probe_detail="Connection failed: timeout"
    )
    response = env.client.post(
        "/api/ultrawiki/storage/supabase/link",
        json={
            "project_ref": "abcdefghijklmnopqrst",
            "db_password": "hunter2",
            "save_anyway": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["probe_ok"] is False
    assert "ultrawiki_db_url" in written
    assert env.cfg.ultrawiki.db_backend == "postgres"


def test_supabase_link_stores_the_uri_and_flips_the_slot(env, monkeypatch) -> None:
    written = _stub_supabase(
        monkeypatch,
        probe_ok=True,
        probe_detail="Connected: PostgreSQL 16; pgvector is available",
    )
    response = env.client.post(
        "/api/ultrawiki/storage/supabase/link",
        json={"project_ref": "abcdefghijklmnopqrst", "db_password": "p@ss/word"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["probe_ok"] is True
    assert body["endpoint"]["host"] == "aws-1-eu-central-2.pooler.supabase.com"
    # The credential is the connection string, stored under the store's own
    # secret slot — never in the TOML (AP-12) — with the password encoded.
    assert "ultrawiki_db_url" in written
    assert written["ultrawiki_db_url"].startswith("postgresql://")
    assert "p%40ss%2Fword" in written["ultrawiki_db_url"]
    assert "p@ss/word" not in env.toml.read_text(encoding="utf-8")
    assert env.cfg.ultrawiki.db_backend == "postgres"
    assert env.cfg.ultrawiki.storage_provider == "supabase"


def test_supabase_link_is_flagged_dangerous(env) -> None:
    spec = env.server.app.openapi()
    operation = spec["paths"]["/api/ultrawiki/storage/supabase/link"]["post"]
    assert operation.get("x-jarvis-dangerous") is True


# ---------------------------------------------------------------------------
# Model lists per slot
# ---------------------------------------------------------------------------


def test_slot_models_answer_in_the_shape_the_model_picker_consumes(
    env, monkeypatch
) -> None:
    """The slots reuse the API-Keys model picker, so the payload must match it.

    A separate look-alike picker was the alternative, and it would have drifted
    from the original within a release. Same shape in, same component out.
    """
    from jarvis.ultrawiki import embedding_models

    async def fake_list(provider, cfg, *, transport=None):
        return embedding_models.EmbeddingModelList(
            models=(embedding_models.EmbeddingModel(id="bge-m3", label="bge-m3"),),
            source="live",
        )

    monkeypatch.setattr(embedding_models, "list_embedding_models", fake_list)
    _activate(env)
    response = env.client.get("/api/ultrawiki/models/embedding")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "provider",
        "current_model",
        "models",
        "source",
        "fetched_at",
        "selects",
    }
    assert body["provider"] == "gemini"
    assert body["current_model"] == "fake-embed"
    assert body["models"] == [{"id": "bge-m3", "label": "bge-m3"}]
    assert body["selects"] == "model"


def test_slot_models_are_empty_but_honest_before_a_provider_is_picked(env) -> None:
    response = env.client.get("/api/ultrawiki/models/embedding")
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == []
    assert "no provider" in body["reason"]


def test_a_vendor_reranker_offers_no_model_choice(env) -> None:
    """Voyage and Cohere pin their own cross-encoder; an empty picker is right."""
    response = env.client.get("/api/ultrawiki/models/rerank?provider=cohere")
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == []
    assert "fixed model" in body["reason"]


def test_an_unknown_slot_is_a_404(env) -> None:
    assert env.client.get("/api/ultrawiki/models/storage").status_code == 404
    assert env.client.get("/api/ultrawiki/models/nonsense").status_code == 404


def test_a_dead_model_catalog_degrades_instead_of_500ing(env, monkeypatch) -> None:
    """A settings screen must render even when a provider catalog is down."""
    import jarvis.ui.web.provider_routes as provider_routes

    def boom(_request):
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(provider_routes, "_get_model_catalog", boom)
    response = env.client.get("/api/ultrawiki/models/distill?provider=gemini")
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == []
    assert "RuntimeError" in body["reason"]
