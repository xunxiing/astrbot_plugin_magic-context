from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext


def _normalize_text(text: str | None) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _shorten(text: str | None, limit: int = 72) -> str:
    raw = " ".join(str(text or "").strip().split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3] + "..."


def _result_call_id(message_id: str | None) -> str | None:
    if not isinstance(message_id, str):
        return None
    if not message_id.startswith("tool:"):
        return None
    call_id = message_id[5:]
    return call_id or None


def _build_reduce_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "match": {
                "type": "string",
                "description": (
                    "A short prefix from the target tool args or tool result. "
                    "Use the first 5-12 characters and make it longer if ambiguous."
                ),
            },
            "kind": {
                "type": "string",
                "description": "Optional target kind: tool_call or tool_result.",
                "enum": ["tool_call", "tool_result"],
            },
            "tool_name": {
                "type": "string",
                "description": "Optional exact tool name filter.",
            },
        },
        "required": ["match"],
        "additionalProperties": False,
    }


def _build_compact_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": (
                    "lite = deterministic old tool cleanup; "
                    "hard = historian summary compaction."
                ),
                "enum": ["lite", "hard"],
            }
        },
        "required": ["mode"],
        "additionalProperties": False,
    }


def _build_tool_groups(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_tags = [tag for tag in tags if tag.get("status") == "active"]
    call_tags: dict[str, dict[str, Any]] = {}
    result_tags: dict[str, list[dict[str, Any]]] = {}

    for tag in active_tags:
        tag_type = tag.get("type")
        if tag_type == "tool_call":
            message_id = str(tag.get("message_id", "") or "")
            if message_id:
                call_tags[message_id] = tag
        elif tag_type == "tool_result":
            call_id = _result_call_id(tag.get("message_id"))
            if call_id:
                result_tags.setdefault(call_id, []).append(tag)

    groups: list[dict[str, Any]] = []
    for call_id in sorted(set(call_tags) | set(result_tags)):
        call_tag = call_tags.get(call_id)
        result_group = sorted(
            result_tags.get(call_id, []),
            key=lambda item: int(item.get("tag_number", 0) or 0),
        )
        tool_name = str(call_tag.get("tool_name", "") or "") if call_tag else ""
        call_text = str(call_tag.get("original_text", "") or "") if call_tag else ""
        result_texts = [
            str(tag.get("original_text", "") or "")
            for tag in result_group
            if str(tag.get("original_text", "") or "").strip()
        ]
        tag_numbers = []
        if call_tag:
            tag_numbers.append(int(call_tag.get("tag_number", 0) or 0))
        tag_numbers.extend(int(tag.get("tag_number", 0) or 0) for tag in result_group)
        tag_numbers = [value for value in tag_numbers if value > 0]
        if not tag_numbers:
            continue
        groups.append(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "call_tag": call_tag,
                "result_tags": result_group,
                "call_text": call_text,
                "result_texts": result_texts,
                "tag_numbers": sorted(set(tag_numbers)),
                "max_tag_number": max(tag_numbers),
                "sample": _shorten(result_texts[0] if result_texts else call_text),
            }
        )
    groups.sort(key=lambda item: item["max_tag_number"])
    return groups


def resolve_ctx_reduce_target(
    tags: list[dict[str, Any]],
    match: str,
    *,
    kind: str | None = None,
    tool_name: str | None = None,
    protected_tags: int = 20,
) -> dict[str, Any]:
    match_norm = _normalize_text(match)
    if not match_norm:
        return {"ok": False, "error": "Error: `match` is required."}

    if kind and kind not in {"tool_call", "tool_result"}:
        return {
            "ok": False,
            "error": "Error: `kind` must be `tool_call` or `tool_result`.",
        }

    active_tag_numbers = sorted(
        (
            int(tag.get("tag_number", 0) or 0)
            for tag in tags
            if tag.get("status") == "active"
        ),
        reverse=True,
    )
    protected_set = set(active_tag_numbers[: max(0, int(protected_tags))])
    groups = _build_tool_groups(tags)
    if tool_name:
        tool_name_norm = tool_name.strip().lower()
        groups = [
            group
            for group in groups
            if str(group.get("tool_name", "") or "").strip().lower() == tool_name_norm
        ]

    matched: list[dict[str, Any]] = []
    for group in groups:
        candidate_texts: list[str] = []
        if kind in (None, "tool_call"):
            if group["call_text"]:
                candidate_texts.append(group["call_text"])
            if group["tool_name"]:
                candidate_texts.append(group["tool_name"])
        if kind in (None, "tool_result"):
            candidate_texts.extend(group["result_texts"])

        normalized = [_normalize_text(text) for text in candidate_texts if text]
        if any(text.startswith(match_norm) for text in normalized):
            matched.append(group)

    if not matched:
        return {
            "ok": False,
            "error": (
                "Error: no active tool context matched that prefix. "
                "Use the first 5-12 characters from the target tool args or tool result, "
                "and add `tool_name` if needed."
            ),
        }

    if len(matched) > 1:
        previews = []
        for group in matched[:3]:
            label = group.get("tool_name") or "(unknown tool)"
            sample = group.get("sample") or "(empty)"
            previews.append(f"`{label}` -> `{sample}`")
        return {
            "ok": False,
            "error": (
                "Error: that prefix matched multiple tool contexts: "
                + "; ".join(previews)
                + ". Provide a longer prefix."
            ),
        }

    chosen = matched[0]
    if any(tag_number in protected_set for tag_number in chosen["tag_numbers"]):
        return {
            "ok": False,
            "error": (
                "Error: the matched tool context is still inside the protected recent window. "
                "Pick an older tool call/result."
            ),
        }

    return {"ok": True, "group": chosen}


def _messages_to_context_dicts(messages: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "model_dump"):
            serialized.append(msg.model_dump())
        elif isinstance(msg, dict):
            serialized.append(dict(msg))
        else:
            serialized.append(
                {
                    "role": getattr(msg, "role", ""),
                    "content": getattr(msg, "content", None),
                    "tool_calls": getattr(msg, "tool_calls", None),
                    "tool_call_id": getattr(msg, "tool_call_id", None),
                }
            )
    return serialized


async def apply_ctx_reduce(
    plugin,
    event: AstrMessageEvent,
    run_context: ContextWrapper[AstrAgentContext],
    *,
    match: str,
    kind: str | None = None,
    tool_name: str | None = None,
) -> str:
    session_id = event.unified_msg_origin
    tags = await plugin.db.get_tags_by_session(session_id)
    resolved = resolve_ctx_reduce_target(
        tags,
        match,
        kind=kind,
        tool_name=tool_name,
        protected_tags=int(plugin.config.get("protected_tags", 20)),
    )
    if not resolved.get("ok"):
        return str(resolved.get("error", "Error: ctx_reduce failed."))

    group = resolved["group"]
    dropped = 0
    for tag_number in group["tag_numbers"]:
        await plugin.db.update_tag_drop_mode(session_id, tag_number, "full")
        await plugin.db.update_tag_status(session_id, tag_number, "dropped")
        dropped += 1

    await plugin.heuristic.cleanup_phase(event, run_context)
    tool_label = group.get("tool_name") or "tool context"
    sample = group.get("sample") or _shorten(match)
    return (
        f"Dropped {dropped} context item(s) for `{tool_label}`. "
        f"Matched prefix: `{sample}`."
    )


async def apply_ctx_compact(
    plugin,
    event: AstrMessageEvent,
    run_context: ContextWrapper[AstrAgentContext],
    *,
    mode: str,
) -> str:
    session_id = event.unified_msg_origin
    mode = str(mode or "").strip().lower()
    if mode not in {"lite", "hard"}:
        return "Error: `mode` must be `lite` or `hard`."

    messages = list(run_context.messages)
    context_dicts = _messages_to_context_dicts(messages)
    input_tokens = plugin._estimate_context_tokens(context_dicts)
    context_limit = plugin._resolve_request_context_limit(event)
    ratio = input_tokens / max(context_limit, 1)

    if mode == "lite":
        (
            dropped_count,
            saved_tokens,
        ) = await plugin.idle_compaction._apply_lite_tool_compaction(session_id)
        if dropped_count <= 0:
            return "No eligible old tool context was found for lite compaction."
        await plugin.heuristic.cleanup_phase(event, run_context)
        await plugin.db.update_session_meta(
            session_id,
            last_compaction_at=plugin._now_ms(),
            last_compaction_mode="lite",
            last_compaction_input_tokens=input_tokens,
            last_compaction_ratio=ratio,
            last_compaction_context_limit=context_limit,
        )
        await plugin.db.record_compaction_event(
            session_id,
            mode="lite",
            source="llm_tool",
            input_tokens=input_tokens,
            saved_tokens=saved_tokens,
            context_limit=context_limit,
            ratio=ratio,
        )
        return (
            f"Lite compaction dropped {dropped_count} old tool context item(s) "
            f"and saved about {saved_tokens} input tokens."
        )

    if len(context_dicts) < int(plugin.config.get("historian_min_messages", 20)):
        return (
            "Hard compaction needs more history before it can build a useful summary."
        )

    old_keep_recent = plugin.historian.config.get("historian_keep_recent")
    keep_recent = int(
        plugin.config.get(
            "historian_keep_recent_hard",
            plugin.config.get("historian_keep_recent", 10),
        )
    )
    try:
        plugin.historian.config["historian_keep_recent"] = keep_recent
        result = await plugin.historian.run_compartment_agent(session_id, context_dicts)
    finally:
        plugin.historian.config["historian_keep_recent"] = old_keep_recent

    if not result or not result.get("compartments"):
        return "Hard compaction did not find any new older context to summarize."

    last_end = max(comp["end_message"] for comp in result["compartments"])
    await plugin.db.update_session_meta(
        session_id,
        last_compaction_at=plugin._now_ms(),
        last_compaction_mode="hard",
        last_compaction_input_tokens=input_tokens,
        last_compaction_ratio=ratio,
        last_compaction_source_end_message=last_end,
        last_compaction_context_limit=context_limit,
    )
    await plugin.db.record_compaction_event(
        session_id,
        mode="hard",
        source="llm_tool",
        input_tokens=input_tokens,
        saved_tokens=0,
        context_limit=context_limit,
        ratio=ratio,
    )
    return (
        f"Hard compaction stored {len(result['compartments'])} summary compartment(s). "
        "They will be injected on the next model step."
    )


@dataclass
class CtxReduceTool(FunctionTool[AstrAgentContext]):
    name: str = "ctx_reduce"
    description: str = (
        "Deterministically remove old tool calls/results by matching a short text prefix. "
        "Best for stale tool outputs you already used."
    )
    parameters: dict = Field(default_factory=_build_reduce_parameters)
    plugin: Any = None

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        if self.plugin is None:
            return "Error: ctx_reduce tool is not initialized."
        return await apply_ctx_reduce(
            self.plugin,
            context.context.event,
            context,
            match=str(kwargs.get("match", "") or ""),
            kind=kwargs.get("kind"),
            tool_name=kwargs.get("tool_name"),
        )


@dataclass
class CtxCompactTool(FunctionTool[AstrAgentContext]):
    name: str = "ctx_compact"
    description: str = (
        "Compact the current session. "
        "`lite` performs deterministic old tool cleanup; "
        "`hard` stores historian summaries for older context."
    )
    parameters: dict = Field(default_factory=_build_compact_parameters)
    plugin: Any = None

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        if self.plugin is None:
            return "Error: ctx_compact tool is not initialized."
        return await apply_ctx_compact(
            self.plugin,
            context.context.event,
            context,
            mode=str(kwargs.get("mode", "") or ""),
        )
