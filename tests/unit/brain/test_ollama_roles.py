"""Roles: the four Ollama slots read from the config, judged against the
installed downloads, pointed at the shortlist's pick, and written through
the existing config writers only.
"""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_pull, ollama_roles
from jarvis.core import config_writer
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://localhost:11434"


def _cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model="qwen3.5:4b", deep_model="gemma4:12b-it-qat"
    )
    cfg.ultrawiki.embedding_provider = "ollama"
    cfg.ultrawiki.embedding_model = "qwen3-embedding:4b"
    cfg.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command=(
            "python -m server --model_name qwen3.5:4b-voice-8k "
            "--responses_api_base_url http://127.0.0.1:11434/v1"
        )
    )
    return cfg


@pytest.fixture
def fake() -> FakeOllamaServer:
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", capabilities=("completion", "tools", "vision"))
    server.add("gemma4:12b-it-qat", capabilities=("completion", "tools", "thinking"))
    server.add("qwen3-embedding:4b", capabilities=("embedding",))
    server.add("broken:1b", show_fails=True)
    return server


@pytest.fixture
def shortlist(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows = [
        {
            "id": "qwen3.8:27b",
            "role": "chat",
            "vision": True,
            "size_gb": 18.0,
            "installed": False,
            "recommended": True,
            "recommended_for": ["chat", "vision"],
        },
        {
            "id": "ornith:9b",
            "role": "coder",
            "vision": False,
            "size_gb": 5.6,
            "installed": False,
            "recommended": True,
            "recommended_for": ["coder"],
        },
        {
            "id": "qwen3-embedding:4b",
            "role": "embedding",
            "vision": False,
            "size_gb": 2.5,
            "installed": True,
            "recommended": False,
            "recommended_for": [],
        },
        {
            "id": "embeddinggemma",
            "role": "embedding",
            "vision": False,
            "size_gb": 0.3,
            "installed": True,
            "recommended": False,
            "recommended_for": [],
        },
    ]

    async def _recommendations() -> dict:
        return {"models": rows}

    monkeypatch.setattr(ollama_pull, "recommendations", _recommendations)
    return rows


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        config_writer,
        "set_brain_provider_model",
        lambda provider, **kw: calls.append(("brain", provider, kw)),
    )
    monkeypatch.setattr(
        config_writer,
        "set_ultrawiki_slot",
        lambda key, value: calls.append(("ultrawiki", key, value)),
    )
    return calls


def test_the_five_writable_roles_come_first_in_order() -> None:
    assert ollama_roles.WRITABLE_ROLE_IDS == ("chat", "voice", "tools_screen", "deep", "embedding")
    assert [r.id for r in ollama_roles.ROLES][5:] == ["ack", "polish"]
    assert all(r.advanced and not r.writable for r in ollama_roles.ROLES[5:])
    # Voice is its own slot, never a mirror of the chat pick.
    voice = ollama_roles.role_spec("voice")
    assert voice.writable and not voice.advanced
    assert voice.config_key != ollama_roles.role_spec("chat").config_key


def test_current_pick_reads_every_slot() -> None:
    cfg = _cfg()
    cfg.brain.providers["ollama"].cu_model = "legacy:1b"
    assert ollama_roles.current_pick(cfg, "chat") == ("qwen3.5:4b", "")
    # Canonical tool_model wins; the legacy cu_model is the fallback.
    assert ollama_roles.current_pick(cfg, "tools_screen") == ("legacy:1b", "")
    cfg.brain.providers["ollama"].tool_model = "qwen3.5:4b"
    assert ollama_roles.current_pick(cfg, "tools_screen") == ("qwen3.5:4b", "")
    assert ollama_roles.current_pick(cfg, "deep") == ("gemma4:12b-it-qat", "")
    assert ollama_roles.current_pick(cfg, "embedding") == ("qwen3-embedding:4b", "")
    # The voice brain is the launch command's model, alias folded to the base tag.
    assert ollama_roles.current_pick(cfg, "voice") == ("qwen3.5:4b", "")
    del cfg.brain.providers["local-realtime"]
    tag, note = ollama_roles.current_pick(cfg, "voice")
    assert tag == "" and "not installed" in note
    assert ollama_roles.current_pick(cfg, "ack")[0] == cfg.ack_brain.providers.ollama.model
    assert ollama_roles.current_pick(None, "chat") == ("", "")


def test_a_slot_served_elsewhere_says_so_instead_of_showing_a_foreign_tag() -> None:
    cfg = _cfg()
    cfg.ultrawiki.embedding_provider = "openai"
    cfg.ultrawiki.embedding_model = "text-embedding-3-small"
    current, note = ollama_roles.current_pick(cfg, "embedding")
    assert current == ""
    assert "openai" in note


@pytest.mark.asyncio
async def test_list_roles_judges_qualifying_models_by_capability(fake, shortlist) -> None:
    states, error = await ollama_roles.list_roles(ROOT, _cfg(), transport=fake.transport())
    assert error is None
    by_id = {s.spec.id: s for s in states}
    assert by_id["chat"].current == "qwen3.5:4b"
    assert by_id["chat"].installed is True
    # Unprobed rows never qualify; the embedder has no completion capability.
    assert by_id["chat"].qualifying == ("qwen3.5:4b", "gemma4:12b-it-qat")
    assert by_id["tools_screen"].qualifying == ("qwen3.5:4b",)
    assert by_id["tools_screen"].current == ""
    assert by_id["deep"].qualifying == ("qwen3.5:4b", "gemma4:12b-it-qat")
    assert by_id["embedding"].qualifying == ("qwen3-embedding:4b",)


@pytest.mark.asyncio
async def test_list_roles_points_at_the_shortlists_pick_or_the_largest_installed_one(
    fake, shortlist
) -> None:
    states, _ = await ollama_roles.list_roles(ROOT, _cfg(), transport=fake.transport())
    by_id = {s.spec.id: s for s in states}
    assert by_id["chat"].recommended == "qwen3.8:27b"
    # The multimodal chat pick serves the screen role too.
    assert by_id["tools_screen"].recommended == "qwen3.8:27b"
    assert by_id["deep"].recommended == "ornith:9b"
    # The picker marks nothing once the role has a curated model installed;
    # the largest installed one is named so the button still has an answer.
    assert by_id["embedding"].recommended == "qwen3-embedding:4b"


@pytest.mark.asyncio
async def test_list_roles_keeps_the_rows_when_the_server_is_offline(fake, shortlist) -> None:
    fake.offline = True
    states, error = await ollama_roles.list_roles(ROOT, _cfg(), transport=fake.transport())
    assert error and "did not answer" in error
    assert len(states) == len(ollama_roles.ROLES)
    assert all(s.qualifying == () and s.installed is False for s in states)
    assert states[0].current == "qwen3.5:4b"


@pytest.mark.asyncio
async def test_list_roles_survives_a_failing_shortlist(fake, monkeypatch) -> None:
    async def _boom() -> dict:
        raise RuntimeError("registry down")

    monkeypatch.setattr(ollama_pull, "recommendations", _boom)
    states, error = await ollama_roles.list_roles(ROOT, _cfg(), transport=fake.transport())
    assert error is None
    assert all(s.recommended == "" for s in states)


def test_set_role_writes_through_the_provider_card_writers(writes) -> None:
    cfg = _cfg()
    out = ollama_roles.set_role("chat", "gemma4:12b-it-qat", cfg=cfg)
    ollama_roles.set_role("tools_screen", "qwen3.5:4b", cfg=cfg)
    ollama_roles.set_role("deep", "", cfg=cfg)
    assert out == {
        "role": "chat",
        "model": "gemma4:12b-it-qat",
        "config_key": "brain.providers.ollama.model",
    }
    assert writes == [
        ("brain", "ollama", {"model": "gemma4:12b-it-qat"}),
        ("brain", "ollama", {"tool_model": "qwen3.5:4b", "cu_model": "qwen3.5:4b"}),
        ("brain", "ollama", {"deep_model": ""}),
    ]
    provider = cfg.brain.providers["ollama"]
    assert provider.model == "gemma4:12b-it-qat"
    assert provider.tool_model == "qwen3.5:4b"
    assert provider.cu_model == "qwen3.5:4b"
    assert provider.deep_model == ""


def test_set_role_embedding_switches_the_wiki_to_ollama_when_needed(writes) -> None:
    cfg = _cfg()
    cfg.ultrawiki.embedding_provider = "openai"
    ollama_roles.set_role("embedding", "embeddinggemma", cfg=cfg)
    assert writes == [
        ("ultrawiki", "embedding_provider", "ollama"),
        ("ultrawiki", "embedding_model", "embeddinggemma"),
    ]
    assert cfg.ultrawiki.embedding_provider == "ollama"
    assert cfg.ultrawiki.embedding_model == "embeddinggemma"
    with pytest.raises(ValueError):
        ollama_roles.set_role("embedding", "", cfg=cfg)


def test_set_role_voice_rewrites_only_the_launch_command_model(
    writes, monkeypatch: pytest.MonkeyPatch
) -> None:
    rewrites: list[str] = []
    monkeypatch.setattr(
        config_writer,
        "update_local_realtime_launch_model",
        lambda model, **kw: rewrites.append(model) or True,
    )
    cfg = _cfg()
    out = ollama_roles.set_role("voice", "gemma4:12b-it-qat", cfg=cfg)
    assert out["config_key"] == "brain.providers.local-realtime.launch_command"
    assert rewrites == ["gemma4:12b-it-qat"]
    assert "--model_name gemma4:12b-it-qat " in cfg.brain.providers["local-realtime"].launch_command
    assert writes == []  # the chat slot is untouched
    with pytest.raises(ValueError, match="needs a model name"):
        ollama_roles.set_role("voice", "", cfg=cfg)
    del cfg.brain.providers["local-realtime"]
    with pytest.raises(ValueError, match="Install the managed voice server"):
        ollama_roles.set_role("voice", "x", cfg=cfg)
    assert rewrites == ["gemma4:12b-it-qat"]


def test_set_role_refuses_read_only_and_unknown_roles(writes) -> None:
    with pytest.raises(ValueError, match="own card"):
        ollama_roles.set_role("ack", "x", cfg=_cfg())
    with pytest.raises(ValueError, match="Unknown role"):
        ollama_roles.set_role("nope", "x", cfg=_cfg())
    assert writes == []


def test_roles_using_is_latest_tolerant() -> None:
    cfg = _cfg()
    # The fixture's voice server runs the same base tag, so both slots answer.
    assert ollama_roles.roles_using(cfg, "qwen3.5:4b") == ["chat", "voice"]
    cfg.brain.providers["ollama"].model = "gemma4"
    assert ollama_roles.roles_using(cfg, "gemma4:latest") == ["chat"]
    assert ollama_roles.roles_using(cfg, "qwen3-embedding:4b") == ["embedding"]
