"""Regression guards for windowed-Python standard streams."""

from __future__ import annotations

import io
import sys

from jarvis.core.process_utils import ensure_standard_streams


def test_missing_windowed_streams_are_replaced(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_standard_streams()

    stdout = sys.stdout
    stderr = sys.stderr
    assert stdout is not None
    assert stderr is not None
    assert isinstance(stdout.isatty(), bool)
    assert isinstance(stderr.isatty(), bool)
    assert stdout.write("discarded") == len("discarded")
    assert stderr.write("discarded") == len("discarded")
    stdout.flush()
    stderr.flush()


def test_existing_streams_are_preserved(monkeypatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    ensure_standard_streams()

    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_stream_repair_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_standard_streams()
    stdout = sys.stdout
    stderr = sys.stderr
    ensure_standard_streams()

    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_uvicorn_logging_accepts_repaired_windowed_streams(monkeypatch) -> None:
    import uvicorn

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    ensure_standard_streams()

    async def app(scope, receive, send) -> None:
        del scope, receive, send

    config = uvicorn.Config(app=app, log_level="warning")

    assert config.log_level == "warning"
