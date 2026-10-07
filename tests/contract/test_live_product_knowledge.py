"""A live voice model knows what Personal Jarvis is and can read its guide."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.live.config import LiveConfig
from jarvis.live.product import PRODUCT_BRIEF
from jarvis.plugins.tool import product_help
from jarvis.plugins.tool.product_help import ProductHelpTool


@pytest.fixture(autouse=True)
def _no_web_app(monkeypatch):
    # Without a running server the tool builds its own registry over docs/product.
    monkeypatch.setattr(runtime_refs, "get_web_app", lambda: None)
    monkeypatch.setattr(product_help, "_FALLBACK_REGISTRY", [])


def test_both_live_models_carry_the_product_brief():
    wire = LiveConfig(configured=True, backend_model="m").session_config(language="de", tools=[])
    assert PRODUCT_BRIEF in wire["instructions"]
    assert PRODUCT_BRIEF in wire["delegation"]["responses"]["instructions"]
    assert "Agentic IDE" in PRODUCT_BRIEF and "product_help" in PRODUCT_BRIEF
    # Small enough to ride along in every call without a real cost.
    assert len(PRODUCT_BRIEF) < 2600


def test_voice_catalog_offers_product_help_but_workers_do_not_get_it():
    manager = SimpleNamespace(_tools={}, _config=SimpleNamespace(computer_use=None))
    gateway = BrainSupervisorToolGateway(manager)
    assert "product_help" in {d.name for d in gateway.voice_catalog()}
    assert "product_help" not in {d.name for d in gateway.catalog()}


async def test_spoken_question_finds_the_matching_guide():
    result = await ProductHelpTool().execute({"query": "wake word microphone not working"}, None)
    assert result.success
    assert result.output["matches"]
    assert result.output["guide"]["text"]
    assert "<mark>" not in result.output["matches"][0]["excerpt"]


async def test_slug_reads_one_whole_guide():
    result = await ProductHelpTool().execute({"slug": "welcome-to-personal-jarvis"}, None)
    assert result.success
    assert result.output["guide"]["title"] == "Welcome to Personal Jarvis"


async def test_unknown_topic_returns_the_table_of_contents():
    result = await ProductHelpTool().execute({"query": "zzqx"}, None)
    assert result.success
    assert result.output["matches"] == []
    slugs = {row["slug"] for row in result.output["available_guides"]}
    assert "welcome-to-personal-jarvis" in slugs


async def test_empty_arguments_are_refused():
    result = await ProductHelpTool().execute({}, None)
    assert not result.success
