import re

_THINKING_TAG = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)


def strip_thinking_tags(text: str) -> str:
    return _THINKING_TAG.sub("[thinking: summarized]", text)


def strip_reasoning_from_messages(messages: list) -> None:
    for msg in messages:
        if hasattr(msg, "reasoning_content") and isinstance(msg.reasoning_content, str):
            if msg.reasoning_content:
                msg.reasoning_content = "[reasoning: removed]"

        content = getattr(msg, "content", None)
        if content is None:
            continue

        if isinstance(content, str):
            msg.content = strip_thinking_tags(content)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                part_type = getattr(part, "type", None)
                if part_type == "think":
                    new_parts.append(type(part)("text", text="[thinking: removed]"))
                elif part_type in ("text", None):
                    new_parts.append(part)
                else:
                    new_parts.append(part)
            msg.content = new_parts


def strip_reasoning_from_dicts(contexts: list[dict]) -> None:
    for ctx in contexts:
        content = ctx.get("content")
        if content is None:
            continue

        if isinstance(content, str):
            ctx["content"] = strip_thinking_tags(content)
        elif isinstance(content, list):
            ctx["content"] = [item for item in content if item.get("type") != "think"]
