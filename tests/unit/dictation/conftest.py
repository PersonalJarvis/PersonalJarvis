"""Keep dictation unit tests independent of the machine they run on.

Two probes read the live install: whether the configured recognizer runs on
this machine (``stt_runs_on_device`` reads jarvis.toml) and whether a managed
local LLM owns the GPU (``_local_brain_owns_accelerator`` looks for an installed
llama-server). A developer box with a local recognizer or a local brain flipped
both, and tests written for the cloud path silently took the on-device one.
Each test that is ABOUT one of these decisions sets it explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_machine_probes(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.dictation import local_preview, polish_client

    # The privacy module tests the recognizer probe itself and fakes its inputs.
    if not request.node.module.__name__.endswith("test_polish_privacy"):
        monkeypatch.setattr(polish_client, "stt_runs_on_device", lambda: False)
    monkeypatch.setattr(local_preview, "_local_brain_owns_accelerator", lambda: False)
