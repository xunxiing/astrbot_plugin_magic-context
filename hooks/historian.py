"""
Historian Agent -- core LLM-based conversation compression system.

Ports the full 9-step incremental pipeline from the TypeScript
magic-context plugin (compartment-runner-incremental.ts et al).

Architecture:
    historian = HistorianAgent(db, config)
    historian._llm_fn = my_async_callable
    result = await historian.run_compartment_agent(session_id, raw_messages)
"""

import asyncio
import json
import re
import time
import xml.sax.saxutils as saxutils
from typing import Any

from ._historian_prompts import (
    COMPARTMENT_AGENT_SYSTEM_PROMPT,
    HISTORIAN_EDITOR_SYSTEM_PROMPT,
    build_historian_editor_prompt,
)
from ..storage.database import MagicContextDB


def escape_xml_attr(value: str) -> str:
    return saxutils.escape(value, {'"': "&quot;", "'": "&apos;"})


def escape_xml_content(value: str) -> str:
    return saxutils.escape(value)


def unescape_xml(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&apos;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


_FACT_CATEGORIES = frozenset(
    {
        "WORKFLOW_RULES",
        "ARCHITECTURE_DECISIONS",
        "CONSTRAINTS",
        "CONFIG_DEFAULTS",
        "KNOWN_ISSUES",
        "ENVIRONMENT",
        "NAMING",
        "USER_PREFERENCES",
        "USER_DIRECTIVES",
    }
)

_COMPARTMENT_REGEX = re.compile(
    r'<compartment\s+(?:id="[^"]*"\s+)?start="(\d+)"\s+end="(\d+)"\s+title="([^"]+)"\s*>(.*?)</compartment>',
    re.DOTALL,
)
_CATEGORY_BLOCK_REGEX = re.compile(
    r"<(WORKFLOW_RULES|ARCHITECTURE_DECISIONS|CONSTRAINTS|CONFIG_DEFAULTS|KNOWN_ISSUES|ENVIRONMENT|NAMING|USER_PREFERENCES|USER_DIRECTIVES)>(.*?)</\1>",
    re.DOTALL,
)
_FACT_ITEM_REGEX = re.compile(r"^\s*\*\s*(.+)$", re.MULTILINE)
_UNPROCESSED_REGEX = re.compile(r"<unprocessed_from>(\d+)</unprocessed_from>")
_USER_OBSERVATIONS_REGEX = re.compile(
    r"<user_observations>(.*?)</user_observations>", re.DOTALL
)
_USER_OBS_ITEM_REGEX = re.compile(r"^\s*\*\s*(.+)$", re.MULTILINE)


def _get_role(msg: dict[str, Any] | Any) -> str:
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


def _get_content(msg: dict[str, Any] | Any):
    if isinstance(msg, dict):
        return msg.get("content")
    return getattr(msg, "content", None)


def _get_tool_calls(msg: dict[str, Any] | Any):
    if isinstance(msg, dict):
        return msg.get("tool_calls")
    return getattr(msg, "tool_calls", None)


def _get_msg_id(msg: dict[str, Any] | Any) -> str:
    if isinstance(msg, dict):
        return msg.get("id", "") or msg.get("message_id", "")
    return getattr(msg, "id", "") or getattr(msg, "message_id", "")


def _extract_text_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(str(p.get("text", "")))
                elif p.get("type") in ("reasoning", "thinking", "redacted_thinking"):
                    parts.append("[thinking]")
            elif hasattr(p, "text"):
                parts.append(str(getattr(p, "text", "")))
            elif hasattr(p, "type"):
                pt = getattr(p, "type", "")
                if pt == "text":
                    parts.append(str(getattr(p, "text", "")))
                elif pt in ("reasoning", "thinking"):
                    parts.append("[thinking]")
        return " ".join(parts)
    return str(content)


def _tool_args_summary(tool_call: dict[str, Any] | Any) -> str:
    name = ""
    args_str = ""
    if isinstance(tool_call, dict):
        func = tool_call.get("function", {})
        name = func.get("name", "")
        args = func.get("arguments", "")
        args_str = args if isinstance(args, str) else json.dumps(args)
    else:
        func = getattr(tool_call, "function", None)
        if func is not None:
            if isinstance(func, dict):
                name = func.get("name", "")
                args = func.get("arguments", "")
                args_str = args if isinstance(args, str) else json.dumps(args)
            else:
                name = getattr(func, "name", "")
                args = getattr(func, "arguments", "")
                args_str = args if isinstance(args, str) else json.dumps(args)
    args_summary = _summarize_args(name, args_str)
    return f"{name}({args_summary})"


def _summarize_args(tool_name: str, args_str: str) -> str:
    if not args_str:
        return ""
    if len(args_str) <= 80:
        return args_str
    try:
        obj = json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return args_str[:80]
    keys = list(obj.keys())
    if not keys:
        return ""
    parts = []
    for k in keys[:3]:
        v = obj[k]
        if isinstance(v, str) and len(v) > 30:
            v = v[:27] + "..."
        parts.append(f"{k}={json.dumps(v)}")
    result = ", ".join(parts)
    if len(keys) > 3:
        result += ", ..."
    return result


def _truncate_text(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class HistorianAgent:
    def __init__(self, db: MagicContextDB, config: dict | None = None):
        self.db = db
        self.config = {
            "historian_chunk_tokens": 4000,
            "historian_timeout_ms": 30000,
            "historian_min_messages": 20,
            "historian_keep_recent": 5,
            "historian_two_pass": False,
        }
        if config:
            self.config.update(config)
        self._llm_fn: Any = None

    async def run_compartment_agent(
        self, session_id: str, raw_messages: list[dict]
    ) -> dict | None:
        completed = False
        try:
            await self.db.update_session_meta(session_id, compartment_in_progress=1)

            prior_compartments = await self.db.get_compartments(session_id)
            prior_facts = await self.db.get_session_facts(session_id)

            if prior_compartments:
                try:
                    self._validate_stored_compartments(prior_compartments)
                except ValueError as e:
                    await self.db.update_session_meta(
                        session_id,
                        compartment_in_progress=0,
                        historian_last_error=str(e),
                    )
                    return None

            offset = 0
            if prior_compartments:
                last_end = prior_compartments[-1]["end_message"]
                offset = last_end + 1

            protected_tail_start = self._get_protected_tail_start(raw_messages)
            if protected_tail_start <= offset:
                await self.db.update_session_meta(session_id, compartment_in_progress=0)
                return None

            existing_state_xml = self._build_existing_state_xml(
                prior_compartments, prior_facts
            )

            chunk_result = self._format_chunk_text(
                raw_messages, offset, protected_tail_start
            )
            chunk_text = chunk_result["text"]
            chunk_meta = chunk_result["meta"]

            system_prompt = self._build_agent_prompt(
                existing_state_xml, chunk_text, offset, protected_tail_start
            )

            # Run validated historian pass with retry/repair/fallback chain.
            raw_output = await self._run_validated_historian_pass(
                session_id, system_prompt, offset, protected_tail_start, chunk_meta
            )

            if raw_output is None:
                return None

            if self.config.get("historian_two_pass"):
                raw_output = await self._run_editor_pass(raw_output)

            compartments, facts, meta = self._parse_historian_output(raw_output)

            if not compartments:
                await self.db.update_session_meta(
                    session_id,
                    compartment_in_progress=0,
                    historian_last_error="historian returned no compartments",
                )
                return None

            mapped = self._map_compartments_to_messages(
                compartments, raw_messages, offset, prior_compartments
            )

            await self.db.append_compartments(session_id, mapped)
            await self.db.replace_session_facts(session_id, facts)

            now_ms = int(time.time() * 1000)
            await self.db.update_session_meta(
                session_id,
                compartment_in_progress=0,
                historian_failure_count=0,
                updated_at=now_ms,
            )

            completed = True
            return {"compartments": mapped, "facts": facts, "meta": meta}

        except Exception as e:
            try:
                await self.db.update_session_meta(
                    session_id,
                    compartment_in_progress=0,
                    historian_last_error=str(e)[:500],
                )
            except Exception:
                pass
            return None

        finally:
            if not completed:
                try:
                    await self.db.update_session_meta(
                        session_id, compartment_in_progress=0
                    )
                except Exception:
                    pass

    def _get_protected_tail_start(self, raw_messages: list) -> int:
        """Find the start of the protected tail using meaningful user text filtering.

        Skips user messages that are pure system notifications (e.g., background
        task completions, nudges) to count only substantive user turns.
        """
        keep_recent = self.config.get("historian_keep_recent", 5)
        user_indices = []
        for i, msg in enumerate(raw_messages):
            role = _get_role(msg)
            if role != "user":
                continue
            content = _get_content(msg)
            text = _extract_text_content(content)
            if not text.strip():
                continue
            # Filter out pure system notifications
            if self._is_system_notification(text):
                continue
            user_indices.append(i)
        if len(user_indices) < keep_recent:
            return 0
        return user_indices[-keep_recent]

    def _is_system_notification(self, text: str) -> bool:
        """Check if a user message is a pure system notification, not a substantive turn."""
        stripped = text.strip()
        # Common notification patterns that should not count as user turns
        notification_prefixes = [
            "[system]",
            "[notification]",
            "[nudge]",
            "[auto]",
            "background task",
            "task completed",
            "operation complete",
        ]
        lowered = stripped.lower()
        for prefix in notification_prefixes:
            if lowered.startswith(prefix):
                return True
        # Very short messages (≤10 chars) that are just acknowledgments
        if len(stripped) <= 10 and stripped in (
            "ok",
            "yes",
            "done",
            "got it",
            "thanks",
            "thx",
        ):
            return True
        return False

    def _validate_stored_compartments(self, compartments: list[dict]):
        sorted_comps = sorted(compartments, key=lambda c: c.get("sequence", 0))
        for i in range(1, len(sorted_comps)):
            prev = sorted_comps[i - 1]
            curr = sorted_comps[i]
            prev_end = prev.get("end_message", 0)
            curr_start = curr.get("start_message", 0)
            if curr_start < prev_end:
                raise ValueError(
                    f"compartments overlap: sequence {prev.get('sequence')} ends at "
                    f"{prev_end} but sequence {curr.get('sequence')} starts at {curr_start}"
                )
            if curr_start > prev_end + 1:
                raise ValueError(
                    f"gap between compartments: sequence {prev.get('sequence')} ends at "
                    f"{prev_end} but sequence {curr.get('sequence')} starts at {curr_start}"
                )

    def _build_existing_state_xml(
        self, compartments: list[dict], facts: list[dict]
    ) -> str:
        lines: list[str] = []

        for c in compartments:
            start = c.get("start_message", 0)
            end = c.get("end_message", 0)
            title = c.get("title", "")
            content = c.get("content", c.get("summary", ""))
            lines.append(
                f'<compartment start="{start}" end="{end}" title="{escape_xml_attr(title)}">'
            )
            lines.append(escape_xml_content(content))
            lines.append("</compartment>")
            lines.append("")

        if facts:
            lines.append(
                "<!-- Rewrite all facts below into canonical present-tense operational "
                "form. Do not copy wording verbatim. Drop stale or task-local facts. -->"
            )
            lines.append("")

        facts_by_category: dict[str, list[str]] = {}
        for f in facts:
            cat = f.get("category", "general")
            content = f.get("content", "")
            facts_by_category.setdefault(cat, []).append(content)

        for category, items in facts_by_category.items():
            lines.append(f"<{category}>")
            for item in items:
                lines.append(f"* {escape_xml_content(item)}")
            lines.append(f"</{category}>")
            lines.append("")

        return "\n".join(lines)

    def _format_chunk_text(
        self, raw_messages: list, offset: int, protected_tail_start: int
    ) -> dict:
        """Format messages as chunk text with block merging and token budget.

        Returns {"text": str, "meta": {"lines": [{"ordinal": int, "message_id": str}]}}
        """
        slice_msgs = raw_messages[offset:protected_tail_start]
        token_budget = self.config.get("historian_chunk_tokens", 4000)

        # Phase 1: Build blocks by merging adjacent same-role messages.
        # Also merge tool-result-only user messages into preceding assistant block.
        blocks: list[dict] = []
        current_block: dict | None = None
        tool_only_ranges: list[tuple[int, int]] = []

        for idx, msg in enumerate(slice_msgs):
            abs_idx = offset + idx
            role = _get_role(msg)
            content = _get_content(msg)
            tool_calls = _get_tool_calls(msg)
            msg_id = _get_msg_id(msg)

            if role == "system":
                continue

            text = _extract_text_content(content)
            tc_texts = []
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    tc_texts.append(_tool_args_summary(tc))

            # Detect tool-only messages (user messages with only tool calls, no text).
            is_tool_only = False
            if role == "user" and not text.strip() and tc_texts:
                is_tool_only = True

            if role == "assistant" and not text.strip() and not tc_texts:
                continue
            if role == "user" and not text.strip() and not tc_texts:
                continue
            if role == "tool" and not text.strip():
                continue

            # Merge tool-only user messages into preceding assistant block.
            if is_tool_only and current_block and current_block["role"] == "assistant":
                for tc_text in tc_texts:
                    current_block["lines"].append(
                        {
                            "ordinal": abs_idx,
                            "message_id": msg_id,
                            "text": "",
                            "tool_calls": [tc_text],
                        }
                    )
                current_block["end_idx"] = abs_idx
                tool_only_ranges.append((abs_idx, abs_idx))
                continue

            # Try to merge with previous block if same role.
            if current_block and current_block["role"] == role:
                current_block["lines"].append(
                    {
                        "ordinal": abs_idx,
                        "message_id": msg_id,
                        "text": text,
                        "tool_calls": tc_texts,
                    }
                )
                current_block["end_idx"] = abs_idx
            else:
                if current_block:
                    blocks.append(current_block)
                current_block = {
                    "role": role,
                    "start_idx": abs_idx,
                    "end_idx": abs_idx,
                    "lines": [
                        {
                            "ordinal": abs_idx,
                            "message_id": msg_id,
                            "text": text,
                            "tool_calls": tc_texts,
                        }
                    ],
                }

        if current_block:
            blocks.append(current_block)

        # Phase 2: Format blocks into lines with token budget
        lines: list[str] = []
        line_meta: list[dict] = []
        total_tokens = 0

        for block in blocks:
            role = block["role"]
            block_lines: list[str] = []
            block_line_meta: list[dict] = []

            for line_info in block["lines"]:
                abs_idx = line_info["ordinal"]
                msg_id = line_info["message_id"]
                text = line_info["text"]
                tc_texts = line_info["tool_calls"]

                if role == "user":
                    formatted = f"[{abs_idx}] U: {_truncate_text(text, 500)}"
                    block_lines.append(formatted)
                    block_line_meta.append({"ordinal": abs_idx, "message_id": msg_id})

                elif role == "assistant":
                    if text.strip():
                        formatted = f"[{abs_idx}] A: {_truncate_text(text, 500)}"
                        block_lines.append(formatted)
                        block_line_meta.append(
                            {"ordinal": abs_idx, "message_id": msg_id}
                        )
                    for tc_text in tc_texts:
                        formatted = f"[{abs_idx}] TC: {_truncate_text(tc_text, 300)}"
                        block_lines.append(formatted)
                        block_line_meta.append(
                            {"ordinal": abs_idx, "message_id": msg_id}
                        )

                elif role == "tool":
                    formatted = f"[{abs_idx}] R: {_truncate_text(text, 200)}"
                    block_lines.append(formatted)
                    block_line_meta.append({"ordinal": abs_idx, "message_id": msg_id})

            # Estimate token count for this block (rough: 1 token ≈ 4 chars)
            block_text = "\n".join(block_lines)
            block_tokens = len(block_text) // 4

            if total_tokens + block_tokens > token_budget and lines:
                # Token budget exceeded — stop adding more blocks
                break

            lines.extend(block_lines)
            line_meta.extend(block_line_meta)
            total_tokens += block_tokens

        return {
            "text": "\n".join(lines),
            "meta": {
                "lines": line_meta,
                "total_tokens": total_tokens,
                "tool_only_ranges": tool_only_ranges,
            },
        }

    def _build_agent_prompt(
        self,
        existing_state_xml: str,
        chunk_text: str,
        offset: int,
        protected_tail_start: int,
    ) -> str:
        state_block = (
            existing_state_xml
            if existing_state_xml
            else "This is your first run. No existing state."
        )
        return "\n".join(
            [
                COMPARTMENT_AGENT_SYSTEM_PROMPT,
                "",
                "Existing state (read-only context for continuity and fact normalization -- do NOT re-emit these compartments):",
                state_block,
                "",
                "<new_messages>",
                f"Messages {offset}-{protected_tail_start - 1}:",
                "",
                chunk_text,
                "</new_messages>",
                "",
                "Instructions:",
                "- Return ONLY new compartments for the messages inside <new_messages>, plus the full normalized fact list.",
                "- Use the exact absolute raw ordinals from the input ranges for every compartment start/end and for <unprocessed_from>.",
                "- Rewrite every fact into terse, present-tense operational form. Merge semantic duplicates within each category.",
                "- Drop any session fact already covered by a project memory in the existing state.",
                "- Do not preserve prior narrative wording verbatim; if a fact is already canonical and still correct, keep or lightly normalize it.",
                "- Drop obsolete or task-local facts.",
            ]
        )

    async def _run_editor_pass(self, draft: str) -> str:
        try:
            editor_prompt = build_historian_editor_prompt(draft)
            full_prompt = HISTORIAN_EDITOR_SYSTEM_PROMPT + "\n\n" + editor_prompt
            result = await self._call_llm_with_timeout(full_prompt)
            return result if result.strip() else draft
        except Exception:
            return draft

    async def _run_validated_historian_pass(
        self,
        session_id: str,
        system_prompt: str,
        offset: int,
        protected_tail_start: int,
        chunk_meta: dict,
    ) -> str | None:
        """Run historian with retry, validation, repair, and fallback chain."""
        max_retries = self.config.get("max_historian_retries", 2)
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                raw_output = await self._call_llm_with_timeout(system_prompt)
            except Exception as e:
                last_error = str(e)
                if self._is_transient_error(e):
                    # Exponential backoff: 1s, 2s
                    wait_s = 2**attempt
                    await asyncio.sleep(wait_s)
                    continue
                # Permanent error — don't retry
                raise

            # Validate output structure
            validation = self._validate_historian_output(
                raw_output, offset, protected_tail_start, chunk_meta
            )
            if validation["valid"]:
                return raw_output

            # Validation failed — try repair on next attempt
            last_error = validation.get("error", "validation failed")
            system_prompt = self._build_repair_prompt(
                system_prompt, raw_output, validation["error"], chunk_meta
            )

        # All retries exhausted — try fallback model if available
        fallback_output = await self._run_fallback_historian_pass(
            session_id, system_prompt
        )
        if fallback_output:
            return fallback_output

        # Record failure
        await self.db.update_session_meta(
            session_id,
            historian_last_error=last_error[:500]
            if last_error
            else "all retries exhausted",
            historian_failure_count=1,  # Will be incremented by caller if needed
        )
        return None

    def _is_transient_error(self, error: Exception) -> bool:
        """Classify errors as transient (retryable) or permanent.

        Deny-list first (auth / 400-class → permanent), then allow-list.
        """
        msg = str(error).lower()
        # Deny-list: permanent errors that should NOT be retried
        permanent_keywords = [
            "invalid request",
            "bad request",
            "unauthorized",
            "forbidden",
            "authentication",
            "auth",
            "400",
            "401",
            "403",
        ]
        if any(kw in msg for kw in permanent_keywords):
            return False
        # Allow-list: transient errors that SHOULD be retried
        transient_keywords = [
            "timeout",
            "timed out",
            "rate limit",
            "too many requests",
            "connection",
            "network",
            "temporary",
            "unavailable",
            "overloaded",
            "econnreset",
            "etimedout",
            "500",
            "503",
            "429",
            "502",
            "504",
        ]
        return any(kw in msg for kw in transient_keywords)

    def _validate_historian_output(
        self, raw: str, offset: int, protected_tail_start: int, chunk_meta: dict
    ) -> dict:
        """Validate historian output for structural correctness."""
        compartments, facts, meta = self._parse_historian_output(raw)

        if not compartments:
            return {"valid": False, "error": "no compartments found in output"}

        # Check 1: compartments must be within [offset, protected_tail_start)
        for comp in compartments:
            start = comp["start_message"]
            end = comp["end_message"]
            if start < offset or end >= protected_tail_start:
                return {
                    "valid": False,
                    "error": f"compartment [{start}-{end}] out of range [{offset}-{protected_tail_start - 1}]",
                }
            if start > end:
                return {
                    "valid": False,
                    "error": f"compartment start {start} > end {end}",
                }

        # Check 2: compartments must be sequential and non-overlapping
        sorted_comps = sorted(compartments, key=lambda c: c["start_message"])
        for i in range(1, len(sorted_comps)):
            prev_end = sorted_comps[i - 1]["end_message"]
            curr_start = sorted_comps[i]["start_message"]
            if curr_start <= prev_end:
                return {
                    "valid": False,
                    "error": f"compartments overlap: prev ends at {prev_end}, next starts at {curr_start}",
                }

        # Check 3: gap healing — small gaps between compartments are acceptable
        # (historian may skip empty system messages), but large gaps are suspicious
        for i in range(1, len(sorted_comps)):
            prev_end = sorted_comps[i - 1]["end_message"]
            curr_start = sorted_comps[i]["start_message"]
            if curr_start > prev_end + 3:
                return {
                    "valid": False,
                    "error": f"large gap between compartments: prev ends at {prev_end}, next starts at {curr_start}",
                }

        # Check 4: coverage — first compartment should start at or near offset
        if sorted_comps[0]["start_message"] > offset + 2:
            return {
                "valid": False,
                "error": f"first compartment starts at {sorted_comps[0]['start_message']}, expected near {offset}",
            }

        # Check 5: unprocessed_from consistency
        unprocessed_from = meta.get("unprocessed_from")
        if unprocessed_from is not None:
            if unprocessed_from < offset or unprocessed_from > protected_tail_start:
                return {
                    "valid": False,
                    "error": f"unprocessed_from {unprocessed_from} out of range",
                }
            # unprocessed_from should be after the last compartment
            last_comp_end = sorted_comps[-1]["end_message"]
            if unprocessed_from <= last_comp_end:
                return {
                    "valid": False,
                    "error": f"unprocessed_from {unprocessed_from} must be after last compartment end {last_comp_end}",
                }

        return {"valid": True}

    def _build_repair_prompt(
        self, original_prompt: str, bad_output: str, error: str, chunk_meta: dict
    ) -> str:
        """Build a repair prompt that includes the original prompt, the bad output, and the error."""
        return (
            f"{original_prompt}\n\n"
            f"--- PREVIOUS ATTEMPT FAILED ---\n"
            f"Error: {error}\n\n"
            f"Your previous output was:\n{bad_output}\n\n"
            f"Please fix the error and return corrected XML. "
            f"Ensure all compartment start/end values are within the message range, "
            f"compartments are sequential and non-overlapping, and <unprocessed_from> is consistent."
        )

    async def _run_fallback_historian_pass(
        self, session_id: str, prompt: str
    ) -> str | None:
        """Fallback pass using a simpler prompt or different model."""
        # For now, just try one more time with the same prompt.
        # In production, this could switch to a different model or simpler system prompt.
        try:
            return await self._call_llm_with_timeout(prompt)
        except Exception:
            return None

    async def _call_llm_with_timeout(self, prompt: str) -> str:
        if self._llm_fn is None:
            raise RuntimeError(
                "HistorianAgent._llm_fn is not set. "
                "Assign an async callable: historian._llm_fn = my_async_fn"
            )
        timeout_ms = self.config.get("historian_timeout_ms", 30000)
        timeout_s = timeout_ms / 1000.0
        try:
            result = await asyncio.wait_for(self._llm_fn(prompt), timeout=timeout_s)
            return result if isinstance(result, str) else str(result)
        except asyncio.TimeoutError:
            raise RuntimeError(f"historian LLM call timed out after {timeout_ms}ms")

    def _parse_historian_output(self, raw: str) -> tuple[list[dict], list[dict], dict]:
        compartments: list[dict] = []
        facts: list[dict] = []

        for m in _COMPARTMENT_REGEX.finditer(raw):
            start_msg = int(m.group(1))
            end_msg = int(m.group(2))
            title = unescape_xml(m.group(3))
            content = unescape_xml(m.group(4).strip())
            if start_msg >= 0 and end_msg >= 0 and title and content:
                compartments.append(
                    {
                        "start_message": start_msg,
                        "end_message": end_msg,
                        "title": title,
                        "content": content,
                    }
                )

        for cat_match in _CATEGORY_BLOCK_REGEX.finditer(raw):
            category = cat_match.group(1)
            block_content = cat_match.group(2)
            for item_match in _FACT_ITEM_REGEX.finditer(block_content):
                fact_content = unescape_xml(item_match.group(1).strip())
                if fact_content:
                    facts.append({"category": category, "content": fact_content})

        compartments.sort(key=lambda c: c["start_message"])

        unprocessed_match = _UNPROCESSED_REGEX.search(raw)
        unprocessed_from = (
            int(unprocessed_match.group(1)) if unprocessed_match else None
        )

        user_observations: list[str] = []
        obs_match = _USER_OBSERVATIONS_REGEX.search(raw)
        if obs_match:
            for item_match in _USER_OBS_ITEM_REGEX.finditer(obs_match.group(1)):
                obs = unescape_xml(item_match.group(1).strip())
                if obs:
                    user_observations.append(obs)

        meta: dict = {}
        if unprocessed_from is not None:
            meta["unprocessed_from"] = unprocessed_from
        if user_observations:
            meta["user_observations"] = user_observations

        return compartments, facts, meta

    def _map_compartments_to_messages(
        self,
        parsed: list[dict],
        raw_messages: list,
        offset: int,
        prior_compartments: list[dict],
    ) -> list[dict]:
        max_seq = max(
            (c.get("sequence", idx) for idx, c in enumerate(prior_compartments)),
            default=-1,
        )
        next_seq = max_seq + 1
        now_ms = int(time.time() * 1000)

        mapped: list[dict] = []
        for comp in parsed:
            start_idx = comp["start_message"]
            end_idx = comp["end_message"]
            start_msg_id = (
                _get_msg_id(raw_messages[start_idx])
                if 0 <= start_idx < len(raw_messages)
                else ""
            )
            end_msg_id = (
                _get_msg_id(raw_messages[end_idx])
                if 0 <= end_idx < len(raw_messages)
                else ""
            )
            mapped.append(
                {
                    "sequence": next_seq,
                    "start_message": start_idx,
                    "end_message": end_idx,
                    "start_message_id": start_msg_id,
                    "end_message_id": end_msg_id,
                    "title": comp["title"],
                    "content": comp["content"],
                    "created_at": now_ms,
                }
            )
            next_seq += 1

        return mapped
