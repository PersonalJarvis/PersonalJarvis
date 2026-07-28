"""Offline unit tests for the UltraWiki distillation slot.

The provider chain is faked by monkeypatching ``complete_with_fallback`` /
``build_wiki_provider_chain`` / ``credential_ready_wiki_providers`` at the
distill module's import site — no network, no credentials, no real providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import jarvis.ultrawiki.distill as distill_mod
from jarvis.ultrawiki.distill import (
    PROMPT_VERSION,
    DistillError,
    DistillResult,
    build_distill_prompt,
    distill_cache_key,
    distill_text,
)


@dataclass
class FakeAggregate:
    """Duck-type of the streaming aggregate the chain hands back."""

    text: str


class FakeRegistry:
    def available(self) -> list[str]:
        return ["gemini", "openai"]


def _cfg(distill_provider: str = "", distill_model: str = "", primary: str = "gemini"):
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            distill_provider=distill_provider, distill_model=distill_model
        ),
        brain=SimpleNamespace(primary=primary),
    )


@pytest.fixture
def chain_env(monkeypatch):
    """Fake the whole provider-chain surface; capture what distill passes in."""
    captured: dict = {}

    monkeypatch.setattr(
        distill_mod,
        "credential_ready_wiki_providers",
        lambda *, available, config: set(available),
    )

    def fake_build_chain(*, primary, model_override, available, credential_ready=None):
        captured["primary"] = primary
        captured["model_override"] = model_override
        captured["available"] = set(available)
        return [(primary or "gemini", model_override or "cheap-default")]

    monkeypatch.setattr(distill_mod, "build_wiki_provider_chain", fake_build_chain)

    def script(result_text: str | None):
        async def fake_complete(**kwargs):
            captured.update(kwargs)
            if result_text is None:
                return None
            return FakeAggregate(text=result_text), "gemini"

        monkeypatch.setattr(distill_mod, "complete_with_fallback", fake_complete)

    captured["script"] = script
    return captured


GOOD_JSON = (
    '{"question":"When was the deploy fixed?",'
    '"summary":"The deploy failed on a missing key and was fixed.",'
    '"resolution":"Rotated the key and redeployed.",'
    '"entities":["Deploy Pipeline","Alice"],'
    '"refs":["https://example.test/run/42"]}'
)


# ----------------------------------------------------------------------
# Happy path + salvage
# ----------------------------------------------------------------------


async def test_distill_happy_path_parses_all_fields(chain_env):
    chain_env["script"](GOOD_JSON)

    result = await distill_text(
        _cfg(), title="Deploy incident", body="the raw log", source_kind="chat",
        registry=FakeRegistry(),
    )

    assert isinstance(result, DistillResult)
    assert result.question == "When was the deploy fixed?"
    assert result.summary.startswith("The deploy failed")
    assert result.resolution == "Rotated the key and redeployed."
    assert result.entities == ["Deploy Pipeline", "Alice"]
    assert result.refs == ["https://example.test/run/42"]
    assert result.raw_json == GOOD_JSON


async def test_distill_salvages_json_inside_markdown_fences(chain_env):
    chain_env["script"](f"Here is the document:\n```json\n{GOOD_JSON}\n```\nDone.")

    result = await distill_text(
        _cfg(), title="t", body="b", source_kind="note", registry=FakeRegistry(),
    )

    assert result.question == "When was the deploy fixed?"


async def test_distill_salvages_json_embedded_in_prose(chain_env):
    chain_env["script"](f"Sure! {GOOD_JSON} Anything else?")

    result = await distill_text(
        _cfg(), title="t", body="b", source_kind="note", registry=FakeRegistry(),
    )

    assert result.summary.startswith("The deploy failed")


async def test_distill_missing_keys_become_empty_values(chain_env):
    chain_env["script"]('{"summary":"only a summary arrived"}')

    result = await distill_text(
        _cfg(), title="t", body="b", source_kind="mail", registry=FakeRegistry(),
    )

    assert result.summary == "only a summary arrived"
    assert result.question == ""
    assert result.resolution == ""
    assert result.entities == []
    assert result.refs == []


async def test_distill_scalar_entities_are_wrapped_into_a_list(chain_env):
    chain_env["script"]('{"summary":"s","entities":"Alice","refs":["", "r1", 42]}')

    result = await distill_text(
        _cfg(), title="t", body="b", source_kind="mail", registry=FakeRegistry(),
    )

    assert result.entities == ["Alice"]
    assert result.refs == ["r1", "42"]  # empty entries drop, scalars stringify


async def test_distill_total_chain_failure_raises_distill_error(chain_env):
    chain_env["script"](None)

    with pytest.raises(DistillError):
        await distill_text(
            _cfg(), title="t", body="b", source_kind="chat", registry=FakeRegistry(),
        )


# ----------------------------------------------------------------------
# Provider resolution
# ----------------------------------------------------------------------


async def test_configured_distill_provider_leads_the_chain(chain_env):
    chain_env["script"](GOOD_JSON)

    await distill_text(
        _cfg(distill_provider="openai", distill_model="gpt-cheap"),
        title="t", body="b", source_kind="chat", registry=FakeRegistry(),
    )

    assert chain_env["primary"] == "openai"
    assert chain_env["model_override"] == "gpt-cheap"


async def test_codex_subscription_card_forces_the_subscription_auth_path(chain_env):
    chain_env["script"](GOOD_JSON)

    await distill_text(
        _cfg(distill_provider="codex", distill_model="gpt-5.6-luna"),
        title="t", body="b", source_kind="chat", registry=FakeRegistry(),
    )

    assert chain_env["provider_options"] == {
        "codex": {"prefer_subscription": True}
    }


async def test_unset_distill_provider_falls_back_to_brain_primary(chain_env):
    chain_env["script"](GOOD_JSON)

    await distill_text(
        _cfg(distill_provider="", distill_model="ignored", primary="claude-api"),
        title="t", body="b", source_kind="chat", registry=FakeRegistry(),
    )

    assert chain_env["primary"] == "claude-api"
    # distill_model belongs to distill_provider; without one it does not pin
    # the generic chain's lead model.
    assert chain_env["model_override"] == ""


async def test_validate_callback_rejects_non_json_and_accepts_json(chain_env):
    chain_env["script"](GOOD_JSON)
    await distill_text(
        _cfg(), title="t", body="b", source_kind="chat", registry=FakeRegistry(),
    )

    validate = chain_env["validate"]
    assert validate(FakeAggregate(text="no json here, sorry")) is not None
    assert validate(FakeAggregate(text=GOOD_JSON)) is None


# ----------------------------------------------------------------------
# Prompt + truncation
# ----------------------------------------------------------------------


def test_prompt_carries_xml_sections_and_strict_json_contract():
    prompt = build_distill_prompt(title="My title", body="body text", source_kind="chat")

    assert "<task>" in prompt
    assert '<source kind="chat">' in prompt
    assert "<title>My title</title>" in prompt
    assert "<output_format>" in prompt
    assert "minified JSON" in prompt
    for key in ('"question"', '"summary"', '"resolution"', '"entities"', '"refs"'):
        assert key in prompt


def test_prompt_truncates_long_bodies_with_a_marker():
    long_body = "x" * 20_000

    prompt = build_distill_prompt(title="t", body=long_body, source_kind="note")

    assert "truncated for distillation" in prompt
    # The body is capped at _BODY_TRUNCATE_CHARS; the rest is the fixed
    # scaffolding (the JSON contract plus the event/time rules of prompt
    # version 2), which is what this headroom covers.
    assert len(prompt) < 12_000


async def test_request_sent_to_chain_carries_the_truncated_prompt(chain_env):
    chain_env["script"](GOOD_JSON)

    await distill_text(
        _cfg(), title="t", body="y" * 20_000, source_kind="chat",
        registry=FakeRegistry(),
    )

    request = chain_env["request"]
    user_content = request.messages[0].content
    assert "truncated for distillation" in user_content
    assert request.system  # the system prompt rides along


# ----------------------------------------------------------------------
# Cache key
# ----------------------------------------------------------------------


def test_cache_key_carries_prompt_version_and_model():
    content_hash, version, model = distill_cache_key(
        title="t", body="b", model="gemini-2.5-flash"
    )

    assert version == PROMPT_VERSION
    assert model == "gemini-2.5-flash"
    assert len(content_hash) == 64  # sha256 hex


def test_cache_key_changes_with_prompt_version(monkeypatch):
    before = distill_cache_key(title="t", body="b", model="m")
    monkeypatch.setattr(distill_mod, "PROMPT_VERSION", PROMPT_VERSION + 1)
    after = distill_mod.distill_cache_key(title="t", body="b", model="m")

    assert before[0] == after[0]  # same content
    assert before[1] != after[1]  # version bump invalidates the cache row


def test_cache_key_is_stable_and_content_sensitive():
    assert distill_cache_key(title="t", body="b", model="m") == distill_cache_key(
        title="t", body="b", model="m"
    )
    assert distill_cache_key(title="t", body="b", model="m") != distill_cache_key(
        title="t", body="other", model="m"
    )
