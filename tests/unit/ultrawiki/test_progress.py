"""The cumulative progress model, and the contradiction it exists to prevent.

The anchor case throughout this file is the 2026-07-26 screenshot: a live
corpus of 4 712 items where the import strip said "Processing (3237 pending)"
while the checklist directly below said "Everything is processed. No backlog."
"""

from __future__ import annotations

import pytest

from jarvis.ultrawiki import health as health_mod
from jarvis.ultrawiki.progress import build_progress

#: The exact count vector from the screenshot that started this work.
SCREENSHOT_COUNTS = {
    "captured": 0,
    "keyword_indexed": 0,
    "embedded": 3237,
    "distilled": 1475,
    "failed": 0,
    "total": 4712,
}


def test_buckets_add_up_to_the_corpus():
    progress = build_progress(SCREENSHOT_COUNTS)
    assert progress["total"] == 4712


def test_searchable_is_cumulative_not_the_bucket():
    """The "Keyword-searchable 0" defect: 0 items sit in that bucket, and all
    4 712 are past it."""
    progress = build_progress(SCREENSHOT_COUNTS)
    assert progress["searchable"] == 4712
    assert progress["buckets"]["keyword_indexed"] == 0


def test_waiting_counts_every_unfinished_stage():
    progress = build_progress(SCREENSHOT_COUNTS)
    assert progress["waiting"] == 3237
    assert progress["state"] == "working"


def test_next_step_names_what_the_queue_waits_for_not_what_it_finished():
    """An ``embedded`` item is done embedding and queued for distillation."""
    progress = build_progress(SCREENSHOT_COUNTS)
    assert progress["next_step"] == "summarising"


def test_next_step_prefers_the_earliest_stage_on_a_tie():
    progress = build_progress(
        {"captured": 5, "keyword_indexed": 5, "embedded": 5, "distilled": 0}
    )
    assert progress["next_step"] == "indexing"


def test_milestones_are_nested_subsets():
    progress = build_progress(
        {"captured": 10, "keyword_indexed": 20, "embedded": 30, "distilled": 40}
    )
    reached = [m["reached"] for m in progress["milestones"]]
    assert reached == [100, 90, 40]
    assert reached == sorted(reached, reverse=True)


def test_failed_items_are_never_claimed_as_searchable():
    """An item can dead-letter at any stage, so the counts cannot prove it
    ever reached the index. Claiming otherwise would be a guess."""
    progress = build_progress({"captured": 0, "distilled": 10, "failed": 5})
    assert progress["total"] == 15
    assert progress["searchable"] == 10
    assert progress["waiting"] == 0


def test_empty_and_malformed_payloads_do_not_raise():
    for payload in (None, {}, {"captured": "nonsense"}, {"embedded": -4}):
        progress = build_progress(payload)
        assert progress["total"] >= 0
        assert progress["state"] in {"empty", "working", "done"}
    assert build_progress({})["state"] == "empty"


def test_a_fully_drained_store_is_done():
    progress = build_progress({"distilled": 12})
    assert progress["state"] == "done"
    assert progress["waiting"] == 0
    assert progress["next_step"] is None


# ---------------------------------------------------------------------------
# Cross-surface parity — the actual bug
# ---------------------------------------------------------------------------


def _status(counts: dict[str, int], pipeline_state: str = "processing") -> dict:
    progress = build_progress(counts)
    return {
        "enabled": True,
        "started": True,
        "counts": counts,
        "progress": progress,
        "pipeline": {"running": True, "state": pipeline_state, "reason": ""},
        "sources": [
            {
                "id": "s1",
                "label": "A source",
                "consent": "approved",
                "enabled": True,
                "last_sync_at": "2026-07-26T09:00:00Z",
            }
        ],
        "search_legs": {
            "keyword": {"available": True},
            "vector": {"available": True},
        },
        "slots": {},
        "degradations": [],
    }


def test_the_checklist_no_longer_claims_everything_is_processed():
    """The regression. Same payload, same second — the two lines agreed on
    "3237 pending" and "no backlog" before this test existed."""
    health = health_mod.build_health(_status(SCREENSHOT_COUNTS), [])
    row = next(c for c in health["checks"] if c["id"] == "processing")

    assert row["state"] == "working"
    assert "3 237" in row["title"]
    assert "summarised" in row["title"]
    assert "Everything is processed" not in row["title"]


def test_the_checklist_and_the_strip_report_the_same_backlog():
    status = _status(SCREENSHOT_COUNTS)
    health = health_mod.build_health(status, [])
    row = next(c for c in health["checks"] if c["id"] == "processing")
    assert row["facts"]["waiting"] == status["progress"]["waiting"]


@pytest.mark.parametrize(
    "counts",
    [
        {"captured": 100},
        {"keyword_indexed": 7},
        {"embedded": 3237, "distilled": 1475},
        {"captured": 1, "keyword_indexed": 1, "embedded": 1, "distilled": 1},
    ],
)
def test_processing_is_never_ok_while_anything_is_queued(counts):
    """The invariant that kills this bug class: a non-empty queue can never
    produce a green processing row, whatever the mix of stages."""
    health = health_mod.build_health(_status(counts), [])
    row = next(c for c in health["checks"] if c["id"] == "processing")
    assert row["state"] != "ok"


def test_processing_is_ok_only_when_the_queue_is_genuinely_empty():
    health = health_mod.build_health(_status({"distilled": 9}, "idle"), [])
    row = next(c for c in health["checks"] if c["id"] == "processing")
    assert row["state"] == "ok"


def test_a_paused_pipeline_with_a_backlog_asks_for_attention():
    health = health_mod.build_health(_status({"embedded": 40}, "paused"), [])
    row = next(c for c in health["checks"] if c["id"] == "processing")
    assert row["state"] == "attention"
    assert "40" in row["title"]


def test_the_content_row_reports_the_cumulative_searchable_count():
    health = health_mod.build_health(_status(SCREENSHOT_COUNTS), [])
    row = next(c for c in health["checks"] if c["id"] == "content")
    assert "4 712 item(s) stored" == row["title"]
    assert "4 712" in row["detail"]


def test_failed_items_are_named_in_the_content_row_instead_of_vanishing():
    health = health_mod.build_health(_status({"distilled": 10, "failed": 3}, "idle"), [])
    row = next(c for c in health["checks"] if c["id"] == "content")
    assert row["facts"]["searchable"] == 10
    assert "3" in row["detail"]


def test_health_ships_the_progress_block_for_the_overview():
    health = health_mod.build_health(_status(SCREENSHOT_COUNTS), [])
    assert health["progress"]["waiting"] == 3237
    assert health["progress"]["searchable"] == 4712
