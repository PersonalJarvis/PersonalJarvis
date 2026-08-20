"""A finished LOOKUP is never reported to the user as a finished JOB.

Live forensic 2026-08-20 19:32:41 (session 6a82cdf9, vertex-live). The
maintainer asked a question about Y Combinator. ``search_web`` ran, succeeded
and came back with hits. The live model then closed its generation without
speaking — the ordinary Gemini tool-call boundary — and the session's
direct-tool recovery built its spoken line, found no ``spoken_reply`` on the
search result, and fell through to ``cu_done``: **"Erledigt."**

Two lies in nine characters. Nothing was "erledigt": the maintainer never gave
a task, he asked a question. And the answer he did ask for was sitting in the
payload, unspoken, while the model's own rendering of it arrived two seconds
later and was withheld as a duplicate.

Contract:

* A tool that ACTED and reported no words keeps ``cu_done`` — that is what the
  phrase is for, and a lookup-shaped NAME on a contentless receipt does not
  change that (``gmail`` sends mail as well as listing it).
* A lookup that came back with CONTENT owes the user that content: the facts go
  to the honesty-bound composer, and the floor under it says why the answer is
  missing — never that something was completed.
* A lookup that came back EMPTY says so. "I found nothing" is the honest answer
  to a question; "Erledigt." reports a job.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.realtime.session import RealtimeVoiceSession, _lookup_facts
from jarvis.voice.action_phrases import action_phrase

SEARCH_HITS = [
    {
        "title": "Aqua Security",
        "snippet": "Cloud native security company founded in 2015.",
        "url": "https://example.invalid/aqua",
    },
    {
        "title": "Y Combinator",
        "snippet": "Startup accelerator based in California.",
        "url": "https://example.invalid/yc",
    },
]


class FakeTransport:
    creates_responses_automatically = False

    async def send_text(self, text):
        del text

    async def send_tool_result(self, call_id, name, result):
        del call_id, name, result

    async def request_response(self, *, required_tool=None):
        del required_tool

    async def interrupt(self):
        pass

    async def close(self):
        pass


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        del cfg
        return FakeTransport()


class AnsweringComposer:
    """A live composer that rephrases the facts it was handed."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls: list[dict] = []

    async def compose(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


def _build(composer=None):
    brain = SimpleNamespace(_readback_composer=composer) if composer else None
    sess = RealtimeVoiceSession(
        session_id="lookup-done",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=FakeProvider(),
        config=_cfg(),
        bus=None,
        brain=brain,
    )
    sess._session = FakeTransport()
    return sess


def _search_result(rows):
    return {
        "success": True,
        "output": {
            "query": "Aqua Security Y Combinator",
            "results": rows,
            "backend": "duckduckgo",
            "status": "ok",
            **({"answer_instruction": "Answer from these hits."} if rows else {}),
        },
    }


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_search_is_never_spoken_as_erledigt() -> None:
    sess = _build()
    sess._direct_tool_results.append(("search_web", _search_result(SEARCH_HITS)))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text != action_phrase("cu_done", sess._language)
    assert text == action_phrase("lookup_unspoken", sess._language)


@pytest.mark.asyncio
async def test_the_retrieved_answer_is_spoken_when_a_composer_is_live() -> None:
    composer = AnsweringComposer("Aqua Security is a cloud native security firm.")
    sess = _build(composer)
    sess._direct_tool_results.append(("search_web", _search_result(SEARCH_HITS)))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == "Aqua Security is a cloud native security firm."
    # The composer may only rephrase what the tool returned.
    assert composer.calls[0]["honesty_bound"] is True
    retrieved = composer.calls[0]["facts"]["retrieved"]
    assert any("Aqua Security" in item for item in retrieved)


@pytest.mark.asyncio
async def test_an_empty_search_says_so_instead_of_reporting_a_job_done() -> None:
    sess = _build()
    sess._direct_tool_results.append(("search_web", _search_result([])))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == action_phrase("lookup_empty", sess._language)


# ---------------------------------------------------------------------------
# What must NOT change: an action that ran still reports "done"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wordless_action_still_reports_done() -> None:
    sess = _build()
    sess._direct_tool_results.append(("open_app", {"success": True, "output": {}}))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == action_phrase("cu_done", sess._language)


@pytest.mark.asyncio
async def test_a_lookup_shaped_name_on_a_bare_receipt_still_reports_done() -> None:
    """``gmail`` sends mail as well as listing it — the payload decides."""
    sess = _build()
    sess._direct_tool_results.append(("gmail", {"success": True, "output": {}}))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == action_phrase("cu_done", sess._language)


@pytest.mark.asyncio
async def test_a_turn_that_also_acted_still_owes_the_answer() -> None:
    sess = _build()
    sess._direct_tool_results.append(("open_app", {"success": True, "output": {}}))
    sess._direct_tool_results.append(("search_web", _search_result(SEARCH_HITS)))

    text, _succeeded = await sess._direct_tool_fallback_text()

    assert text != action_phrase("cu_done", sess._language)


# ---------------------------------------------------------------------------
# _lookup_facts — the ground truth handed to the composer
# ---------------------------------------------------------------------------


def test_facts_carry_the_content_and_never_the_urls() -> None:
    facts = _lookup_facts([("search_web", _search_result(SEARCH_HITS))])

    joined = " ".join(facts["retrieved"])
    assert "Cloud native security company" in joined
    assert "example.invalid" not in joined


def test_an_unknown_payload_shape_yields_no_facts() -> None:
    """A raw ``str(dict)`` dump is exactly what the output filter exists to stop."""
    facts = _lookup_facts([
        ("mcp_thing", {"success": True, "output": {"rows": [{"a": 1}]}}),
    ])

    assert facts == {}


def test_a_plain_string_payload_is_read() -> None:
    facts = _lookup_facts([
        ("wiki_read", {"success": True, "output": "The meeting is on Tuesday."}),
    ])

    assert facts == {"retrieved": ["The meeting is on Tuesday."]}


def test_the_fact_list_is_bounded() -> None:
    rows = [
        {"title": f"Hit {index}", "snippet": "x" * 900, "url": ""}
        for index in range(20)
    ]
    facts = _lookup_facts([("search_web", _search_result(rows))])

    assert len(facts["retrieved"]) <= 5
    assert all(len(item) <= 320 for item in facts["retrieved"])
