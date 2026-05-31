import re

_ARTICLES_PRONOUNS = re.compile(
    r"\b(the|a|an|I|you|we|they|he|she|it)\b",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r"[,;:'\"()\[\]{}]")
_WHITESPACE = re.compile(r"\s+")
_LONG_WORD = re.compile(r"\b\w{4,}\b")


def cave_man_compress(text: str, level: str) -> str:
    if level == "none":
        return text

    if level == "lite":
        words = text.split()
        if len(words) <= 10:
            return text
        return " ".join(words[:10]) + " ..."

    if level == "full":
        result = _ARTICLES_PRONOUNS.sub("", text)
        result = _PUNCTUATION.sub("", result)
        result = _WHITESPACE.sub(" ", result).strip()
        return result

    if level == "ultra":
        words = _LONG_WORD.findall(text)
        return " ".join(words[:15])

    return text


def cave_man_level_for_depth(depth: int) -> str:
    if depth <= 1:
        return "lite"
    if depth <= 3:
        return "full"
    return "ultra"


def estimate_tokens_from_text(text: str) -> int:
    return round(len(text) * 0.5)


def estimate_tokens_from_messages(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            total += estimate_tokens_from_text(content)
        elif isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            total += estimate_tokens_from_text("".join(text_parts))
    return total


def format_message_for_historian(msg: dict) -> str:
    role = msg.get("role", "unknown")
    content = msg.get("content", "")
    if content is None:
        content = ""
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        content = "".join(text_parts)
    if not isinstance(content, str):
        content = str(content)
    return f"[{role}]: {content[:500]}"
