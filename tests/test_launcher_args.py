"""Pure unit tests for the standalone launcher's argparse parser."""

from __future__ import annotations

import pytest

from jarvis.ui.web.launcher import _parse_args


def test_defaults():
    ns = _parse_args([])
    assert ns.headless is False
    assert ns.dev is False
    assert ns.port is None
    assert ns.no_lock is False


def test_headless_flag():
    ns = _parse_args(["--headless"])
    assert ns.headless is True
    assert ns.dev is False


def test_dev_and_port():
    ns = _parse_args(["--dev", "--port", "9999"])
    assert ns.dev is True
    assert ns.port == 9999


def test_no_lock_flag():
    ns = _parse_args(["--no-lock"])
    assert ns.no_lock is True


def test_port_is_int():
    ns = _parse_args(["--port", "8080"])
    assert isinstance(ns.port, int)
    assert ns.port == 8080


def test_invalid_port_fails():
    with pytest.raises(SystemExit):
        _parse_args(["--port", "notanumber"])


def test_combined_flags():
    ns = _parse_args(["--headless", "--dev", "--no-lock", "--port", "1234"])
    assert ns.headless is True
    assert ns.dev is True
    assert ns.no_lock is True
    assert ns.port == 1234


def test_second_desktop_start_focuses_existing_instance(monkeypatch):
    from jarvis.ui import desktop_app
    from jarvis.ui.web import launcher

    focused = {"called": False}

    def _raise_lock(*args, **kwargs):
        raise desktop_app.SingleInstanceError("already running")

    def _focus():
        focused["called"] = True
        return True

    monkeypatch.setattr(desktop_app, "acquire_single_instance_lock", _raise_lock)
    monkeypatch.setattr(desktop_app, "focus_existing_instance_robust", _focus)

    assert launcher._run_desktop(cfg=object(), use_lock=True) == 3
    assert focused["called"] is True
