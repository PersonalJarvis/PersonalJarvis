"""Full-app tests for the /api/ultrawiki/explore REST surface.

The Explore routes are what makes the store readable — entity pages, moment
pages, and the graph over them. They are pure reads over the projection, so
these tests seed the store directly (a documented service seam) instead of
driving the ingestion pipeline.

Emptiness is the interesting case here, not the happy path: a knowledge base
that looks empty has THREE different causes the user cannot tell apart, and
a previous forensic found one of them undiagnosed for days. Every one of them
gets its own asserted reason code.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer
from jarvis.ultrawiki import service as uw_service_mod
from jarvis.ultrawiki.service import UltraWikiService
from jarvis.ultrawiki.types import DocType, ExploreReason, ItemState, RawItem


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(toml_path))

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.memory.data_dir = str(tmp_path / "data")
    cfg.ultrawiki.enabled = True

    server = WebServer(cfg, bus=EventBus())
    service = UltraWikiService(cfg, embedding_backend_factory=lambda: None)
    server.app.state.ultrawiki = service
    uw_service_mod.clear_jobs()
    with TestClient(server.app) as client:
        yield SimpleNamespace(client=client, service=service, cfg=cfg, tmp=tmp_path)
        client.portal.call(service.shutdown)
    uw_service_mod.clear_jobs()


def seed(env, rows: list[dict], *, source_label: str = "Docs") -> None:
    """Put distilled documents straight into the store.

    Each row: external_id, question, entities, timestamp, optional summary.
    """

    async def _run() -> None:
        await env.service.ensure_started()
        store = env.service._store  # noqa: SLF001 — documented test seam
        assert store is not None
        await store.upsert_source("src1", connector="local-folder", label=source_label)
        for row in rows:
            await store.upsert_items(
                "src1",
                [
                    RawItem(
                        external_id=row["external_id"],
                        body=row.get("summary", "") or row["question"],
                        permalink=f"app://{row['external_id']}",
                        timestamp_utc=row.get("timestamp", "2026-03-01T10:00:00Z"),
                        title="Conversation on 2026-03-01",
                    )
                ],
            )
            item = await store.get_item_by_external_id("src1", row["external_id"])
            assert item is not None
            if row.get("distilled", True):
                await store.add_document(
                    item["id"],
                    DocType.SUMMARY,
                    row.get("summary", "") or row["question"],
                    distill_json=json.dumps(
                        {
                            "question": row["question"],
                            "summary": row.get("summary", ""),
                            "resolution": row.get("resolution", ""),
                            "entities": row.get("entities", []),
                            "refs": [],
                        }
                    ),
                    distill_version=1,
                )
                await store.mark_stage_done(item["id"], ItemState.DISTILLED)

    env.client.portal.call(_run)


def seed_source_only(env) -> None:
    async def _run() -> None:
        await env.service.ensure_started()
        store = env.service._store  # noqa: SLF001 — documented test seam
        await store.upsert_source("src1", connector="local-folder", label="Docs")

    env.client.portal.call(_run)


TRIP = [
    {
        "external_id": "a",
        "question": "How do I get to Bora Bora?",
        "summary": "Routes via Tahiti.",
        "entities": ["Bora Bora", "Tahiti"],
        "timestamp": "2026-03-01T10:00:00Z",
    },
    {
        "external_id": "b",
        "question": "Which airline flies to Tahiti?",
        "entities": ["Bora Bora", "Tahiti"],
        "timestamp": "2026-04-01T10:00:00Z",
    },
    {
        "external_id": "c",
        "question": "What is the weather in Berlin?",
        "entities": ["Berlin"],
        "timestamp": "2026-05-01T10:00:00Z",
    },
]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_entities_list_is_ranked_by_mentions(env):
    seed(env, TRIP)

    response = env.client.get("/api/ultrawiki/explore/entities")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert [e["label"] for e in body["entities"]][:2] == ["Bora Bora", "Tahiti"]
    assert body["total"] == 3
    assert body["reason"] == ExploreReason.OK


def test_entities_can_be_filtered_by_query(env):
    seed(env, TRIP)

    response = env.client.get("/api/ultrawiki/explore/entities", params={"q": "bor"})

    assert [e["label"] for e in response.json()["entities"]] == ["Bora Bora"]


def test_entity_detail_carries_its_moments(env):
    seed(env, TRIP)

    response = env.client.get("/api/ultrawiki/explore/entities/tahiti")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity"]["label"] == "Tahiti"
    assert body["entity"]["mentions"] == 2
    assert [m["title"] for m in body["moments"]] == [
        "Which airline flies to Tahiti?",
        "How do I get to Bora Bora?",
    ]
    assert [n["label"] for n in body["entity"]["neighbors"]] == ["Bora Bora"]
    assert body["entity"]["neighbor_total"] == 1


def test_unknown_entity_is_a_404_not_an_empty_page(env):
    seed(env, TRIP)

    response = env.client.get("/api/ultrawiki/explore/entities/atlantis")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------


def test_moments_are_newest_first_with_their_evidence_link(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/moments").json()

    assert [m["title"] for m in body["moments"]][0] == "What is the weather in Berlin?"
    assert body["moments"][0]["permalink"] == "app://c"
    assert body["total"] == 3


def test_moments_can_be_scoped_to_one_entity(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/moments", params={"entity": "berlin"}).json()

    assert [m["title"] for m in body["moments"]] == ["What is the weather in Berlin?"]


def test_moments_can_be_scoped_to_one_month(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/moments", params={"month": "2026-04"}).json()

    assert [m["title"] for m in body["moments"]] == ["Which airline flies to Tahiti?"]


def test_moments_paginate(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/moments", params={"limit": 1, "offset": 1}).json()

    assert [m["title"] for m in body["moments"]] == ["Which airline flies to Tahiti?"]
    assert body["total"] == 3


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def test_graph_defaults_to_the_two_mention_floor(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/graph").json()

    assert {n["key"] for n in body["nodes"]} == {"bora bora", "tahiti"}
    assert body["edges"] == [{"source": "bora bora", "target": "tahiti", "weight": 2}]
    assert body["min_mentions"] == 2


def test_graph_floor_of_one_shows_the_whole_corpus(env):
    seed(env, TRIP)

    body = env.client.get("/api/ultrawiki/explore/graph", params={"min_mentions": 1}).json()

    assert len(body["nodes"]) == 3


def test_graph_caps_payload_without_leaving_dangling_edges(env):
    seed(env, TRIP)

    body = env.client.get(
        "/api/ultrawiki/explore/graph",
        params={"min_mentions": 1, "max_nodes": 1, "max_edges": 1},
    ).json()

    assert len(body["nodes"]) == 1
    assert body["edges"] == []
    assert body["available_nodes"] == 3
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# The three honest empty states
# ---------------------------------------------------------------------------


def test_no_sources_says_so_rather_than_showing_an_empty_canvas(env):
    body = env.client.get("/api/ultrawiki/explore/entities").json()

    assert body["entities"] == []
    assert body["reason"] == ExploreReason.NO_SOURCES
    assert body["corpus"] == {"sources": 0, "items": 0, "distilled": 0}


def test_a_source_with_nothing_imported_is_its_own_reason(env):
    seed_source_only(env)

    body = env.client.get("/api/ultrawiki/explore/entities").json()

    assert body["reason"] == ExploreReason.NOTHING_IMPORTED
    assert body["corpus"]["sources"] == 1


def test_imported_but_undistilled_items_are_their_own_reason(env):
    seed(
        env,
        [{"external_id": "a", "question": "Q", "distilled": False}],
    )

    body = env.client.get("/api/ultrawiki/explore/entities").json()

    assert body["reason"] == ExploreReason.NOTHING_DISTILLED
    assert body["corpus"] == {"sources": 1, "items": 1, "distilled": 0}


def test_distilled_items_that_named_nobody_are_their_own_reason(env):
    seed(env, [{"external_id": "a", "question": "Q", "entities": []}])

    body = env.client.get("/api/ultrawiki/explore/entities").json()

    assert body["reason"] == ExploreReason.NO_ENTITIES
    assert body["corpus"]["distilled"] == 1
    # The moments still exist — only the entity layer is empty.
    assert env.client.get("/api/ultrawiki/explore/moments").json()["total"] == 1


# ---------------------------------------------------------------------------
# Mode discipline
# ---------------------------------------------------------------------------


def test_explore_answers_409_while_ultra_mode_is_off(env):
    env.cfg.ultrawiki.enabled = False

    for path in (
        "/api/ultrawiki/explore/entities",
        "/api/ultrawiki/explore/moments",
        "/api/ultrawiki/explore/graph",
    ):
        assert env.client.get(path).status_code == 409, path
