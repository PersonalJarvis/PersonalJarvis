"""Agent reasoning loop executed on a host, with model/tool authorization at the hub."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .models import MachineCommand


async def run_host_loop(command: MachineCommand, rpc: Any, check_lease: Any) -> dict[str, Any]:
    """Host maintains conversation state; no provider credential travels to this process."""
    messages = list(command.args["messages"])
    final_text = ""
    for _ in range(40):
        check_lease()
        response = await rpc(command.job_id, "model", {"messages": messages})
        check_lease()
        text = str(response.get("text", ""))
        calls = response.get("calls", [])
        final_text = text
        if not calls:
            return {"text": final_text}
        blocks = [{"type": "text", "text": text}] if text else []
        for call in calls:
            call.setdefault("id", uuid4().hex)
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call.get("input", {}),
                    **(
                        {"thought_signature": call["thought_signature"]}
                        if call.get("thought_signature")
                        else {}
                    ),
                }
            )
        messages.append({"role": "assistant", "content": blocks})
        for call in calls:
            check_lease()
            result = await rpc(command.job_id, "tool", call)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    raise RuntimeError("Remote turn reached its round limit; review the recorded progress")
