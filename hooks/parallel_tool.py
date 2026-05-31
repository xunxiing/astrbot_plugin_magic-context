import asyncio
import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor


def _build_parallel_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool_uses": {
                "type": "array",
                "description": "Parallel tool calls to execute.",
                "items": {
                    "type": "object",
                    "properties": {
                        "recipient_name": {
                            "type": "string",
                            "description": "Tool name to execute.",
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Arguments for the tool call.",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["recipient_name", "parameters"],
                    "additionalProperties": False,
                },
                "minItems": 1,
                "maxItems": 8,
            }
        },
        "required": ["tool_uses"],
        "additionalProperties": False,
    }


def _extract_text_result(tool_result: Any) -> str:
    if tool_result is None:
        return ""
    content = getattr(tool_result, "content", None)
    if not content:
        return str(tool_result)

    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        if isinstance(item, dict) and "text" in item:
            parts.append(str(item["text"]))
            continue
        parts.append(str(item))
    return "\n".join(part for part in parts if part)


async def _execute_one_tool(
    run_context: ContextWrapper[AstrAgentContext],
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "parallel_tool_use":
        return {
            "recipient_name": tool_name,
            "ok": False,
            "result": "Error: recursive parallel_tool_use is not allowed.",
        }

    tool_mgr = run_context.context.context.get_llm_tool_manager()
    tool = tool_mgr.get_func(tool_name)
    if tool is None:
        return {
            "recipient_name": tool_name,
            "ok": False,
            "result": f"Error: tool `{tool_name}` not found.",
        }
    if not getattr(tool, "active", True):
        return {
            "recipient_name": tool_name,
            "ok": False,
            "result": f"Error: tool `{tool_name}` is inactive.",
        }

    final_result = ""
    try:
        async for resp in FunctionToolExecutor.execute(tool, run_context, **parameters):
            text = _extract_text_result(resp)
            if text:
                final_result = text
        return {
            "recipient_name": tool_name,
            "ok": True,
            "result": final_result or "(no result)",
        }
    except Exception as exc:
        return {
            "recipient_name": tool_name,
            "ok": False,
            "result": f"Error: {exc}",
        }


async def run_parallel_tool_calls(
    run_context: ContextWrapper[AstrAgentContext],
    tool_uses: list[dict[str, Any]],
) -> str:
    tasks = []
    normalized_calls: list[tuple[str, dict[str, Any]]] = []
    for item in tool_uses:
        if not isinstance(item, dict):
            raise ValueError("Each tool use must be an object.")
        tool_name = str(item.get("recipient_name", "") or "").strip()
        parameters = item.get("parameters", {})
        if not tool_name:
            raise ValueError("recipient_name is required.")
        if not isinstance(parameters, dict):
            raise ValueError(f"parameters for `{tool_name}` must be an object.")
        normalized_calls.append((tool_name, parameters))

    for tool_name, parameters in normalized_calls:
        tasks.append(_execute_one_tool(run_context, tool_name, parameters))

    results = await asyncio.gather(*tasks)
    return json.dumps({"results": results}, ensure_ascii=False)


@dataclass
class ParallelToolUseTool(FunctionTool[AstrAgentContext]):
    name: str = "parallel_tool_use"
    description: str = (
        "Run multiple independent tools in parallel. "
        "Use only when calls do not depend on each other's outputs."
    )
    parameters: dict = Field(default_factory=_build_parallel_parameters)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        tool_uses = kwargs.get("tool_uses")
        if not isinstance(tool_uses, list) or not tool_uses:
            return "Error: `tool_uses` must be a non-empty array."
        try:
            return await run_parallel_tool_calls(context, tool_uses)
        except ValueError as exc:
            return f"Error: {exc}"
