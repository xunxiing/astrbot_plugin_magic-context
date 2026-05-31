import json
from types import SimpleNamespace

import pytest
from hooks.parallel_tool import ParallelToolUseTool

from astrbot.core.agent.run_context import ContextWrapper


class DummyToolManager:
    def __init__(self, tool_map):
        self._tool_map = tool_map

    def get_func(self, name: str):
        return self._tool_map.get(name)


class DummyPluginContext:
    def __init__(self, tool_map):
        self._tool_mgr = DummyToolManager(tool_map)

    def get_llm_tool_manager(self):
        return self._tool_mgr


class DummyLocalTool:
    def __init__(self, name, handler, active=True):
        self.name = name
        self.handler = handler
        self.active = active
        self.is_background_task = False
        self.handler_module_path = "tests"


async def fast_tool(event, value: str):
    return f"fast:{value}"


async def slow_tool(event, value: str):
    return f"slow:{value}"


async def broken_tool(event):
    raise RuntimeError("boom")


def make_run_context(tool_map):
    event = SimpleNamespace(get_result=lambda: None)
    plugin_context = DummyPluginContext(tool_map)
    agent_context = SimpleNamespace(context=plugin_context, event=event)
    return ContextWrapper(context=agent_context, messages=[], tool_call_timeout=5)


@pytest.mark.asyncio
async def test_parallel_tool_use_runs_multiple_tools():
    tool = ParallelToolUseTool()
    run_context = make_run_context(
        {
            "fast_tool": DummyLocalTool("fast_tool", fast_tool),
            "slow_tool": DummyLocalTool("slow_tool", slow_tool),
        }
    )

    result = await tool.call(
        run_context,
        tool_uses=[
            {"recipient_name": "fast_tool", "parameters": {"value": "a"}},
            {"recipient_name": "slow_tool", "parameters": {"value": "b"}},
        ],
    )

    payload = json.loads(result)
    assert len(payload["results"]) == 2
    assert payload["results"][0]["ok"] is True
    assert payload["results"][0]["result"] == "fast:a"
    assert payload["results"][1]["ok"] is True
    assert payload["results"][1]["result"] == "slow:b"


@pytest.mark.asyncio
async def test_parallel_tool_use_blocks_recursion():
    tool = ParallelToolUseTool()
    run_context = make_run_context({})

    result = await tool.call(
        run_context,
        tool_uses=[
            {"recipient_name": "parallel_tool_use", "parameters": {"tool_uses": []}}
        ],
    )

    payload = json.loads(result)
    assert payload["results"][0]["ok"] is False
    assert "recursive parallel_tool_use" in payload["results"][0]["result"]


@pytest.mark.asyncio
async def test_parallel_tool_use_captures_tool_error():
    tool = ParallelToolUseTool()
    run_context = make_run_context(
        {
            "broken_tool": DummyLocalTool("broken_tool", broken_tool),
        }
    )

    result = await tool.call(
        run_context,
        tool_uses=[
            {"recipient_name": "broken_tool", "parameters": {}},
        ],
    )

    payload = json.loads(result)
    assert payload["results"][0]["ok"] is False
    assert "boom" in payload["results"][0]["result"]
