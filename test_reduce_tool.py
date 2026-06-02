from types import SimpleNamespace

import pytest
from hooks.llm_reduce_tool import (
    CtxCompactTool,
    CtxReduceTool,
    resolve_ctx_reduce_target,
)

from astrbot.core.agent.run_context import ContextWrapper


def _make_tags():
    return [
        {
            "tag_number": 1,
            "message_id": "call-1",
            "type": "tool_call",
            "status": "active",
            "tool_name": "web_search",
            "original_text": '{"query":"astrbot docs"}',
            "tool_owner_message_id": "owner-1",
        },
        {
            "tag_number": 2,
            "message_id": "tool:call-1",
            "type": "tool_result",
            "status": "active",
            "tool_name": None,
            "original_text": "AstrBot docs result body",
            "tool_owner_message_id": "owner-1",
        },
        {
            "tag_number": 3,
            "message_id": "call-2",
            "type": "tool_call",
            "status": "active",
            "tool_name": "web_search",
            "original_text": '{"query":"astrbot api"}',
            "tool_owner_message_id": "owner-2",
        },
        {
            "tag_number": 4,
            "message_id": "tool:call-2",
            "type": "tool_result",
            "status": "active",
            "tool_name": None,
            "original_text": "AstrBot api result body",
            "tool_owner_message_id": "owner-2",
        },
    ]


def test_resolve_ctx_reduce_target_unique_tool_name_match():
    result = resolve_ctx_reduce_target(
        _make_tags(),
        "astrbot docs",
        kind="tool_result",
        tool_name="web_search",
        protected_tags=0,
    )
    assert result["ok"] is True
    assert result["group"]["call_id"] == "call-1"
    assert result["group"]["tag_numbers"] == [1, 2]


def test_resolve_ctx_reduce_target_ambiguous_prefix():
    result = resolve_ctx_reduce_target(
        _make_tags(),
        "astrbot",
        kind="tool_result",
        protected_tags=0,
    )
    assert result["ok"] is False
    assert "multiple tool contexts" in result["error"]


def test_resolve_ctx_reduce_target_respects_protected_window():
    result = resolve_ctx_reduce_target(
        _make_tags(),
        "astrbot api",
        kind="tool_result",
        protected_tags=2,
    )
    assert result["ok"] is False
    assert "protected recent window" in result["error"]


class FakeDB:
    def __init__(self):
        self.updated_status = []
        self.updated_drop_mode = []
        self.meta_updates = []
        self.compaction_events = []

    async def get_tags_by_session(self, session_id):
        return _make_tags()

    async def update_tag_drop_mode(self, session_id, tag_number, drop_mode):
        self.updated_drop_mode.append((session_id, tag_number, drop_mode))

    async def update_tag_status(self, session_id, tag_number, status):
        self.updated_status.append((session_id, tag_number, status))

    async def update_session_meta(self, session_id, **kwargs):
        self.meta_updates.append((session_id, kwargs))

    async def record_compaction_event(self, session_id, **kwargs):
        self.compaction_events.append((session_id, kwargs))


class FakeHeuristic:
    def __init__(self):
        self.calls = 0

    async def cleanup_phase(self, event, run_context):
        self.calls += 1
        return {}


class FakeIdleCompaction:
    def __init__(self, result=(2, 123)):
        self.result = result
        self.calls = []

    async def _apply_lite_tool_compaction(self, session_id):
        self.calls.append(session_id)
        return self.result


class FakeHistorian:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.config = {"historian_keep_recent": 10}

    async def run_compartment_agent(self, session_id, context_dicts):
        self.calls.append(
            (session_id, context_dicts, self.config["historian_keep_recent"])
        )
        return self.result


def _make_run_context():
    event = SimpleNamespace(unified_msg_origin="ses-1")
    agent_context = SimpleNamespace(event=event)
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "tool step"},
        {"role": "tool", "content": "tool output"},
    ]
    return event, ContextWrapper(
        context=agent_context, messages=messages, tool_call_timeout=5
    )


@pytest.mark.asyncio
async def test_ctx_reduce_tool_drops_unique_group():
    event, run_context = _make_run_context()
    plugin = SimpleNamespace(
        db=FakeDB(),
        heuristic=FakeHeuristic(),
        config={"protected_tags": 0},
    )
    tool = CtxReduceTool(plugin=plugin)

    result = await tool.call(
        run_context,
        match="astrbot docs",
        kind="tool_result",
        tool_name="web_search",
    )

    assert "Dropped 2 context item(s)" in result
    assert plugin.heuristic.calls == 1
    assert ("ses-1", 1, "dropped") in plugin.db.updated_status
    assert ("ses-1", 2, "dropped") in plugin.db.updated_status


@pytest.mark.asyncio
async def test_ctx_compact_tool_lite_uses_deterministic_cleanup():
    event, run_context = _make_run_context()
    plugin = SimpleNamespace(
        db=FakeDB(),
        heuristic=FakeHeuristic(),
        idle_compaction=FakeIdleCompaction((3, 456)),
        historian=FakeHistorian(None),
        config={"historian_min_messages": 2},
        _estimate_context_tokens=lambda contexts: 1000,
        _resolve_request_context_limit=lambda _event: 10000,
        _now_ms=lambda: 1234567890,
    )
    tool = CtxCompactTool(plugin=plugin)

    result = await tool.call(run_context, mode="lite")

    assert "Lite compaction dropped 3 old tool context item(s)" in result
    assert plugin.idle_compaction.calls == ["ses-1"]
    assert plugin.heuristic.calls == 1
    assert plugin.db.compaction_events[0][1]["mode"] == "lite"


@pytest.mark.asyncio
async def test_ctx_compact_tool_hard_runs_historian():
    event, run_context = _make_run_context()
    plugin = SimpleNamespace(
        db=FakeDB(),
        heuristic=FakeHeuristic(),
        idle_compaction=FakeIdleCompaction((0, 0)),
        historian=FakeHistorian(
            {
                "compartments": [
                    {"end_message": 1, "title": "old", "content": "summary"}
                ],
                "facts": [],
            }
        ),
        config={
            "historian_min_messages": 2,
            "historian_keep_recent": 10,
            "historian_keep_recent_hard": 6,
        },
        _estimate_context_tokens=lambda contexts: 5000,
        _resolve_request_context_limit=lambda _event: 10000,
        _now_ms=lambda: 1234567890,
    )
    tool = CtxCompactTool(plugin=plugin)

    result = await tool.call(run_context, mode="hard")

    assert "Hard compaction stored 1 summary compartment(s)" in result
    assert len(plugin.historian.calls) == 1
    assert plugin.db.compaction_events[0][1]["mode"] == "hard"
