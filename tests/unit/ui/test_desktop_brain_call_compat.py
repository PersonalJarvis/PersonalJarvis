"""Compatibility guards for the desktop's deferred brain proxy."""

from __future__ import annotations

from typing import Any

from jarvis.ui.desktop_app import _supported_call_kwargs


def test_concrete_legacy_brain_drops_new_optional_turn_control() -> None:
    """A source reload must not pass a new keyword to an older live object."""

    async def legacy_generate(
        text: str,
        *,
        publish_response: bool = True,
        use_history: bool = True,
    ) -> str:
        return text

    filtered = _supported_call_kwargs(
        legacy_generate,
        {
            "emit_tool_ack": False,
            "publish_response": False,
            "use_history": False,
        },
    )

    assert filtered == {
        "publish_response": False,
        "use_history": False,
    }


def test_extensible_brain_keeps_all_turn_controls() -> None:
    async def extensible_generate(text: str, **kwargs: Any) -> str:
        return text

    controls = {
        "emit_tool_ack": False,
        "publish_response": False,
    }

    assert _supported_call_kwargs(extensible_generate, controls) == controls
