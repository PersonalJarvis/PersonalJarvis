"""Contract-Tests — jede CancelToken-Implementierung erfuellt das Protocol."""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from jarvis.core.protocols import CancelToken
from tests.fixtures.control.fake_cancel_token import FakeCancelToken


def _get_tokens() -> list[CancelToken]:
    tokens: list[CancelToken] = [FakeCancelToken()]
    with contextlib.suppress(Exception):
        from jarvis.control.cancel import CancelToken as ProdToken  # type: ignore[attr-defined]
        tokens.append(ProdToken())
    return tokens


@pytest.mark.parametrize("token", _get_tokens(), ids=lambda t: type(t).__name__)
def test_cancel_token_structurally_matches_protocol(token):
    assert isinstance(token, CancelToken)


@pytest.mark.asyncio
async def test_cancel_flips_state_and_sets_reason():
    tok = FakeCancelToken()
    assert not tok.is_cancelled()
    assert tok.reason is None
    tok.cancel("budget_task_exceeded")
    assert tok.is_cancelled()
    assert tok.reason == "budget_task_exceeded"


@pytest.mark.asyncio
async def test_wait_until_cancelled_unblocks():
    tok = FakeCancelToken()

    async def canceller():
        await asyncio.sleep(0.01)
        tok.cancel("test")

    await asyncio.gather(tok.wait_until_cancelled(), canceller())
    assert tok.is_cancelled()


@pytest.mark.asyncio
async def test_first_reason_wins_on_multiple_cancels():
    """Only the first `cancel()` reason counts — so a later kill switch
    can't overwrite a budget-exceeded reason.
    """
    tok = FakeCancelToken()
    tok.cancel("budget_task_exceeded")
    tok.cancel("kill_switch")
    assert tok.reason == "budget_task_exceeded"
