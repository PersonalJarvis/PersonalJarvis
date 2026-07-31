from __future__ import annotations

import threading

import jarvis.core.child_lifeline as lifeline


def test_parent_eof_kills_the_supervised_process_group(monkeypatch) -> None:
    killed: list[bool] = []
    monkeypatch.setattr(lifeline.os, "read", lambda _fd, _size: b"")
    monkeypatch.setattr(lifeline, "_kill_process_group", lambda: killed.append(True))

    lifeline._watch_parent(7, threading.Event())

    assert killed == [True]


def test_normal_child_completion_disarms_parent_eof(monkeypatch) -> None:
    killed: list[bool] = []
    completed = threading.Event()
    completed.set()
    monkeypatch.setattr(lifeline.os, "read", lambda _fd, _size: b"")
    monkeypatch.setattr(lifeline, "_kill_process_group", lambda: killed.append(True))

    lifeline._watch_parent(7, completed)

    assert killed == []


def test_main_rejects_malformed_internal_contract() -> None:
    assert lifeline.main([]) == 2
    assert lifeline.main(["not-an-fd", "--", "codex"]) == 2
