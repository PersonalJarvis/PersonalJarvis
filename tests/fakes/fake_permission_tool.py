"""A side-effect-free tool for approval-policy contracts."""

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class FakePermissionTool:
    name = "fake_write"
    description = "Record a permitted call without performing an external action."
    schema = {"type": "object", "properties": {}}

    def __init__(self, risk_tier: str = "ask") -> None:
        self.risk_tier = risk_tier
        self.calls: list[ExecutionContext] = []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls.append(ctx)
        return ToolResult(success=True, output={"calls": len(self.calls)})
