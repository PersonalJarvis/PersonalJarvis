"""The "is my knowledge base working?" checklist.

The anchor test is :func:`test_the_forensic_state_is_diagnosed_in_one_line` —
it replays the exact live state that left a user staring at an empty knowledge
base for days while every individual surface reported the truth. If that test
ever goes green for the wrong reason, the checklist has stopped earning its
place.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.ultrawiki.health import build_health


def _status(**overrides: Any) -> dict[str, Any]:
    """A healthy, fully-imported install; tests override one thing at a time.

    The counts are per-stage buckets an item sits in EXACTLY ONE of, so
    "fully processed" means every item has arrived in ``distilled``. This
    fixture used to spread them over three stages and still assert that every
    check was green — which is precisely the reading error that let the
    checklist call an 80-item backlog "Everything is processed".
    """
    base: dict[str, Any] = {
        "enabled": True,
        "started": True,
        "db_backend": "sqlite",
        "backend_in_use": "sqlite",
        "counts": {
            "captured": 0,
            "keyword_indexed": 0,
            "embedded": 0,
            "distilled": 100,
            "failed": 0,
            "total": 100,
        },
        "pipeline": {"running": True, "state": "idle", "reason": "idle"},
        "sources": [
            {
                "id": "normal-wiki",
                "label": "Built-in Wiki",
                "consent": "approved",
                "enabled": True,
                "last_sync_at": "2026-07-25T10:00:00Z",
            }
        ],
        "search_legs": {
            "keyword": {"available": True},
            "vector": {"available": True},
        },
    }
    base.update(overrides)
    return base


def _check(health: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(c for c in health["checks"] if c["id"] == check_id)


def test_a_fully_imported_install_reports_ok_and_usable():
    health = build_health(_status(), [])
    assert health["overall"] == "ok"
    assert health["usable"] is True
    assert {c["state"] for c in health["checks"]} == {"ok"}


def test_the_forensic_state_is_diagnosed_in_one_line():
    """The 2026-07-25 case: approved everywhere, imported nowhere.

    Three sources approved, none ever synced, zero items, and a pipeline
    truthfully reporting "everything is processed" — of nothing. Every surface
    was right and the user still could not tell what was wrong. The checklist
    has to name the ONE missing step and offer the button that does it.
    """
    sources = [
        {
            "id": sid,
            "label": label,
            "consent": "approved",
            "enabled": True,
            "last_sync_at": None,
        }
        for sid, label in (
            ("normal-wiki", "Built-in Wiki"),
            ("jarvis-conversations", "Jarvis Conversations"),
        )
    ]
    health = build_health(
        _status(
            sources=sources,
            counts={
                "captured": 0,
                "keyword_indexed": 0,
                "embedded": 0,
                "distilled": 0,
                "failed": 0,
                "total": 0,
            },
            search_legs={
                "keyword": {"available": True},
                "vector": {"available": False, "reason": "No vectors yet."},
            },
        ),
        [],
    )
    sources_check = _check(health, "sources")
    assert sources_check["state"] == "attention"
    assert "never" in sources_check["detail"].lower()
    # The fix must be one click away, not a hunt through four tabs.
    assert sources_check["action"] == {"kind": "sync_all"}
    assert health["usable"] is False
    # And the pipeline's "everything is processed" must NOT read as success
    # while the store is empty — the content check is what says otherwise.
    assert _check(health, "content")["state"] == "attention"


def test_a_running_first_import_reads_as_progress_not_as_a_fault():
    """`working` exists so a busy system never looks broken."""
    health = build_health(
        _status(
            sources=[
                {
                    "id": "normal-wiki",
                    "label": "Built-in Wiki",
                    "consent": "approved",
                    "enabled": True,
                    "last_sync_at": None,
                    "active_job": {"job_id": "abc", "status": "running"},
                }
            ]
        ),
        [],
    )
    check = _check(health, "sources")
    assert check["state"] == "working"
    assert check["action"] is None  # nothing to click — it is already happening


def test_a_draining_backlog_still_counts_as_usable():
    """Half-processed is a working knowledge base, not a broken one.

    4 590 items take hours to distil. Reporting "not ready" for all of it would
    be false: everything already processed answers questions right now.
    """
    health = build_health(
        _status(
            counts={
                "captured": 4000,
                "keyword_indexed": 500,
                "embedded": 80,
                "distilled": 10,
                "failed": 0,
                "total": 4590,
            }
        ),
        [],
    )
    assert health["usable"] is True
    assert _check(health, "processing")["state"] == "working"
    assert _check(health, "content")["state"] == "ok"


def test_connected_apps_without_a_reader_are_blocked_not_broken():
    """Seven "connected" plugins that can never yield an item, said plainly."""
    health = build_health(
        _status(),
        [
            {"id": "plugin:github", "label": "GitHub", "has_pull_adapter": False},
            {"id": "plugin:gmail", "label": "Gmail", "has_pull_adapter": False},
        ],
    )
    check = _check(health, "integrations")
    assert check["state"] == "blocked"
    # No action: this is a missing capability, and offering a button would
    # send the user hunting for a switch that does not exist.
    assert check["action"] is None
    assert "GitHub and Gmail are connected" in check["detail"]
    # A missing reader must not make the rest of the corpus unusable.
    assert health["usable"] is True


def test_a_source_with_no_reader_is_never_offered_as_importable():
    """Do not hand the user a button that provably does nothing.

    A plugin-bridge source for GitHub is "approved" and "never imported" —
    both true — but it can never import, because no reader exists. Offering
    "Import now" there sends them to press a button that starts a job yielding
    zero items, and to conclude the app is lying. The integrations check
    already explains WHY; this one must stay quiet about it.
    """
    health = build_health(
        _status(
            sources=[
                {
                    "id": "plugin-bridge-1",
                    "label": "GitHub",
                    "integration_id": "plugin:github",
                    "consent": "approved",
                    "enabled": True,
                    "last_sync_at": None,
                }
            ]
        ),
        [{"id": "plugin:github", "label": "GitHub", "has_pull_adapter": False}],
    )
    check = _check(health, "sources")
    assert check["state"] == "blocked"
    assert check["facts"]["never_imported"] == 0
    assert check["facts"]["no_reader"] == 1


def test_a_source_whose_reader_exists_is_still_offered():
    """The same source becomes actionable the moment a reader is registered."""
    health = build_health(
        _status(
            sources=[
                {
                    "id": "plugin-bridge-1",
                    "label": "GitHub",
                    "integration_id": "plugin:github",
                    "consent": "approved",
                    "enabled": True,
                    "last_sync_at": None,
                }
            ]
        ),
        [{"id": "plugin:github", "label": "GitHub", "has_pull_adapter": True}],
    )
    check = _check(health, "sources")
    assert check["state"] == "attention"
    assert check["action"] == {"kind": "sync_all"}


def test_an_integration_with_a_reader_is_reported_as_importable():
    health = build_health(
        _status(),
        [{"id": "plugin:github", "label": "GitHub", "has_pull_adapter": True}],
    )
    assert _check(health, "integrations")["state"] == "ok"


def test_the_mixed_case_still_names_the_apps_that_cannot_be_read():
    """One reader must not hide the apps that still contribute nothing.

    Reporting only "1 app can be imported" is technically true and leaves the
    user wondering for weeks why their Gmail never shows up.
    """
    health = build_health(
        _status(),
        [
            {"id": "plugin:github", "label": "GitHub", "has_pull_adapter": True},
            {"id": "plugin:gmail", "label": "Gmail", "has_pull_adapter": False},
            {"id": "plugin:linear", "label": "Linear", "has_pull_adapter": False},
        ],
    )
    check = _check(health, "integrations")
    assert check["state"] == "ok"
    assert check["facts"] == {"connected": 3, "readable": 1, "pending": 2}
    assert "GitHub" in check["detail"]
    assert "Gmail and Linear" in check["detail"]
    assert "no reader" in check["detail"]


def test_a_silent_storage_fallback_is_surfaced():
    """Postgres configured, SQLite answering: the wiki works, elsewhere."""
    health = build_health(
        _status(db_backend="postgres", backend_in_use="sqlite"), []
    )
    check = _check(health, "storage")
    assert check["state"] == "attention"
    assert "postgres" in check["detail"]
    assert check["action"] == {"kind": "open_settings", "tab": "storage"}


def test_keyword_only_search_is_attention_not_failure():
    """No embedding key is a real, working configuration — say what it costs."""
    health = build_health(
        _status(
            search_legs={
                "keyword": {"available": True},
                "vector": {"available": False, "reason": "No embedding key."},
            }
        ),
        [],
    )
    check = _check(health, "search")
    assert check["state"] == "attention"
    assert "Exact words are found" in check["detail"]
    assert health["usable"] is True


def test_a_dead_keyword_index_blocks_search():
    health = build_health(
        _status(
            search_legs={
                "keyword": {"available": False, "reason": "FTS index missing."},
                "vector": {"available": False},
            }
        ),
        [],
    )
    assert _check(health, "search")["state"] == "blocked"
    assert health["usable"] is False


def test_mode_off_is_the_first_thing_reported():
    health = build_health(_status(enabled=False), [])
    assert health["checks"][0]["id"] == "mode"
    assert health["checks"][0]["state"] == "attention"
    assert health["checks"][0]["action"] == {"kind": "enable_mode"}


def test_failed_items_do_not_hide_that_the_rest_works():
    health = build_health(
        _status(
            counts={
                "captured": 0,
                "keyword_indexed": 0,
                "embedded": 0,
                "distilled": 90,
                "failed": 10,
                "total": 100,
            }
        ),
        [],
    )
    check = _check(health, "processing")
    assert check["state"] == "attention"
    assert "10" in check["title"]
    # The row promises that retrying usually clears them, so it must offer the
    # retry. It did not, and the only retry button lived in a strip the
    # overview hides — found by clicking the live app, not by a test.
    assert check["action"] == {"kind": "retry_failed"}
    # NOT "they stay keyword-searchable": an item can dead-letter at any
    # stage, including before it was ever indexed, so the counts cannot prove
    # that. The row promises only what it can show.
    assert "keyword-searchable" not in check["detail"]
    assert health["usable"] is True
    # The 10 are named where the corpus is described, instead of quietly
    # thinning the searchable number by ten with no explanation.
    assert "10" in _check(health, "content")["detail"]


@pytest.mark.parametrize(
    "status_kwargs",
    [
        {},
        {"enabled": False},
        {"started": False},
        {"sources": []},
        {"counts": {}},
        {"pipeline": {}},
        {"search_legs": {}},
    ],
)
def test_the_checklist_never_raises_on_a_partial_status(status_kwargs):
    """The screen a user opens BECAUSE something is wrong must always render."""
    health = build_health(_status(**status_kwargs), [])
    assert health["overall"] in ("ok", "working", "attention", "blocked")
    assert len(health["checks"]) == 7
    for check in health["checks"]:
        assert check["title"].strip()
        assert check["detail"].strip()


def test_a_rebuilding_vector_leg_is_not_reported_as_working_search():
    """The health row that lied for the whole length of a model switch.

    The leg probe is a CREDENTIAL check by contract (AP-21) and stays green
    during a rebuild, while the ANN index still mirrors the space the store is
    pinned to and the query is already embedded with the NEW model — so every
    semantic query is refused on a dimension mismatch. The checklist printed
    "Both exact words and meaning are searchable" throughout.
    ``ultrawiki_routes._apply_reembed_to_legs`` is what turns the leg off; this
    pins the consequence the user actually reads.
    """
    from jarvis.ui.web.ultrawiki_routes import _apply_reembed_to_legs

    legs = _apply_reembed_to_legs(
        {
            "keyword": {"available": True},
            "vector": {"available": True, "backend": "ollama", "model": "bge-m3"},
        },
        {"model": "bge-m3", "done": 300, "total": 4712},
    )
    assert legs["vector"]["available"] is False
    assert legs["vector"]["rebuilding"] is True
    reason = legs["vector"]["reason"]
    assert "300" in reason and "4712" in reason

    check = _check(build_health(_status(search_legs=legs), []), "search")
    assert check["state"] == "attention"
    assert "rebuilding" in check["detail"]


def test_the_rebuild_counter_says_which_population_it_counts():
    """The number that was read as the whole job (2026-07-27).

    ``2 592 of 4 712`` sat a few centimetres under "235 915 items are queued
    for processing". Both were right; nothing said they counted different
    populations, so the rebuild looked nearly finished while the real backlog
    was fifty times larger and untouched by that fraction. The counter now
    names its own scope and says the rest queues behind it.
    """
    from jarvis.ui.web.ultrawiki_routes import _apply_reembed_to_legs

    reason = _apply_reembed_to_legs(
        {"vector": {"available": True}},
        {"model": "bge-m3", "done": 2592, "total": 4712},
    )["vector"]["reason"]
    assert "already be searched by meaning" in reason
    assert "queues behind it" in reason


def test_a_measured_rate_puts_a_duration_on_the_rebuild():
    """A backlog without a duration is the defect; carry the measured one."""
    from jarvis.ui.web.ultrawiki_routes import _apply_reembed_to_legs

    reason = _apply_reembed_to_legs(
        {"vector": {"available": True}},
        {"model": "bge-m3", "done": 2592, "total": 4712},
        {"embed": {"backlog": 232_163, "eta_seconds": 232_163 / 0.65}},
    )["vector"]["reason"]
    assert "days" in reason


def test_an_unmeasured_rate_adds_no_invented_duration():
    """Silence beats a guess — the whole contract of throughput.py."""
    from jarvis.ui.web.ultrawiki_routes import _apply_reembed_to_legs

    reason = _apply_reembed_to_legs(
        {"vector": {"available": True}},
        {"model": "bge-m3", "done": 2592, "total": 4712},
        {"embed": {"backlog": 232_163, "eta_seconds": None}},
    )["vector"]["reason"]
    assert "at the current rate" not in reason


def test_no_rebuild_leaves_the_vector_leg_exactly_as_probed():
    """The steady state must not pay for the rebuild path."""
    from jarvis.ui.web.ultrawiki_routes import _apply_reembed_to_legs

    legs = {"keyword": {"available": True}, "vector": {"available": True}}
    assert _apply_reembed_to_legs(legs, {}) is legs
    assert _check(build_health(_status(search_legs=legs), []), "search")["state"] == "ok"


def test_a_backlog_states_how_long_it_takes():
    """The sentence that made a four-day queue look like a detail.

    "You do not have to wait for the rest" is fair over a queue of minutes and
    false over one of days. On the 2026-07-27 store it sat under 235 915
    queued items moving at 0.65 items a second, with nothing on screen saying
    so. The row now quotes the measured pace instead.
    """
    status = _status(
        counts={
            "captured": 98_506,
            "keyword_indexed": 133_657,
            "embedded": 3_752,
            "distilled": 216,
            "failed": 0,
            "total": 236_131,
        },
        pipeline={"running": True, "state": "processing", "reason": "working"},
        throughput={
            "embed": {
                "rate_per_hour": 2340.0,
                "backlog": 232_163,
                "eta_seconds": 232_163 / 0.65,
                "stalled": False,
                "paused_reason": "",
            }
        },
    )
    check = _check(build_health(status, []), "processing")
    assert check["state"] == "working"
    assert "days" in check["detail"]
    assert "you do not have to wait" not in check["detail"].lower()


def test_an_unmeasured_backlog_says_it_is_measuring():
    """No rate yet is not the same as no backlog, and neither is a guess."""
    status = _status(
        counts={
            "captured": 500,
            "keyword_indexed": 0,
            "embedded": 0,
            "distilled": 0,
            "failed": 0,
            "total": 500,
        },
        pipeline={"running": True, "state": "processing", "reason": "working"},
        throughput={"embed": {"rate_per_hour": None, "backlog": 500}},
    )
    check = _check(build_health(status, []), "processing")
    assert "still being measured" in check["detail"]


def test_a_deliberately_parked_summary_lane_explains_itself():
    """216 summaries frozen for hours with no reason on screen.

    The lane was doing exactly what it was told — distillation pauses during an
    embedding rebuild, because every summary written meanwhile is written again
    after the swap — but that reason only ever reached the log file, so a
    correctly parked stage and a broken one looked identical.
    """
    status = _status(
        counts={
            "captured": 0,
            "keyword_indexed": 0,
            "embedded": 3_752,
            "distilled": 216,
            "failed": 0,
            "total": 3_968,
        },
        pipeline={
            "running": True,
            "state": "processing",
            "reason": "working",
            "paused": {
                "distill": (
                    "summaries are paused while the search index is rebuilt on "
                    "the new embedding model"
                )
            },
        },
    )
    health = build_health(status, [])
    check = _check(health, "summaries")
    assert check["state"] == "working"
    assert "rebuilt" in check["detail"]
    assert "216" in check["detail"]


def test_no_summaries_row_when_nothing_is_parked():
    """The steady state must not grow an extra row explaining nothing."""
    health = build_health(_status(), [])
    assert all(c["id"] != "summaries" for c in health["checks"])
