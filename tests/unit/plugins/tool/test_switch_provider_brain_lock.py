"""The voice/chat ``switch-provider`` tool may NOT switch the brain provider.

The active brain provider is the user's hard choice: it changes only via the
control CLI or the manual provider switch in the desktop app. Jarvis itself —
which is what calls this tool — must refuse a brain switch with an honest
message and never reach the actual switch path. TTS / STT / subagent switches
stay fully voice-controllable.
"""

from __future__ import annotations

import jarvis.brain.app_control as app_control
from jarvis.plugins.tool.switch_provider import SwitchProviderTool


async def test_brain_switch_is_refused_and_never_applied(monkeypatch) -> None:
    calls: list[tuple] = []

    async def _spy(*args, **kwargs):  # pragma: no cover - must not run
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(app_control, "apply_provider_switch", _spy, raising=True)

    tool = SwitchProviderTool()
    result = await tool.execute(
        {"tier": "brain", "provider": "gemini", "reason": "switch to gemini"},
        None,  # ctx is unused by the tool
    )

    assert result.success is False
    assert "provider_switch_locked" in (result.error or "")
    assert calls == []  # the real switch was never attempted


async def test_tts_switch_still_reaches_apply(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def _spy(tier, provider, *, cfg, persist=True):
        seen["tier"] = tier
        return {
            "ok": True,
            "tier": tier,
            "old_provider": "gemini-flash-tts",
            "new_provider": provider,
            "persisted": True,
            "applied_live": True,
            "requires_restart": False,
        }

    monkeypatch.setattr(app_control, "apply_provider_switch", _spy, raising=True)
    monkeypatch.setattr(app_control, "resolve_running_cfg", lambda: object(), raising=True)

    tool = SwitchProviderTool()
    result = await tool.execute(
        {"tier": "tts", "provider": "elevenlabs", "reason": "use elevenlabs"},
        None,
    )

    assert seen.get("tier") == "tts"
    assert result.success is True
