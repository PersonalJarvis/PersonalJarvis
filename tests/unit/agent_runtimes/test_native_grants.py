"""Native runtime tools must not circumvent the agent's capability selection."""

from types import SimpleNamespace

import pytest

from jarvis.agent_chat.runner_acp import _denied_native


@pytest.mark.parametrize(
    ("mode", "grants", "denies", "plan", "expected"),
    [
        ("all", [], [], False, set()),
        ("allowlist", [], [], False, {"shell", "web"}),
        ("allowlist", ["core:browser"], [], False, {"shell"}),
        ("allowlist", ["core:shell"], [], False, {"web"}),
        ("allowlist", ["core:shell", "core:browser"], [], False, set()),
        ("allowlist", ["core:browser"], ["core:browser"], False, {"shell", "web"}),
        ("all", [], ["core:browser"], True, {"shell", "web"}),
        ("all", [], ["core:shell"], False, {"shell"}),
        ("all", [], [], True, {"shell"}),
    ],
)
def test_native_tools_obey_allowlist_denies_and_plan(mode, grants, denies, plan, expected):
    agent = SimpleNamespace(grant_mode=mode, grants=grants, denies=denies)
    assert _denied_native(agent, plan) == frozenset(expected)
