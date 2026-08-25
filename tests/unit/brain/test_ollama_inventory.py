"""The native Ollama inventory, driven through the in-process fake server.

What would lie if it broke: a download shown without its facts, an alias of
Jarvis's own making shown as a user's model (or counted twice on disk), a
broken manifest blanking the whole table, an unload that quietly did
nothing, a delete that reported success for a name the server never had.
"""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_inventory as inv
from tests.fakes.fake_ollama_server import FakeOllamaServer

ROOT = "http://localhost:11434"


@pytest.fixture
def fake() -> FakeOllamaServer:
    inv._reset_for_tests()
    server = FakeOllamaServer()
    server.add(
        "qwen3.5:4b",
        size=3_400_000_000,
        modified_at="2026-08-20T10:00:00Z",
        capabilities=("completion", "tools", "vision", "thinking"),
        context_length=262_144,
    )
    server.add(
        "qwen3-embedding:4b",
        size=2_500_000_000,
        modified_at="2026-08-22T10:00:00Z",
        family="qwen3",
        capabilities=("embedding",),
        context_length=40_960,
    )
    return server


# ── Alias hiding ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("name", "hidden"),
    [
        ("qwen3.5-4b-jarvis-ab12cd34", True),
        ("qwen3.5-4b-jarvis-ab12cd34:latest", True),
        ("qwen3.5:4b-voice-8k", True),
        ("qwen3.5:4b", False),
        ("gemma4:12b-it-qat", False),
        # Eight hex characters exactly — a user tag that merely contains
        # "jarvis" is not ours.
        ("my-jarvis-model:latest", False),
        ("", False),
    ],
)
def test_hidden_aliases_are_recognised_by_shape_only(name: str, hidden: bool) -> None:
    assert inv.is_hidden_alias(name) is hidden


def test_same_model_treats_latest_as_the_bare_name() -> None:
    assert inv.same_model("qwen3.5", "qwen3.5:latest")
    assert not inv.same_model("qwen3.5", "qwen3.5:4b")
    assert not inv.same_model("", "qwen3.5")


# ── list_models ──────────────────────────────────────────────────────────
async def test_list_models_joins_tags_with_show_facts(fake: FakeOllamaServer) -> None:
    models = await inv.list_models(ROOT, transport=fake.transport())
    by_name = {m.name: m for m in models}
    chat = by_name["qwen3.5:4b"]
    assert chat.size_bytes == 3_400_000_000
    assert chat.capabilities == ("completion", "tools", "vision", "thinking")
    assert chat.context_length == 262_144
    assert chat.quantization_level == "Q4_K_M"
    assert chat.parameter_size == "4B"
    assert chat.license == "Apache-2.0"
    assert chat.probed is True
    # Newest first — the user looks for what they just pulled.
    assert [m.name for m in models] == ["qwen3-embedding:4b", "qwen3.5:4b"]


async def test_list_models_hides_jarvis_aliases_unless_asked(fake: FakeOllamaServer) -> None:
    fake.add("qwen3.5-4b-jarvis-ab12cd34:latest", size=3_400_000_000)
    fake.add("qwen3.5:4b-voice-8k", size=3_400_000_000)
    visible = await inv.list_models(ROOT, transport=fake.transport())
    assert {m.name for m in visible} == {"qwen3.5:4b", "qwen3-embedding:4b"}
    everything = await inv.list_models(ROOT, include_hidden=True, transport=fake.transport())
    assert len(everything) == 4


async def test_list_models_drops_cloud_references(fake: FakeOllamaServer) -> None:
    fake.add("gpt-oss:cloud", size=0)
    models = await inv.list_models(ROOT, transport=fake.transport())
    assert all(not m.name.endswith(":cloud") for m in models)


async def test_a_broken_manifest_keeps_its_row_without_facts(fake: FakeOllamaServer) -> None:
    """One download whose /api/show fails must not blank the table (fail-open)."""
    fake.add("broken:latest", size=10, show_fails=True)
    models = await inv.list_models(ROOT, transport=fake.transport())
    broken = next(m for m in models if m.name == "broken:latest")
    assert broken.probed is False
    assert broken.capabilities == ()
    assert broken.context_length is None
    assert broken.size_bytes == 10  # the /api/tags facts survive


async def test_list_models_raises_an_english_sentence_when_offline(fake: FakeOllamaServer) -> None:
    fake.offline = True
    with pytest.raises(inv.OllamaServerError) as excinfo:
        await inv.list_models(ROOT, transport=fake.transport())
    assert "did not answer" in str(excinfo.value)


async def test_get_model_is_latest_tolerant_and_404s_honestly(fake: FakeOllamaServer) -> None:
    fake.add("nomic-embed-text:latest", size=1)
    info = await inv.get_model(ROOT, "nomic-embed-text", transport=fake.transport())
    assert info.name == "nomic-embed-text:latest"
    with pytest.raises(inv.OllamaModelNotFound):
        await inv.get_model(ROOT, "never-pulled:7b", transport=fake.transport())


def test_native_context_length_reads_the_architecture_key() -> None:
    assert (
        inv.native_context_length(
            {"general.architecture": "gemma4", "gemma4.context_length": 131072}
        )
        == 131072
    )
    # No architecture hint: any ``*.context_length`` key still counts.
    assert inv.native_context_length({"llama.context_length": 8192}) == 8192
    assert inv.native_context_length({"general.architecture": "x"}) is None
    assert inv.native_context_length(None) is None


# ── running / unload / delete / disk ─────────────────────────────────────
async def test_running_models_reports_vram_and_expiry(fake: FakeOllamaServer) -> None:
    fake.load("qwen3.5:4b", size_vram=3_000_000_000)
    running = await inv.running_models(ROOT, transport=fake.transport())
    assert len(running) == 1
    assert running[0].name == "qwen3.5:4b"
    assert running[0].size_vram_bytes == 3_000_000_000
    assert running[0].expires_at.startswith("2026-")
    assert running[0].context_length == 8192


async def test_unload_sends_keep_alive_zero_and_frees_the_model(fake: FakeOllamaServer) -> None:
    fake.load("qwen3.5:4b")
    await inv.unload_model(ROOT, "qwen3.5:4b", transport=fake.transport())
    assert ("POST", "/api/generate", {"model": "qwen3.5:4b", "keep_alive": 0}) in fake.calls
    assert await inv.running_models(ROOT, transport=fake.transport()) == []


async def test_unload_of_an_unknown_model_is_a_404_not_a_success(
    fake: FakeOllamaServer,
) -> None:
    with pytest.raises(inv.OllamaModelNotFound):
        await inv.unload_model(ROOT, "ghost:latest", transport=fake.transport())


async def test_delete_removes_the_download_and_404s_afterwards(fake: FakeOllamaServer) -> None:
    await inv.delete_model(ROOT, "qwen3.5:4b", transport=fake.transport())
    assert ("DELETE", "/api/delete", {"model": "qwen3.5:4b"}) in fake.calls
    assert "qwen3.5:4b" not in fake.models
    with pytest.raises(inv.OllamaModelNotFound):
        await inv.delete_model(ROOT, "qwen3.5:4b", transport=fake.transport())


async def test_disk_usage_counts_each_weight_once(fake: FakeOllamaServer) -> None:
    """An alias shares its weights with the base — counting it would double the total."""
    fake.add("qwen3.5-4b-jarvis-ab12cd34:latest", size=3_400_000_000)
    assert await inv.disk_usage(ROOT, transport=fake.transport()) == (3_400_000_000 + 2_500_000_000)


# ── Shared snapshot ──────────────────────────────────────────────────────
async def test_concurrent_readers_share_one_sweep(fake) -> None:
    """Four panels opening at once used to mean four ``/api/tags`` and four
    full ``/api/show`` sweeps; they now join the sweep in flight."""
    import asyncio

    first, second, third = await asyncio.gather(
        inv.cached_snapshot(ROOT, transport=fake.transport()),
        inv.cached_snapshot(ROOT, transport=fake.transport()),
        inv.cached_snapshot(ROOT, transport=fake.transport()),
    )
    assert first is second is third
    assert [c[1] for c in fake.calls].count("/api/tags") == 1
    assert [c[1] for c in fake.calls].count("/api/show") == len(fake.models)
    assert [c[1] for c in fake.calls].count("/api/ps") == 1
    assert {m.name for m in first.models} == set(fake.models)
    assert first.fetched_at > 0


async def test_unload_and_delete_drop_the_snapshot(fake) -> None:
    fake.load("qwen3.5:4b")
    before = await inv.cached_snapshot(ROOT, transport=fake.transport())
    assert [r.name for r in before.running] == ["qwen3.5:4b"]
    await inv.unload_model(ROOT, "qwen3.5:4b", transport=fake.transport())
    after = await inv.cached_snapshot(ROOT, transport=fake.transport())
    assert after is not before and after.running == ()
    await inv.delete_model(ROOT, "qwen3-embedding:4b", transport=fake.transport())
    gone = await inv.cached_snapshot(ROOT, transport=fake.transport())
    assert [m.name for m in gone.models] == ["qwen3.5:4b"]


async def test_an_offline_server_is_asked_once_per_window(fake) -> None:
    """Every panel shows the same sentence after ONE refused connection."""
    fake.offline = True
    with pytest.raises(inv.OllamaServerError):
        await inv.cached_snapshot(ROOT, transport=fake.transport())
    fake.offline = False
    # Still within the window: the remembered failure answers, no request.
    with pytest.raises(inv.OllamaServerError):
        await inv.cached_snapshot(ROOT, transport=fake.transport())
    assert fake.calls == []
    inv.invalidate_snapshot(ROOT)
    snapshot = await inv.cached_snapshot(ROOT, transport=fake.transport())
    assert len(snapshot.models) == 2


async def test_the_snapshot_expires(fake) -> None:
    first = await inv.cached_snapshot(ROOT, transport=fake.transport())
    second = await inv.cached_snapshot(ROOT, max_age_s=0.0, transport=fake.transport())
    assert second is not first
    assert inv.peek_snapshot(ROOT) is second
