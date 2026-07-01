"""dispatch_to_admin tool — brain calls admin ops via a UAC-elevated helper.

Risk tier = ``ask``. That means every brain call is routed through the
approval workflow, **unless** the user has set a pattern like
``dispatch_to_admin *`` via ``[safety.whitelist]``. Destructive ops
(DESTRUCTIVE_OPS) get a second prompt on top, even with a global whitelist —
that is intentional.

Call flow:

1. Brain calls ``{"op": {...AdminOperation JSON...}}``.
2. Tool validates the JSON against the Pydantic schema.
3. ``AdminClient.execute(op, destructive_approved=...)``.
4. On ``DestructiveRequiresApproval``:
   → Tool returns ``ToolResult(success=False, error="destructive_requires_approval",
   output={"op_id": ..., "op_type": ...})``. The caller (risk-tier executor)
   shows the user a prompt and calls the tool again with
   ``destructive_approved=True``.
5. On success/failure: the ``AdminResponse`` model is packed into
   ``ToolResult.output`` as a dict.
"""
from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from jarvis.admin.client import AdminClient, DestructiveRequiresApproval
from jarvis.admin.schema import DESTRUCTIVE_OPS, AdminOperation
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, ToolResult

_ADMIN_OP_ADAPTER: TypeAdapter[AdminOperation] = TypeAdapter(AdminOperation)


class DispatchToAdminTool:
    """Protocol-compatible tool. Called by the brain via a tool call."""

    name: str = "dispatch_to_admin"
    risk_tier: str = "ask"
    description: str = (
        "Sends an admin operation (winget install, service start/stop, "
        "firewall, registry write, scheduled task, write_protected_path) "
        "to the UAC-elevated admin helper. Destructive ops "
        "(uninstall, remove, write_registry_hklm, write_protected_path) "
        "need explicit approval."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {
                "type": "object",
                "description": (
                    "AdminOperation JSON. Must have a 'type' key "
                    "(e.g. 'install_winget') and the fields required for "
                    "that op type. See jarvis.admin.schema."
                ),
            },
            "destructive_approved": {
                "type": "boolean",
                "description": (
                    "If true, the op runs even for a destructive type. "
                    "Default false — the risk-tier executor sets this "
                    "after the user's approval."
                ),
                "default": False,
            },
        },
        "required": ["op"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        client_factory: Any = None,
    ) -> None:
        self._bus = bus
        # Optional injection for tests — otherwise lazily built in execute.
        self._client_factory = client_factory

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        op_payload = args.get("op")
        destructive_approved = bool(args.get("destructive_approved", False))

        if not isinstance(op_payload, dict):
            return ToolResult(
                success=False, output=None,
                error="'op' must be a JSON object.",
            )

        try:
            op = _ADMIN_OP_ADAPTER.validate_python(op_payload)
        except ValidationError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"AdminOperation invalid: {exc.error_count()} error(s)",
            )

        client = self._build_client()
        try:
            resp = await client.execute(
                op, destructive_approved=destructive_approved
            )
        except DestructiveRequiresApproval as dra:
            return ToolResult(
                success=False,
                output={
                    "op_id": dra.op_id,
                    "op_type": dra.op_type,
                    "destructive": True,
                },
                error="destructive_requires_approval",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        return ToolResult(
            success=resp.success,
            output={
                "op_id": str(resp.op_id),
                "op_type": op.type,
                "success": resp.success,
                "duration_ms": resp.duration_ms,
                "result": resp.result,
                "destructive": op.type in DESTRUCTIVE_OPS,
            },
            error=resp.error_message if not resp.success else None,
        )

    def _build_client(self) -> AdminClient:
        if self._client_factory is not None:
            return self._client_factory()
        return AdminClient(bus=self._bus)


__all__ = ["DispatchToAdminTool"]
