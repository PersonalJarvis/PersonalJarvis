"""A voice capture request has its own tool and requires fresh image evidence."""

import pytest

from jarvis.live.config import LiveConfig
from jarvis.live.native import _fit_declarations
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.fakes.fake_subscription_session import AppshotSubscriptionGateway


@pytest.mark.parametrize("defer", [True, False])
def test_appshot_is_declared_once_even_when_other_tools_are_deferred(tmp_path, defer):
    gateway = AppshotSubscriptionGateway()
    ledger = LiveLedger(tmp_path / "live.sqlite3")
    try:
        runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="m")
        declared = runtime.declarations(defer_catalog=defer)
        appshot = next(d for d in gateway.catalog() if d.name == "take_appshot")
        assert [d["parameters"] for d in declared if d["name"] == "take_appshot"] == [
            appshot.input_schema,
        ]
        assert "take_appshot" not in runtime._names.values()
        assert "take_appshot" in {d["name"] for d in _fit_declarations(declared, 1)}
    finally:
        ledger.close()


@pytest.mark.parametrize("auth_mode", ["api_key", "chatgpt_subscription"])
def test_new_capture_requires_fresh_appshot_evidence_in_both_live_modes(auth_mode):
    config = LiveConfig(
        configured=True, auth_mode=auth_mode, backend_model="api-model",
        subscription_backend_model="subscription-model",
    )
    instructions = config.backend_config(language="en", tools=[])["instructions"]
    assert "call take_appshot for a fresh capture" in instructions
    assert "only after take_appshot succeeds" in instructions
    assert "old image as current" in instructions
