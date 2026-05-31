import xml.sax.saxutils as saxutils

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest


def _escape_xml(text: str) -> str:
    return saxutils.escape(str(text))


def build_compartment_block(
    compartments: list[dict],
    facts: list[dict] | None = None,
    memory_block: str | None = None,
    date_ranges: dict | None = None,
) -> str:
    """Static utility that renders compartment + fact XML."""
    parts: list[str] = []

    if compartments:
        parts.append("<session-history>")
        for comp in compartments:
            start = comp.get("start_message", 0)
            end = comp.get("end_message", 0)
            title = comp.get("title", "")
            content = comp.get("content", comp.get("summary", ""))

            attrs = f'start="{start}" end="{end}"'
            if title:
                attrs += f' title="{_escape_xml(title)}"'
            if date_ranges:
                by_id = date_ranges.get("byId", date_ranges.get("by_id", {}))
                dates = by_id.get(comp.get("id"))
                if dates:
                    attrs += f' startDate="{_escape_xml(dates["start"])}"'
                    attrs += f' endDate="{_escape_xml(dates["end"])}"'

            parts.append(f"<compartment {attrs}>")
            parts.append(content)
            parts.append("</compartment>")
        parts.append("</session-history>")

    if facts:
        facts_by_category: dict[str, list[dict]] = {}
        for f in facts:
            cat = f.get("category", "general")
            facts_by_category.setdefault(cat, []).append(f)

        for cat, cat_facts in sorted(facts_by_category.items()):
            parts.append(f"<{cat}>")
            for f in cat_facts:
                parts.append(f"* {_escape_xml(f.get('content', ''))}")
            parts.append(f"</{cat}>")

    if memory_block:
        parts.append(memory_block)

    return "\n".join(parts)


class Injector:
    """Injects compartment summaries and session facts into messages."""

    def __init__(self, db):
        self.db = db

    async def inject_phase(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """Hook handler for @filter.on_llm_request(priority=40)"""
        session_id = event.unified_msg_origin

        try:
            compartments = await self.db.get_compartments(session_id)
        except Exception:
            compartments = []

        try:
            facts = await self.db.get_session_facts(session_id)
        except Exception:
            facts = []

        if not compartments and not facts:
            return

        try:
            block = self._render_block(compartments, facts)
        except Exception as e:
            logger.error(f"[MagicContext] Error rendering injection block: {e}")
            return

        try:
            if any(c.get("_magic_context") for c in req.contexts):
                return

            max_end = max(
                (int(comp.get("end_message", -1)) for comp in compartments),
                default=-1,
            )
            system_contexts = [c for c in req.contexts if c.get("role") == "system"]
            non_system_contexts = [c for c in req.contexts if c.get("role") != "system"]
            recent_tail = (
                non_system_contexts[max_end + 1 :]
                if 0 <= max_end < len(non_system_contexts)
                else non_system_contexts
            )

            req.contexts = [
                *system_contexts,
                {
                    "role": "user",
                    "content": block,
                    "_magic_context": True,
                },
                *recent_tail,
            ]
        except Exception as e:
            logger.error(f"[MagicContext] Error injecting context: {e}")

    def _render_block(self, compartments: list[dict], facts: list[dict]) -> str:
        return build_compartment_block(compartments, facts)
