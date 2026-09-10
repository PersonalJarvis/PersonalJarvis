"""The subscription's text-only path must not require voice transport support."""
from __future__ import annotations
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.plugins.brain.codex import CodexBrain
from jarvis.plugins.brain.codex import _build_cli_command

def test_browser_inference_does_not_load_host_tools():
    command = _build_cli_command("codex", "", text_only=True)
    assert "--ignore-user-config" in command and "--ephemeral" in command
    for feature in ("shell_tool", "plugins", "apps", "browser_use", "unified_exec"):
        index = command.index(feature)
        assert command[index - 1] == "--disable"

class RecordingCodex(CodexBrain):
    def _api_key(self):
        return None
    async def _complete_via_cli(self, req):
        yield BrainDelta(content="cli")
    async def _complete_via_app_server(self, req):
        yield BrainDelta(content="voice")

async def test_browser_text_subscription_uses_cli():
    brain = RecordingCodex(prefer_subscription=True, subscription_text_only=True)
    assert [d.content async for d in brain.complete(BrainRequest(messages=()))] == ["cli"]
    assert not brain.supports_vision

async def test_existing_subscription_transport_is_unchanged():
    brain = RecordingCodex(prefer_subscription=True)
    assert [d.content async for d in brain.complete(BrainRequest(messages=()))] == ["voice"]
