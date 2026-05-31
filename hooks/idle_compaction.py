import asyncio
import time
from typing import Any

from astrbot.api import logger


class IdleCompactionService:
    def __init__(self, db, historian, config: dict, context):
        self.db = db
        self.historian = historian
        self.config = config
        self.context = context
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        if not self.config.get("idle_compaction_enabled", True):
            return
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="magic-context-idle-compaction"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        interval = max(
            15, int(self.config.get("idle_compaction_check_interval_seconds", 60))
        )
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[MagicContext] Idle compaction loop error: {e}")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def run_once(self) -> None:
        session_rows = await self.db.list_session_meta()
        for meta in session_rows:
            session_id = meta.get("session_id")
            if not session_id:
                continue
            if not self._should_compact(meta):
                continue
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
            if lock.locked():
                continue
            async with lock:
                await self._compact_session(session_id, meta)

    def _should_compact(self, meta: dict[str, Any]) -> bool:
        now_ms = int(time.time() * 1000)
        idle_after_ms = (
            int(self.config.get("idle_compaction_after_minutes", 10)) * 60 * 1000
        )
        max_idle_ms = (
            int(self.config.get("idle_compaction_max_idle_minutes", 120)) * 60 * 1000
        )
        last_response_time = meta.get("last_response_time") or 0
        if last_response_time <= 0:
            return False
        idle_ms = now_ms - int(last_response_time)
        if idle_ms < idle_after_ms or idle_ms > max_idle_ms:
            return False
        if int(meta.get("compartment_in_progress", 0) or 0) != 0:
            return False
        recent_count = int(meta.get("recent_24h_message_count", 0) or 0)
        min_recent = int(self.config.get("active_session_min_messages_24h", 12))
        if recent_count < min_recent:
            return False
        input_tokens = int(meta.get("last_request_input_tokens", 0) or 0)
        min_tokens = int(
            self.config.get(
                "idle_compaction_min_tokens",
                self.config.get("historian_chunk_tokens", 4000),
            )
        )
        if input_tokens < min_tokens:
            return False
        return True

    async def _compact_session(self, session_id: str, meta: dict[str, Any]) -> None:
        context_limit = self._resolve_context_limit(meta)
        if context_limit <= 0:
            return
        input_tokens = int(meta.get("last_request_input_tokens", 0) or 0)
        if input_tokens <= 0:
            conversation = await self._get_conversation_history(session_id)
            if not conversation:
                return
            input_tokens = self._estimate_context_tokens(conversation)
        ratio = input_tokens / max(context_limit, 1)
        threshold = float(self.config.get("lite_compaction_ratio_threshold", 0.4))
        mode = "lite" if ratio < threshold else "hard"

        if mode == "lite":
            dropped_count, saved_tokens = await self._apply_lite_tool_compaction(
                session_id
            )
            if dropped_count <= 0:
                return
            await self.db.update_session_meta(
                session_id,
                last_compaction_at=int(time.time() * 1000),
                last_compaction_mode="lite",
                last_compaction_input_tokens=input_tokens,
                last_compaction_ratio=ratio,
                last_compaction_context_limit=context_limit,
            )
            await self._record_compaction_event(
                session_id=session_id,
                mode="lite",
                source="idle",
                input_tokens=input_tokens,
                saved_tokens=saved_tokens,
                context_limit=context_limit,
                ratio=ratio,
            )
            logger.info(
                f"[MagicContext] Idle compaction completed for {session_id}: mode=lite ratio={ratio:.3f} dropped_tools={dropped_count}"
            )
            return

        conversation = await self._get_conversation_history(session_id)
        if not conversation:
            return

        min_messages = int(
            self.config.get(
                "idle_compaction_min_messages",
                self.config.get("historian_min_messages", 20),
            )
        )
        if len(conversation) < min_messages:
            return

        keep_recent = int(
            self.config.get(
                "historian_keep_recent_lite"
                if mode == "lite"
                else "historian_keep_recent_hard",
                self.config.get("historian_keep_recent", 10),
            )
        )

        old_keep_recent = self.historian.config.get("historian_keep_recent")
        try:
            self.historian.config["historian_keep_recent"] = keep_recent
            result = await self.historian.run_compartment_agent(
                session_id, conversation
            )
        finally:
            self.historian.config["historian_keep_recent"] = old_keep_recent

        if not result or not result.get("compartments"):
            return

        last_end = max(comp["end_message"] for comp in result["compartments"])
        prev_end = meta.get("last_compaction_source_end_message")
        if prev_end is not None and int(prev_end) == int(last_end):
            return

        await self.db.update_session_meta(
            session_id,
            last_compaction_at=int(time.time() * 1000),
            last_compaction_mode=mode,
            last_compaction_input_tokens=input_tokens,
            last_compaction_ratio=ratio,
            last_compaction_source_end_message=last_end,
            last_compaction_context_limit=context_limit,
        )
        await self._record_compaction_event(
            session_id=session_id,
            mode=mode,
            source="idle",
            input_tokens=input_tokens,
            saved_tokens=0,
            context_limit=context_limit,
            ratio=ratio,
        )
        logger.info(
            f"[MagicContext] Idle compaction completed for {session_id}: mode={mode} ratio={ratio:.3f}"
        )

    async def _apply_lite_tool_compaction(self, session_id: str) -> tuple[int, int]:
        tags = await self.db.get_active_tags(session_id)
        if not tags:
            return 0, 0

        max_tag = max((int(tag.get("tag_number", 0) or 0) for tag in tags), default=0)
        protected_cutoff = max_tag - int(self.config.get("protected_tags", 20))
        tool_age_cutoff = max_tag - int(self.config.get("auto_drop_tool_age", 20))
        drop_mode = (
            "full" if self.config.get("drop_tool_structure", False) else "truncated"
        )

        dropped_count = 0
        saved_tokens = 0
        for tag in tags:
            tag_num = int(tag.get("tag_number", 0) or 0)
            tag_type = tag.get("type", "")
            if tag_type not in ("tool_call", "tool_result"):
                continue
            if tag_num > protected_cutoff:
                continue
            if tag_num > tool_age_cutoff:
                continue
            await self.db.update_tag_status(session_id, tag_num, "dropped")
            await self.db.update_tag_drop_mode(session_id, tag_num, drop_mode)
            dropped_count += 1
            byte_size = int(
                tag.get("byte_size", 0) or tag.get("input_byte_size", 0) or 0
            )
            saved_tokens += max(1, int(byte_size * 0.5))

        return dropped_count, saved_tokens

    async def _get_conversation_history(self, session_id: str) -> list[dict]:
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(session_id)
        if not cid:
            return []
        conv = await conv_mgr.get_conversation(session_id, cid)
        if not conv or not getattr(conv, "history", None):
            return []
        import json

        try:
            return json.loads(conv.history)
        except Exception:
            return []

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

    def _resolve_context_limit(self, meta: dict[str, Any]) -> int:
        configured = int(self.config.get("expected_context_tokens", 0) or 0)
        if configured > 0:
            return configured
        recorded = int(meta.get("last_request_context_limit", 0) or 0)
        if recorded > 0:
            return recorded
        cfg = self.context.get_config()
        try:
            return int(
                cfg["provider_settings"].get("fallback_max_context_tokens", 128000)
            )
        except Exception:
            return 128000

    async def _record_compaction_event(self, **payload) -> None:
        record_fn = getattr(self.db, "record_compaction_event", None)
        if not callable(record_fn):
            return
        await record_fn(**payload)
