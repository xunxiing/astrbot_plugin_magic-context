import hashlib
import json

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper


class Tagger:
    """Assigns tag numbers to messages and their parts."""

    def __init__(self, db):
        self.db = db
        self._tag_counter = 0

    async def tag_phase(
        self, event: AstrMessageEvent, run_context: ContextWrapper
    ) -> None:
        """Hook handler for @filter.on_agent_begin()"""
        session_id = event.unified_msg_origin
        max_tag = await self.db.get_max_tag_number(session_id)
        count = 0

        msg_list = list(run_context.messages)
        for idx, msg in enumerate(msg_list):
            role = getattr(msg, "role", "")
            if role == "_checkpoint":
                continue

            content = getattr(msg, "content", None)
            source_id = self._message_source_id(event, msg, idx, content)

            if isinstance(content, list):
                for part_index, part in enumerate(content):
                    count += 1
                    pt = getattr(part, "type", "unknown")
                    if pt == "text":
                        text = getattr(part, "text", "") or ""
                        byte_size = len(text.encode("utf-8"))
                        tag_number = await self._assign_stable_tag(
                            session_id=session_id,
                            next_tag_number=max_tag + 1,
                            message_id=f"{source_id}:p{part_index}:text",
                            tag_type="text",
                            byte_size=byte_size,
                            original_text=text,
                        )
                        max_tag = max(max_tag, tag_number)
                    elif pt in ("thinking", "think"):
                        text = (
                            getattr(part, "text", "")
                            or getattr(part, "thinking", "")
                            or ""
                        )
                        byte_size = len(text.encode("utf-8"))
                        tag_number = await self._assign_stable_tag(
                            session_id=session_id,
                            next_tag_number=max_tag + 1,
                            message_id=f"{source_id}:p{part_index}:thinking",
                            tag_type="thinking",
                            byte_size=byte_size,
                            original_text=text,
                        )
                        max_tag = max(max_tag, tag_number)
            elif isinstance(content, str):
                count += 1
                byte_size = len(content.encode("utf-8"))
                tag_number = await self._assign_stable_tag(
                    session_id=session_id,
                    next_tag_number=max_tag + 1,
                    message_id=source_id,
                    tag_type="message",
                    byte_size=byte_size,
                    original_text=content,
                )
                max_tag = max(max_tag, tag_number)

            if role == "assistant":
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    owner_id = source_id
                    for tc in tool_calls:
                        count += 1
                        info = self._extract_tool_call_info(tc)
                        tag_number = await self._assign_stable_tag(
                            session_id=session_id,
                            next_tag_number=max_tag + 1,
                            message_id=info["call_id"],
                            tag_type="tool_call",
                            tool_name=info["tool_name"],
                            input_byte_size=info["input_byte_size"],
                            tool_owner_message_id=owner_id,
                        )
                        max_tag = max(max_tag, tag_number)

            if role == "tool":
                tool_call_id = getattr(msg, "tool_call_id", None)
                if tool_call_id:
                    count += 1
                    byte_size = self._estimate_byte_size(content)
                    original_text = (
                        content
                        if isinstance(content, str)
                        else json.dumps(content, ensure_ascii=False)
                    )
                    owner_id = self._nearest_tool_owner(
                        msg_list, idx, tool_call_id, event
                    )
                    tag_number = await self._assign_stable_tag(
                        session_id=session_id,
                        next_tag_number=max_tag + 1,
                        message_id=f"tool:{tool_call_id}",
                        tag_type="tool_result",
                        byte_size=byte_size,
                        original_text=original_text,
                        tool_owner_message_id=owner_id,
                    )
                    max_tag = max(max_tag, tag_number)

        logger.info(f"[MagicContext] Tagged {count} items for session {session_id}")

    async def _assign_stable_tag(
        self,
        session_id: str,
        next_tag_number: int,
        message_id: str,
        tag_type: str,
        **kwargs,
    ) -> int:
        existing = await self.db.get_tag_number_by_identity(
            session_id,
            message_id,
            tag_type,
            kwargs.get("tool_owner_message_id"),
        )
        tag_number = existing if existing is not None else next_tag_number
        await self.db.assign_tag(
            session_id=session_id,
            tag_number=tag_number,
            message_id=message_id,
            tag_type=tag_type,
            **kwargs,
        )
        return tag_number

    def _message_source_id(
        self, event: AstrMessageEvent, msg, index: int, content
    ) -> str:
        checkpoint = getattr(msg, "_checkpoint_after", None)
        checkpoint_id = getattr(checkpoint, "id", None)
        if checkpoint_id:
            return f"ckpt:{checkpoint_id}"

        tool_call_id = getattr(msg, "tool_call_id", None)
        if tool_call_id:
            return f"tool-result:{tool_call_id}:{index}"

        explicit_id = getattr(msg, "id", None)
        if isinstance(explicit_id, str) and explicit_id:
            return f"msg:{explicit_id}"

        return f"ctx:{event.unified_msg_origin}:idx:{index}:hash:{self._content_hash(content)}"

    def _nearest_tool_owner(
        self,
        messages: list,
        tool_index: int,
        tool_call_id: str,
        event: AstrMessageEvent,
    ) -> str | None:
        for owner_index in range(tool_index - 1, -1, -1):
            candidate = messages[owner_index]
            if getattr(candidate, "role", "") != "assistant":
                continue
            for tc in getattr(candidate, "tool_calls", None) or []:
                info = self._extract_tool_call_info(tc)
                if info["call_id"] == tool_call_id:
                    return self._message_source_id(
                        event,
                        candidate,
                        owner_index,
                        getattr(candidate, "content", None),
                    )
        return None

    @staticmethod
    def _content_hash(content) -> str:
        try:
            if isinstance(content, str):
                raw = content
            else:
                raw = json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            raw = str(content)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _extract_tool_call_info(tc):
        if isinstance(tc, dict):
            call_id = tc.get("id", str(id(tc)))
            func = tc.get("function", {})
            tool_name = func.get("name", "") if isinstance(func, dict) else ""
            args = func.get("arguments", "") if isinstance(func, dict) else ""
        else:
            call_id = getattr(tc, "id", str(id(tc)))
            func = getattr(tc, "function", None)
            if func is not None:
                tool_name = getattr(func, "name", "") or ""
                args = getattr(func, "arguments", "") or ""
            else:
                tool_name = ""
                args = ""
        try:
            input_byte_size = len(json.dumps(args).encode("utf-8"))
        except Exception:
            input_byte_size = len(str(args).encode("utf-8"))
        return {
            "call_id": call_id,
            "tool_name": tool_name,
            "input_byte_size": input_byte_size,
        }

    @staticmethod
    def _estimate_byte_size(content):
        if content is None:
            return 0
        if isinstance(content, str):
            return len(content.encode("utf-8"))
        if isinstance(content, list):
            total = 0
            for p in content:
                total += len(str(p).encode("utf-8"))
            return total
        return len(str(content).encode("utf-8"))
