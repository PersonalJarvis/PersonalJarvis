"""Ollama profiles: a stable alias name, one create per option set, honest advice.

The server is a fake behind ``httpx.MockTransport`` (a real httpx feature, no
``unittest.mock``): it records every call so the tests can pin how many
round-trips a turn costs, which is the whole point of the idempotency memo.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.brain import ollama_profiles as profiles
from jarvis.brain.ollama_profiles import (
    BAKEABLE_KEYS,
    ensure_profile,
    is_profile_alias,
    profile_name,
    suggest_options,
    to_v1_kwargs,
    warm,
)
from jarvis.core.config import OllamaModelOptions

ROOT = "http://fake:11434"


class FakeOllamaServer:
    """Just enough of ``/api/tags``, ``/api/create``, ``/api/delete``, ``/api/generate``."""

    def __init__(self, models: list[str]) -> None:
        self.models = list(models)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.fail_create = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.models]})
        if request.url.path == "/api/create":
            if self.fail_create:
                return httpx.Response(500, json={"error": "boom"})
            self.models.append(f"{body['model']}:latest")
            return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/api/delete":
            self.models = [m for m in self.models if m.removesuffix(":latest") != body["model"]]
            return httpx.Response(200)
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"model": body["model"], "done": True})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]


@pytest.fixture(autouse=True)
def _fresh_memo() -> None:
    profiles.reset_process_memo()


# ── Naming ───────────────────────────────────────────────────────────────


def test_profile_name_is_stable_and_folds_the_tag() -> None:
    a = profile_name("qwen3.5:9b", OllamaModelOptions(num_ctx=16384, num_gpu=-1))
    b = profile_name("qwen3.5:9b", OllamaModelOptions(num_gpu=-1, num_ctx=16384, keep_alive="2h"))
    assert a == b  # key order and non-bakeable knobs do not matter
    assert a.startswith("qwen3.5-9b-jarvis-")
    assert len(a.rsplit("-", 1)[1]) == 8
    assert profile_name("hf.co/u/r:Q4", OllamaModelOptions(num_ctx=4096)).startswith(
        "hf.co-u-r-Q4-jarvis-"
    )


def test_profile_name_changes_with_any_bakeable_knob() -> None:
    base = OllamaModelOptions(num_ctx=8192)
    names = {profile_name("m:1", base)}
    for key in BAKEABLE_KEYS:
        if key == "num_ctx":
            continue
        value: Any = ["x"] if key == "stop" else 1
        names.add(profile_name("m:1", OllamaModelOptions(num_ctx=8192, **{key: value})))
    assert len(names) == len(BAKEABLE_KEYS)


def test_is_profile_alias() -> None:
    assert is_profile_alias("qwen3.5-9b-jarvis-0123abcd")
    assert is_profile_alias("qwen3.5-9b-jarvis-0123abcd:latest")
    assert not is_profile_alias("qwen3.5:9b")
    assert not is_profile_alias("qwen3.5:4b-voice-8k")


# ── ensure_profile ───────────────────────────────────────────────────────


async def test_ensure_profile_creates_once_and_is_idempotent() -> None:
    server = FakeOllamaServer(["qwen3.5:9b"])
    opts = OllamaModelOptions(num_ctx=16384, keep_alive="30m")
    alias = await ensure_profile(ROOT, "qwen3.5:9b", opts, transport=server.transport)
    assert alias == profile_name("qwen3.5:9b", opts)
    create = [b for m, p, b in server.calls if p == "/api/create"]
    assert create == [
        {"model": alias, "from": "qwen3.5:9b", "parameters": {"num_ctx": 16384}, "stream": False}
    ]
    # Second call: the process memo answers, no HTTP at all.
    before = len(server.calls)
    assert await ensure_profile(ROOT, "qwen3.5:9b", opts, transport=server.transport) == alias
    assert len(server.calls) == before
    # A fresh process finds the alias on the server and skips the create.
    profiles.reset_process_memo()
    assert await ensure_profile(ROOT, "qwen3.5:9b", opts, transport=server.transport) == alias
    assert server.paths("POST").count("/api/create") == 1


async def test_ensure_profile_deletes_the_stale_alias_when_the_hash_changes() -> None:
    old = profile_name("qwen3.5:9b", OllamaModelOptions(num_ctx=8192))
    other = profile_name("gemma4:12b", OllamaModelOptions(num_ctx=8192))
    server = FakeOllamaServer(["qwen3.5:9b", f"{old}:latest", f"{other}:latest"])
    new_opts = OllamaModelOptions(num_ctx=16384)
    alias = await ensure_profile(ROOT, "qwen3.5:9b", new_opts, transport=server.transport)
    deleted = [b["model"] for m, p, b in server.calls if p == "/api/delete"]
    assert deleted == [old]  # the other base's alias is left alone
    assert f"{alias}:latest" in server.models
    assert f"{other}:latest" in server.models


async def test_ensure_profile_without_bakeable_knobs_returns_the_base() -> None:
    server = FakeOllamaServer(["qwen3.5:9b"])
    opts = OllamaModelOptions(temperature=0.2, keep_alive="30m", think=False)
    alias = await ensure_profile(ROOT, "qwen3.5:9b", opts, transport=server.transport)
    assert alias == "qwen3.5:9b"
    assert server.calls == []


async def test_ensure_profile_raises_an_english_sentence_on_create_failure() -> None:
    server = FakeOllamaServer(["qwen3.5:9b"])
    server.fail_create = True
    with pytest.raises(RuntimeError, match="could not create the Ollama profile"):
        await ensure_profile(
            ROOT, "qwen3.5:9b", OllamaModelOptions(num_ctx=4096), transport=server.transport
        )


# ── warm ─────────────────────────────────────────────────────────────────


async def test_warm_pings_once_per_model_and_keep_alive() -> None:
    server = FakeOllamaServer(["qwen3.5:9b"])
    assert await warm(ROOT, "qwen3.5:9b", "30m", transport=server.transport) is True
    assert await warm(ROOT, "qwen3.5:9b", "30m", transport=server.transport) is True
    assert server.paths() == ["/api/generate"]
    assert server.calls[0][2] == {"model": "qwen3.5:9b", "keep_alive": "30m", "stream": False}
    assert await warm(ROOT, "qwen3.5:9b", -1, transport=server.transport) is True
    assert server.paths().count("/api/generate") == 2


async def test_warm_never_raises() -> None:
    def _dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await warm(ROOT, "qwen3.5:9b", "5m", transport=httpx.MockTransport(_dead)) is False


# ── to_v1_kwargs ─────────────────────────────────────────────────────────


def test_to_v1_kwargs_maps_the_request_channel() -> None:
    """``think`` -> ``reasoning_effort`` is what Ollama 0.32.15 honours on ``/v1``
    (checked live 2026-08-24: "none" = 3 completion tokens and no reasoning
    field; "high" = a reasoning field)."""
    assert to_v1_kwargs(None) == {}
    assert to_v1_kwargs(OllamaModelOptions(num_ctx=8192)) == {}
    assert to_v1_kwargs(OllamaModelOptions(temperature=0.2, num_predict=512, think=False)) == {
        "temperature": 0.2,
        "max_tokens": 512,
        "reasoning_effort": "none",
    }
    assert to_v1_kwargs(OllamaModelOptions(think="low")) == {"reasoning_effort": "low"}
    assert to_v1_kwargs(OllamaModelOptions(think=True)) == {}
    # Ollama's unlimited sentinels have no /v1 spelling and stay with the server.
    assert to_v1_kwargs(OllamaModelOptions(num_predict=-1)) == {}


# ── suggest_options — the five-case matrix ───────────────────────────────


def test_no_accelerator_uses_the_ram_rule_honestly() -> None:
    opts, reasons = suggest_options(
        size_gb=4.0, native_context=262_144, accelerator_gb=0.0, source="none", ram_gb=16.0
    )
    assert opts.num_gpu is None  # placement left to Ollama, never a promise
    assert opts.num_ctx == 32768  # 5 + 0.12 * 32.8 = 8.9 <= 9.6 (60 % of 16); 64k = 12.9
    assert opts.keep_alive == "10m"
    assert any("RAM rule" in r for r in reasons)
    assert any("no accelerator" in r.lower() for r in reasons)


def test_eight_gb_card_keeps_a_seven_gb_model_partly_on_the_cpu() -> None:
    opts, reasons = suggest_options(
        size_gb=7.2, native_context=131_072, accelerator_gb=8.0, source="nvidia-smi", ram_gb=32.0
    )
    assert opts.num_gpu is None  # 7.2 + 1 > 8: Ollama splits the layers itself
    assert opts.num_ctx == 4096  # nothing bigger fits the 8 GB budget
    assert opts.keep_alive == "10m"
    assert any("splits layers" in r for r in reasons)


def test_sixteen_gb_card_runs_a_mid_model_fully_on_the_gpu() -> None:
    opts, reasons = suggest_options(
        size_gb=7.2, native_context=131_072, accelerator_gb=16.0, source="nvidia-smi", ram_gb=32.0
    )
    assert opts.num_gpu == -1
    assert opts.num_ctx == 32768  # 8.2 + 0.216 * 32.8 = 15.3 <= 16; 64k would be 22.4
    assert opts.keep_alive == "30m"
    assert any("graphics memory" in r for r in reasons)


def test_apple_unified_memory_counts_as_the_accelerator() -> None:
    opts, reasons = suggest_options(
        size_gb=18.0,
        native_context=262_144,
        accelerator_gb=32.0,
        source="apple-unified",
        ram_gb=32.0,
    )
    assert opts.num_gpu == -1
    assert opts.num_ctx == 16384  # 19 + 0.54 * 16.4 = 27.9 <= 32; 32k = 36.7
    assert any("unified memory" in r for r in reasons)


def test_headless_box_with_unreadable_memory_stays_conservative() -> None:
    opts, reasons = suggest_options(
        size_gb=4.0, native_context=None, accelerator_gb=0.0, source="none", ram_gb=None
    )
    assert opts.num_ctx == 4096
    assert opts.num_gpu is None
    assert opts.keep_alive == "10m"
    assert any("could not be read" in r for r in reasons)


def test_native_context_caps_the_ladder() -> None:
    opts, _ = suggest_options(
        size_gb=1.0, native_context=8192, accelerator_gb=24.0, source="nvidia-smi", ram_gb=64.0
    )
    assert opts.num_ctx == 8192


def test_every_knob_has_one_sentence() -> None:
    opts, reasons = suggest_options(
        size_gb=3.4, native_context=262_144, accelerator_gb=16.0, source="nvidia-smi", ram_gb=32.0
    )
    set_keys = [k for k in ("num_ctx", "num_gpu", "keep_alive") if getattr(opts, k) is not None]
    assert len(reasons) == 1 + 3  # the budget sentence + one per knob (placement always speaks)
    assert set_keys == ["num_ctx", "num_gpu", "keep_alive"]
