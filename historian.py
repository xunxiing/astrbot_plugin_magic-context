import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any


class HistorianAgent:
    def __init__(self, tags_db, compartments_db, config: dict):
        self.tags_db = tags_db
        self.compartments_db = compartments_db
        self.config = {
            "historian_chunk_tokens": 4000,
            "historian_timeout_ms": 30000,
            "historian_min_messages": 20,
            "historian_keep_recent": 10,
            "historian_max_retries": 3,
            "historian_model": None,
        }
        self.config.update(config)

    async def compress(self, session_id: str, messages: list) -> dict | None:
        conversation = [m for m in messages if m.get("role") != "system"]

        if len(conversation) < self.config["historian_min_messages"]:
            return None

        estimated_tokens = sum(len(m.get("content", "")) * 0.5 for m in conversation)
        if estimated_tokens < self.config["historian_chunk_tokens"]:
            return None

        keep_recent = self.config["historian_keep_recent"]
        old_messages = conversation[:-keep_recent] if keep_recent > 0 else conversation

        formatted_parts = []
        for m in old_messages[-50:]:
            content = m.get("content", "")
            role = m.get("role", "unknown")
            formatted_parts.append(f"[{role}]: {content[:500]}")
        formatted_text = "\n".join(formatted_parts)

        try:
            llm_output = await self._call_llm(formatted_text)
            result = await self._parse_json_safe(llm_output)

            summary = result.get("summary", "")
            facts = result.get("facts", [])
            topics = result.get("topics", [])

            await self.compartments_db.save_compartment(
                session_id=session_id,
                start_tag=0,
                end_tag=len(old_messages),
                depth=1,
                summary=summary,
                topics=topics,
            )

            for fact in facts:
                await self.compartments_db.save_fact(
                    session_id=session_id,
                    content=fact,
                )

            return result
        except Exception:
            return None

    async def _call_llm(self, prompt_text: str) -> str:
        raise NotImplementedError(
            "HistorianAgent._call_llm must be overridden. "
            "Use get_llm_callback() to inject the real LLM implementation."
        )

    async def _parse_json_safe(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {"summary": "", "facts": []}

    async def full_compress(self, session_id: str, messages: list) -> dict | None:
        conversation = [m for m in messages if m.get("role") != "system"]

        if len(conversation) < self.config["historian_min_messages"]:
            return None

        estimated_tokens = sum(len(m.get("content", "")) * 0.5 for m in conversation)
        if estimated_tokens < self.config["historian_chunk_tokens"]:
            return None

        keep_recent = self.config["historian_keep_recent"]
        old_messages = conversation[:-keep_recent] if keep_recent > 0 else conversation

        formatted_parts = []
        for m in old_messages[-50:]:
            content = m.get("content", "")
            role = m.get("role", "unknown")
            formatted_parts.append(f"[{role}]: {content[:500]}")
        formatted_text = "\n".join(formatted_parts)

        max_retries = self.config["historian_max_retries"]
        best_result = None

        for attempt in range(max_retries + 1):
            prompt = formatted_text
            if attempt > 0:
                prompt = (
                    formatted_text
                    + "\n\nPlease output ONLY valid JSON, no markdown formatting."
                )

            try:
                llm_output = await self._call_llm(prompt)
                result = await self._parse_json_safe(llm_output)

                summary = result.get("summary", "")
                facts = result.get("facts", [])

                if summary or facts:
                    best_result = result
                    break

                best_result = best_result or result
            except Exception:
                continue

        if best_result is None:
            return None

        summary = best_result.get("summary", "")
        facts = best_result.get("facts", [])
        topics = best_result.get("topics", [])

        try:
            await self.compartments_db.save_compartment(
                session_id=session_id,
                start_tag=0,
                end_tag=len(old_messages),
                depth=1,
                summary=summary,
                topics=topics,
            )

            for fact in facts:
                await self.compartments_db.save_fact(
                    session_id=session_id,
                    content=fact,
                )
        except Exception:
            pass

        return best_result

    def get_llm_callback(self) -> callable:
        historian = self

        async def callback(
            provider_id: str,
            context: Any,
            prompt: str,
            system_prompt: str,
            timeout: int = 30000,
        ) -> str:
            raise NotImplementedError(
                "LLM callback not yet injected. Instantiate via the factory returned "
                "by get_llm_callback() itself, or call cb() with provider_id and context "
                "after the real implementation has been bound."
            )

        return callback


async def run_historian_pass(
    historian: HistorianAgent,
    session_id: str,
    messages: list,
) -> dict | None:
    try:
        result = await historian.full_compress(session_id, messages)
        if result is not None:
            print(f"[Historian] Compression succeeded for session {session_id}")
        else:
            print(
                f"[Historian] Compression skipped or returned empty for session {session_id}"
            )

        try:
            await historian.tags_db.set_session_meta(
                session_id=session_id,
                key="last_historian_run",
                value=datetime.now().isoformat(),
            )
        except Exception:
            pass

        return result
    except Exception as e:
        print(
            f"[Historian] Error during compression pass for session {session_id}: {e}"
        )
        return None
