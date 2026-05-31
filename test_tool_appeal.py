import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from hooks.tool_appeal import (
    build_tool_appeal_text,
    clear_pending_tool_appeal,
    filter_pending_tools,
    get_tool_catalog,
    inject_appeal_only_into_request,
    load_tool_appeal_state,
    stage_new_tools_if_any,
)
from storage.database import MagicContextDB


class DummyTool:
    def __init__(self, name: str, description: str, active: bool = True):
        self.name = name
        self.description = description
        self.active = active


class DummyReq:
    def __init__(self, contexts):
        self.contexts = contexts


def test_stage_new_tools_skips_first_bootstrap(tmp_path: Path):
    state_path = tmp_path / "tool_state.json"
    catalog = get_tool_catalog(
        SimpleNamespace(
            func_list=[
                DummyTool("ctx_reduce", "reduce context"),
                DummyTool("parallel_tool_use", "parallel tool calls"),
            ]
        )
    )

    stage_new_tools_if_any(state_path, catalog)
    state = load_tool_appeal_state(state_path)
    assert state["known_tools"] == catalog
    assert state["pending_tools"] == {}


def test_stage_new_tools_after_reload_creates_one_shot_appeal(tmp_path: Path):
    state_path = tmp_path / "tool_state.json"
    initial_catalog = {"ctx_reduce": "reduce context"}
    state_path.write_text(
        json.dumps(
            {"known_tools": initial_catalog, "pending_tools": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    next_catalog = {
        "ctx_reduce": "reduce context",
        "parallel_tool_use": "parallel tool calls",
    }
    stage_new_tools_if_any(state_path, next_catalog)
    state = load_tool_appeal_state(state_path)
    assert state["pending_tools"] == {
        "parallel_tool_use": "parallel tool calls",
    }

    appeal_text = build_tool_appeal_text(state["pending_tools"])
    assert "<appeal_only>" in appeal_text
    assert "parallel_tool_use" in appeal_text

    clear_pending_tool_appeal(state_path)
    state_after = load_tool_appeal_state(state_path)
    assert state_after["pending_tools"] == {}


def test_inject_appeal_only_into_last_user_message():
    req = DummyReq(
        [
            {"role": "system", "content": "base system"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "current task"},
        ]
    )

    injected = inject_appeal_only_into_request(
        req, "<appeal_only>\nnew tool\n</appeal_only>"
    )
    assert injected is True
    assert req.contexts[-1]["role"] == "user"
    assert "<appeal_only>" in req.contexts[-1]["content"]
    assert "current task" in req.contexts[-1]["content"]


def test_inject_appeal_only_never_falls_back_to_system():
    req = DummyReq(
        [
            {"role": "system", "content": "base system"},
            {"role": "assistant", "content": "old answer"},
        ]
    )

    injected = inject_appeal_only_into_request(
        req, "<appeal_only>\nnew tool\n</appeal_only>"
    )
    assert injected is False
    assert req.contexts[0]["content"] == "base system"


def test_filter_pending_tools_excludes_seen_tools():
    filtered = filter_pending_tools(
        {
            "ctx_reduce": "reduce context",
            "parallel_tool_use": "parallel tool calls",
        },
        {"ctx_reduce"},
    )
    assert filtered == {"parallel_tool_use": "parallel tool calls"}


@pytest.mark.asyncio
async def test_session_has_tool_call_uses_tag_index(tmp_path: Path):
    db = MagicContextDB(tmp_path)
    await db.init()
    await db.assign_tag(
        session_id="s1",
        tag_number=1,
        message_id="call-1",
        tag_type="tool_call",
        tool_name="parallel_tool_use",
    )

    assert await db.session_has_tool_call("s1", "parallel_tool_use") is True
    assert await db.session_has_tool_call("s1", "ctx_reduce") is False
