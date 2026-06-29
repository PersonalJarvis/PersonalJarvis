"""M5 (honest status): on a headless host without the [desktop] extra, the local
faster-whisper STT import raises ModuleNotFoundError. The API-Keys "Test" must
report NOT_CONFIGURED ("extra not installed") — an actionable amber chip — not a
red ERROR "integration bug".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.brain.provider_test import NOT_CONFIGURED, run_provider_test


def test_local_stt_import_error_reports_not_installed():
    spec = SimpleNamespace(id="faster-whisper", tier="stt", auth_mode="none")

    def _make_stt(cfg, provider):
        raise ModuleNotFoundError("No module named 'faster_whisper'")

    res = asyncio.run(
        run_provider_test(spec, SimpleNamespace(stt=SimpleNamespace()), make_stt=_make_stt)
    )
    assert res.status == NOT_CONFIGURED
    assert "extra" in res.detail.lower() or "install" in res.detail.lower()
