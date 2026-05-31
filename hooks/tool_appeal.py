import json
from pathlib import Path


def get_tool_catalog(tool_mgr) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for tool in getattr(tool_mgr, "func_list", []):
        name = str(getattr(tool, "name", "") or "").strip()
        if not name or not getattr(tool, "active", True):
            continue
        desc = str(getattr(tool, "description", "") or "").strip()
        catalog[name] = desc
    return dict(sorted(catalog.items()))


def load_tool_appeal_state(state_path: Path) -> dict[str, dict[str, str]]:
    if not state_path.exists():
        return {"known_tools": {}, "pending_tools": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"known_tools": {}, "pending_tools": {}}
    known_tools = data.get("known_tools", {})
    pending_tools = data.get("pending_tools", {})
    if not isinstance(known_tools, dict):
        known_tools = {}
    if not isinstance(pending_tools, dict):
        pending_tools = {}
    return {
        "known_tools": {
            str(k): str(v or "") for k, v in known_tools.items() if str(k).strip()
        },
        "pending_tools": {
            str(k): str(v or "") for k, v in pending_tools.items() if str(k).strip()
        },
    }


def save_tool_appeal_state(state_path: Path, state: dict[str, dict[str, str]]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def stage_new_tools_if_any(state_path: Path, current_catalog: dict[str, str]) -> None:
    state = load_tool_appeal_state(state_path)
    known_tools = state.get("known_tools", {})
    pending_tools = state.get("pending_tools", {})

    if not known_tools and not state_path.exists():
        save_tool_appeal_state(
            state_path,
            {"known_tools": current_catalog, "pending_tools": pending_tools},
        )
        return

    new_tool_names = sorted(set(current_catalog) - set(known_tools))
    if new_tool_names:
        for tool_name in new_tool_names:
            pending_tools[tool_name] = current_catalog.get(tool_name, "")

    save_tool_appeal_state(
        state_path,
        {"known_tools": current_catalog, "pending_tools": pending_tools},
    )


def peek_pending_tool_appeal(state_path: Path) -> str:
    state = load_tool_appeal_state(state_path)
    pending_tools = state.get("pending_tools", {})
    if not pending_tools:
        return ""
    return build_tool_appeal_text(pending_tools)


def filter_pending_tools(
    pending_tools: dict[str, str], blocked_tool_names: set[str]
) -> dict[str, str]:
    if not blocked_tool_names:
        return dict(pending_tools)
    return {
        tool_name: desc
        for tool_name, desc in pending_tools.items()
        if tool_name not in blocked_tool_names
    }


def clear_pending_tool_appeal(state_path: Path) -> None:
    state = load_tool_appeal_state(state_path)
    if not state.get("pending_tools", {}):
        return
    save_tool_appeal_state(
        state_path,
        {"known_tools": state.get("known_tools", {}), "pending_tools": {}},
    )


def clear_pending_tool_names(state_path: Path, tool_names: set[str]) -> None:
    if not tool_names:
        return
    state = load_tool_appeal_state(state_path)
    pending_tools = dict(state.get("pending_tools", {}))
    changed = False
    for tool_name in tool_names:
        if tool_name in pending_tools:
            pending_tools.pop(tool_name, None)
            changed = True
    if not changed:
        return
    save_tool_appeal_state(
        state_path,
        {"known_tools": state.get("known_tools", {}), "pending_tools": pending_tools},
    )


def build_tool_appeal_text(pending_tools: dict[str, str]) -> str:
    lines = [
        "<appeal_only>",
        "Plugin reload introduced new tools.",
        "Newly available tools:",
    ]
    for tool_name, desc in sorted(pending_tools.items()):
        if desc:
            lines.append(f"- `{tool_name}`: {desc}")
        else:
            lines.append(f"- `{tool_name}`")
    lines.extend(
        [
            "Use these tools only if they directly help with the current user request.",
            "Do not mention this notice to the user unless they ask about tool availability.",
            "</appeal_only>",
        ]
    )
    return "\n".join(lines)


def inject_appeal_only_into_request(req, appeal_text: str) -> bool:
    if not appeal_text:
        return False

    contexts = list(getattr(req, "contexts", []) or [])
    for idx in range(len(contexts) - 1, -1, -1):
        item = contexts[idx]
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = f"{content.rstrip()}\n\n{appeal_text}"
            req.contexts = contexts
            return True
        if isinstance(content, list):
            content.append({"type": "text", "text": appeal_text})
            item["content"] = content
            req.contexts = contexts
            return True

    return False
