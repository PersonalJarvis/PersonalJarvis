"""Exact-base gate comparison detects replacements as well as count increases."""

import pytest

from scripts.ci.check_changed_baseline_gates import (
    async_findings,
    changed_paths,
    reference_drift_is_new,
    silent_findings,
)


def test_silent_handler_line_shift_is_not_new() -> None:
    base = "def action():\n    try:\n        work()\n    except ValueError:\n        pass\n"
    head = "# Moved down one line\n" + base
    assert silent_findings(head) - silent_findings(base) == {}


def test_replacing_a_silent_handler_fails_even_at_the_same_count() -> None:
    base = "def action():\n    try:\n        work()\n    except ValueError:\n        pass\n"
    head = base.replace("ValueError", "RuntimeError")
    assert sum((silent_findings(head) - silent_findings(base)).values()) == 1


def test_justified_handler_removes_a_finding() -> None:
    base = "def action():\n    try:\n        work()\n    except ValueError:\n        pass\n"
    head = base.replace(
        "        pass", "        # Optional probe may fail without affecting startup.\n        pass"
    )
    assert silent_findings(head) == {}


def test_new_blocking_route_detected_without_counting_existing_one() -> None:
    base = "@router.get('/old')\nasync def old():\n    return read_disk()\n"
    head = base + "@router.get('/new')\nasync def new():\n    return read_disk()\n"
    assert sum((async_findings(head) - async_findings(base)).values()) == 1


def test_retargeted_blocking_route_is_new_even_with_the_same_name() -> None:
    base = "@router.get('/old')\nasync def route():\n    return read_disk()\n"
    head = base.replace("'/old'", "'/new'")
    assert sum((async_findings(head) - async_findings(base)).values()) == 1


def test_reference_drift_is_blocking_only_when_its_inputs_change() -> None:
    assert not reference_drift_is_new({"jarvis/ui/web/agent_chat_routes.py"})
    assert reference_drift_is_new({"jarvis/commands/registry.py"})


def test_missing_base_fails_closed() -> None:
    with pytest.raises(ValueError, match="Cannot resolve base"):
        changed_paths("missing-base-commit")
