from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.diagnostics import doctor
from jarvis.local_models import llama_server
from jarvis.plugins.tts import voicevox_engine


def test_missing_local_stack_is_never_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llama_server, "find_binary", lambda: None)
    monkeypatch.setattr(llama_server, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(voicevox_engine, "installed", lambda: False)
    findings = doctor.check_local_stack()
    assert findings
    assert not doctor.has_failures(findings)
    assert any(f.hint and "install_local_brain" in f.hint for f in findings)


def test_binary_without_model_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llama_server, "find_binary", lambda: Path("llama-server.exe"))
    monkeypatch.setattr(llama_server, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(voicevox_engine, "installed", lambda: False)
    assert any(f.status == "warn" for f in doctor.check_local_stack())
