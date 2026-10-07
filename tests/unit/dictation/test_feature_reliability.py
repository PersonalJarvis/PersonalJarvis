"""User-visible dictation contracts, including failures hidden by happy paths."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from jarvis.dictation import polish, polish_client, prompt_mode


@pytest.fixture(autouse=True)
def isolated_state():
    polish.reset_polish_state()
    prompt_mode.set_prompt_mode_paused(False)
    yield
    polish.reset_polish_state()


class Reply:
    def __init__(self, text: str, *, delay: float = 0):
        self.text = text
        self.delay = delay
        self.cancelled = False

    async def complete(self, *args, **kwargs):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.text


@pytest.mark.parametrize(
    "raw,answer",
    [
        (
            "the program is broken and we need to make a decision about it tomorrow",
            "The application is faulty, and we need to decide about it tomorrow.",
        ),
        ("make a decision", "Decide."),
    ],
)
async def test_precision_works_without_the_cleanup_switch(monkeypatch, raw, answer):
    monkeypatch.setattr(
        polish, "resolve_polish_chain", lambda cfg: polish_client.POLISH_FAMILIES[:1]
    )
    monkeypatch.setattr(polish, "build_polish_client", lambda *a, **kw: Reply(answer))
    cfg = SimpleNamespace(polish=False, polish_precision=True)
    result = await polish.polish_transcript(raw, language="en", cfg=cfg)
    assert result.status == "applied"
    assert result.text == answer


@pytest.mark.parametrize(
    "raw,answer",
    [
        (
            "Fix src/auth.py without changing its API.",
            "Fix the authentication bug without changing its API.",
        ),
        (
            "Run 3 retries before reporting the failure.",
            "Run 9 retries before reporting the failure.",
        ),
        (
            "Wait 12 seconds before retrying the request.",
            "Wait 120 seconds before retrying the request.",
        ),
        (
            'Name the button "Retry" and keep it visible.',
            'Name the button "Retrying" and keep it visible.',
        ),
    ],
)
def test_prompt_does_not_drop_or_change_a_single_explicit_detail(raw, answer):
    assert prompt_mode.prompt_guard_reason(raw, answer) == "dropped_detail"


def test_prompt_does_not_change_the_sign_of_a_number():
    assert prompt_mode.prompt_guard_reason(
        "Set the offset to 3 for this run.", "Set the offset to -3 for this run."
    ) == "dropped_detail"


async def test_slow_primary_leaves_time_for_another_family(monkeypatch):
    raw = "please send the report to the team tomorrow"
    answer = "Please send the report to the team tomorrow."
    slow = Reply(answer, delay=10)
    fast = Reply(answer)
    families = polish_client.POLISH_FAMILIES[:2]
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: families)
    monkeypatch.setattr(
        polish, "build_polish_client", lambda family, **kw: slow if family == families[0] else fast
    )
    result = await polish.polish_transcript(
        raw, language="en", cfg=SimpleNamespace(polish=True), timeout_s=1.2
    )
    assert result.status == "applied"
    assert result.provider == families[1].id
    assert slow.cancelled


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
async def test_incomplete_openai_text_is_never_delivered(monkeypatch, finish_reason):
    def respond(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "This is a complete sentence but the rest is missing."
                        },
                        "finish_reason": finish_reason,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(polish_client, "_HTTP", SimpleNamespace(get=lambda: http))
        client = polish_client.OpenAIChatPolishClient(
            polish_client.POLISH_FAMILIES[0], model="test", api_key=None
        )
        with pytest.raises(polish_client.PolishProviderError):
            await client.complete(
                "system", "user", max_output_tokens=64, temperature=0, timeout_s=1
            )


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY", "RECITATION"])
async def test_incomplete_gemini_text_is_never_delivered(monkeypatch, reason):
    async def generate_content(**kwargs):
        return SimpleNamespace(
            text="Only the first sentence survived.",
            candidates=[
                SimpleNamespace(finish_reason=SimpleNamespace(name=reason)),
            ],
        )

    family = next(f for f in polish_client.POLISH_FAMILIES if f.transport == "gemini")
    client = polish_client.GeminiPolishClient(family, model="test", api_key="test")
    client._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    with pytest.raises(polish_client.PolishProviderError):
        await client.complete("system", "user", max_output_tokens=64, temperature=0, timeout_s=1)


async def test_provider_errors_do_not_expose_transcript_or_credentials(monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="private transcript and secret credential"),
        )
    ) as http:
        monkeypatch.setattr(polish_client, "_HTTP", SimpleNamespace(get=lambda: http))
        client = polish_client.OpenAIChatPolishClient(
            polish_client.POLISH_FAMILIES[0], model="test", api_key=None
        )
        with pytest.raises(polish_client.PolishProviderError) as caught:
            await client.complete(
                "system", "user", max_output_tokens=64, temperature=0, timeout_s=1
            )
        assert "private transcript" not in str(caught.value)
        assert "secret credential" not in str(caught.value)
