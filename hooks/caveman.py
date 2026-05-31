"""
Deterministic rule-based text compression in the style of caveman-speak.

Inspired by the caveman Claude Code skill (JuliusBrussee/caveman).
Port of caveman.ts and caveman-cleanup.ts from the magic-context plugin.

Preservation guarantees (all levels):
 - Code blocks (` and ``` fenced)
 - URLs (http://, https://)
 - File paths
 - Commit hashes (7-40 hex chars)
 - Compartment markers (§N§, msg_*, ses_*, toolu_*)
 - Lines starting with "U: " (user quotes)
"""

import re
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEPTH_UNTOUCHED = 0
DEPTH_LITE = 1
DEPTH_FULL = 2
DEPTH_ULTRA = 3
DEPTH_TO_LEVEL: dict[int, str | None] = {
    DEPTH_LITE: "lite",
    DEPTH_FULL: "full",
    DEPTH_ULTRA: "ultra",
}

# ---------------------------------------------------------------------------
# Preservation: detect regions that must pass through untouched.
# ---------------------------------------------------------------------------

_PRESERVATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"```[\s\S]*?```"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"https?://\S+"),
    re.compile(r"§\d+§"),
    re.compile(r"\b(?:msg|ses|toolu)_[A-Za-z0-9]+"),
    re.compile(r"(?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+\.\w{1,6}"),
    re.compile(r"(?<![a-z0-9])[0-9a-f]{7,40}(?![a-z0-9])", re.IGNORECASE),
]


def protect_regions(text: str) -> tuple[str, dict[str, str]]:
    placeholder_map: dict[str, str] = {}
    working = text
    counter = 0

    for pattern in _PRESERVATION_PATTERNS:
        parts: list[str] = []
        last_end = 0
        for m in pattern.finditer(working):
            parts.append(working[last_end : m.start()])
            placeholder = f"__REGION_{counter}__"
            placeholder_map[placeholder] = m.group(0)
            parts.append(placeholder)
            counter += 1
            last_end = m.end()
        parts.append(working[last_end:])
        working = "".join(parts)

    return working, placeholder_map


def restore_regions(text: str, placeholder_map: dict[str, str]) -> str:
    working = text
    for placeholder, original in reversed(list(placeholder_map.items())):
        working = working.replace(placeholder, original)
    return working


# ---------------------------------------------------------------------------
# Wordlists
# ---------------------------------------------------------------------------

FILLER_WORDS = [
    "just",
    "really",
    "basically",
    "actually",
    "essentially",
    "simply",
    "clearly",
    "obviously",
    "quite",
    "very",
    "somewhat",
    "rather",
    "fairly",
    "sort of",
    "kind of",
    "a bit",
]

HEDGING_PHRASES = [
    "i think",
    "i believe",
    "i feel",
    "probably",
    "perhaps",
    "maybe",
    "it seems",
    "it appears",
    "arguably",
    "i suppose",
    "i guess",
]

PLEASANTRIES = ["please", "thanks", "thank you", "kindly", "if possible"]

AUXILIARIES = [
    "was",
    "were",
    "is",
    "are",
    "am",
    "be",
    "been",
    "being",
    "has been",
    "had been",
    "have been",
    "will be",
    "would be",
    "could be",
    "should be",
    "might be",
    "may be",
]

PHRASE_SHORTENINGS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bin order to\b", re.IGNORECASE), "to"),
    (re.compile(r"\bdue to the fact that\b", re.IGNORECASE), "because"),
    (re.compile(r"\bat this point in time\b", re.IGNORECASE), "now"),
    (re.compile(r"\bat the moment\b", re.IGNORECASE), "now"),
    (re.compile(r"\bin the event that\b", re.IGNORECASE), "if"),
    (re.compile(r"\bfor the purpose of\b", re.IGNORECASE), "for"),
    (re.compile(r"\bwith regard to\b", re.IGNORECASE), "about"),
    (re.compile(r"\bin spite of the fact that\b", re.IGNORECASE), "though"),
    (re.compile(r"\bon the grounds that\b", re.IGNORECASE), "because"),
    (re.compile(r"\bfor the reason that\b", re.IGNORECASE), "because"),
]

ULTRA_CONNECTIVE_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:and then|then after|afterwards)\b", re.IGNORECASE), "\u2192"),
    (re.compile(r"\bbecause of\b", re.IGNORECASE), "//"),
    (re.compile(r"\btherefore\b", re.IGNORECASE), "\u2192"),
    (re.compile(r"\bbecause\b", re.IGNORECASE), "//"),
    (re.compile(r"\bhowever\b", re.IGNORECASE), "but"),
    (re.compile(r"\bfurthermore\b", re.IGNORECASE), "+"),
    (re.compile(r"\badditionally\b", re.IGNORECASE), "+"),
    (re.compile(r"\bas well as\b", re.IGNORECASE), "+"),
    (re.compile(r" and ", re.IGNORECASE), " + "),
    (re.compile(r" or ", re.IGNORECASE), " | "),
]

ULTRA_ABBREVIATIONS: dict[str, str] = {
    "historian": "hist",
    "compartment": "cmpt",
    "compartments": "cmpts",
    "compressor": "cmp",
    "compression": "cmp",
    "context": "ctx",
    "message": "msg",
    "messages": "msgs",
    "session": "ses",
    "configuration": "cfg",
    "config": "cfg",
    "implementation": "impl",
    "implemented": "impl",
    "repository": "repo",
    "database": "db",
    "directory": "dir",
}

# ---------------------------------------------------------------------------
# Transformation helpers
# ---------------------------------------------------------------------------


def _build_phrase_drop_regex(phrases: list[str]) -> re.Pattern:
    escaped = [re.escape(p) for p in phrases]
    alternation = "|".join(escaped)
    return re.compile(rf"(\s+)?\b(?:{alternation})\b", re.IGNORECASE)


def _drop_phrases(text: str, phrases: list[str]) -> str:
    return _build_phrase_drop_regex(phrases).sub("", text)


def _drop_articles(text: str) -> str:
    working = re.sub(r"\b(?:the|a|an)\b\s+", "", text, flags=re.IGNORECASE)
    working = re.sub(r" +", " ", working)
    return working


def _drop_auxiliaries(text: str) -> str:
    sorted_aux = sorted(AUXILIARIES, key=len, reverse=True)
    escaped = [re.escape(a).replace(r"\ ", r"\s+") for a in sorted_aux]
    alternation = "|".join(escaped)
    pattern = re.compile(
        rf"\s+\b(?:{alternation})\b\s+(?=\w+(?:ed|en|ing|ized|ised)\b)",
        re.IGNORECASE,
    )
    working = pattern.sub(" ", text)
    working = re.sub(r" +", " ", working)
    return working


def _apply_phrase_shortenings(text: str) -> str:
    working = text
    for pattern, replacement in PHRASE_SHORTENINGS:
        working = pattern.sub(replacement, working)
    return working


def _apply_ultra_connectives(text: str) -> str:
    working = text
    for pattern, replacement in ULTRA_CONNECTIVE_REPLACEMENTS:
        working = pattern.sub(replacement, working)
    return working


def _count_word_occurrences(text: str, term: str) -> int:
    escaped = re.escape(term)
    matches = re.findall(rf"\b{escaped}\b", text, re.IGNORECASE)
    return len(matches)


def _apply_ultra_abbreviations(text: str) -> str:
    working = text
    for term, abbreviation in ULTRA_ABBREVIATIONS.items():
        if _count_word_occurrences(working, term) < 3:
            continue
        escaped = re.escape(term)

        def _replacer(m: re.Match[str], abbr: str = abbreviation) -> str:
            if m.group(0)[0].isupper():
                return abbr[0].upper() + abbr[1:]
            return abbr

        working = re.sub(rf"\b{escaped}\b", _replacer, working, flags=re.IGNORECASE)
    return working


def _transform_preserving_user_lines(text: str, transform: Callable[[str], str]) -> str:
    lines = text.split("\n")
    output: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        output.append(transform("\n".join(buffer)))
        buffer.clear()

    for line in lines:
        if line.startswith("U: "):
            flush_buffer()
            output.append(line)
        else:
            buffer.append(line)

    flush_buffer()
    return "\n".join(output)


def _normalize_whitespace(text: str) -> str:
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip(" \t") for line in lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def caveman_compress(text: str, level: str) -> str:
    if not text:
        return text or ""

    protected_text, placeholder_map = protect_regions(text)

    def _transform_chunk(chunk: str) -> str:
        working = chunk
        working = _drop_phrases(working, FILLER_WORDS)
        working = _drop_phrases(working, HEDGING_PHRASES)
        working = _drop_phrases(working, PLEASANTRIES)
        working = _apply_phrase_shortenings(working)

        if level in ("full", "ultra"):
            working = _drop_auxiliaries(working)
            working = _drop_articles(working)

        if level == "ultra":
            working = _apply_ultra_connectives(working)
            working = _apply_ultra_abbreviations(working)

        return working

    transformed = _transform_preserving_user_lines(protected_text, _transform_chunk)
    restored = restore_regions(transformed, placeholder_map)
    return _normalize_whitespace(restored).strip()


def caveman_level_for_depth(depth: int) -> str | None:
    if depth <= 1:
        return None
    if depth == 2:
        return "lite"
    if depth == 3:
        return "full"
    if depth == 4:
        return "ultra"
    return None


def compute_target_depth(position_index: int, total_eligible: int) -> int:
    if total_eligible <= 0:
        return DEPTH_UNTOUCHED
    fraction = position_index / total_eligible
    if fraction < 0.2:
        return DEPTH_ULTRA
    if fraction < 0.4:
        return DEPTH_FULL
    if fraction < 0.6:
        return DEPTH_LITE
    return DEPTH_UNTOUCHED


async def apply_caveman_cleanup(
    session_id: str,
    db,
    targets: dict[int, Any],
    tags: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, int]:
    result = {
        "compressed_to_lite": 0,
        "compressed_to_full": 0,
        "compressed_to_ultra": 0,
    }

    if not config.get("enabled", False):
        return result

    max_tag = max((t.get("tag_number", 0) for t in tags), default=0)
    protected_cutoff = max_tag - config.get("protected_tags", 0)

    eligible = [
        t
        for t in tags
        if t.get("type") == "message"
        and t.get("status") == "active"
        and t.get("tag_number", 0) <= protected_cutoff
        and t.get("byte_size", 0) >= config.get("min_chars", 0)
    ]
    eligible.sort(key=lambda t: t.get("tag_number", 0))

    if not eligible:
        return result

    position_by_tag: dict[int, int] = {}
    for i, t in enumerate(eligible):
        position_by_tag[t["tag_number"]] = i

    tags_needing: list[tuple[dict[str, Any], int, int]] = []
    for index, tag in enumerate(eligible):
        target = targets.get(tag["tag_number"])
        if (
            not target
            or not hasattr(target, "get_content")
            or not hasattr(target, "set_content")
        ):
            continue
        target_depth = compute_target_depth(index, len(eligible))
        if target_depth > tag.get("caveman_depth", 0):
            tags_needing.append((tag, index, target_depth))

    if not tags_needing:
        return result

    # Batch-load pristine originals from DB (source of truth).
    tag_numbers = [t["tag_number"] for t, _, _ in tags_needing]
    original_by_tag = await db.get_source_contents(session_id, tag_numbers)

    # Batch all depth updates in a single transaction.
    depth_updates: list[tuple[int, int]] = []

    for tag, position_index, target_depth in tags_needing:
        original_text = original_by_tag.get(tag["tag_number"])
        if not isinstance(original_text, str) or len(original_text) == 0:
            continue

        level = DEPTH_TO_LEVEL.get(target_depth)
        if not level:
            continue

        # Compress from the ORIGINAL, never from an already-cavemaned intermediate.
        compressed = caveman_compress(original_text, level)
        if len(compressed) == 0:
            continue

        target = targets.get(tag["tag_number"])
        if not target:
            continue

        target.set_content(compressed)
        depth_updates.append((tag["tag_number"], target_depth))

        if target_depth == DEPTH_LITE:
            result["compressed_to_lite"] += 1
        elif target_depth == DEPTH_FULL:
            result["compressed_to_full"] += 1
        elif target_depth == DEPTH_ULTRA:
            result["compressed_to_ultra"] += 1

    # Apply all depth updates in a single transaction.
    if depth_updates:
        await db.update_caveman_depths(session_id, depth_updates)

    return result


async def replay_caveman_compression(
    session_id: str,
    db,
    targets: dict[int, Any],
    tags: list[dict[str, Any]],
) -> int:
    compressed_tags = [
        t
        for t in tags
        if t.get("type") == "message"
        and t.get("status") == "active"
        and t.get("caveman_depth", 0) > 0
        and t.get("tag_number") in targets
    ]

    if not compressed_tags:
        return 0

    # Batch-load pristine originals from DB.
    tag_numbers = [t["tag_number"] for t in compressed_tags]
    original_by_tag = await db.get_source_contents(session_id, tag_numbers)

    replayed = 0
    for tag in compressed_tags:
        original_text = original_by_tag.get(tag["tag_number"])
        if not isinstance(original_text, str) or len(original_text) == 0:
            continue

        level = DEPTH_TO_LEVEL.get(tag.get("caveman_depth", 0))
        if not level:
            continue

        compressed = caveman_compress(original_text, level)
        if len(compressed) == 0:
            continue

        target = targets.get(tag["tag_number"])
        if not target:
            continue

        if target.set_content(compressed):
            replayed += 1

    return replayed
