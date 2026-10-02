"""Streaming chat parsing against a scripted loopback Ollama."""

from __future__ import annotations

from jarvis.voice_engine.llm import OllamaChat
from tests.fakes.fake_ollama_chat_stream import FakeOllamaChatStream


def _done(**counters: int) -> dict:
    return {"message": {"content": ""}, "done": True, **counters}


def test_text_clauses_and_counters() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script([
            {"message": {"content": "The moon is about "}, "done": False},
            {"message": {"content": "384,000 km away. "}, "done": False},
            {"message": {"content": "That is far."}, "done": False},
            _done(prompt_eval_count=120, prompt_eval_cached_count=100,
                  prompt_eval_duration=50_000_000, eval_count=20, eval_duration=200_000_000),
        ])
        result = OllamaChat("m", base_url=fake.base_url).chat([{"role": "user", "content": "?"}])
        body = fake.requests[0]["body"]
    assert result.error == ""
    assert result.clauses == ["The moon is about 384,000 km away.", "That is far."]
    assert result.prompt_tokens == 120 and result.prompt_cached_tokens == 100
    assert result.generation_tps == 100.0
    assert result.t_first_token is not None and result.t_first_clause is not None
    assert body["think"] is False and body["options"]["num_ctx"] == 8192
    assert body["stream"] is True


def test_tool_calls_are_collected() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script([
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "set_timer", "arguments": {"minutes": 10}}}]}, "done": False},
            _done(),
        ])
        result = OllamaChat("m", base_url=fake.base_url).chat(
            [{"role": "user", "content": "timer"}], tools=[{"type": "function"}]
        )
    assert result.tool_calls == [{"name": "set_timer", "arguments": {"minutes": 10}}]
    assert result.text == ""


def test_models_without_a_thinking_switch_are_retried_without_it() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script_error(400, '{"error":"\\"m\\" does not support thinking"}')
        fake.script([{"message": {"content": "Hi."}, "done": False}, _done()])
        chat = OllamaChat("m", base_url=fake.base_url)
        result = chat.chat([{"role": "user", "content": "hi"}])
        second = chat.chat([{"role": "user", "content": "again"}])
        bodies = [r["body"] for r in fake.requests]
    assert result.text == "Hi."
    assert "think" in bodies[0] and "think" not in bodies[1] and "think" not in bodies[2]
    assert second.error == ""


def test_server_errors_are_reported_not_raised() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script_error(500, "boom")
        result = OllamaChat("m", base_url=fake.base_url).chat([{"role": "user", "content": "x"}])
    assert result.error.startswith("HTTP 500")
