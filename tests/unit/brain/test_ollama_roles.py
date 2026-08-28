"""Roles: the four Ollama slots read from the config, judged against the
installed downloads, recommended installed-first (the shortlist only when
nothing installed qualifies), and written through the existing config
writers only.
"""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_inventory, ollama_pull, ollama_roles
from jarvis.core import config_writer
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://localhost:11434"


def _cfg() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["ollama"] = BrainProviderConfig(
        model="qwen3.5:4b", deep_model="gemma4:12b-it-qat"
    )
    cfg.brain.providers["local-realtime"] = BrainProviderConfig(
        launch_command=(
            "python -m server --model_name qwen3.5:4b-voice-8k "
            "--responses_api_base_url http://127.0.0.1:11434/v1"
        )
    )
    return cfg


@pytest.fixture
def fake() -> FakeOllamaServer:
    ollama_inventory._reset_for_tests()
    server = FakeOllamaServer()
    server.add("qwen3.5:4b", size=3_400_000_000, capabilities=("completion", "tools", "vision"))
    server.add(
        "gemma4:12b-it-qat",
        size=7_200_000_000,
        capabilities=("completion", "tools", "thinking"),
    )
    server.add("qwen3-embedding:4b", size=2_500_000_000, capabilities=("embedding",))
    server.add("broken:1b", show_fails=True)
    return server


def _info(
    name: str, size_gb: float, *caps: str, probed: bool = True
) -> ollama_inventory.OllamaModelInfo:
    return ollama_inventory.OllamaModelInfo(
        name=name,
        size_bytes=int(size_gb * 1024**3),
        digest="",
        modified_at="",
        family="",
        parameter_size="",
        quantization_level="",
        context_length=None,
        capabilities=tuple(caps),
        license="",
        probed=probed,
    )


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
    return calls


def test_the_four_writable_roles_come_first_in_order() -> None:
    assert ollama_roles.WRITABLE_ROLE_IDS == ("chat", "voice", "tools_screen", "deep")
    assert [r.id for r in ollama_roles.ROLES][4:] == ["ack", "polish"]
    assert all(r.advanced and not r.writable for r in ollama_roles.ROLES[4:])
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
    # The voice brain is the launch command's model, alias folded to the base tag.
    assert ollama_roles.current_pick(cfg, "voice") == ("qwen3.5:4b", "")
    del cfg.brain.providers["local-realtime"]
    tag, note = ollama_roles.current_pick(cfg, "voice")
    assert tag == "" and "not installed" in note
    assert ollama_roles.current_pick(cfg, "ack")[0] == cfg.ack_brain.providers.ollama.model
    assert ollama_roles.current_pick(None, "chat") == ("", "")


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


@pytest.mark.asyncio
async def test_list_roles_recommends_installed_models_before_the_shortlist(
    fake, shortlist
) -> None:
    """Eleven models on disk must not end in "download qwen3.8:27b"."""
    states, _ = await ollama_roles.list_roles(ROOT, _cfg(), transport=fake.transport())
    by_id = {s.spec.id: s for s in states}
    # The shortlist answered no memory figures, so every fit is "unknown"
    # and the largest installed model with the preferred capability wins.
    assert by_id["chat"].recommended == "gemma4:12b-it-qat"
    assert "installed" in by_id["chat"].recommended_reason
    # Screen reading needs vision: only the 4B declares it.
    assert by_id["tools_screen"].recommended == "qwen3.5:4b"
    # Deep work prefers thinking; the 12B declares it.
    assert by_id["deep"].recommended == "gemma4:12b-it-qat"
    # A call wants the fast class: the 12B is over the voice size cap.
    assert by_id["voice"].recommended == "qwen3.5:4b"
    assert "fast" in by_id["voice"].recommended_reason
    # The shortlist's downloads are never named while something installed qualifies.
    assert all(s.recommended != "qwen3.8:27b" for s in states)


@pytest.mark.asyncio
async def test_list_roles_judges_installed_picks_against_this_machines_memory(
    fake, shortlist
) -> None:
    machine = ollama_roles.Machine(memory_gb=32.0, accelerator_gb=8.0)
    states, _ = await ollama_roles.list_roles(
        ROOT, _cfg(), transport=fake.transport(), machine=machine
    )
    by_id = {s.spec.id: s for s in states}
    # 7.2 GB + overhead is over the 8 GB card (tight); 3.4 GB fits — it wins
    # although the 12B is bigger and thinks.
    assert by_id["chat"].recommended == "qwen3.5:4b"
    assert "8 GB of graphics memory" in by_id["chat"].recommended_reason
    assert by_id["deep"].recommended == "qwen3.5:4b"


@pytest.mark.asyncio
async def test_list_roles_falls_back_to_the_shortlist_when_nothing_installed_qualifies(
    shortlist,
) -> None:
    ollama_inventory._reset_for_tests()
    server = FakeOllamaServer()
    server.add("qwen3-embedding:4b", capabilities=("embedding",))
    states, _ = await ollama_roles.list_roles(ROOT, _cfg(), transport=server.transport())
    by_id = {s.spec.id: s for s in states}
    assert by_id["chat"].recommended == "qwen3.8:27b"
    assert "download" in by_id["chat"].recommended_reason
    assert by_id["deep"].recommended == "ornith:9b"


def test_pick_installed_prefers_the_roles_capability_over_raw_size() -> None:
    chat = ollama_roles.role_spec("chat")
    machine = ollama_roles.Machine(memory_gb=64.0, accelerator_gb=24.0)
    models = [
        _info("big-no-tools:14b", 9.0, "completion"),
        _info("mid-tools:12b", 7.2, "completion", "tools"),
        _info("small-tools:4b", 3.4, "completion", "tools"),
        _info("unprobed:30b", 18.0, probed=False),
    ]
    tag, reason = ollama_roles.pick_installed(chat, models, machine)
    assert tag == "mid-tools:12b"
    assert reason == "Largest installed model with tools that fits in the 24 GB of graphics memory."


def test_pick_installed_names_the_smallest_when_everything_is_tight() -> None:
    chat = ollama_roles.role_spec("chat")
    machine = ollama_roles.Machine(memory_gb=16.0, accelerator_gb=4.0)
    models = [
        _info("a:14b", 9.0, "completion", "tools"),
        _info("b:8b", 5.0, "completion", "tools"),
    ]
    tag, reason = ollama_roles.pick_installed(chat, models, machine)
    assert tag == "b:8b"
    assert "tight" in reason


def test_pick_installed_keeps_the_voice_pick_in_the_fast_class() -> None:
    voice = ollama_roles.role_spec("voice")
    machine = ollama_roles.Machine(memory_gb=64.0, accelerator_gb=24.0)
    models = [
        _info("deep:27b", 17.0, "completion", "tools"),
        _info("mid:9b", 5.6, "completion", "tools"),
        _info("tiny:1b", 0.8, "completion"),
    ]
    tag, reason = ollama_roles.pick_installed(voice, models, machine)
    assert tag == "mid:9b"
    assert "under 6 GB" in reason
    # Nothing under the cap: the smallest one, with the reason saying so.
    tag, reason = ollama_roles.pick_installed(voice, [models[0]], machine)
    assert tag == "deep:27b" and "nothing under 6 GB" in reason
    assert ollama_roles.pick_installed(voice, [], machine) == ("", "")


def test_machine_from_reads_the_shortlists_figures() -> None:
    assert ollama_roles.machine_from({"memory_gb": 32, "accelerator_gb": 15.9}) == (
        ollama_roles.Machine(memory_gb=32.0, accelerator_gb=15.9)
    )
    assert ollama_roles.machine_from({"models": []}) == ollama_roles.Machine()
    assert ollama_roles.machine_from(None) == ollama_roles.Machine()


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
    # The installed picks do not need the registry; only a download would.
    by_id = {s.spec.id: s for s in states}
    assert by_id["chat"].recommended == "gemma4:12b-it-qat"
    assert all("download" not in s.recommended_reason for s in states)


def test_set_role_writes_through_the_provider_card_writers(writes) -> None:
    cfg = _cfg()
    out = ollama_roles.set_role("chat", "gemma4:12b-it-qat", cfg=cfg)
    ollama_roles.set_role("tools_screen", "qwen3.5:4b", cfg=cfg)
    ollama_roles.set_role("deep", "", cfg=cfg)
    assert out == {
        "role": "chat",
        "model": "gemma4:12b-it-qat",
        "config_key": "brain.providers.ollama.model",
        "drift_guarded": True,
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


def test_set_role_reports_when_the_drift_baseline_did_not_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer's receipt decides the flag: baseline_ok=False -> drift_guarded=False."""
    receipts: list[config_writer.WriteReceipt] = []

    def _writer(provider: str, **kw) -> config_writer.WriteReceipt:
        receipt = config_writer.WriteReceipt(
            toml_ok=True,
            baseline_ok=(kw.get("model") != "gemma4:12b-it-qat"),
            baseline_path="baseline",
        )
        receipts.append(receipt)
        return receipt

    monkeypatch.setattr(config_writer, "set_brain_provider_model", _writer)
    cfg = _cfg()
    assert ollama_roles.set_role("chat", "gemma4:12b-it-qat", cfg=cfg)["drift_guarded"] is False
    assert ollama_roles.set_role("chat", "qwen3.5:4b", cfg=cfg)["drift_guarded"] is True
    assert ollama_roles.set_role("deep", "", cfg=cfg)["drift_guarded"] is True
    assert len(receipts) == 3
    # The in-memory config still takes the pick: the TOML write itself landed.
    assert cfg.brain.providers["ollama"].model == "qwen3.5:4b"


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


# -- Idle release + the user's own choice --


@pytest.mark.asyncio
async def test_voice_context_reports_where_the_size_came_from(monkeypatch) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(supervisor, "_voice_context_override", lambda model: None)
    monkeypatch.setattr(
        supervisor,
        "voice_brain_context_tokens",
        lambda root, model, timeout, override: (65_536, "auto"),
    )
    assert await ollama_roles.voice_context(_cfg(), "qwen3.5:4b") == (65_536, "automatic")
    monkeypatch.setattr(supervisor, "_voice_context_override", lambda model: 16_384)
    assert (await ollama_roles.voice_context(_cfg(), "qwen3.5:4b"))[1] == "manual"
    # No launch command -> no root -> no guess.
    assert await ollama_roles.voice_context(JarvisConfig(), "qwen3.5:4b") == (None, "")
