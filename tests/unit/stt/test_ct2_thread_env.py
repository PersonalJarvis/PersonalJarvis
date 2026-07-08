"""Defensive hardening for the CPU stt_match wake path (AP-24/AP-25/BUG-036):
``_bound_ct2_threads`` caps the ctranslate2/OpenMP CPU thread pool via
environment variables, without ever clobbering an explicit user setting.

This is defensive only — it does not claim to cure the constellation-specific
ctranslate2<->OpenMP deadlock (AP-25); the real fix is the vosk_kws engine
bypassing this path entirely.
"""

import os


def test_bound_ct2_threads_sets_env_when_unset(monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    from jarvis.plugins.stt.fwhisper import _bound_ct2_threads

    _bound_ct2_threads(default=2)

    assert os.environ["OMP_NUM_THREADS"] == "2"


def test_bound_ct2_threads_respects_user_override(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "8")

    from jarvis.plugins.stt.fwhisper import _bound_ct2_threads

    _bound_ct2_threads(default=2)

    assert os.environ["OMP_NUM_THREADS"] == "8"  # never clobber an explicit setting
