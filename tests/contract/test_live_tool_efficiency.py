"""Cost reductions must preserve the selected model and every authorized capability."""

import json

import pytest

from jarvis.brain.cost import calculate_cost_usd
from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
from jarvis.live.config import LiveConfig
from jarvis.live.cost import backend_cost_usd
from jarvis.live.discovery import discover
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools


def descriptor(name, description, *, size=0):
    return SupervisorToolDescriptor(
        name,
        description,
        {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "x" * size}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "monitor",
    )


class Gateway:
    def __init__(self, tools):
        self.tools = tuple(tools)
        self.calls = []

    def catalog(self):
        return self.tools

    async def execute(self, name, arguments, request):
        self.calls.append((name, arguments, request))
        return ToolResult(True, {"verified": True, "text": arguments["text"]})


def test_large_catalog_is_deferred_without_model_or_reasoning_downgrade(tmp_path):
    gateway = Gateway(
        descriptor(f"tool_{i:03}", "Available application tool", size=900) for i in range(100)
    )
    ledger = LiveLedger(tmp_path / "live.db")
    try:
        runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="selected")
        eager = runtime.declarations()
        deferred = runtime.declarations(defer_catalog=True)
        assert len(json.dumps(deferred)) < len(json.dumps(eager)) * 0.15
        assert "x" * 900 not in json.dumps(deferred)
        assert all(tool.name in deferred[1]["description"] for tool in gateway.tools)
        assert {tool["name"] for tool in deferred} == {
            "discover_tools",
            "call_tool",
            "confirm_action",
            "end_call",
        }
        config = LiveConfig(configured=True, backend_model="selected", reasoning_effort="high")
        backend = config.session_config(language="en", tools=deferred)["delegation"]["responses"]
        assert backend["model"] == "selected"
        assert backend["reasoning"] == {"effort": "high"}
        assert "max_output_tokens" not in backend
        assert {tool["type"] for tool in backend["tools"]} == {"function", "web_search"}
    finally:
        ledger.close()


def test_sentence_query_finds_available_agent_status_instead_of_empty_results():
    tools = [
        descriptor("agent_status", "Read the status of active agents and their tasks"),
        descriptor("write_file", "Write a file"),
        descriptor("list_files", "List files in a directory"),
    ]
    result = discover(tools, "list Jarvis agents current active agents tasks status")
    assert result["tools"][0]["name"] == "agent_status"
    assert "write_file" not in {tool["name"] for tool in result["tools"]}


def test_exact_lookup_keeps_full_schema_and_does_not_include_name_mentions():
    requested = descriptor("agent_status", "Read agent status", size=20000)
    result = discover([descriptor("help", "See agent_status"), requested], "agent_status")
    assert result["total"] == 1
    assert result["tools"][0]["parameters"] == requested.input_schema
    assert result["next_offset"] is None


def test_pagination_keeps_every_tool_reachable_and_stable():
    catalog = [descriptor(f"tool_{i:02}", "Read data", size=6000) for i in range(13)]
    offset, names = 0, []
    while offset is not None:
        result = discover(reversed(catalog), "", offset)
        assert result["tools"]
        names.extend(tool["name"] for tool in result["tools"])
        offset = result["next_offset"]
    assert names == [tool.name for tool in catalog]
    assert discover(catalog, "", 999)["next_offset"] is None


def test_empty_search_explains_how_to_recover_without_inventing_tools():
    result = discover([descriptor("write_file", "Write data")], "nonexistent-capability")
    assert result["tools"] == []
    assert "empty query" in result["hint"]


@pytest.mark.asyncio
async def test_discovered_tool_uses_existing_executor_validation_and_durable_receipt(tmp_path):
    gateway = Gateway([descriptor("write_file", "Write a file")])
    ledger = LiveLedger(tmp_path / "live.db")
    try:
        runtime = LiveTools(gateway, ledger, "s", language="en", backend_model="selected")
        runtime.declarations(defer_catalog=True)
        found = await runtime.execute("find", "discover_tools", {"query": "write file"}, 0)
        assert found["tools"][0]["parameters"] == gateway.tools[0].input_schema
        assert gateway.calls == []
        args = {"name": "write_file", "arguments_json": '{"text":"unchanged quality"}'}
        result = await runtime.execute("run", "call_tool", args, 0)
        assert result["success"] is True
        assert result["output"]["text"] == "unchanged quality"
        assert await runtime.execute("run", "call_tool", args, 0) == result
        assert len(gateway.calls) == 1
        assert gateway.calls[0][2].config_snapshot["live_backend_model"] == "selected"
        bad = {"name": "write_file", "arguments_json": '{"text":123}'}
        assert (await runtime.execute("invalid", "call_tool", bad, 0))["success"] is False
        assert len(gateway.calls) == 1
    finally:
        ledger.close()


def test_current_live_backend_usage_is_priced_instead_of_silently_free():
    # Realistic cached tool-loop usage; cached tokens must not pay the input rate again.
    assert calculate_cost_usd("gpt-5.6-terra", 16390, 2162, 124475) == pytest.approx(0.083619)


def test_cache_writes_and_reasoning_are_not_free_or_double_counted():
    usage = {
        "input_tokens": 10337,
        "input_tokens_details": {"cache_write_tokens": 10143, "cached_tokens": 0},
        "output_tokens": 59,
        "output_tokens_details": {"reasoning_tokens": 32},
    }
    assert backend_cost_usd("gpt-5.6-terra", usage) == pytest.approx(0.0264535)
    assert backend_cost_usd("gpt-5.6-terra", {"input_tokens": 1000}) == pytest.approx(0.002)
    assert backend_cost_usd("gpt-5.6-terra", {}) == 0


def test_native_direct_tools_remain_available(tmp_path):
    ledger = LiveLedger(tmp_path / "live.db")
    try:
        runtime = LiveTools(
            Gateway([descriptor("write_file", "Write data")]),
            ledger,
            "s",
            language="en",
            backend_model="",
        )
        assert any(tool["name"].startswith("jarvis_") for tool in runtime.declarations())
    finally:
        ledger.close()
