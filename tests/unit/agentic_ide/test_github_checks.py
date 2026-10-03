"""GitHub's complete check enums and aggregate counts drive the remote verdict."""

import pytest

from jarvis.agentic_ide.github_checks import checks


def test_backend_states_and_payload_match_the_frontend_contract():
    import re
    from dataclasses import fields
    from pathlib import Path

    from jarvis.agentic_ide.github_checks import CHECK_STATES, CONTEXT_STATES
    from jarvis.agentic_ide.github_status import BranchStatus

    root = Path(__file__).resolve().parents[3]
    frontend = root / "jarvis/ui/web/frontend/src/components/agentic"
    marks = (frontend / "SessionGitHubBadge.tsx").read_text(encoding="utf-8")
    marks = marks.split("export const CI_MARKS", 1)[1].split("\n};", 1)[0]
    assert set(re.findall(r"^  (\w+):", marks, re.MULTILINE)) == {
        "none",
        *CHECK_STATES.values(),
        *CONTEXT_STATES.values(),
    }
    schema = (frontend / "useSessionGitHub.ts").read_text(encoding="utf-8")
    schema = schema.split("export interface SessionGitHubStatus", 1)[1].split("\n}", 1)[0]
    assert set(re.findall(r"^  (\w+)\??:", schema, re.MULTILINE)) == {
        item.name for item in fields(BranchStatus)
    }


@pytest.mark.parametrize(
    "state,expected",
    [
        ("ACTION_REQUIRED", "action_required"),
        ("CANCELLED", "cancelled"),
        ("FAILURE", "failure"),
        ("NEUTRAL", "neutral"),
        ("SKIPPED", "skipped"),
        ("STALE", "stale"),
        ("STARTUP_FAILURE", "startup_failure"),
        ("SUCCESS", "success"),
        ("TIMED_OUT", "timed_out"),
        ("COMPLETED", "unknown"),
        ("IN_PROGRESS", "running"),
        ("PENDING", "pending"),
        ("QUEUED", "pending"),
        ("REQUESTED", "requested"),
        ("WAITING", "waiting"),
        ("NEW_UNRECOGNISED_STATE", "unknown"),
    ],
)
def test_all_check_states(state, expected):
    data = {
        "state": "PENDING",
        "contexts": {
            "totalCount": 1,
            "checkRunCountsByState": [{"state": state, "count": 1}],
            "statusContextCountsByState": [],
            "nodes": [],
        },
    }
    assert checks(data, "a" * 40).state == expected


@pytest.mark.parametrize(
    "state,expected",
    [
        ("ERROR", "error"),
        ("EXPECTED", "expected"),
        ("FAILURE", "failure"),
        ("PENDING", "pending"),
        ("SUCCESS", "success"),
    ],
)
def test_legacy_status_context_states(state, expected):
    data = {
        "contexts": {
            "totalCount": 1,
            "nodes": [{"__typename": "StatusContext", "state": state, "context": "external"}],
        }
    }
    assert checks(data, "head").state == expected


def test_checks_beyond_first_page_are_counted_without_guessing():
    data = {
        "state": "PENDING",
        "contexts": {
            "totalCount": 151,
            "checkRunCountsByState": [
                {"state": "SUCCESS", "count": 150},
                {"state": "IN_PROGRESS", "count": 1},
            ],
            "statusContextCountsByState": [],
            "nodes": [],
        },
    }
    value = checks(data, "head")
    assert (value.state, value.passed, value.running, value.total) == ("running", 150, 1, 151)
    assert value.url == ""  # Caller links the commit checks, not a different job.


def test_running_and_cancelled_are_distinct_and_link_the_running_job():
    data = {
        "contexts": {
            "totalCount": 2,
            "nodes": [
                {
                    "__typename": "CheckRun",
                    "name": "old",
                    "status": "COMPLETED",
                    "conclusion": "CANCELLED",
                    "detailsUrl": "old",
                },
                {
                    "__typename": "CheckRun",
                    "name": "new",
                    "status": "IN_PROGRESS",
                    "detailsUrl": "new",
                },
            ],
        }
    }
    value = checks(data, "head")
    assert value.state == "running" and value.states == ["running", "cancelled"]
    assert value.url == "new" and value.names == ["new"]
