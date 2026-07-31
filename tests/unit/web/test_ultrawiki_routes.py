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
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer
from jarvis.ui.web.ultrawiki_routes import ULTRAWIKI_ANSWER_STATUSES
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
    # It writes the uploaded bytes to this machine's disk.
    ("/api/ultrawiki/export/upload", "post"),
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
    """Register + approve a local-folder source; returns (source_id, job_id).

    Approving IS the import: the approve answer carries the job id of the full
    sync it started, so no second call is needed to get data flowing.
    """
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
    body = approved.json()
    assert body["source"]["consent"] == "approved"
    assert body["job_id"], body
    return source_id, body["job_id"]


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


def test_folder_source_settings_can_be_repaired_without_recreating_it(env) -> None:
    _activate(env)
    docs = env.tmp / "editable-docs"
    docs.mkdir()
    created = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Editable docs",
            "config": {"root": str(docs), "exclude": ["archive"]},
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    updated = env.client.patch(
        f"/api/ultrawiki/sources/{source_id}",
        json={"config": {"root": str(docs), "exclude": ["archive", "dist"]}},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["consent"] == "pending"
    assert updated.json()["config"]["exclude"] == ["archive", "dist"]
    listed = env.client.get("/api/ultrawiki/sources").json()["sources"]
    row = next(source for source in listed if source["id"] == source_id)
    assert row["config"] == {"root": str(docs), "exclude": ["archive", "dist"]}


def test_folder_source_update_rejects_a_missing_path(env) -> None:
    _activate(env)
    docs = env.tmp / "existing-docs"
    docs.mkdir()
    created = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Docs",
            "config": {"root": str(docs)},
        },
    )
    source_id = created.json()["id"]

    response = env.client.patch(
        f"/api/ultrawiki/sources/{source_id}",
        json={"config": {"root": str(env.tmp / "missing")}},
    )

    assert response.status_code == 400
    assert "There is no folder" in response.json()["detail"]


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


def test_ask_returns_a_grounded_answer_and_numbered_evidence(
    env, monkeypatch
) -> None:
    from jarvis.ultrawiki.answer import SynthesisResult

    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    async def fake_answer(_cfg, _question, _hits):
        return SynthesisResult(
            answer="The ledger reconciliation is in the Alpha note [1].",
            provider="fake",
            citations=(1,),
        )

    monkeypatch.setattr(
        "jarvis.ultrawiki.answer.answer_question", fake_answer
    )
    response = env.client.post(
        "/api/ultrawiki/ask", json={"question": "Where is the ledger?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_status"] == "answered"
    assert body["citations"] == [1]
    assert body["provider"] == "fake"
    assert body["results"][0]["permalink"]


def test_ask_reports_insufficient_evidence_without_false_citations(
    env, monkeypatch
) -> None:
    from jarvis.ultrawiki.answer import SynthesisResult

    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    async def fake_answer(_cfg, _question, _hits):
        return SynthesisResult(
            answer="The retrieved notes do not answer that question.",
            provider="fake",
            citations=(),
            status="insufficient_evidence",
        )

    monkeypatch.setattr("jarvis.ultrawiki.answer.answer_question", fake_answer)
    response = env.client.post(
        "/api/ultrawiki/ask", json={"question": "When does the ferry leave?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_status"] == "insufficient_evidence"
    assert body["answer"] == "The retrieved notes do not answer that question."
    assert body["citations"] == []


def test_ask_answer_statuses_match_the_frontend_contract() -> None:
    api_source = (
        Path("jarvis/ui/web/frontend/src/lib/ultrawikiApi.ts")
        .read_text(encoding="utf-8")
        .split("export const ULTRAWIKI_ANSWER_STATUSES = [", 1)[1]
        .split("] as const", 1)[0]
    )
    frontend_statuses = tuple(re.findall(r'"([a-z_]+)"', api_source))

    assert frontend_statuses == ULTRAWIKI_ANSWER_STATUSES


def test_ask_keeps_evidence_when_no_chat_provider_can_synthesize(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    response = env.client.post(
        "/api/ultrawiki/ask", json={"question": "Where is the ledger?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_status"] == "answer_unavailable"
    assert body["answer"] == ""
    assert body["results"]
    assert "provider" in body["synthesis_error"]


def test_ask_disabled_is_409(env) -> None:
    response = env.client.post(
        "/api/ultrawiki/ask", json={"question": "Where is the ledger?"}
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Settings guard (D-3: embedding change re-embeds the corpus)
# ---------------------------------------------------------------------------


def test_embedding_change_without_confirm_is_409(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    response = env.client.put(
        "/api/ultrawiki/settings", json={"embedding_model": "fake-embed-2"}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["vector_items"] == 2
    assert "confirm_reembed" in detail["message"]

    confirmed = env.client.put(
        "/api/ultrawiki/settings",
        json={"embedding_model": "fake-embed-2", "confirm_reembed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["changed"] == ["embedding_model"]
    assert body["reembed_started"] is True
    assert env.cfg.ultrawiki.embedding_model == "fake-embed-2"
    assert 'embedding_model = "fake-embed-2"' in env.toml.read_text(encoding="utf-8")

    # The items go through the pipeline again, but their CURRENT vectors were
    # NOT dropped: semantic search keeps answering while the new space is
    # built in the background, and the status says so.
    status = env.client.get("/api/ultrawiki/status").json()
    assert status["counts"]["keyword_indexed"] == 2
    assert status["slots"]["storage"]["vector"]["ready"] is True
    reembed = status["reembed"]
    assert reembed.get("model") == "fake-embed-2"
    assert reembed.get("total", 0) > 0

    hits = env.client.get("/api/ultrawiki/search", params={"q": "ledger"}).json()
    assert hits["total"] >= 1


def test_provider_change_keeping_the_model_needs_no_reembed(env) -> None:
    """The vector space is the MODEL's, not the host's.

    Re-embedding a corpus because the same model is now billed through another
    provider is pure waste — the geometry is identical, so the existing vectors
    stay valid and no confirmation is owed to the user either.
    """
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    response = env.client.put(
        "/api/ultrawiki/settings", json={"embedding_provider": "openai"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["reembed_started"] is False

    counts = env.client.get("/api/ultrawiki/status").json()["counts"]
    assert counts["keyword_indexed"] == 0
    assert counts["embedded"] + counts["distilled"] == 2


def test_update_settings_without_changes_is_noop(env) -> None:
    _activate(env)
    response = env.client.put("/api/ultrawiki/settings", json={})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "changed": [],
        "persisted": True,
        "reembed_started": False,
        "embedding_space_rebuild": "active",
    }


def test_picking_the_already_configured_model_still_repairs_the_store(env) -> None:
    """The trap that locked a maintainer out of his own repair path.

    "Nothing to WRITE" was treated as "nothing to DO". Once the config named
    one embedding model while the store was still pinned to another — however
    that divergence arose — the store rejected every vector the provider
    produced and the embed lane failed 100 % of its work. Re-picking the model
    in the settings screen was the obvious fix, and it hit this branch: the
    values matched the config, `changed` came back empty, the screen reported
    success, and the store was never told. The one screen that exists to
    resolve the divergence was the one screen that could not, and clicking
    again only made it more certain (forensic 2026-07-28).
    """
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    # The divergence: the config names a model the store has never seen. This
    # is reachable through every path that writes config without registering
    # the switch — the activation route, a voice config change, a hand-edited
    # jarvis.toml, a config carried over from another machine.
    env.cfg.ultrawiki.embedding_model = "fake-embed-2"

    response = env.client.put(
        "/api/ultrawiki/settings", json={"embedding_model": "fake-embed-2"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] == []  # still nothing to write...
    assert body["embedding_space_rebuild"] == "started"  # ...but plenty to do
    assert body["reembed_started"] is True

    # The store now builds the space the config actually names, and semantic
    # search keeps answering from the live vectors until it is complete.
    status = env.client.get("/api/ultrawiki/status").json()
    assert status["reembed"].get("model") == "fake-embed-2"
    assert status["slots"]["storage"]["vector"]["ready"] is True


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
        # The stored CHOICES are still known (they live in the config, not in
        # the service), and nothing claims to be ready.
        assert body["slots"]["embedding"]["ready"] is False
        assert body["slots"]["storage"]["ready"] is False
        assert body["sources"] == []
        assert body["pipeline"]["running"] is False
        assert body["pipeline"]["state"] == "paused"
        assert body["pipeline"]["reason"]
        assert any("not wired" in line for line in body["degradations"])
        assert "search_legs" in body
    finally:
        env.server.app.state.ultrawiki = env.service


def test_status_reports_the_stored_choices_while_the_service_is_unwired(
    env,
) -> None:
    """A restart must not look like a fresh install.

    The window comes back before the backend finishes starting, so the very
    first `/status` of a session is answered with `app.state.ultrawiki` still
    `None`. When that answer carried no slots, the Normal/Ultra switch read
    "never configured" and reopened the ONE-TIME activation wizard on an
    install that had been running Ultra for weeks — offering to re-pick the
    embedding model, which re-embeds the whole corpus.
    """
    _activate(env)
    env.server.app.state.ultrawiki = None
    try:
        body = env.client.get("/api/ultrawiki/status").json()
        # The mode itself survives: it is read from the config, not the service.
        assert body["enabled"] is True
        # And so does the answer the wizard gate depends on.
        assert body["configured"] is True
        assert body["slots"]["embedding"]["provider"] == "gemini"
        assert body["slots"]["embedding"]["model"] == "fake-embed"
        assert body["slots"]["storage"]["configured"] == "sqlite"
    finally:
        env.server.app.state.ultrawiki = env.service


def test_status_reports_not_configured_before_the_first_activation(env) -> None:
    """The flag is a real answer in both directions — a fresh install still
    gets the wizard."""
    body = env.client.get("/api/ultrawiki/status").json()
    assert body["configured"] is False
    assert body["enabled"] is False


def test_status_keeps_reporting_configured_after_switching_back_to_normal(
    env,
) -> None:
    """Deactivating is not un-configuring (D-9).

    Switching to Normal and back must re-activate with the stored choices,
    never walk the user through the one-time wizard again.
    """
    _activate(env)
    assert env.client.post("/api/ultrawiki/deactivate").status_code == 200

    body = env.client.get("/api/ultrawiki/status").json()
    assert body["enabled"] is False
    assert body["configured"] is True
    assert body["slots"]["embedding"]["provider"] == "gemini"


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
    # Approving starts the full import itself — that job is the one to cancel.
    job_id = env.client.post(
        f"/api/ultrawiki/sources/{source_id}/approve"
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


def test_catalog_surfaces_subscription_logins_as_distillation_choices(
    env, monkeypatch
) -> None:
    from jarvis.memory.wiki import provider_chain

    monkeypatch.setattr(
        provider_chain,
        "subscription_login_ready",
        lambda provider, *, registry=None: provider in {"codex", "claude-cli"},
    )

    rows = {
        row["id"]: row
        for row in env.client.get("/api/ultrawiki/catalog").json()["slots"]["distill"]
    }
    assert {"codex", "antigravity", "claude-cli"} <= set(rows)
    assert rows["codex"]["auth_mode"] == "codex"
    assert rows["codex"]["ready"] is True
    assert rows["antigravity"]["ready"] is False
    assert "subscription login" in rows["antigravity"]["reason"]


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


def test_one_item_can_be_read_in_full_including_its_stored_text(env) -> None:
    """The inventory says WHICH items exist; this says what is actually in one.

    A stage badge reading "distilled" is a claim about a row the user cannot
    see. Opening the record has to show the captured text itself — otherwise
    "is the real content in there?" stays unanswerable from the app.
    """
    _activate(env)
    _, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    listed = env.client.get("/api/ultrawiki/items").json()
    assert listed["total"] >= 1

    bodies = []
    for row in listed["items"]:
        response = env.client.get(f"/api/ultrawiki/items/{row['id']}")
        assert response.status_code == 200, response.text
        bodies.append(response.json())

    for body in bodies:
        # The text EXACTLY as captured — the whole point of the view.
        assert body["body"].strip()
        assert body["permalink"]
        assert body["content_hash"]
        assert isinstance(body["documents"], list)

    # At least one item must carry what was DERIVED from it, so "embedded"
    # stops being a badge with nothing behind it. Not EVERY item: the staged
    # pipeline advances items independently, and asserting on all of them
    # would pin scheduling order rather than the contract.
    derived = [doc for body in bodies for doc in body["documents"]]
    assert derived, "a driven pipeline must leave at least one derived document"
    assert all("has_vector" in doc for doc in derived)
    assert any(doc["text"].strip() for doc in derived)


def test_an_unknown_item_is_a_404_not_an_empty_record(env) -> None:
    _activate(env)
    assert env.client.get("/api/ultrawiki/items/999999").status_code == 404


def test_reconcile_confirms_the_import_landed(env) -> None:
    _activate(env)
    _, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    body = env.client.get("/api/ultrawiki/reconcile").json()
    folder = next(r for r in body["sources"] if r["source_id"].startswith("local-folder"))
    assert folder["verdict"] == "complete"
    assert folder["read"] == folder["stored"] == 2
    # The default sources were registered but never synced by this test, so the
    # summary must NOT claim everything landed.
    assert body["all_complete"] is False
    assert any(r["verdict"] == "never_imported" for r in body["sources"])


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


# ---------------------------------------------------------------------------
# Approve = import everything (the core visibility fix)
# ---------------------------------------------------------------------------


def test_approve_answers_with_the_import_it_started(env) -> None:
    """The approve click used to only flip a flag and leave "Never synced"."""
    _activate(env)
    docs = env.tmp / "auto"
    docs.mkdir(exist_ok=True)
    (docs / "one.md").write_text("# One\n\nA single note.", encoding="utf-8")
    source_id = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Auto",
            "config": {"root": str(docs)},
        },
    ).json()["id"]

    response = env.client.post(f"/api/ultrawiki/sources/{source_id}/approve")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["consent"] == "approved"
    assert body["auto_sync"] is True
    assert body["job_id"]
    assert body["detail"]
    snapshot = _wait_for_job(env, body["job_id"])
    assert (snapshot["status"], snapshot["new"]) == ("done", 1)


def test_approve_with_auto_sync_false_pulls_nothing(env) -> None:
    _activate(env)
    response = env.client.post(
        "/api/ultrawiki/sources/normal-wiki/approve", params={"auto_sync": "false"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["consent"] == "approved"
    assert body["job_id"] is None
    assert body["auto_sync"] is False
    assert env.client.get("/api/ultrawiki/jobs").json()["total"] == 0


def test_status_carries_the_per_source_outcome_after_the_auto_import(env) -> None:
    _activate(env)
    source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    row = next(
        source
        for source in env.client.get("/api/ultrawiki/status").json()["sources"]
        if source["id"] == source_id
    )

    assert row["active_job"] is None
    assert row["last_outcome"]["status"] == "done"
    assert row["last_outcome"]["new"] == 2
    assert row["last_outcome"]["finished_at"]
    assert row["last_notice"] is None


# ---------------------------------------------------------------------------
# The curated connector roster — what the add-source picker may render
# ---------------------------------------------------------------------------


def test_connectors_answer_with_the_whole_curated_roster(env) -> None:
    """Product data: identical on every install, no credential probe involved."""
    response = env.client.get("/api/ultrawiki/connectors")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == body["builtin"] + body["bridge"]
    assert body["builtin"] >= 4 and body["bridge"] >= 1
    for row in body["connectors"]:
        assert row["kind"] in ("builtin", "bridge")
        assert row["label"].strip()
        assert row["brand"].strip()
        assert row["status"] in ("available", "adapter_pending")
        assert row["description_key"].startswith("ultrawiki.connectors.")


def test_connectors_never_offer_the_bridge_itself_as_a_card(env) -> None:
    """`plugin-bridge` is plumbing; its generic name is what the roster ends."""
    rows = env.client.get("/api/ultrawiki/connectors").json()["connectors"]
    assert all(row["id"] != "plugin-bridge" for row in rows)
    # It is still the connector every bridge entry registers under.
    bridges = [row for row in rows if row["kind"] == "bridge"]
    assert all(row["connector"] == "plugin-bridge" for row in bridges)


def test_bridge_candidates_show_curated_entries_that_are_not_connected(
    env, monkeypatch
) -> None:
    """Seeing what is POSSIBLE is the point — flagged, so nobody adds a dead
    source by mistake."""
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: SimpleNamespace(plugins=[]),
    )
    monkeypatch.setattr("jarvis.mcp.state.load_config", lambda: {"mcpServers": {}})

    body = env.client.get("/api/ultrawiki/bridge/candidates").json()

    assert body["connected"] == 0
    assert body["total"] > 0
    for row in body["candidates"]:
        assert row["connected"] is False
        assert row["connector_kind"] == "bridge"
        assert row["brand"].strip()
        assert row["label"].strip()


def test_bridge_candidates_exclude_an_uncurated_connected_tool(
    env, monkeypatch
) -> None:
    """A raw registry dump is exactly what the picker must never render."""
    monkeypatch.setattr(
        "jarvis.marketplace.catalog_data.load_catalog",
        lambda: SimpleNamespace(plugins=[]),
    )
    monkeypatch.setattr(
        "jarvis.mcp.state.load_config",
        lambda: {
            "mcpServers": {
                "notion": {"enabled": True},
                "some-private-server": {"enabled": True, "display": "grace plug in"},
            }
        },
    )

    body = env.client.get("/api/ultrawiki/bridge/candidates").json()

    connected = [row for row in body["candidates"] if row["connected"]]
    assert [row["catalog_id"] for row in connected] == ["notion"]
    assert connected[0]["label"] == "Notion"
    assert all("grace plug in" not in row["label"] for row in body["candidates"])


def test_status_sources_carry_the_brand_a_card_renders(env) -> None:
    _activate(env)
    rows = env.client.get("/api/ultrawiki/status").json()["sources"]

    assert rows, "activation registers the default local sources"
    for row in rows:
        assert row["connector_kind"] in ("builtin", "bridge", "")
        assert "brand" in row
    wiki = next(row for row in rows if row["connector"] == "normal-wiki")
    assert (wiki["brand"], wiki["connector_kind"]) == ("wiki", "builtin")


# ---------------------------------------------------------------------------
# The contents view — WHICH items are in the database, not just how many
# ---------------------------------------------------------------------------


def test_items_lists_the_stored_inventory(env) -> None:
    _activate(env)
    source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    response = env.client.get("/api/ultrawiki/items")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert (body["limit"], body["offset"]) == (50, 0)
    row = body["items"][0]
    assert row["source_id"] == source_id
    assert row["state"] == "captured"
    assert row["permalink"]
    assert row["ingested_at"] and row["updated_at"]
    assert {item["title"] for item in body["items"]} == {"alpha", "beta"}


def test_items_filter_by_source_and_state(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)
    _drive_pipeline(env)

    distilled = env.client.get(
        "/api/ultrawiki/items", params={"state": "distilled"}
    ).json()
    captured = env.client.get(
        "/api/ultrawiki/items", params={"state": "captured"}
    ).json()
    elsewhere = env.client.get(
        "/api/ultrawiki/items", params={"source_id": "normal-wiki"}
    ).json()

    assert distilled["total"] == 2
    assert captured["total"] == 0
    assert elsewhere["total"] == 0


def test_items_paginate_with_an_honest_total(env) -> None:
    _activate(env)
    _source_id, job_id = _approve_and_sync_folder(env)
    _wait_for_job(env, job_id)

    first = env.client.get("/api/ultrawiki/items", params={"limit": 1}).json()
    second = env.client.get(
        "/api/ultrawiki/items", params={"limit": 1, "offset": 1}
    ).json()

    assert first["total"] == second["total"] == 2  # the total is UNPAGED
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_items_reject_an_unknown_state(env) -> None:
    _activate(env)
    response = env.client.get(
        "/api/ultrawiki/items", params={"state": "half-embedded"}
    )
    assert response.status_code == 400
    assert "half-embedded" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Export files — preview before importing, and get the file here first
# ---------------------------------------------------------------------------


def test_export_preview_reports_the_formats_it_found(env) -> None:
    """The answer to "what does 'import everything' actually mean?"."""
    drop = env.tmp / "drop"
    drop.mkdir()
    (drop / "notes.md").write_text("# Note\n\ncontent", encoding="utf-8")
    (drop / "WhatsApp Chat with Ada.txt").write_text(
        "[01.02.24, 09:00:00] Ada: hello\n[02.02.24, 09:00:00] Ada: again\n",
        encoding="utf-8",
    )
    (drop / "blob.bin").write_bytes(bytes([0, 1, 2, 3] * 32))

    response = env.client.post(
        "/api/ultrawiki/export/preview", json={"path": str(drop)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert body["is_dir"] is True
    assert body["formats"]["markdown"]["items_estimate"] == 1
    assert body["formats"]["whatsapp"]["items_estimate"] == 2
    assert body["unknown"] == [{"extension": ".bin", "files": 1}]
    assert body["truncated"] is False


def test_export_preview_404s_on_a_path_that_is_not_there(env) -> None:
    response = env.client.post(
        "/api/ultrawiki/export/preview",
        json={"path": str(env.tmp / "no-such-export")},
    )
    assert response.status_code == 404
    assert "nothing exists" in response.json()["detail"]


def test_export_preview_changes_nothing(env) -> None:
    """A preview is read-only: no source appears, nothing is imported."""
    _activate(env)
    drop = env.tmp / "peek"
    drop.mkdir()
    (drop / "notes.md").write_text("# Note", encoding="utf-8")
    before = env.client.get("/api/ultrawiki/sources").json()["total"]

    env.client.post("/api/ultrawiki/export/preview", json={"path": str(drop)})

    assert env.client.get("/api/ultrawiki/sources").json()["total"] == before


def test_export_upload_streams_the_file_and_returns_its_path(env) -> None:
    payload = b"# Uploaded note\n\nwith a body\n"
    response = env.client.post(
        "/api/ultrawiki/export/upload",
        files={"file": ("takeout.md", payload, "text/markdown")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "takeout.md"
    assert body["size"] == len(payload)
    stored = Path(body["path"])
    assert stored.read_bytes() == payload
    # Under the configured data directory, never next to the code.
    assert "uploads" in stored.parts and str(env.tmp) in str(stored)


def test_an_uploaded_file_can_be_previewed_and_imported(env) -> None:
    """The whole point of the upload: it lands where the connector reads."""
    _activate(env)
    uploaded = env.client.post(
        "/api/ultrawiki/export/upload",
        files={
            "file": (
                "chat.txt",
                b"[01.02.24, 09:00:00] Ada: hello from the upload\n"
                b"[01.02.24, 09:01:00] Bruno: and a reply\n",
                "text/plain",
            )
        },
    ).json()

    preview = env.client.post(
        "/api/ultrawiki/export/preview", json={"path": uploaded["path"]}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["formats"]["whatsapp"]["items_estimate"] == 1

    created = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "export-import",
            "label": "Dropped chat",
            "config": {"path": uploaded["path"]},
        },
    )
    assert created.status_code == 201, created.text
    # Consent semantics are untouched by the upload/preview detour.
    assert created.json()["consent"] == "pending"

    approved = env.client.post(
        f"/api/ultrawiki/sources/{created.json()['id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    snapshot = _wait_for_job(env, approved.json()["job_id"])
    assert snapshot["status"] == "done", snapshot
    assert snapshot["new"] == 1


def test_export_upload_refuses_a_path_traversal_filename(env) -> None:
    """Refused, not silently renamed: a rewrite would report success for a
    file stored somewhere the caller never asked for."""
    for name in ("../escape.zip", "sub/dir.zip", "C:evil.zip"):
        response = env.client.post(
            "/api/ultrawiki/export/upload",
            files={"file": (name, b"data", "application/zip")},
        )
        assert response.status_code == 400, (name, response.text)
        assert "refusing the filename" in response.json()["detail"]


def test_export_upload_refuses_an_empty_file(env) -> None:
    response = env.client.post(
        "/api/ultrawiki/export/upload",
        files={"file": ("empty.zip", b"", "application/zip")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_export_upload_enforces_its_size_cap_and_keeps_nothing(
    env, monkeypatch
) -> None:
    from jarvis.ui.web import ultrawiki_routes

    monkeypatch.setattr(ultrawiki_routes, "_MAX_EXPORT_UPLOAD_BYTES", 16)
    monkeypatch.setattr(ultrawiki_routes, "_UPLOAD_CHUNK_BYTES", 8)

    response = env.client.post(
        "/api/ultrawiki/export/upload",
        files={"file": ("big.zip", b"x" * 4096, "application/zip")},
    )

    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]
    # A refused transfer leaves no partial file behind.
    uploads = env.tmp / "data" / "ultrawiki" / "uploads"
    assert not uploads.exists() or not any(uploads.rglob("*.zip"))


def test_export_preview_is_not_flagged_dangerous(env) -> None:
    """Reading a path changes nothing, so it must not demand a confirmation."""
    schema = env.server.app.openapi()
    operation = schema["paths"]["/api/ultrawiki/export/preview"]["post"]
    assert "x-jarvis-dangerous" not in operation
    upload = schema["paths"]["/api/ultrawiki/export/upload"]["post"]
    assert upload["x-jarvis-dangerous"] is True
    # A multi-gigabyte transfer must outlive the CLI's default client timeout.
    assert upload["x-jarvis-timeout-seconds"] == 600


# ---------------------------------------------------------------------------
# Folder sources: a path a human typed must be checked while they are looking
# ---------------------------------------------------------------------------


def test_a_folder_source_with_a_missing_path_is_refused_at_creation(env) -> None:
    """The whole point: fail while the user is still in the dialog.

    A path pasted with the shell prompt still attached (``C:/Users/Someone>``)
    registered, imported "successfully", and showed zero items with the reason
    nowhere on screen.
    """
    _activate(env)
    missing = env.tmp / "not-a-real-folder"

    response = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Desktop",
            "config": {"root": str(missing)},
        },
    )

    assert response.status_code == 400, response.text
    assert str(missing) in response.json()["detail"]


def test_a_path_pasted_with_a_shell_prompt_is_cleaned_and_accepted(env) -> None:
    _activate(env)
    docs = env.tmp / "desk"
    docs.mkdir(exist_ok=True)

    response = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Desktop",
            "config": {"root": f"{docs}>"},
        },
    )

    assert response.status_code == 201, response.text
    # Stored CLEANED, so every later run walks the real folder.
    assert response.json()["config"]["root"] == str(docs)


def test_a_file_offered_as_a_folder_source_is_refused(env) -> None:
    _activate(env)
    target = env.tmp / "notes.md"
    target.write_text("# Notes", encoding="utf-8")

    response = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Notes",
            "config": {"root": str(target)},
        },
    )

    assert response.status_code == 400, response.text
    assert "folder" in response.json()["detail"].lower()


def test_a_folder_source_that_imported_nothing_says_why(env) -> None:
    """Zero items with a green card and no explanation is the defect itself."""
    _activate(env)
    empty = env.tmp / "empty-folder"
    empty.mkdir(exist_ok=True)

    created = env.client.post(
        "/api/ultrawiki/sources",
        json={
            "connector": "local-folder",
            "label": "Empty",
            "config": {"root": str(empty)},
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    approved = env.client.post(f"/api/ultrawiki/sources/{source_id}/approve")
    assert approved.status_code == 200, approved.text
    _wait_for_job(env, approved.json()["job_id"])

    listing = env.client.get("/api/ultrawiki/sources")
    assert listing.status_code == 200, listing.text
    row = next(r for r in listing.json()["sources"] if r["id"] == source_id)
    assert row["last_notice"], "an empty import must explain itself"
    assert "folder" in row["last_notice"].lower()
