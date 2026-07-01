"""``reset_orb_position`` — voice-driven recovery for a lost orb.

ADR-0016 L2: when the user says "Orb zurück", "wo bist du", or  # i18n-allow (quotes DE voice-trigger phrases matched by the local-action gate)
"reset orb", the local-action gate dispatches this tool. The tool
publishes :class:`OrbResetRequested` on the EventBus; the orb-side
bridge (``ui.orb.bus_bridge.OrbBusBridge``) subscribes and marshals
the actual reset onto the Tk thread.

Risk tier: ``safe``. The tool does NOT mutate any external system —
it only publishes a bus event whose subscriber resets a Tk window
and clears a TOML section. The plausibility/approval surface is at
the local-action-gate regex layer (regex anchored on ``^`` + tight
phrase list), not here.

The tool stays bus-aware (constructor takes the bus) so the
``_load_local_action_tools`` factory can wire it without leaking a
direct dependency on ``ui.orb``.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.events import OrbResetRequested
from jarvis.core.protocols import ExecutionContext, ToolResult


class ResetOrbPositionTool:
    """Publishes :class:`OrbResetRequested` and returns a short voice
    confirmation. The orb-side handler does the actual reset."""

    name: str = "reset_orb_position"
    risk_tier: str = "safe"
    description: str = (
        "Brings the mascot / orb back to its default anchor on "
        "the main screen (voice recovery for BUG-027)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, *, bus: Any) -> None:
        self._bus = bus

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        """Publish OrbResetRequested and return the voice confirmation."""
        del args, ctx  # tool is parameterless
        try:
            await self._bus.publish(OrbResetRequested(source="voice"))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"OrbResetRequested publish failed: {exc!r}",
            )
        return ToolResult(success=True, output="Orb ist zurück.", error=None)  # i18n-allow (DE TTS voice-confirmation phrase spoken back to the user)


__all__ = ["ResetOrbPositionTool"]
