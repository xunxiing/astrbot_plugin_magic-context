def should_offer_request_compaction(
    input_tokens: int | None,
    context_limit: int | None,
) -> bool:
    if input_tokens is None or context_limit is None:
        return False
    if context_limit <= 0:
        return False
    return input_tokens >= context_limit


def build_reduce_guidance(
    input_tokens: int | None,
    context_limit: int | None,
) -> str:
    if not should_offer_request_compaction(input_tokens, context_limit):
        return ""
    return (
        "## Magic Context\n\n"
        "Use `ctx_reduce` to manage context size.\n"
        "- The estimated input tokens have reached the configured target context length for this session.\n"
        "- Decide for yourself whether cleanup is needed now, whether to wait, and whether lite or hard is appropriate.\n"
        "- `ctx_reduce`: match an old tool call/result by a short content prefix and remove it deterministically.\n"
        "- Parameters: `match` is required; optional `kind` = `tool_call` or `tool_result`; optional `tool_name` narrows the target.\n"
        "- Prefer dropping old `tool_call` and `tool_result` context you already used.\n"
        '- `ctx_compact(mode="lite")`: deterministic old tool cleanup.\n'
        '- `ctx_compact(mode="hard")`: historian summary compaction for older context.\n'
        "- Never drop user messages blindly."
    )
