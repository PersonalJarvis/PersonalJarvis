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


def test_keep_fd_pairs_are_parsed_and_forwarded(monkeypatch) -> None:
    """A descriptor the caller hands in must survive into the REAL child.

    Without ``pass_fds`` on the inner spawn, ``close_fds`` drops everything
    above stdio at exec, so a lock the caller believed the child was holding
    was held only by this supervisor.
    """
    captured: dict[str, object] = {}

    class _Child:
        def wait(self) -> int:
            return 0

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["pass_fds"] = kwargs.get("pass_fds")
        captured["close_fds"] = kwargs.get("close_fds")
        return _Child()

    monkeypatch.setattr(lifeline.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(lifeline.os, "read", lambda _fd, _size: b"x")
    monkeypatch.setattr(lifeline.os, "close", lambda _fd: None)

    assert lifeline.main(["9", "--keep-fd", "11", "--keep-fd", "12", "--", "codex", "x"]) == 0

    assert captured["command"] == ["codex", "x"]
    assert captured["pass_fds"] == (11, 12)
    assert captured["close_fds"] is True


def test_argv_without_keep_fd_keeps_the_original_behaviour(monkeypatch) -> None:
    """The pairs are optional, so an argv built before they existed still runs."""
    captured: dict[str, object] = {}

    class _Child:
        def wait(self) -> int:
            return 0

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["pass_fds"] = kwargs.get("pass_fds")
        return _Child()

    monkeypatch.setattr(lifeline.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(lifeline.os, "read", lambda _fd, _size: b"x")
    monkeypatch.setattr(lifeline.os, "close", lambda _fd: None)

    assert lifeline.main(["9", "--", "codex", "app-server"]) == 0

    assert captured["command"] == ["codex", "app-server"]
    assert captured["pass_fds"] == ()


def test_malformed_keep_fd_arguments_are_refused() -> None:
    assert lifeline.main(["9", "--keep-fd", "not-a-number", "--", "codex"]) == 2
    assert lifeline.main(["9", "--keep-fd", "-3", "--", "codex"]) == 2
    assert lifeline.main(["9", "--keep-fd", "11", "codex"]) == 2
    assert lifeline.main(["9", "--keep-fd", "11", "--"]) == 2
