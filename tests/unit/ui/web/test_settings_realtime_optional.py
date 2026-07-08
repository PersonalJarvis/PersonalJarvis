"""The settings router must import + serve even when the realtime module is absent.

The realtime voice engine is an optional, still-internal module that is stripped
from public distribution snapshots (distribution-denylist). Two shipped sites
reference it — this guards the boot-critical one: ``settings_routes`` imported
``jarvis.realtime.factory`` at MODULE level, so a realtime-stripped public build
would fail to import the whole settings router and the server would not boot. The
reference must be lazy + guarded, degrading to "realtime not available".
"""
from __future__ import annotations

import sys

from jarvis.ui.web import settings_routes


def test_realtime_available_helper_degrades_when_module_stripped(monkeypatch) -> None:
    # Simulate the realtime engine being stripped from a public snapshot: the
    # import raises, and the helper must report "no realtime", not blow up.
    monkeypatch.setitem(sys.modules, "jarvis.realtime.factory", None)
    monkeypatch.setitem(sys.modules, "jarvis.realtime", None)
    assert settings_routes._realtime_available_provider(object()) is None


def test_realtime_available_helper_resolves_when_module_present(monkeypatch) -> None:
    # When the module IS present, the helper delegates to it (here: a stub that
    # reports a reachable provider), so the local/full build is unchanged.
    import types

    stub = types.ModuleType("jarvis.realtime.factory")
    stub.realtime_available_provider = lambda cfg: "openai-realtime"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jarvis.realtime.factory", stub)
    assert settings_routes._realtime_available_provider(object()) == "openai-realtime"
