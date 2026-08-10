from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
from jarvis.plugins.realtime.openai_realtime import (
    LocalRealtimeProvider,
    OpenAIRealtimeProvider,
)
from jarvis.realtime.protocol import RealtimeEvent, RealtimeProvider
from jarvis.realtime.session import RealtimeVoiceSession

# Every installed provider that declares supports_direct_tools=False must pass
# the delegate/decline contracts below. Empty today (the capability-limited
# codex-subscription-realtime adapter was removed 2026-08-10); the harness
# stays so a future external-login provider is pinned automatically.
_CAPABILITY_LIMITED_PROVIDER_CLASSES: tuple[type[Any], ...] = ()


def _session_config(tool_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode=tool_mode),
        latency=SimpleNamespace(enabled=False),
    )


class _CallableBrain:
    async def __call__(self, text: str) -> str:
        return f"handled: {text}"


class _HandoffWire:
    session_id = "contract-wire"
    creates_responses_automatically = False
    isolates_response_generations = False
    supports_tool_updates = False
    direct_speech_is_authoritative = True

    def __init__(self, events: tuple[RealtimeEvent, ...]) -> None:
        self._events = events
        self.spoken: list[str] = []

    async def receive(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_speech(self, text: str) -> None:
        self.spoken.append(text)

    async def interrupt(self, **kwargs: Any) -> None:
        del kwargs

    async def truncate(self, **kwargs: Any) -> None:
        del kwargs

    async def close(self) -> None:
        return None


class _CapabilityWrapper:
    supports_realtime = True
    input_sample_rate = 24_000
    output_sample_rate = 24_000
    supports_direct_tools = False

    def __init__(self, installed: Any, wire: _HandoffWire) -> None:
        self.name = installed.name
        self._wire = wire

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, cfg: Any) -> _HandoffWire:
        del cfg
        return self._wire


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


@pytest.mark.parametrize("provider_cls", _CAPABILITY_LIMITED_PROVIDER_CLASSES)
def test_capability_limited_provider_resolves_callable_brain_to_delegate(
    provider_cls: type[Any],
) -> None:
    """No-native-tools providers retain an action path in direct mode."""
    provider = provider_cls()
    session = RealtimeVoiceSession(
        session_id="contract-delegate",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_session_config("direct"),
        brain=_CallableBrain(),
    )

    assert session._delegate_forced_by_provider is True
    assert session._delegate_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls", _CAPABILITY_LIMITED_PROVIDER_CLASSES)
async def test_capability_limited_provider_declines_handoff_without_ending_call(
    provider_cls: type[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the delegate costs one action, never the live conversation."""
    from jarvis.core import runtime_refs

    monkeypatch.setattr(runtime_refs, "get_supervisor_tool_gateway", lambda: None)
    installed = provider_cls()
    wire = _HandoffWire(
        (
            RealtimeEvent(
                type="input_transcript",
                text="Open the settings view.",
                is_final=True,
            ),
            RealtimeEvent(
                type="handoff_requested",
                text="Open the settings view.",
                handoff_id="contract-handoff",
            ),
            RealtimeEvent(type="output_transcript_delta", text="Still here."),
            RealtimeEvent(type="turn_complete"),
        )
    )
    messages: list[dict[str, Any]] = []
    session = RealtimeVoiceSession(
        session_id="contract-decline",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: messages.append(message) or asyncio.sleep(0),
        provider=_CapabilityWrapper(installed, wire),
        config=_session_config("delegate"),
        brain=None,
    )

    await session.handle_control({"type": "audio_start", "sample_rate": 24_000})
    await session.wait_finished()

    assert wire.spoken, "the unavailable action must be declined audibly"
    assert not [item for item in messages if item.get("type") == "provider_error"]
    assert any(item.get("type") == "turn_complete" for item in messages)
    await session.end(reason="contract")


def test_local_provider_is_structurally_conformant_without_api_key() -> None:
    """The self-hosted card joined every roster except this one (found in
    the 2026-08-08 hardening review) — its protocol conformance was pinned
    only in its own unit file."""
    provider = LocalRealtimeProvider()

    assert isinstance(provider, RealtimeProvider)
    assert provider.supports_realtime is True
    assert provider.name == "local-realtime"
    assert provider.input_sample_rate == 24_000
    assert provider.output_sample_rate == 24_000
    # Keyless by design, and never an ambient stand-in for another card.
    assert provider.credential_candidates == ()
    assert provider.implicit_usage_fallback_allowed is False
    # The small-brain latency capability the session builder keys on.
    assert provider.prefers_compact_instructions is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_cls",
    [OpenAIRealtimeProvider, GeminiLiveProvider],
)
async def test_keyless_capability_probe_is_false(provider_cls):
    assert await provider_cls().can_open_duplex_session() is False


@pytest.mark.asyncio
async def test_unconfigured_local_provider_probe_is_false() -> None:
    assert await LocalRealtimeProvider().can_open_duplex_session() is False


@pytest.mark.parametrize(
    "path",
    [
        Path("jarvis/plugins/realtime/openai_realtime.py"),
        Path("jarvis/plugins/realtime/gemini_live.py"),
    ],
)
def test_plugin_module_imports_no_jarvis_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    direct_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not any(name == "jarvis" or name.startswith("jarvis.") for name in imports)
    assert not any(name == "jarvis" or name.startswith("jarvis.") for name in direct_imports)


@pytest.mark.parametrize(
    ("path", "sdk_root"),
    [
        (Path("jarvis/plugins/realtime/openai_realtime.py"), "openai"),
        (Path("jarvis/plugins/realtime/gemini_live.py"), "google"),
    ],
)
def test_provider_sdk_import_is_lazy(path: Path, sdk_root: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = [alias.name for node in top_level for alias in (getattr(node, "names", []) or [])]
    modules = [getattr(node, "module", "") or "" for node in top_level]
    assert not any(
        name == sdk_root or name.startswith(f"{sdk_root}.") for name in [*names, *modules]
    )


def test_headless_realtime_stack_reports_unavailable_without_audio_libraries() -> None:
    """A slim/no-audio host imports cleanly and returns an honest capability.

    A fresh interpreter is required: the contract module imports provider
    classes above, while this proof must take the actual missing-dependency
    branch before any audio or WebRTC module reaches ``sys.modules``.
    """
    code = "\n".join(
        (
            "import sys",
            "sys.modules['sounddevice'] = None",
            "sys.modules['aiortc'] = None",
            "sys.modules['av'] = None",
            "from jarvis.audio.devices import list_devices",
            "from jarvis.realtime.webrtc_transport import webrtc_unavailable_reason",
            "assert list_devices(output=True) == []",
            "reason = webrtc_unavailable_reason()",
            "assert reason and 'aiortc' in reason.lower(), reason",
            "print('HEADLESS_DEGRADED')",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "HEADLESS_DEGRADED" in result.stdout
