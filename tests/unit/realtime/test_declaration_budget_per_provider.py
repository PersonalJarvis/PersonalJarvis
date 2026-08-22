"""The native tool set is trimmed to the budget of the provider that opens.

ADR-0035 §4: ``gemini-live`` / ``vertex-live`` declare no budget of their own
(the setup is sent once per connection); the OpenAI-protocol wires declare
8 000 tokens. The session used to take the MINIMUM over every candidate in the
chain at construction time — so a Gemini call with an OpenAI fallback behind it
ran the whole session on 8 000 tokens and, live 2026-08-22 18:16, lost every
first-party connector (``spotify``, ``youtube_music``, ``gmail``,
``google_calendar`` …) to a budget that never applied to its wire.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.realtime.session import RealtimeVoiceSession


class _Transport:
    creates_responses_automatically = False

    async def send_text(self, text):
        pass

    async def send_tool_result(self, call_id, name, result):
        pass

    async def request_response(self, *, required_tool=None):
        del required_tool

    async def interrupt(self):
        pass

    async def close(self):
        pass


class _Provider:
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self, name: str, budget_tokens: int = 0):
        self.name = name
        if budget_tokens:
            self.tool_declaration_budget_tokens = budget_tokens

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        del cfg
        return _Transport()


class _Bridge:
    """Records every re-fit; reports a change whenever the budget moved."""

    def __init__(self):
        self.budgets: list[int] = []
        self.declarations = ({"name": "wiki_recall"},)
        self.dropped_names = ()
        self.declaration_chars = 100

    def set_declaration_budget(self, budget_chars: int) -> bool:
        changed = not self.budgets or self.budgets[-1] != budget_chars
        self.budgets.append(budget_chars)
        return changed


def _cfg(budget_tokens: int = 20_000):
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(
            mode="realtime", realtime_tool_declaration_budget_tokens=budget_tokens
        ),
        latency=SimpleNamespace(enabled=False),
    )


def _build(providers, *, budget_tokens: int = 20_000):
    return RealtimeVoiceSession(
        session_id="budget-per-provider",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=providers[0],
        providers=providers,
        config=_cfg(budget_tokens),
        bus=None,
        brain=None,
    )


def test_the_chain_minimum_is_only_the_first_conservative_fit() -> None:
    gemini = _Provider("gemini-live")
    openai = _Provider("openai-realtime", budget_tokens=8_000)
    sess = _build([gemini, openai])

    # Before the call knows its provider: the tightest budget anywhere.
    assert sess._declaration_budget_chars() == 8_000 * 4
    # For the candidate about to open: ITS budget, bounded by the config.
    assert sess._declaration_budget_chars(gemini) == 20_000 * 4
    assert sess._declaration_budget_chars(openai) == 8_000 * 4


def test_the_config_bound_still_caps_a_provider_that_declares_none() -> None:
    gemini = _Provider("gemini-live")
    sess = _build([gemini], budget_tokens=5_000)
    assert sess._declaration_budget_chars(gemini) == 5_000 * 4


def test_the_connect_path_refits_the_bridge_to_the_opening_provider() -> None:
    gemini = _Provider("gemini-live")
    openai = _Provider("openai-realtime", budget_tokens=8_000)
    sess = _build([gemini, openai])
    bridge = _Bridge()
    sess._tool_bridge = bridge
    sess._tool_mode = "hybrid"
    sess._delegate_enabled = True

    sess._fit_declaration_budget(gemini)
    assert bridge.budgets == [20_000 * 4]
    # A cross-family fallback to the OpenAI wire tightens the set again.
    sess._fit_declaration_budget(openai)
    assert bridge.budgets == [20_000 * 4, 8_000 * 4]


def test_a_bridge_without_refit_or_a_non_hybrid_session_is_left_alone() -> None:
    gemini = _Provider("gemini-live")
    sess = _build([gemini])
    sess._tool_bridge = SimpleNamespace(declarations=())  # no set_declaration_budget
    sess._tool_mode = "hybrid"
    sess._delegate_enabled = True
    sess._fit_declaration_budget(gemini)  # must not raise

    bridge = _Bridge()
    sess._tool_bridge = bridge
    sess._tool_mode = "delegate"
    sess._fit_declaration_budget(gemini)
    assert bridge.budgets == []
