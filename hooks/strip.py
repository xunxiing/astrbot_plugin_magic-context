import re
from typing import Any

DROPPED_PLACEHOLDER_PATTERN = re.compile(r"^\[dropped §\d+§\]$")
TAG_PREFIX_PATTERN = re.compile(r"^§\d+§\s*")
INLINE_THINKING_PATTERN = re.compile(
    r"<(?:thinking|think)>[\s\S]*?<\/(?:thinking|think)>\s*",
    re.IGNORECASE | re.DOTALL,
)
SYSTEM_REMINDER_REGEX = re.compile(
    r"<system-reminder>[\s\S]*?<\/system-reminder>",
    re.IGNORECASE | re.DOTALL,
)
OMO_MARKER_REGEX = re.compile(r"<!-- OMO_INTERNAL_INITIATOR -->")
SYSTEM_DIRECTIVE_REGEX = re.compile(
    r"\[SYSTEM DIRECTIVE: OH-MY-(?:OPENCODE|CLAUDE)[^\]]*\][\s\S]*?(?=\n\n(?!\s*[-*])|$)",
    re.IGNORECASE | re.DOTALL,
)

CLEARED_REASONING_TYPES = {"thinking", "reasoning", "redacted_thinking"}

METADATA_PART_TYPES = {
    "step-start",
    "step-finish",
    "snapshot",
    "patch",
    "agent",
    "retry",
    "subtask",
    "compaction",
}

SYSTEM_INJECTION_PATTERNS = [
    re.compile(r"^<!-- OMO_INTERNAL_INITIATOR -->$"),
    re.compile(
        r"^<system-reminder>[\s\S]*<\/system-reminder>$", re.IGNORECASE | re.DOTALL
    ),
    re.compile(r"^\[SYSTEM DIRECTIVE:"),
    re.compile(r"^\[Category\+Skill Reminder\]"),
    re.compile(r"^\[EDIT ERROR - IMMEDIATE ACTION REQUIRED\]"),
    re.compile(r"^\[task CALL FAILED"),
    re.compile(r"^\[EMERGENCY CONTEXT WINDOW WARNING\]"),
]

SYSTEM_INJECTION_MARKERS = [
    "<!-- OMO_INTERNAL_INITIATOR -->",
    "[SYSTEM DIRECTIVE: MAGIC-CONTEXT",
    "[SYSTEM DIRECTIVE: OH-MY-OPENCODE",
    "[Category+Skill Reminder]",
    "[EDIT ERROR - IMMEDIATE ACTION REQUIRED]",
    "[task CALL FAILED - IMMEDIATE RETRY REQUIRED]",
    "[EMERGENCY CONTEXT WINDOW WARNING]",
    "Unstable background agent appears idle",
    "**THE SUBAGENT JUST CLAIMED THIS TASK IS DONE.",
]

TODO_CHANGED_PATTERN = re.compile(r"\[Todo list has changed\]", re.IGNORECASE)
NOTE_PATTERN = re.compile(r"^\[Note:.*?\]", re.IGNORECASE)
USER_WANTS_PATTERN = re.compile(r"^The user wants to\b", re.IGNORECASE)

STRIPPED_SENTINEL_TEXT = "[stripped system injection]"
CLEARED_SENTINEL_TEXT = "[cleared]"
DROPPED_SENTINEL_TEXT = "[dropped]"
IMAGE_REMOVED_TEXT = "[image data: removed]"


def _is_system_injected_text(text: str) -> bool:
    stripped = TAG_PREFIX_PATTERN.sub("", text.strip()).strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in SYSTEM_INJECTION_PATTERNS)


def _make_sentinel(original_part: dict[str, Any] | None = None) -> dict[str, Any]:
    sentinel: dict[str, Any] = {"type": "text", "text": ""}
    if original_part is not None and isinstance(original_part, dict):
        if "cache_control" in original_part:
            sentinel["cache_control"] = original_part["cache_control"]
        if "cacheControl" in original_part:
            sentinel["cacheControl"] = original_part["cacheControl"]
    return sentinel


def _is_sentinel(part: Any) -> bool:
    if not isinstance(part, dict):
        return False
    if part.get("type") != "text":
        return False
    text = part.get("text")
    if not isinstance(text, str):
        return False
    return text == "" or text == DROPPED_SENTINEL_TEXT


def _msg_tag(msg: dict[str, Any], tag_map: dict[Any, int], default: int = 0) -> int:
    mid = msg.get("id") if isinstance(msg, dict) else None
    if mid is not None and mid in tag_map:
        return tag_map[mid]
    return tag_map.get(id(msg), default)


def _all_content_parts_are_dropped_placeholders(parts: list[dict[str, Any]]) -> bool:
    has_content_part = False
    for part in parts:
        if not isinstance(part, dict):
            continue
        pt = part.get("type", "")
        if pt in METADATA_PART_TYPES:
            continue
        if pt == "tool":
            return False
        if pt == "text" and isinstance(part.get("text"), str):
            has_content_part = True
            trimmed = part["text"].strip()
            if not trimmed:
                continue
            segments = re.split(r"(?=\[dropped §)", trimmed)
            non_empty = [s for s in segments if s.strip()]
            if not all(
                DROPPED_PLACEHOLDER_PATTERN.search(s.strip()) for s in non_empty
            ):
                return False
            continue
        if pt == "reasoning" and isinstance(part.get("text"), str):
            has_content_part = True
            trimmed = part["text"].strip()
            if not trimmed:
                continue
            segments = re.split(r"(?=\[dropped §)", trimmed)
            non_empty = [s for s in segments if s.strip()]
            if not all(
                DROPPED_PLACEHOLDER_PATTERN.search(s.strip()) for s in non_empty
            ):
                return False
            continue
        return False
    return has_content_part


# ── 1. strip_system_injection ──────────────────────────────────────────


def strip_system_injection(content: str) -> str | None:
    """Strip system-injected markers from a single text string.

    Recognizes <system-reminder> blocks, [Todo list has changed],
    [Note: ...] prefixes, and "The user wants to..." nudges.
    Returns stripped content, or None if nothing was found.
    """
    has_injection = False

    for marker in SYSTEM_INJECTION_MARKERS:
        if marker in content:
            has_injection = True
            break

    if not has_injection:
        if SYSTEM_REMINDER_REGEX.search(content):
            has_injection = True
        elif TODO_CHANGED_PATTERN.search(content):
            has_injection = True
        elif NOTE_PATTERN.search(content):
            has_injection = True
        elif USER_WANTS_PATTERN.search(content):
            has_injection = True

    if not has_injection:
        return None

    cleaned = content
    cleaned = SYSTEM_REMINDER_REGEX.sub("", cleaned)
    cleaned = OMO_MARKER_REGEX.sub("", cleaned)
    cleaned = SYSTEM_DIRECTIVE_REGEX.sub("", cleaned)

    for marker in SYSTEM_INJECTION_MARKERS:
        if marker.startswith("<!-- ") or marker.startswith("[SYSTEM DIRECTIVE"):
            continue
        idx = cleaned.find(marker)
        if idx == -1:
            continue
        block_end = cleaned.find("\n\n", idx + len(marker))
        if block_end != -1:
            cleaned = cleaned[:idx] + cleaned[block_end:]
        else:
            cleaned = cleaned[:idx]

    return cleaned.strip()


# ── 2. strip_system_injected_messages ──────────────────────────────────


def strip_system_injected_messages(
    messages: list[dict[str, Any]],
    message_tag_numbers: dict[Any, int],
) -> list[int]:
    """Neutralize messages whose content is entirely system-injected markers.

    Only processes non-assistant roles. Replaces injected parts in-place
    with a sentinel object instead of removing them, preserving array
    length for cache-prefix stability.

    Returns a list of message indices that were neutralized.
    """
    stripped_indices: list[int] = []

    for i, msg in enumerate(messages):
        role = msg.get("role", "")
        if role == "assistant":
            continue

        content = msg.get("content")
        if content is None:
            continue

        parts: list[dict[str, Any]] = []
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = content

        if not parts:
            continue

        if len(parts) == 1 and _is_sentinel(parts[0]):
            continue

        has_content_part = False
        all_injected = True

        for part in parts:
            if not isinstance(part, dict):
                continue
            pt = part.get("type", "")
            if pt in METADATA_PART_TYPES:
                continue
            if part.get("ignored") is True:
                continue
            if pt == "tool":
                all_injected = False
                break
            if pt == "text" and isinstance(part.get("text"), str):
                has_content_part = True
                if not _is_system_injected_text(part["text"]):
                    all_injected = False
                    break
                continue
            all_injected = False
            break

        if has_content_part and all_injected:
            sentinel = {"type": "text", "text": STRIPPED_SENTINEL_TEXT}
            msg["content"] = [sentinel]
            stripped_indices.append(i)

    return stripped_indices


# ── 3. strip_dropped_placeholder_messages ──────────────────────────────


def strip_dropped_placeholder_messages(
    messages: list[dict[str, Any]],
) -> list[int]:
    """Neutralize messages that consist entirely of [dropped §N§] placeholders.

    User-role messages are never neutralized (they anchor turn boundaries).
    Replaces matched messages' content with a single sentinel, preserving
    array position for cache stability.

    Returns a list of message indices that were neutralized.
    """
    neutralized: list[int] = []

    for i, msg in enumerate(messages):
        content = msg.get("content")
        if content is None:
            continue

        parts: list[dict[str, Any]] = []
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = content

        if not parts:
            continue

        if msg.get("role") == "user":
            continue

        if len(parts) == 1 and _is_sentinel(parts[0]):
            continue

        if _all_content_parts_are_dropped_placeholders(parts):
            msg["content"] = [{"type": "text", "text": DROPPED_SENTINEL_TEXT}]
            neutralized.append(i)

    return neutralized


# ── 4. strip_thinking_tags ─────────────────────────────────────────────


def strip_thinking_tags(text: str) -> str:
    """Remove <thinking>...</thinking> and <think>...</think> XML blocks.

    Matches the TS regex: /<(?:thinking|think)>[\\s\\S]*?<\\/(?:thinking|think)>\\s*/
    g flag (re.DOTALL | re.IGNORECASE).
    Replaces with empty string.
    """
    return INLINE_THINKING_PATTERN.sub("", text)


# ── 5. clear_old_reasoning ─────────────────────────────────────────────


def clear_old_reasoning(
    messages: list[dict[str, Any]],
    message_tag_numbers: dict[Any, int],
    max_tag: int,
    clear_reasoning_age: int,
) -> None:
    """Mark thinking/reasoning parts as [cleared] for old assistant messages.

    Only processes messages with tag_number <= max_tag - clear_reasoning_age.
    Replaces thinking/text fields on reasoning-like parts with [cleared].
    """
    if max_tag == 0:
        return

    age_cutoff = max_tag - clear_reasoning_age

    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        tag = _msg_tag(msg, message_tag_numbers)
        if tag == 0 or tag > age_cutoff:
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in CLEARED_REASONING_TYPES:
                continue
            if "thinking" in part and part["thinking"] != CLEARED_SENTINEL_TEXT:
                part["thinking"] = CLEARED_SENTINEL_TEXT
            if "text" in part and part["text"] != CLEARED_SENTINEL_TEXT:
                part["text"] = CLEARED_SENTINEL_TEXT


# ── 6. strip_cleared_reasoning ─────────────────────────────────────────


def strip_cleared_reasoning(messages: list[dict[str, Any]]) -> None:
    """Replace [cleared] thinking/reasoning parts with empty-text sentinels.

    Operates on assistant messages only. If a thinking/reasoning part has
    both thinking and text fields set to [cleared] (or undefined), the
    part is replaced in-place with a sentinel.
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for j in range(len(content)):
            part = content[j]
            if not isinstance(part, dict):
                continue
            if part.get("type") not in CLEARED_REASONING_TYPES:
                continue
            if "thinking" not in part and "text" not in part:
                continue

            thinking = part.get("thinking") if "thinking" in part else None
            text_val = part.get("text") if "text" in part else None

            is_cleared = (thinking is None or thinking == CLEARED_SENTINEL_TEXT) and (
                text_val is None or text_val == CLEARED_SENTINEL_TEXT
            )
            if is_cleared:
                content[j] = _make_sentinel(part)


# ── 7. strip_inline_thinking ───────────────────────────────────────────


def strip_inline_thinking(
    messages: list[dict[str, Any]],
    message_tag_numbers: dict[Any, int],
    clear_reasoning_age: int,
    max_tag: int,
) -> None:
    """Strip inline <thinking> tags from old assistant message text.

    Only processes messages with tag_number <= max_tag - clear_reasoning_age.
    Applies strip_thinking_tags to text content (both string and list[part]).
    """
    if max_tag == 0:
        return

    age_cutoff = max_tag - clear_reasoning_age

    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        tag = _msg_tag(msg, message_tag_numbers)
        if tag == 0 or tag > age_cutoff:
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = strip_thinking_tags(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "text":
                    continue
                if not isinstance(part.get("text"), str):
                    continue
                part["text"] = strip_thinking_tags(part["text"])


# ── 8. truncate_errored_tools ──────────────────────────────────────────


def truncate_errored_tools(messages: list[dict[str, Any]]) -> None:
    """Truncate long error strings in tool parts with error status.

    Finds tool parts with state.status == "error" and truncates error
    strings > 100 characters to "{error[:100]}...".
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "tool":
                continue
            state = part.get("state")
            if not isinstance(state, dict):
                continue
            if state.get("status") != "error":
                continue
            error_text = state.get("error")
            if isinstance(error_text, str) and len(error_text) > 100:
                state["error"] = f"{error_text[:100]}..."


# ── 9. strip_processed_images ──────────────────────────────────────────


def strip_processed_images(
    messages: list[dict[str, Any]],
    message_tag_numbers: dict[Any, int],
    max_tag: int,
) -> None:
    """Replace large image-data-URL file parts with sentinel placeholders.

    Watermark-gated: only processes user messages with tag_number below
    max_tag that have a subsequent assistant response. Replaces data-URL
    file parts > 5000 characters with an [image data: removed] sentinel.
    Reverse iteration for safe in-place mutation.
    """
    has_assistant_response = False

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant":
            has_assistant_response = True
            continue
        if msg.get("role") != "user" or not has_assistant_response:
            continue

        tag = _msg_tag(msg, message_tag_numbers)
        if tag > max_tag and max_tag > 0:
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for j in range(len(content)):
            part = content[j]
            if not isinstance(part, dict):
                continue
            if part.get("type") != "file":
                continue
            mime = part.get("mime")
            if not isinstance(mime, str) or not mime.startswith("image/"):
                continue
            url = part.get("url")
            if isinstance(url, str) and url.startswith("data:") and len(url) > 5000:
                content[j] = {"type": "text", "text": IMAGE_REMOVED_TEXT}


# ── 10. replay_cleared_reasoning ───────────────────────────────────────


def replay_cleared_reasoning(messages: list[dict[str, Any]]) -> None:
    """Re-apply reasoning clearing on every pass for cache stability.

    Scans assistant messages for reasoning/thinking parts that contain
    the [cleared] sentinel and replaces them with neutralized sentinels.
    Idempotent: already-sentineled parts are skipped.
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for j in range(len(content)):
            part = content[j]
            if not isinstance(part, dict):
                continue
            if part.get("type") not in CLEARED_REASONING_TYPES:
                continue

            thinking = part.get("thinking") if "thinking" in part else None
            text_val = part.get("text") if "text" in part else None

            if thinking == CLEARED_SENTINEL_TEXT or text_val == CLEARED_SENTINEL_TEXT:
                if thinking != CLEARED_SENTINEL_TEXT and thinking is not None:
                    part["thinking"] = CLEARED_SENTINEL_TEXT
                if text_val != CLEARED_SENTINEL_TEXT and text_val is not None:
                    part["text"] = CLEARED_SENTINEL_TEXT
                content[j] = _make_sentinel(part)


# ── 11. replay_stripped_inline_thinking ────────────────────────────────


def replay_stripped_inline_thinking(messages: list[dict[str, Any]]) -> None:
    """Re-apply inline thinking stripping on every pass for cache stability.

    Applies strip_thinking_tags to text content of all assistant messages.
    Idempotent: applying to already-stripped text is a no-op.
    """
    for msg in messages:
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = strip_thinking_tags(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "text":
                    continue
                if not isinstance(part.get("text"), str):
                    continue
                part["text"] = strip_thinking_tags(part["text"])


# ── 12. strip_reasoning_from_messages ──────────────────────────────────


def strip_reasoning_from_messages(messages: list[dict[str, Any]]) -> None:
    """Convenience: apply reasoning-clearing pipeline in sequence.

    Marks all thinking/reasoning parts as [cleared], replaces them with
    sentinels, and strips inline thinking tags from text content.
    Does not use tag-based age filtering — processes all messages.
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in CLEARED_REASONING_TYPES:
                if "thinking" in part and part["thinking"] != CLEARED_SENTINEL_TEXT:
                    part["thinking"] = CLEARED_SENTINEL_TEXT
                if "text" in part and part["text"] != CLEARED_SENTINEL_TEXT:
                    part["text"] = CLEARED_SENTINEL_TEXT

    strip_cleared_reasoning(messages)

    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = strip_thinking_tags(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "text":
                    continue
                if not isinstance(part.get("text"), str):
                    continue
                part["text"] = strip_thinking_tags(part["text"])
