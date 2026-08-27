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


def test_the_real_local_card_bounds_itself_to_its_own_context_window() -> None:
    """The whole chain, with the shipped provider rather than a stand-in.

    Live 2026-08-27 this card declared no budget at all, so a 220-tool set
    (~62k tokens) went to a 9B brain in a 32k window: the declarations plus
    the transcript overflowed it, llama.cpp shifted the context, the cached
    prefix died and every answer paid a 4.2 s prefill instead of 0.2 s.
    """
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider

    command = (
        '"C:\tree\venv\Scripts\speech-to-speech.exe" --mode realtime '
        "--model_name ornith:9b-voice-32k "
        "--responses_api_base_url http://127.0.0.1:11434/v1 "
        "--responses_api_api_key ollama"
    )
    card = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765", launch_command=command
    )
    sess = _build([card], budget_tokens=0)

    # A quarter of the 32k window this machine's brain actually runs.
    assert card.tool_declaration_budget_tokens == 8_192
    assert sess._declaration_budget_chars(card) == 8_192 * 4


def test_the_local_card_scales_with_the_machine_it_runs_on() -> None:
    """The same code has to give a small laptop and a big box different room."""
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider

    def card(model: str) -> object:
        return LocalRealtimeProvider(
            base_url="http://127.0.0.1:8765",
            launch_command=(
                f'"s.exe" --mode realtime --model_name {model} '
                "--responses_api_base_url http://127.0.0.1:11434/v1 "
                "--responses_api_api_key ollama"
            ),
        )

    laptop = card("qwen3.5:4b-voice-8k").tool_declaration_budget_tokens
    workstation = card("m:14b-voice-64k").tool_declaration_budget_tokens
    assert laptop == 2_048
    assert workstation == 16_384


def test_an_unmanaged_endpoint_is_not_bounded_by_a_guess() -> None:
    """Without a launch command there is no machine to read, and a LAN box may
    be far bigger than this one — the config bound stays the user's lever."""
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider

    card = LocalRealtimeProvider(base_url="http://192.168.1.9:8765")
    assert card.tool_declaration_budget_tokens == 0


def test_a_small_local_brain_gets_a_compact_standing_instructions_block(
    monkeypatch,
) -> None:
    """That block is re-sent on EVERY turn and a self-hosted model has no
    server-side prompt cache to hide behind.

    Live 2026-08-27 the per-turn prompt on the local card had grown from a
    3,060-token median to 16,386 and the call stopped feeling live. Cloud
    wires keep the full block — the 2026-08-24 mandate retired that cap as a
    COST lever, and on those wires it is one.
    """
    from types import SimpleNamespace as NS

    from jarvis.brain import agent_instructions
    from jarvis.realtime import session as sess_mod

    seen: list[int] = []
    monkeypatch.setattr(
        agent_instructions,
        "render_for_prompt",
        lambda config, *, max_chars: (seen.append(max_chars) or "x" * 10),
    )

    sess_mod._preferences_block(NS(), compact=True)
    sess_mod._preferences_block(NS(), compact=False)

    compact, full = seen
    assert compact == sess_mod._PREFERENCES_MAX_CHARS_COMPACT == 4_000
    assert full == sess_mod._PREFERENCES_MAX_CHARS
    assert compact < full


def test_the_local_card_is_the_one_asking_for_that_profile() -> None:
    """The bound follows a declared capability, never a provider name (AP-21)."""
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider

    assert LocalRealtimeProvider.prefers_compact_instructions is True
