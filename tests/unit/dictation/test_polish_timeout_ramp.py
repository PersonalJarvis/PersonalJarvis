"""The wording pass's ceiling grows with the transcript, and only with it.

Why this needs its own file
---------------------------
``polish_timeout_ms`` used to be the whole budget for every dictation, which
made it a promise about the WAIT applied to work whose size the speaker chooses.
Measured on one live install (2026-08-20, 201 wording passes at the fixed
1200 ms ceiling): the passes that came back ran 526 ms median below 25 words and
850-1000 ms at 100-220 words, while the 20 that expired had a median of 77 words
against 15 for the ones that succeeded. A tenth of the feature was being thrown
away by the clock, and it was the long half — the dictations a formatter is most
needed on, and the ones where a failed TRANSLATION is not merely unpunctuated
text but text in the wrong language.

What must stay true, and is what these tests pin:

* a short dictation keeps EXACTLY the configured ceiling — the one-line
  dictation is the common case and the ramp may not quietly spend it;
* a long one gets more, in proportion to how much more there is to write;
* the extra time stops at a ceiling the user owns;
* a user who wants the old fixed behaviour gets it by saying so.

The client is faked, so what is asserted is the deadline the transport was
handed — not wall-clock timing, which would make this file flaky rather than
meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.dictation import polish
from jarvis.dictation.polish import polish_transcript
from jarvis.dictation.polish_client import POLISH_FAMILIES, PolishFamily

pytestmark = pytest.mark.asyncio

GROQ: PolishFamily = POLISH_FAMILIES[0]


#: One word per entry, so a test can ask for a transcript of a stated length
#: without counting anything by hand.
def _words(count: int) -> str:
    return " ".join(["word"] * count)


@dataclass
class _Cfg:
    """Only the keys the ceiling is computed from."""

    polish: bool = True
    polish_provider: str = "auto"
    polish_model: str = ""
    polish_timeout_ms: int = 1200
    polish_timeout_max_ms: int = 2000
    polish_min_words: int = 4
    polish_max_input_chars: int = 0
    polish_max_output_tokens: int = 1200
    polish_temperature: float = 0.0
    polish_drift_max_shrink: float = 0.55
    polish_drift_max_growth: float = 1.20
    polish_style: str = "neutral"


@dataclass
class _FakeClient:
    """Answers with the input unchanged and records the deadline it was given."""

    calls: list[float] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str:
        self.calls.append(timeout_s)
        return "anything"


@pytest.fixture(autouse=True)
def _fresh_breaker() -> None:
    polish.reset_polish_state()


def _wire(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (GROQ,))
    monkeypatch.setattr(polish, "build_polish_client", lambda family, *, model: client)
    return client


async def _deadline_for(monkeypatch: pytest.MonkeyPatch, words: int, cfg: Any) -> float:
    """The per-call deadline the transport was handed, in seconds."""
    client = _wire(monkeypatch)
    await polish_transcript(_words(words), language="en", cfg=cfg)
    assert client.calls, "the pass never reached a provider"
    return client.calls[0]


async def test_a_short_dictation_keeps_the_configured_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The promise the user reads on the settings screen, unchanged.

    Everything up to the free allowance is the dictation people make all day,
    and it must still be delivered inside the budget they configured. A ramp
    that started at the first word would have turned one honest number into a
    number that is true of nothing.
    """
    deadline = await _deadline_for(monkeypatch, 20, _Cfg(polish_timeout_ms=1200))

    # The walk hands the client the time REMAINING, so a few milliseconds of
    # our own work are legitimately missing; what must not happen is the
    # budget growing.
    assert 1.0 < deadline <= 1.2


async def test_a_long_dictation_is_given_more_time_than_a_short_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this ramp exists for, stated as a comparison.

    Two dictations, same config, same provider — the longer one has more text
    to write out and gets proportionally longer before we give up on it.
    """
    short = await _deadline_for(monkeypatch, 20, _Cfg())
    polish.reset_polish_state()
    long = await _deadline_for(monkeypatch, 60, _Cfg())

    assert long > short
    # 60 words = 35 over the free allowance, 15 ms each on top of 1200 ms, and
    # still short of the 2000 ms cap — so this asserts the RAMP rather than
    # the ceiling the next test owns.
    assert 1.5 < long <= 1.725


async def test_the_extra_time_stops_at_the_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A very long dictation must not be able to hang the delivery.

    The ramp is bounded by a key the user owns, so "give the long ones more
    time" can never become "wait indefinitely for a provider that died".
    """
    deadline = await _deadline_for(
        monkeypatch, 5000, _Cfg(polish_timeout_ms=1200, polish_timeout_max_ms=2500)
    )

    assert 2.3 < deadline <= 2.5


async def test_a_maximum_at_the_base_switches_the_ramp_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escape hatch: the old fixed ceiling is still available, by saying so.

    Someone who would rather have their raw words immediately than wait for a
    long dictation to be formatted sets the maximum to the base and gets
    exactly the behaviour that shipped before the ramp existed.
    """
    deadline = await _deadline_for(
        monkeypatch, 800, _Cfg(polish_timeout_ms=900, polish_timeout_max_ms=900)
    )

    assert 0.7 < deadline <= 0.9


async def test_a_maximum_below_the_base_never_shortens_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contradictory config costs the ramp, never the configured budget.

    ``polish_timeout_max_ms`` bounds how far the ceiling may STRETCH; it is not
    a second, competing budget. A hand-edited config that sets it below the
    base gets no ramp — and still gets the base it asked for, rather than a
    dictation pass silently running on a ceiling nobody typed.
    """
    deadline = await _deadline_for(
        monkeypatch, 800, _Cfg(polish_timeout_ms=1500, polish_timeout_max_ms=400)
    )

    assert 1.3 < deadline <= 1.5


async def test_an_explicit_override_is_never_ramped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``timeout_s`` means that many seconds exactly.

    Its two callers are the settings-screen dry run and the tests, and both are
    measuring a provider against a stated number. A ramp underneath them would
    make the reported answer depend on how long the sample happened to be.
    """
    client = _wire(monkeypatch)

    await polish_transcript(_words(900), language="en", cfg=_Cfg(), timeout_s=0.5)

    assert client.calls and 0 < client.calls[0] <= 0.5


async def test_a_config_that_never_heard_of_the_maximum_still_ramps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An install whose ``jarvis.toml`` predates this key is not left behind.

    Every key in this pass is read through ``getattr`` with a default precisely
    so an older config keeps working; the ramp is a fix for a live defect, so
    the default has to be the fixed version, not the broken one.
    """

    class _OlderConfig:
        polish = True
        polish_provider = "auto"
        polish_timeout_ms = 1200
        polish_min_words = 4

    client = _wire(monkeypatch)
    await polish_transcript(_words(300), language="en", cfg=_OlderConfig())

    assert client.calls and client.calls[0] > 1.2
