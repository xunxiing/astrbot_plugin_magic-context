def parse_range_string(expr: str) -> list[int]:
    values: set[int] = set()
    for part in expr.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"invalid range: {token}")
            values.update(range(start, end + 1))
        else:
            value = int(token)
            if value <= 0:
                raise ValueError(f"invalid tag: {token}")
            values.add(value)
    if not values:
        raise ValueError("empty range")
    return sorted(values)


async def queue_ctx_reduce(plugin, session_id: str, drop_expr: str) -> str:
    if plugin is None:
        return "Error: magic-context plugin instance unavailable."

    drop_expr = str(drop_expr or "").strip()
    if not drop_expr:
        return "Error: 'drop' is required."

    try:
        drop_ids = parse_range_string(drop_expr)
    except ValueError as exc:
        return f"Error: invalid drop range. {exc}"

    all_tags = await plugin.db.get_tags_by_session(session_id)
    found_set = {int(tag.get("tag_number", 0) or 0) for tag in all_tags}
    unknown_ids = [tag_id for tag_id in drop_ids if tag_id not in found_set]
    if unknown_ids:
        return (
            "Error: unknown tag(s) "
            + ", ".join(f"§{tag_id}§" for tag_id in unknown_ids)
            + "."
        )

    active_tags = [tag for tag in all_tags if tag.get("status") == "active"]
    protected_count = int(plugin.config.get("protected_tags", 20))
    protected_tag_ids = sorted(
        (int(tag.get("tag_number", 0) or 0) for tag in active_tags), reverse=True
    )[:protected_count]
    protected_set = set(protected_tag_ids)

    tag_status_map = {
        int(tag.get("tag_number", 0) or 0): tag.get("status") for tag in all_tags
    }
    pending_ops = await plugin.db.get_pending_ops(session_id)
    pending_map = {
        int(op.get("tag_id", 0) or 0): op.get("operation") for op in pending_ops
    }

    conflicts = [
        tag_id for tag_id in drop_ids if tag_status_map.get(tag_id) == "compacted"
    ]
    if conflicts:
        return (
            "Error: conflicting compacted tag(s) "
            + ", ".join(f"§{tag_id}§" for tag_id in conflicts)
            + "."
        )

    filtered_ids = [
        tag_id
        for tag_id in drop_ids
        if tag_status_map.get(tag_id) != "dropped" and pending_map.get(tag_id) != "drop"
    ]
    skipped_count = len(drop_ids) - len(filtered_ids)
    if not filtered_ids:
        return "All requested tags were already queued or processed."

    for tag_id in filtered_ids:
        await plugin.db.queue_pending_op(session_id, tag_id, "drop")

    immediate = [tag_id for tag_id in filtered_ids if tag_id not in protected_set]
    deferred = [tag_id for tag_id in filtered_ids if tag_id in protected_set]

    parts: list[str] = []
    if immediate:
        parts.append("drop " + ", ".join(f"§{tag_id}§" for tag_id in immediate))
    if deferred:
        parts.append("deferred drop " + ", ".join(f"§{tag_id}§" for tag_id in deferred))
    suffix = (
        f" {skipped_count} tag(s) were already queued." if skipped_count > 0 else ""
    )
    return f"Queued: {', '.join(parts)}.{suffix}"
