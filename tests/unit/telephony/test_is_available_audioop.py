"""L7 (cross-platform): Python 3.13 removed the stdlib ``audioop`` module that the
telephony media path needs. is_available() must report False on 3.13+ when neither
``audioop`` nor the ``audioop-lts`` backport is importable — else it claims
"available" and then 500s on the first media socket.
"""
from __future__ import annotations

import importlib.util

import jarvis.telephony as tel


def test_is_available_false_when_audioop_missing_on_py313(monkeypatch):
    monkeypatch.setattr(tel, "_PY313_PLUS", True)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "audioop" else object()
    )
    assert tel.is_available() is False


def test_is_available_true_when_audioop_present_on_py313(monkeypatch):
    monkeypatch.setattr(tel, "_PY313_PLUS", True)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert tel.is_available() is True


def test_is_available_ignores_audioop_before_py313(monkeypatch):
    # On <=3.12 audioop is stdlib; only the twilio SDK gates availability.
    monkeypatch.setattr(tel, "_PY313_PLUS", False)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if name == "twilio" else None
    )
    assert tel.is_available() is True


def test_is_available_false_without_twilio(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert tel.is_available() is False
