"""Diagnostic latency injection: delay native completion on both revisions."""

import time

import pytest


@pytest.fixture(autouse=True)
def delayed_native_completion(request, monkeypatch):
    if not request.node.module.__name__.endswith("test_local_preview"):
        return
    for name in ("_Engine", "_ControlledEngine"):
        cls = getattr(request.node.module, name, None)
        if cls is None:
            continue
        original = cls._transcribe_sync

        def delayed(*args, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            time.sleep(0.35)
            return result

        monkeypatch.setattr(cls, "_transcribe_sync", delayed)
