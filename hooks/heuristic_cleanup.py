import hashlib
import json
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper

SYSTEM_REMINDER_REGEX = re.compile(
    r"<system-reminder>[\s\S]*?<\/system-reminder>",
    re.IGNORECASE | re.DOTALL,
)
TODO_CHANGED_PATTERN = re.compile(r"\[Todo list has changed\]", re.IGNORECASE)
NOTE_PATTERN = re.compile(r"^\[Note:.*?\]", re.IGNORECASE)

DEFAULT_CONFIG = {
    "auto_drop_tool_age": 20,
    "drop_tool_structure": True,
    "protected_tags": 20,
    "truncated_tool_chars": 800,
}


def _strip_system_injection(content: str) -> str | None:
    has_injection = False
    if SYSTEM_REMINDER_REGEX.search(content):
        has_injection = True
    if TODO_CHANGED_PATTERN.search(content):
        has_injection = True
    if NOTE_PATTERN.search(content):
        has_injection = True
    if not has_injection:
        return None
    cleaned = SYSTEM_REMINDER_REGEX.sub("", content)
    return cleaned.strip()


class HeuristicCleanup:
    """Removes low-value content via heuristic rules."""

    DEDUP_SAFE_TOOLS = {
        "mcp_grep",
        "mcp_read",
        "mcp_glob",
        "mcp_ast_grep_search",
        "mcp_lsp_diagnostics",
        "mcp_lsp_symbols",
        "mcp_lsp_find_references",
        "mcp_lsp_goto_definition",
        "mcp_lsp_prepare_rename",
    }

    def __init__(self, db, config=None):
        self.db = db
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    async def cleanup_phase(
        self, event: AstrMessageEvent, run_context: ContextWrapper
    ) -> dict:
        """Hook handler for @filter.on_agent_begin()"""
        session_id = event.unified_msg_origin
        tags = await self.db.get_tags_by_session(session_id)
        max_tag = await self.db.get_max_tag_number(session_id)

        tool_age_cutoff = max_tag - self.config["auto_drop_tool_age"]
        protected_cutoff = max_tag - self.config.get("protected_tags", 20)

        messages = run_context.messages

        dropped_tools = 0
        dedup_count = 0
        dropped_injections = 0
        applied_drops = 0
        truncated_tools = 0
        pending_drops = 0

        # Phase A: Auto-drop old tool tags
        for tag in self._active_tags(tags):
            tag_num = tag.get("tag_number", 0)
            tag_type = tag.get("type", "")
            tag_status = tag.get("status", "")
            if tag_status != "active":
                continue
            if tag_num > protected_cutoff:
                continue
            if tag_num <= tool_age_cutoff and tag_type in ("tool_call", "tool_result"):
                await self.db.update_tag_status(session_id, tag_num, "dropped")
                drop_mode = (
                    "full" if self.config["drop_tool_structure"] else "truncated"
                )
                await self.db.update_tag_drop_mode(session_id, tag_num, drop_mode)
                tag["status"] = "dropped"
                tag["drop_mode"] = drop_mode
                dropped_tools += 1

        # Phase B: Strip system injections from old messages
        for msg in messages:
            content = getattr(msg, "content", None)
            if not isinstance(content, str):
                continue

            stripped = _strip_system_injection(content)
            if stripped is None:
                continue

            if stripped.strip() == "":
                msg.content = "[dropped]"
                dropped_injections += 1
            elif stripped != content:
                msg.content = stripped
                dropped_injections += 1

        # Phase C: Tool deduplication
        tag_index = self._build_tag_index(tags)
        tags_by_number = {tag.get("tag_number"): tag for tag in tags}
        fingerprints = self._build_tool_fingerprints(messages, tag_index, event)
        if fingerprints:
            fp_groups: dict[str, list[tuple[int, dict]]] = {}
            for fp_key, entries in fingerprints.items():
                for entry in entries:
                    msg_idx, tag = entry
                    tag_num = tag.get("tag_number", 0)
                    if tag_num > protected_cutoff:
                        continue
                    fp_groups.setdefault(fp_key, []).append((tag_num, entry))

            for fp_key, group in fp_groups.items():
                if len(group) <= 1:
                    continue
                group.sort(key=lambda x: x[0])
                for i in range(len(group) - 1):
                    tag_num = group[i][0]
                    await self.db.update_tag_drop_mode(
                        session_id,
                        tag_num,
                        "full" if self.config["drop_tool_structure"] else "truncated",
                    )
                    await self.db.update_tag_status(session_id, tag_num, "dropped")
                    tag = tags_by_number.get(tag_num)
                    if tag is None:
                        continue
                    tag["status"] = "dropped"
                    tag["drop_mode"] = (
                        "full" if self.config["drop_tool_structure"] else "truncated"
                    )
                    dedup_count += 1

        # Phase D: Apply dropped/truncated tag state to live messages.
        apply_result = self._apply_tag_drops(messages, tags, tag_index, event)
        applied_drops = apply_result["dropped"]
        truncated_tools = apply_result["truncated"]

        # Phase E: Remove empty/sentinel messages after applying drops.
        to_remove = []
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", None)
            has_tool_calls = bool(getattr(msg, "tool_calls", None))

            if role == "_checkpoint":
                continue

            if isinstance(content, str):
                if content.strip() == "[dropped]":
                    to_remove.append(i)
                    continue
                if len(content.strip()) < 2 and not has_tool_calls and role != "system":
                    to_remove.append(i)
                    continue
            elif isinstance(content, list):
                if not content and not has_tool_calls:
                    to_remove.append(i)
                    continue
            elif content is None:
                if not has_tool_calls and role != "system":
                    to_remove.append(i)
                    continue

        for idx in to_remove:
            del messages[idx]

        # Phase F: Apply pending ops and replay them immediately.
        pending_ops = await self.db.get_pending_ops(session_id)
        if pending_ops:
            tags_by_num = {t.get("tag_number", 0): t for t in tags}
            for op in pending_ops:
                tag_id = op.get("tag_id")
                if tag_id is None:
                    continue
                tag = tags_by_num.get(tag_id)
                if tag and tag.get("status") in ("compacted", "dropped"):
                    continue
                await self.db.update_tag_status(session_id, tag_id, "dropped")
                if tag:
                    tag["status"] = "dropped"
                    pending_drops += 1
            await self.db.clear_pending_ops(session_id)
            apply_result = self._apply_tag_drops(messages, tags, tag_index, event)
            applied_drops += apply_result["dropped"]
            truncated_tools += apply_result["truncated"]

        if (
            dropped_tools
            or dedup_count
            or dropped_injections
            or applied_drops
            or truncated_tools
            or pending_drops
        ):
            logger.info(
                f"[MagicContext] heuristic cleanup: dropped {dropped_tools} tool tags, "
                f"deduplicated {dedup_count} tool calls, "
                f"dropped {dropped_injections} system injections, "
                f"applied {applied_drops} drops, truncated {truncated_tools} tools"
            )

        return {
            "dropped_tools": dropped_tools,
            "deduplicated": dedup_count,
            "dropped_injections": dropped_injections,
            "applied_drops": applied_drops,
            "truncated_tools": truncated_tools,
            "pending_drops": pending_drops,
        }

    @staticmethod
    def _active_tags(tags: list[dict]) -> list[dict]:
        return [tag for tag in tags if tag.get("status") == "active"]

    def _build_tag_index(self, tags: list[dict]) -> dict[tuple, dict]:
        index: dict[tuple, dict] = {}
        for tag in tags:
            tag_type = tag.get("type")
            message_id = tag.get("message_id")
            owner_id = tag.get("tool_owner_message_id")
            if not message_id:
                continue
            index[(tag_type, message_id, owner_id)] = tag
            if tag_type in ("tool_call", "tool_result"):
                index[(tag_type, message_id, None)] = tag
        return index

    def _build_tool_fingerprints(
        self, messages, tag_index: dict[tuple, dict], event: AstrMessageEvent
    ) -> dict[str, list[tuple[int, dict]]]:
        fingerprints: dict[str, list[tuple[int, dict]]] = {}

        for msg_idx, msg in enumerate(list(messages)):
            role = getattr(msg, "role", "")
            if role != "assistant":
                continue
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                continue
            owner_id = self._message_source_id(
                event, msg, msg_idx, getattr(msg, "content", None)
            )

            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    tool_name = func.get("name", "") if isinstance(func, dict) else ""
                    args = func.get("arguments", "") if isinstance(func, dict) else ""
                    call_id = tc.get("id", str(id(tc)))
                else:
                    func = getattr(tc, "function", None)
                    if func is not None:
                        tool_name = getattr(func, "name", "") or ""
                        args = getattr(func, "arguments", "") or ""
                    else:
                        tool_name = ""
                        args = ""
                    call_id = getattr(tc, "id", str(id(tc)))

                if tool_name not in self.DEDUP_SAFE_TOOLS:
                    continue
                tag = tag_index.get(("tool_call", call_id, owner_id)) or tag_index.get(
                    ("tool_call", call_id, None)
                )
                if not tag:
                    continue

                try:
                    args_key = json.dumps(args, sort_keys=True) if args else "{}"
                except Exception:
                    args_key = str(args)

                fp_key = hashlib.md5(f"{tool_name}:{args_key}".encode()).hexdigest()
                fingerprints.setdefault(fp_key, []).append(
                    (
                        msg_idx,
                        {
                            **tag,
                            "tool_name": tool_name,
                            "args": args_key,
                            "call_id": call_id,
                        },
                    )
                )

        return fingerprints

    def _apply_tag_drops(
        self,
        messages,
        tags: list[dict],
        tag_index: dict[tuple, dict],
        event: AstrMessageEvent,
    ) -> dict[str, int]:
        dropped = 0
        truncated = 0
        dropped_tags = [
            tag
            for tag in tags
            if tag.get("status") == "dropped"
            and tag.get("type") in ("message", "text", "tool_call", "tool_result")
        ]
        if not dropped_tags:
            return {"dropped": 0, "truncated": 0}

        dropped_by_tag = {tag.get("tag_number"): tag for tag in dropped_tags}
        for idx, msg in enumerate(list(messages)):
            role = getattr(msg, "role", "")
            if role == "_checkpoint":
                continue
            source_id = self._message_source_id(
                event, msg, idx, getattr(msg, "content", None)
            )

            if role == "assistant":
                dropped += self._apply_tool_call_drops(msg, source_id, dropped_tags)

            if role == "tool":
                tool_call_id = getattr(msg, "tool_call_id", None)
                if not tool_call_id:
                    continue
                owner_id = self._nearest_tool_owner(messages, idx, tool_call_id, event)
                tag = tag_index.get(("tool_result", f"tool:{tool_call_id}", owner_id))
                if not tag or tag.get("tag_number") not in dropped_by_tag:
                    tag = tag_index.get(("tool_call", tool_call_id, owner_id))
                if not tag or tag.get("tag_number") not in dropped_by_tag:
                    continue
                mode = tag.get("drop_mode") or "full"
                if mode == "truncated":
                    if self._truncate_message_content(msg, tag):
                        truncated += 1
                else:
                    if self._drop_message_content(msg, tag):
                        dropped += 1
                continue

            for tag in dropped_tags:
                message_id = tag.get("message_id", "")
                if not isinstance(message_id, str):
                    continue
                if message_id != source_id and not message_id.startswith(
                    f"{source_id}:"
                ):
                    continue
                mode = tag.get("drop_mode") or "full"
                if mode == "truncated":
                    if self._truncate_message_content(msg, tag):
                        truncated += 1
                else:
                    if self._drop_message_content(msg, tag):
                        dropped += 1
                break

        return {"dropped": dropped, "truncated": truncated}

    def _apply_tool_call_drops(
        self,
        msg,
        owner_id: str,
        dropped_tags: list[dict],
    ) -> int:
        tool_calls = getattr(msg, "tool_calls", None)
        if not isinstance(tool_calls, list) or not tool_calls:
            return 0

        remaining = []
        removed = 0
        for tc in tool_calls:
            call_id = self._tool_call_id(tc)
            matched = False
            for tag in dropped_tags:
                if tag.get("type") != "tool_call":
                    continue
                if tag.get("message_id") != call_id:
                    continue
                tag_owner = tag.get("tool_owner_message_id")
                if tag_owner is not None and tag_owner != owner_id:
                    continue
                matched = True
                break
            if matched:
                removed += 1
                continue
            remaining.append(tc)

        if removed <= 0:
            return 0

        msg.tool_calls = remaining or None
        content = getattr(msg, "content", None)
        if msg.tool_calls is None and (
            content is None or (isinstance(content, str) and not content.strip())
        ):
            msg.content = "[dropped]"
        return removed

    def _drop_message_content(self, msg, tag: dict) -> bool:
        content = getattr(msg, "content", None)
        placeholder = "[dropped]"
        if isinstance(content, str):
            if content == placeholder:
                return False
            msg.content = placeholder
            return True
        if isinstance(content, list):
            changed = False
            message_id = tag.get("message_id", "")
            for part_index, part in enumerate(content):
                part_id = f":p{part_index}:"
                if isinstance(message_id, str) and part_id not in message_id:
                    continue
                if hasattr(part, "text") and getattr(part, "text", None) != placeholder:
                    part.text = placeholder
                    changed = True
            return changed
        return False

    def _truncate_message_content(self, msg, tag: dict) -> bool:
        limit = int(self.config.get("truncated_tool_chars", 800))
        content = getattr(msg, "content", None)
        prefix = "[truncated] "
        if isinstance(content, str):
            if content.startswith(prefix):
                return False
            msg.content = prefix + content[:limit]
            return True
        return False

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
        messages,
        tool_index: int,
        tool_call_id: str,
        event: AstrMessageEvent,
    ) -> str | None:
        for owner_index in range(tool_index - 1, -1, -1):
            candidate = messages[owner_index]
            if getattr(candidate, "role", "") != "assistant":
                continue
            for tc in getattr(candidate, "tool_calls", None) or []:
                if self._tool_call_id(tc) == tool_call_id:
                    return self._message_source_id(
                        event,
                        candidate,
                        owner_index,
                        getattr(candidate, "content", None),
                    )
        return None

    @staticmethod
    def _tool_call_id(tc) -> str:
        if isinstance(tc, dict):
            return tc.get("id", str(id(tc)))
        return getattr(tc, "id", str(id(tc)))

    @staticmethod
    def _content_hash(content) -> str:
        try:
            raw = (
                content
                if isinstance(content, str)
                else json.dumps(content, default=str)
            )
        except Exception:
            raw = str(content)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
