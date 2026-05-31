import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from quart import request

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.run_context import ContextWrapper

from .hooks.caveman import apply_caveman_cleanup
from .hooks.heuristic_cleanup import HeuristicCleanup
from .hooks.historian import HistorianAgent
from .hooks.idle_compaction import IdleCompactionService
from .hooks.injection import Injector
from .hooks.llm_reduce_tool import queue_ctx_reduce
from .hooks.parallel_tool import ParallelToolUseTool
from .hooks.postprocess import PostProcessor
from .hooks.strip import strip_cleared_reasoning, strip_inline_thinking
from .hooks.tag import Tagger
from .hooks.tool_appeal import (
    build_tool_appeal_text,
    clear_pending_tool_names,
    filter_pending_tools,
    get_tool_catalog,
    inject_appeal_only_into_request,
    load_tool_appeal_state,
    stage_new_tools_if_any,
)
from .storage.database import MagicContextDB

PLUGIN_NAME = "astrbot_plugin_magic_context"


class _MessageTarget:
    """Adapter wrapping an AstrBot Message to provide get_content/set_content."""

    def __init__(self, msg, index: int):
        self._msg = msg
        self._index = index

    def get_content(self) -> str:
        content = getattr(self._msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                getattr(p, "text", "") or ""
                for p in content
                if hasattr(p, "type") and getattr(p, "type", "") == "text"
            )
        return ""

    def set_content(self, text: str):
        if hasattr(self._msg, "content") and isinstance(self._msg.content, str):
            self._msg.content = text


def _message_to_dict(msg, index: int, message_id: str | None = None) -> dict:
    content = getattr(msg, "content", None)
    role = getattr(msg, "role", "user")
    mid = message_id or getattr(msg, "id", None) or str(id(msg))
    if isinstance(content, list):
        content = [
            {
                "type": getattr(p, "type", "text"),
                "text": getattr(p, "text", ""),
            }
            for p in content
            if hasattr(p, "type")
        ]
    return {"id": mid, "role": role, "content": content, "_index": index}


def _dicts_to_messages(dicts: list[dict], messages: list):
    for d in dicts:
        idx = d.get("_index", -1)
        if 0 <= idx < len(messages):
            msg = messages[idx]
            new_content = d.get("content")
            if hasattr(msg, "content") and isinstance(msg.content, list):
                for i, part in enumerate(msg.content):
                    if (
                        isinstance(new_content, list)
                        and i < len(new_content)
                        and isinstance(new_content[i], dict)
                    ):
                        if hasattr(part, "text") and "text" in new_content[i]:
                            part.text = new_content[i]["text"]


DEFAULT_CONFIG = {
    "idle_compaction_enabled": True,
    "idle_compaction_after_minutes": 10,
    "idle_compaction_max_idle_minutes": 120,
    "idle_compaction_check_interval_seconds": 60,
    "active_session_window_hours": 24,
    "active_session_min_messages_24h": 12,
    "idle_compaction_min_messages": 20,
    "idle_compaction_min_tokens": 4000,
    "expected_context_tokens": 0,
    "lite_compaction_ratio_threshold": 0.4,
    "historian_keep_recent_lite": 12,
    "historian_keep_recent_hard": 6,
    "request_fallback_ratio_threshold": 0.85,
    "auto_drop_tool_age": 20,
    "protected_tags": 20,
    "drop_tool_structure": False,
    "historian_chunk_tokens": 4000,
    "historian_timeout_ms": 30000,
    "historian_min_messages": 20,
    "historian_keep_recent": 10,
    "historian_two_pass": False,
    "max_historian_retries": 2,
    "caveman_enabled": True,
    "caveman_min_chars": 200,
    "strip_thinking": True,
    "clear_reasoning_age": 50,
}


class MagicContextPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.data_dir = Path(StarTools.get_data_dir())
        self.db = MagicContextDB(self.data_dir)
        merged = dict(DEFAULT_CONFIG)
        if config:
            merged.update(dict(config))
        self.config = merged

        self.tagger = Tagger(self.db)
        self.heuristic = HeuristicCleanup(self.db, self.config)
        self.historian = HistorianAgent(self.db, self.config)
        self.injector = Injector(self.db)
        self.postprocessor = PostProcessor(self.db)
        self.parallel_tool = ParallelToolUseTool()
        self.tool_appeal_state_path = self.data_dir / "tool_appeal_state.json"
        self.idle_compaction = IdleCompactionService(
            self.db, self.historian, self.config, self.context
        )
        self._register_page_apis()
        self.context.add_llm_tools(self.parallel_tool)

    async def __start__(self):
        await self.db.init()
        self.historian._llm_fn = self._historian_llm
        await self.idle_compaction.start()
        self._stage_new_tools_after_reload()
        logger.info("[MagicContext] Plugin started, all modules initialized")

    async def __stop__(self):
        await self.idle_compaction.stop()
        self.context.unregister_llm_tool(self.parallel_tool.name)

    def _register_page_apis(self) -> None:
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard/overview",
            self.page_dashboard_overview,
            ["GET"],
            "Magic Context dashboard overview",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard/curve",
            self.page_dashboard_curve,
            ["GET"],
            "Magic Context dashboard curve",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard/config",
            self.page_dashboard_config_get,
            ["GET"],
            "Magic Context dashboard config",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard/config",
            self.page_dashboard_config_post,
            ["POST"],
            "Magic Context dashboard config save",
        )

    async def _historian_llm(self, prompt_text: str) -> str:
        """LLM backend for Historian Agent. Uses AstrBot's provider."""
        try:
            provider = self.context.get_using_provider()
            system_prompt = (
                "You are a conversation historian. Output ONLY the requested XML format, "
                "no markdown fences, no extra text."
            )
            raw = await provider.text_chat(
                prompt=prompt_text,
                system_prompt=system_prompt,
                session_id=None,
            )
            if isinstance(raw, LLMResponse):
                return getattr(raw, "completion_text", "") or ""
            return str(raw) if raw else ""
        except Exception as e:
            logger.error(f"[MagicContext] Historian LLM call failed: {e}")
            return ""

    # ── Phase 1: Tagging ──────────────────────────────────────────
    @filter.on_agent_begin()
    async def phase_1_tag(self, event: AstrMessageEvent, run_context: ContextWrapper):
        await self.tagger.tag_phase(event, run_context)

    # ── Phase 2: Heuristic Cleanup ─────────────────────────────────
    @filter.on_agent_begin()
    async def phase_2_cleanup(
        self, event: AstrMessageEvent, run_context: ContextWrapper
    ):
        result = await self.heuristic.cleanup_phase(event, run_context)
        if result and sum(result.values()) > 0:
            logger.info(f"[MagicContext] Cleanup: {result}")

    # ── Phase 3: Content Compression (reasoning + caveman) ────────
    @filter.on_agent_begin()
    async def phase_3_compress(
        self, event: AstrMessageEvent, run_context: ContextWrapper
    ):
        messages = run_context.messages
        session_id = event.unified_msg_origin

        max_tag = await self.db.get_max_tag_number(session_id)
        if max_tag == 0:
            return

        # Build {message_id: tag_number} map for strip functions
        all_tags = await self.db.get_active_tags(session_id)
        tag_map: dict[str, int] = {}
        message_targets: dict[int, object] = {}
        msg_list = list(messages)
        self._apply_visible_tag_prefixes(event, msg_list, all_tags)
        source_ids = [
            self.tagger._message_source_id(
                event, msg, idx, getattr(msg, "content", None)
            )
            for idx, msg in enumerate(msg_list)
        ]
        for tag in all_tags or []:
            mid = tag.get("message_id")
            tn = tag.get("tag_number")
            if mid and tn is not None:
                tag_map[mid] = tn
            # Build caveman targets: tag_number -> message wrapper
            for idx, msg in enumerate(msg_list):
                source_id = source_ids[idx]
                if (
                    getattr(msg, "id", None) == mid
                    or str(id(msg)) == mid
                    or mid == source_id
                    or (isinstance(mid, str) and mid.startswith(f"{source_id}:"))
                ):
                    message_targets[tn] = _MessageTarget(msg, idx)
                    break

        clear_age = self.config.get("clear_reasoning_age", 50)
        if self.config.get("strip_thinking", True) and tag_map:
            msg_dicts = [
                _message_to_dict(m, i, source_ids[i]) for i, m in enumerate(msg_list)
            ]
            strip_inline_thinking(msg_dicts, tag_map, clear_age, max_tag)
            strip_cleared_reasoning(msg_dicts)
            _dicts_to_messages(msg_dicts, msg_list)

        if self.config.get("caveman_enabled", True) and all_tags and message_targets:
            await apply_caveman_cleanup(
                session_id,
                self.db,
                message_targets,
                all_tags,
                {
                    "enabled": True,
                    "min_chars": self.config.get("caveman_min_chars", 200),
                    "protected_tags": self.config.get("protected_tags", 20),
                },
            )

    # ── Phase 4: Historian ─────────────────────────────────────────
    @filter.on_llm_request(priority=50)
    async def phase_4_historian(self, event: AstrMessageEvent, req: ProviderRequest):
        self._ensure_magic_context_guidance(req)
        await self._inject_pending_tool_appeal(event, req)
        session_id = event.unified_msg_origin
        context_limit = self._resolve_request_context_limit(event)
        input_tokens = self._estimate_context_tokens(req.contexts)
        ratio = input_tokens / max(context_limit, 1)
        await self.db.get_or_create_session_meta(session_id)
        await self.db.update_session_meta(
            session_id,
            last_request_input_tokens=input_tokens,
            last_request_context_limit=context_limit,
        )
        await self.db.record_context_sample(
            session_id,
            source="request",
            input_tokens=input_tokens,
            context_limit=context_limit,
            ratio=ratio,
        )

        existing_compartments = await self.db.get_compartments(session_id)
        if existing_compartments:
            return

        if len(req.contexts) < self.config.get("historian_min_messages", 20):
            return

        if ratio < float(self.config.get("request_fallback_ratio_threshold", 0.85)):
            return

        try:
            result = await asyncio.wait_for(
                self.historian.run_compartment_agent(session_id, req.contexts),
                timeout=self.config.get("historian_timeout_ms", 30000) / 1000,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[MagicContext] Historian timed out for {session_id}")
            return
        except Exception as e:
            logger.error(f"[MagicContext] Historian error: {e}")
            return

        if result and result.get("compartments"):
            mode = (
                "lite"
                if ratio
                < float(self.config.get("lite_compaction_ratio_threshold", 0.4))
                else "hard"
            )
            last_end = max(comp["end_message"] for comp in result["compartments"])
            await self.db.update_session_meta(
                session_id,
                last_compaction_at=self._now_ms(),
                last_compaction_mode=mode,
                last_compaction_input_tokens=input_tokens,
                last_compaction_ratio=ratio,
                last_compaction_source_end_message=last_end,
                last_compaction_context_limit=context_limit,
            )
            await self.db.record_compaction_event(
                session_id,
                mode=mode,
                source="request_fallback",
                input_tokens=input_tokens,
                saved_tokens=0,
                context_limit=context_limit,
                ratio=ratio,
            )
            logger.info(
                f"[MagicContext] Request fallback historian: {len(result['compartments'])} compartments, "
                f"{len(result.get('facts', []))} facts, ratio={ratio:.3f}"
            )

    # ── Phase 5: Injection ─────────────────────────────────────────
    @filter.on_llm_request(priority=40)
    async def phase_5_inject(self, event: AstrMessageEvent, req: ProviderRequest):
        await self.injector.inject_phase(event, req)

    # ── Phase 6: Post-processing ──────────────────────────────────
    @filter.on_llm_response(priority=90)
    async def phase_6a_tokens(self, event: AstrMessageEvent, response: LLMResponse):
        await self.postprocessor.token_recording_phase(event, response)

    @filter.after_message_sent(priority=50)
    async def phase_6b_archive(self, event: AstrMessageEvent):
        await self.postprocessor.archive_phase(event)

    def _resolve_request_context_limit(self, event: AstrMessageEvent) -> int:
        configured = int(self.config.get("expected_context_tokens", 0) or 0)
        if configured > 0:
            return configured
        provider = self.context.get_using_provider(event.unified_msg_origin)
        provider_limit = 0
        try:
            provider_limit = int(
                getattr(provider, "provider_config", {}).get("max_context_tokens", 0)
                or 0
            )
        except Exception:
            provider_limit = 0
        if provider_limit > 0:
            return provider_limit
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        try:
            return int(
                cfg["provider_settings"].get("fallback_max_context_tokens", 128000)
            )
        except Exception:
            return 128000

    def _estimate_context_tokens(self, contexts: list[dict]) -> int:
        total_chars = 0
        for item in contexts:
            content = item.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total_chars += len(str(part.get("text", "")))
        return max(1, int(total_chars * 0.5))

    def _now_ms(self) -> int:
        import time

        return int(time.time() * 1000)

    def _ensure_magic_context_guidance(self, req: ProviderRequest) -> None:
        guidance = self._build_reduce_guidance()
        contexts = list(getattr(req, "contexts", []) or [])
        if not contexts:
            req.contexts = [{"role": "system", "content": guidance}]
            return

        if contexts and contexts[0].get("role") == "system":
            content = str(contexts[0].get("content", "") or "")
            if "Use `ctx_reduce` to manage context size." in content:
                req.contexts = contexts
                return
            contexts[0]["content"] = f"{content.rstrip()}\n\n{guidance}"
        else:
            contexts.insert(0, {"role": "system", "content": guidance})
        req.contexts = contexts

    def _stage_new_tools_after_reload(self) -> None:
        tool_mgr = self.context.get_llm_tool_manager()
        current_catalog = get_tool_catalog(tool_mgr)
        stage_new_tools_if_any(self.tool_appeal_state_path, current_catalog)

    async def _inject_pending_tool_appeal(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        pending_tools = load_tool_appeal_state(self.tool_appeal_state_path).get(
            "pending_tools", {}
        )
        if not pending_tools:
            return

        session_id = event.unified_msg_origin
        if not session_id:
            return

        seen_tool_names: set[str] = set()
        for tool_name in pending_tools:
            if await self.db.session_has_tool_call(session_id, tool_name):
                seen_tool_names.add(tool_name)

        eligible_tools = filter_pending_tools(pending_tools, seen_tool_names)
        if not eligible_tools:
            return

        appeal_text = build_tool_appeal_text(eligible_tools)
        if inject_appeal_only_into_request(req, appeal_text):
            clear_pending_tool_names(self.tool_appeal_state_path, set(eligible_tools))

    def _build_reduce_guidance(self) -> str:
        protected = int(self.config.get("protected_tags", 20))
        return (
            "## Magic Context\n\n"
            "Messages and tool outputs may contain tag markers like §12§.\n"
            "Use `ctx_reduce` to manage context size.\n"
            "- `drop`: remove old tags entirely, best for tool outputs you already used.\n"
            f"- Syntax: `3-5`, `1,2,9`, `1-5,8`. Last {protected} tags are protected.\n"
            "- Prefer dropping old `tool_call` and `tool_result` tags.\n"
            "- Never drop user messages blindly.\n"
            "- Before your turn finishes, consider using `ctx_reduce` to drop large tool outputs you no longer need."
        )

    def _apply_visible_tag_prefixes(
        self, event: AstrMessageEvent, messages: list, all_tags: list[dict]
    ) -> None:
        if not all_tags:
            return

        tag_index: dict[tuple[str, str], int] = {}
        for tag in all_tags:
            message_id = str(tag.get("message_id", "") or "")
            tag_type = str(tag.get("type", "") or "")
            tag_number = int(tag.get("tag_number", 0) or 0)
            if message_id and tag_type and tag_number > 0:
                tag_index[(tag_type, message_id)] = tag_number

        for idx, msg in enumerate(messages):
            role = getattr(msg, "role", "")
            if role == "_checkpoint":
                continue
            content = getattr(msg, "content", None)
            source_id = self.tagger._message_source_id(event, msg, idx, content)

            if isinstance(content, str):
                tag_number = tag_index.get(("message", source_id))
                if tag_number and not content.startswith(f"§{tag_number}§"):
                    msg.content = f"§{tag_number}§ {content}"
                continue

            if isinstance(content, list):
                for part_index, part in enumerate(content):
                    part_type = getattr(part, "type", "")
                    if part_type not in ("text", "thinking", "think"):
                        continue
                    suffix = "text" if part_type == "text" else "thinking"
                    tag_number = tag_index.get(
                        (suffix, f"{source_id}:p{part_index}:{suffix}")
                    )
                    text_attr = "text"
                    part_text = getattr(part, text_attr, "") or ""
                    if (
                        tag_number
                        and part_text
                        and not part_text.startswith(f"§{tag_number}§")
                    ):
                        setattr(part, text_attr, f"§{tag_number}§ {part_text}")

    @filter.llm_tool(name="ctx_reduce")
    async def ctx_reduce(self, event: AstrMessageEvent, drop: str) -> str:
        """Drop old context tags you no longer need.

        Args:
            drop(string): Tag ids to drop entirely, like 3-5 or 1,2,9. Prefer old tool_call and tool_result tags. Never drop user messages blindly.
        """
        return await queue_ctx_reduce(self, event.unified_msg_origin, drop)

    @filter.llm_tool(name="parallel_tool_use")
    async def parallel_tool_use_placeholder(
        self,
        event: AstrMessageEvent,
        tool_uses: str,
    ) -> str:
        """Run multiple independent tools in parallel.

        Args:
            tool_uses(string): JSON array of tool calls for UI visibility only. Runtime uses the structured dataclass tool schema instead of this string form.
        """
        return (
            "Error: `parallel_tool_use` requires the structured runtime schema. "
            "Call it with `tool_uses: [{recipient_name, parameters}]` from tool use, "
            "not via the string placeholder."
        )

    async def page_dashboard_overview(self):
        now = datetime.now().astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(day_start.timestamp() * 1000)
        events = await self.db.get_compaction_events_since(start_ms)
        samples = await self.db.get_context_samples_since(start_ms, limit=240)
        session_rows = await self.db.list_session_meta()

        lite_count = sum(1 for item in events if item.get("mode") == "lite")
        hard_count = sum(1 for item in events if item.get("mode") == "hard")
        compacted_tokens = sum(int(item.get("input_tokens", 0) or 0) for item in events)
        saved_tokens = sum(int(item.get("saved_tokens", 0) or 0) for item in events)
        avg_ratio = 0.0
        if samples:
            avg_ratio = sum(float(item.get("ratio", 0) or 0) for item in samples) / len(
                samples
            )

        active_cutoff_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
        active_sessions = sum(
            1
            for meta in session_rows
            if int(meta.get("last_response_time", 0) or 0) >= active_cutoff_ms
        )
        latest_event_at = events[-1]["created_at"] if events else None

        return {
            "date_label": day_start.strftime("%Y-%m-%d"),
            "compaction_count": len(events),
            "compacted_tokens": compacted_tokens,
            "saved_tokens": saved_tokens,
            "lite_count": lite_count,
            "hard_count": hard_count,
            "avg_ratio": round(avg_ratio, 4),
            "active_sessions": active_sessions,
            "latest_event_at": latest_event_at,
        }

    async def page_dashboard_curve(self):
        now = datetime.now().astimezone()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(day_start.timestamp() * 1000)
        samples = await self.db.get_context_samples_since(start_ms, limit=240)
        return {
            "points": [
                {
                    "created_at": int(item.get("created_at", 0) or 0),
                    "source": item.get("source", "request"),
                    "input_tokens": int(item.get("input_tokens", 0) or 0),
                    "context_limit": int(item.get("context_limit", 0) or 0),
                    "ratio": round(float(item.get("ratio", 0) or 0), 4),
                }
                for item in samples
            ]
        }

    async def page_dashboard_config_get(self):
        return {
            "config": {
                key: self.config.get(key, DEFAULT_CONFIG.get(key))
                for key in self._quick_config_keys()
            }
        }

    async def page_dashboard_config_post(self):
        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return {"ok": False, "message": "配置格式无效"}

        updates = {}
        for key in self._quick_config_keys():
            if key not in payload:
                continue
            updates[key] = self._coerce_quick_config_value(key, payload[key])

        if not updates:
            return {"ok": False, "message": "没有可保存的配置项"}

        metadata = self.context.get_registered_star(PLUGIN_NAME)
        if not metadata or not getattr(metadata, "config", None):
            return {"ok": False, "message": "未找到插件配置对象"}

        merged = dict(metadata.config)
        merged.update(updates)
        metadata.config.save_config(merged)
        self.config.update(updates)
        await self._refresh_idle_compaction_runtime()

        return {
            "ok": True,
            "message": "配置已保存",
            "config": {
                key: self.config.get(key, DEFAULT_CONFIG.get(key))
                for key in self._quick_config_keys()
            },
        }

    async def _refresh_idle_compaction_runtime(self) -> None:
        await self.idle_compaction.stop()
        if self.config.get("idle_compaction_enabled", True):
            await self.idle_compaction.start()

    def _quick_config_keys(self) -> tuple[str, ...]:
        return (
            "idle_compaction_enabled",
            "idle_compaction_after_minutes",
            "idle_compaction_max_idle_minutes",
            "active_session_min_messages_24h",
            "expected_context_tokens",
            "lite_compaction_ratio_threshold",
            "protected_tags",
            "auto_drop_tool_age",
        )

    def _coerce_quick_config_value(self, key: str, value):
        if key == "idle_compaction_enabled":
            return bool(value)
        if key == "lite_compaction_ratio_threshold":
            return max(0.05, min(0.95, float(value)))
        return max(0, int(value))
