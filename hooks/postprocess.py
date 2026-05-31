import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse


class PostProcessor:
    """Post-processing: token recording, session archive."""

    def __init__(self, db):
        self.db = db

    async def token_recording_phase(
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        """Hook handler for @filter.on_llm_response(priority=90)"""
        session_id = event.unified_msg_origin

        try:
            usage = getattr(response, "usage", None)
            if not usage:
                return

            total = 0
            prompt = 0
            completion = 0

            if isinstance(usage, dict):
                total = usage.get("total_tokens", 0) or usage.get("total", 0)
                prompt = usage.get("prompt_tokens", 0) or usage.get("input", 0)
                completion = usage.get("completion_tokens", 0) or usage.get("output", 0)
            else:
                total = getattr(usage, "total_tokens", 0) or getattr(usage, "total", 0)
                prompt = getattr(usage, "prompt_tokens", 0) or getattr(
                    usage, "input", 0
                )
                completion = getattr(usage, "completion_tokens", 0) or getattr(
                    usage, "output", 0
                )

            meta = await self.db.get_or_create_session_meta(session_id)
            prev_total = meta.get("total_tokens_used", 0) or 0
            recent_count = self._roll_recent_count(meta)
            await self.db.update_session_meta(
                session_id,
                total_tokens_used=prev_total + total,
                recent_24h_message_count=recent_count,
                recent_24h_window_start=self._current_window_start_ms(),
            )
            await self.db.update_session_meta(
                session_id,
                last_response_time=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            )

            logger.info(
                f"[MagicContext] Token recording: total={total} prompt={prompt} completion={completion}"
            )
        except Exception as e:
            logger.error(f"[MagicContext] Error recording token usage: {e}")

    async def archive_phase(self, event: AstrMessageEvent) -> None:
        """Hook handler for @filter.after_message_sent(priority=50)"""
        session_id = event.unified_msg_origin

        try:
            await self.db.update_session_meta(
                session_id,
                compartment_in_progress=0,
                updated_at=int(time.time() * 1000),
            )
        except Exception:
            pass

        logger.info(f"[MagicContext] Session archive complete for {session_id}")

    def _current_window_start_ms(self) -> int:
        now_ms = int(time.time() * 1000)
        return now_ms - (24 * 60 * 60 * 1000)

    def _roll_recent_count(self, meta: dict) -> int:
        window_start = meta.get("recent_24h_window_start")
        count = int(meta.get("recent_24h_message_count", 0) or 0)
        now_ms = int(time.time() * 1000)
        if not window_start or now_ms - int(window_start) > 24 * 60 * 60 * 1000:
            return 1
        return count + 1
