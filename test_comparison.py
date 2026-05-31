"""
Test script to compare caveman.ts and caveman.py implementations behaviorally.
"""

import re
from typing import Callable

# ---------------------------------------------------------------------------
# Python implementation (from caveman.py)
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

PHRASE_SHORTENINGS = [
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
ULTRA_CONNECTIVE_REPLACEMENTS = [
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
ULTRA_ABBREVIATIONS = {
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


# ---------------------------------------------------------------------------
# Simulated TypeScript implementation (mimicking the TS logic in Python)
# ---------------------------------------------------------------------------

PRESERVATION_PATTERNS_TS = [
    re.compile(r"```[\s\S]*?```", re.MULTILINE),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"https?://\S+"),
    re.compile(r"§\d+§"),
    re.compile(r"\b(?:msg|ses|toolu)_[A-Za-z0-9]+"),
    re.compile(r"(?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+\.\w{1,6}"),
    re.compile(r"(?<![a-z0-9])[0-9a-f]{7,40}(?![a-z0-9])", re.IGNORECASE),
]


def protect_regions_ts(text: str) -> tuple[str, list[tuple[str, str]]]:
    preserved: list[tuple[str, str]] = []
    working = text
    for pattern in PRESERVATION_PATTERNS_TS:

        def replacer(match: re.Match) -> str:
            placeholder = f"\u0000MC_PRES_{len(preserved)}\u0000"
            preserved.append((placeholder, match.group(0)))
            return placeholder

        working = pattern.sub(replacer, working)
    return working, preserved


def restore_regions_ts(text: str, preserved: list[tuple[str, str]]) -> str:
    working = text
    for placeholder, original in reversed(preserved):
        working = original.join(working.split(placeholder))
    return working


def build_phrase_drop_regex_ts(phrases: list[str]) -> re.Pattern:
    escaped = [re.escape(p) for p in phrases]
    return re.compile(r"(\s+)?\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


def drop_phrases_ts(text: str, phrases: list[str]) -> str:
    return build_phrase_drop_regex_ts(phrases).sub("", text)


def drop_articles_ts(text: str) -> str:
    working = re.sub(r"\b(?:the|a|an)\b\s+", "", text, flags=re.IGNORECASE)
    working = re.sub(r" +", " ", working)
    return working


def drop_auxiliaries_ts(text: str) -> str:
    sorted_aux = sorted(AUXILIARIES, key=len, reverse=True)
    escaped = [a.replace(r" ", r"\s+") for a in sorted_aux]
    pattern = re.compile(
        r"\s+\b(?:" + "|".join(escaped) + r")\b\s+(?=\w+(?:ed|en|ing|ized|ised)\b)",
        re.IGNORECASE,
    )
    working = pattern.sub(" ", text)
    working = re.sub(r" +", " ", working)
    return working


def apply_phrase_shortenings_ts(text: str) -> str:
    working = text
    for pattern, replacement in PHRASE_SHORTENINGS:
        working = pattern.sub(replacement, working)
    return working


def apply_ultra_connectives_ts(text: str) -> str:
    working = text
    for pattern, replacement in ULTRA_CONNECTIVE_REPLACEMENTS:
        working = pattern.sub(replacement, working)
    return working


def count_word_occurrences_ts(text: str, term: str) -> int:
    escaped = re.escape(term)
    matches = re.findall(rf"\b{escaped}\b", text, re.IGNORECASE)
    return len(matches)


def apply_ultra_abbreviations_ts(text: str) -> str:
    working = text
    for term, abbreviation in ULTRA_ABBREVIATIONS.items():
        if count_word_occurrences_ts(working, term) < 3:
            continue
        escaped = re.escape(term)

        def replacer(match: re.Match) -> str:
            if match.group(0)[0] == match.group(0)[0].upper():
                return abbreviation[0].upper() + abbreviation[1:]
            return abbreviation

        working = re.sub(rf"\b{escaped}\b", replacer, working, flags=re.IGNORECASE)
    return working


def transform_preserving_user_lines_ts(
    text: str, transform: Callable[[str], str]
) -> str:
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


def normalize_whitespace_ts(text: str) -> str:
    lines = text.split("\n")
    lines = [re.sub(r"[ \t]+", " ", line) for line in lines]
    lines = [re.sub(r"[ \t]+$", "", line) for line in lines]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def caveman_compress_ts(text: str, level: str) -> str:
    if len(text) == 0:
        return text
    protected_text, preserved = protect_regions_ts(text)

    def transform_chunk(chunk: str) -> str:
        working = chunk
        working = drop_phrases_ts(working, FILLER_WORDS)
        working = drop_phrases_ts(working, HEDGING_PHRASES)
        working = drop_phrases_ts(working, PLEASANTRIES)
        working = apply_phrase_shortenings_ts(working)
        if level in ("full", "ultra"):
            working = drop_auxiliaries_ts(working)
            working = drop_articles_ts(working)
        if level == "ultra":
            working = apply_ultra_connectives_ts(working)
            working = apply_ultra_abbreviations_ts(working)
        return working

    transformed = transform_preserving_user_lines_ts(protected_text, transform_chunk)
    restored = restore_regions_ts(transformed, preserved)
    return normalize_whitespace_ts(restored).strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_protect_regions():
    print("=== protect_regions ===")
    # Test 1: URL inside code block
    text1 = "```python\nprint('https://example.com')\n```"
    py_protected, py_map = protect_regions(text1)
    ts_protected, ts_list = protect_regions_ts(text1)
    print(f"Input: {text1!r}")
    print(f"PY protected: {py_protected!r}")
    print(f"TS protected: {ts_protected!r}")
    print(f"Match: {py_protected == ts_protected}")
    print()

    # Test 2: Overlapping patterns
    text2 = "Check https://example.com/path and `code` here"
    py_protected, py_map = protect_regions(text2)
    ts_protected, ts_list = protect_regions_ts(text2)
    print(f"Input: {text2!r}")
    print(f"PY protected: {py_protected!r}")
    print(f"TS protected: {ts_protected!r}")
    print(f"Match: {py_protected == ts_protected}")
    print()

    # Test 3: Nested placeholders scenario
    text3 = "`https://example.com` and ```code\nurl: https://test.com\n```"
    py_protected, py_map = protect_regions(text3)
    ts_protected, ts_list = protect_regions_ts(text3)
    print(f"Input: {text3!r}")
    print(f"PY protected: {py_protected!r}")
    print(f"TS protected: {ts_protected!r}")
    print(f"Match: {py_protected == ts_protected}")
    print()

    # Test 4: URL inside inline code (critical overlap case)
    text4 = "Use `curl https://example.com` to test"
    py_protected, py_map = protect_regions(text4)
    ts_protected, ts_list = protect_regions_ts(text4)
    print(f"Input: {text4!r}")
    print(f"PY protected: {py_protected!r}")
    print(f"TS protected: {ts_protected!r}")
    print(f"Match: {py_protected == ts_protected}")
    print()


def test_restore_regions():
    print("=== restore_regions ===")
    # Test: restored text contains placeholder-like string
    text = "hello __REGION_0__ world"
    placeholder_map = {"__REGION_0__": "foo", "__REGION_1__": "bar"}
    restored_py = restore_regions(text, placeholder_map)

    preserved = [("\u0000MC_PRES_0\u0000", "foo"), ("\u0000MC_PRES_1\u0000", "bar")]
    restored_ts = restore_regions_ts("hello \u0000MC_PRES_0\u0000 world", preserved)
    print(f"PY restored: {restored_py!r}")
    print(f"TS restored: {restored_ts!r}")
    print(f"Match: {restored_py == restored_ts}")
    print()


def test_drop_phrases():
    print("=== drop_phrases ===")
    tests = [
        "I just want to say really clearly",
        "sort of kind of a bit messy",
        "just really basically actually",
        "I think it seems maybe probably",
        "please and thank you kindly",
        "  just  really  basically  ",
    ]
    for t in tests:
        py = _drop_phrases(t, FILLER_WORDS)
        ts = drop_phrases_ts(t, FILLER_WORDS)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_drop_articles():
    print("=== drop_articles ===")
    tests = [
        "the quick brown fox",
        "A beautiful day",
        "an apple a day",
        "The Start of line",
        "read the book and a pen",
        "THE BIG DOG",
    ]
    for t in tests:
        py = _drop_articles(t)
        ts = drop_articles_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_drop_auxiliaries():
    print("=== drop_auxiliaries ===")
    tests = [
        "historian was compressed",
        "files were being modified",
        "it has been implemented",
        "code will be tested",
        "this could be optimized",
        "was complex",  # should NOT drop
    ]
    for t in tests:
        py = _drop_auxiliaries(t)
        ts = drop_auxiliaries_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_regex_patterns():
    print("=== drop_auxiliaries regex patterns ===")
    sorted_aux = sorted(AUXILIARIES, key=len, reverse=True)
    py_escaped = [re.escape(a).replace(r"\ ", r"\s+") for a in sorted_aux]
    ts_escaped = [a.replace(r" ", r"\s+") for a in sorted_aux]
    print(f"PY escaped: {py_escaped}")
    print(f"TS escaped: {ts_escaped}")
    print(f"Match: {py_escaped == ts_escaped}")
    print()


def test_phrase_shortenings():
    print("=== apply_phrase_shortenings ===")
    tests = [
        "in order to do this",
        "due to the fact that it works",
        "at this point in time we are ready",
        "for the purpose of testing",
        "with regard to your request",
    ]
    for t in tests:
        py = _apply_phrase_shortenings(t)
        ts = apply_phrase_shortenings_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_ultra_connectives():
    print("=== apply_ultra_connectives ===")
    tests = [
        "do this and then do that",
        "because of the reason",
        "therefore we proceed",
        "however it works",
        "furthermore and additionally",
        "A and B or C",
    ]
    for t in tests:
        py = _apply_ultra_connectives(t)
        ts = apply_ultra_connectives_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_ultra_abbreviations():
    print("=== apply_ultra_abbreviations ===")
    tests = [
        "The historian compressed the historian data and the historian output",
        "compartment and compartments are here",
        "context message session configuration",
        "Context Message Session Configuration",
    ]
    for t in tests:
        py = _apply_ultra_abbreviations(t)
        ts = apply_ultra_abbreviations_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_transform_preserving_user_lines():
    print("=== transform_preserving_user_lines ===")
    tests = [
        "line1\nline2\nU: user said\nline3",
        "U: first\nU: second\nnormal line",
        "\n\nU: hello\n\n",
    ]
    for t in tests:
        py = _transform_preserving_user_lines(t, lambda x: x.upper())
        ts = transform_preserving_user_lines_ts(t, lambda x: x.upper())
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_normalize_whitespace():
    print("=== normalize_whitespace ===")
    tests = [
        "line  with   spaces\nand\ttabs\t\there",
        "trailing spaces   \nnext line",
        "line1\n\n\n\nline2",
        "\t\t\t",
        "a b c   \n   d e f   \n\n\n   g h i",
    ]
    for t in tests:
        py = _normalize_whitespace(t)
        ts = normalize_whitespace_ts(t)
        match = py == ts
        print(f"Input: {t!r}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_caveman_compress():
    print("=== caveman_compress ===")
    tests = [
        ("", "lite"),
        ("I just want to say really clearly that the code is working", "lite"),
        ("I think it has been implemented and the historian was compressed", "full"),
        (
            "The historian compressed the historian data and the historian output because of the configuration",
            "ultra",
        ),
        ("U: user said hello\nI just want to say thanks", "lite"),
        ("```python\nprint('hello')\n```\nThe code is really working", "full"),
    ]
    for text, level in tests:
        py = caveman_compress(text, level)
        ts = caveman_compress_ts(text, level)
        match = py == ts
        print(f"Input: {text!r}, Level: {level}")
        print(f"PY: {py!r}")
        print(f"TS: {ts!r}")
        print(f"Match: {match}")
        if not match:
            print("DIFF!")
        print()


def test_none_vs_empty():
    print("=== None vs empty string ===")
    print(f"PY caveman_compress(None, 'lite'): ", end="")
    try:
        result = caveman_compress(None, "lite")  # type: ignore
        print(f"{result!r}")
    except Exception as e:
        print(f"ERROR: {e}")

    print(f"TS caveman_compress_ts('', 'lite'): {caveman_compress_ts('', 'lite')!r}")
    print()


if __name__ == "__main__":
    test_protect_regions()
    test_restore_regions()
    test_drop_phrases()
    test_drop_articles()
    test_drop_auxiliaries()
    test_regex_patterns()
    test_phrase_shortenings()
    test_ultra_connectives()
    test_ultra_abbreviations()
    test_transform_preserving_user_lines()
    test_normalize_whitespace()
    test_caveman_compress()
    test_none_vs_empty()
