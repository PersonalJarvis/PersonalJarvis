from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
from jarvis.realtime.protocol import RealtimeProvider


@pytest.mark.parametrize(
    ("provider_cls", "provider_id", "input_rate"),
    [
        (OpenAIRealtimeProvider, "openai-realtime", 24_000),
        (GeminiLiveProvider, "gemini-live", 16_000),
    ],
)
def test_provider_is_structurally_conformant(provider_cls, provider_id, input_rate):
    provider = provider_cls()
    assert isinstance(provider, RealtimeProvider)
    assert provider.supports_realtime is True
    assert provider.name == provider_id
    assert provider.input_sample_rate == input_rate
    assert provider.output_sample_rate == 24_000
    assert provider.credential_candidates


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_cls",
    [OpenAIRealtimeProvider, GeminiLiveProvider],
)
async def test_keyless_capability_probe_is_false(provider_cls):
    assert await provider_cls().can_open_duplex_session() is False


@pytest.mark.parametrize(
    "path",
    [
        Path("jarvis/plugins/realtime/openai_realtime.py"),
        Path("jarvis/plugins/realtime/gemini_live.py"),
    ],
)
def test_plugin_module_imports_no_jarvis_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]
    direct_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not any(name == "jarvis" or name.startswith("jarvis.") for name in imports)
    assert not any(
        name == "jarvis" or name.startswith("jarvis.") for name in direct_imports
    )


@pytest.mark.parametrize(
    ("path", "sdk_root"),
    [
        (Path("jarvis/plugins/realtime/openai_realtime.py"), "openai"),
        (Path("jarvis/plugins/realtime/gemini_live.py"), "google"),
    ],
)
def test_provider_sdk_import_is_lazy(path: Path, sdk_root: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    names = [
        alias.name
        for node in top_level
        for alias in (getattr(node, "names", []) or [])
    ]
    modules = [getattr(node, "module", "") or "" for node in top_level]
    assert not any(
        name == sdk_root or name.startswith(f"{sdk_root}.")
        for name in [*names, *modules]
    )
