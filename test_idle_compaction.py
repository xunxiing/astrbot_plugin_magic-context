import time
from types import SimpleNamespace

import pytest
from hooks.heuristic_cleanup import HeuristicCleanup
from hooks.idle_compaction import IdleCompactionService
from hooks.injection import Injector


class FakeDB:
    def __init__(self):
        self.meta_rows = []
        self.updated = []
        self.compartments = {}
        self.facts = {}
        self.tags = {}
        self.tag_status_updates = []
        self.tag_drop_mode_updates = []

    async def list_session_meta(self):
        return list(self.meta_rows)

    async def update_session_meta(self, session_id, **kwargs):
        self.updated.append((session_id, kwargs))

    async def get_compartments(self, session_id):
        return list(self.compartments.get(session_id, []))

    async def get_session_facts(self, session_id):
        return list(self.facts.get(session_id, []))

    async def get_active_tags(self, session_id):
        return list(self.tags.get(session_id, []))

    async def update_tag_status(self, session_id, tag_number, status):
        self.tag_status_updates.append((session_id, tag_number, status))

    async def update_tag_drop_mode(self, session_id, tag_number, drop_mode):
        self.tag_drop_mode_updates.append((session_id, tag_number, drop_mode))


class FakeConversationManager:
    def __init__(self, history):
        self.history = history

    async def get_curr_conversation_id(self, session_id):
        return "cid-1"

    async def get_conversation(self, session_id, cid):
        import json

        return SimpleNamespace(history=json.dumps(self.history))


class FakeHistorian:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.config = {"historian_keep_recent": 10}

    async def run_compartment_agent(self, session_id, conversation):
        self.calls.append(
            (session_id, conversation, self.config["historian_keep_recent"])
        )
        return self.result


@pytest.mark.asyncio
async def test_idle_compaction_skips_zombie_session():
    now_ms = int(time.time() * 1000)
    db = FakeDB()
    db.meta_rows = [
        {
            "session_id": "ses-zombie",
            "last_response_time": now_ms - (3 * 60 * 60 * 1000),
            "recent_24h_message_count": 100,
            "compartment_in_progress": 0,
            "last_request_input_tokens": 9000,
            "last_request_context_limit": 10000,
        }
    ]
    historian = FakeHistorian(
        {
            "compartments": [{"end_message": 5, "title": "A", "content": "B"}],
            "facts": [],
        }
    )
    context = SimpleNamespace(
        conversation_manager=FakeConversationManager(
            [{"role": "user", "content": "x"}]
        ),
        get_config=lambda: {
            "provider_settings": {"fallback_max_context_tokens": 128000}
        },
    )
    svc = IdleCompactionService(
        db,
        historian,
        {
            "idle_compaction_enabled": True,
            "idle_compaction_after_minutes": 10,
            "idle_compaction_max_idle_minutes": 120,
            "active_session_min_messages_24h": 12,
            "idle_compaction_min_tokens": 4000,
            "lite_compaction_ratio_threshold": 0.4,
        },
        context,
    )
    await svc.run_once()
    assert historian.calls == []


@pytest.mark.asyncio
async def test_idle_compaction_runs_for_active_session():
    now_ms = int(time.time() * 1000)
    db = FakeDB()
    db.meta_rows = [
        {
            "session_id": "ses-hot",
            "last_response_time": now_ms - (20 * 60 * 1000),
            "recent_24h_message_count": 20,
            "compartment_in_progress": 0,
            "last_request_input_tokens": 9000,
            "last_request_context_limit": 10000,
            "last_compaction_source_end_message": None,
        }
    ]
    history = [{"role": "user", "content": f"msg {i}"} for i in range(25)]
    historian = FakeHistorian(
        {
            "compartments": [
                {"end_message": 15, "title": "Older work", "content": "summary"}
            ],
            "facts": [],
        }
    )
    context = SimpleNamespace(
        conversation_manager=FakeConversationManager(history),
        get_config=lambda: {
            "provider_settings": {"fallback_max_context_tokens": 128000}
        },
    )
    svc = IdleCompactionService(
        db,
        historian,
        {
            "idle_compaction_enabled": True,
            "idle_compaction_after_minutes": 10,
            "idle_compaction_max_idle_minutes": 120,
            "active_session_min_messages_24h": 12,
            "idle_compaction_min_tokens": 4000,
            "idle_compaction_min_messages": 20,
            "lite_compaction_ratio_threshold": 0.4,
            "historian_keep_recent_lite": 12,
            "historian_keep_recent_hard": 6,
        },
        context,
    )
    await svc.run_once()
    assert len(historian.calls) == 1
    assert any(
        session_id == "ses-hot" and payload.get("last_compaction_mode") == "hard"
        for session_id, payload in db.updated
    )


@pytest.mark.asyncio
async def test_idle_lite_only_drops_old_tool_tags_without_historian():
    now_ms = int(time.time() * 1000)
    db = FakeDB()
    db.meta_rows = [
        {
            "session_id": "ses-lite",
            "last_response_time": now_ms - (20 * 60 * 1000),
            "recent_24h_message_count": 20,
            "compartment_in_progress": 0,
            "last_request_input_tokens": 3000,
            "last_request_context_limit": 10000,
        }
    ]
    db.tags["ses-lite"] = [
        {"tag_number": 1, "type": "tool_call"},
        {"tag_number": 2, "type": "tool_result"},
        {"tag_number": 30, "type": "tool_call"},
    ]
    historian = FakeHistorian(
        {
            "compartments": [
                {"end_message": 15, "title": "Older work", "content": "summary"}
            ],
            "facts": [],
        }
    )
    context = SimpleNamespace(
        conversation_manager=FakeConversationManager([]),
        get_config=lambda: {
            "provider_settings": {"fallback_max_context_tokens": 128000}
        },
    )
    svc = IdleCompactionService(
        db,
        historian,
        {
            "idle_compaction_enabled": True,
            "idle_compaction_after_minutes": 10,
            "idle_compaction_max_idle_minutes": 120,
            "active_session_min_messages_24h": 12,
            "idle_compaction_min_tokens": 1000,
            "lite_compaction_ratio_threshold": 0.4,
            "protected_tags": 20,
            "auto_drop_tool_age": 20,
            "drop_tool_structure": False,
        },
        context,
    )
    await svc.run_once()
    assert historian.calls == []
    assert ("ses-lite", 1, "dropped") in db.tag_status_updates
    assert ("ses-lite", 2, "dropped") in db.tag_status_updates
    assert ("ses-lite", 30, "dropped") not in db.tag_status_updates
    assert any(
        session_id == "ses-lite" and payload.get("last_compaction_mode") == "lite"
        for session_id, payload in db.updated
    )


@pytest.mark.asyncio
async def test_heuristic_cleanup_removes_dropped_tool_calls_from_assistant():
    class LocalDB:
        async def get_tags_by_session(self, session_id):
            return [
                {
                    "tag_number": 1,
                    "message_id": "call-1",
                    "type": "tool_call",
                    "status": "dropped",
                    "tool_owner_message_id": "msg:assistant-1",
                },
                {
                    "tag_number": 2,
                    "message_id": "tool:call-1",
                    "type": "tool_result",
                    "status": "dropped",
                    "tool_owner_message_id": "msg:assistant-1",
                    "drop_mode": "full",
                },
            ]

        async def get_max_tag_number(self, session_id):
            return 2

        async def get_pending_ops(self, session_id):
            return []

        async def clear_pending_ops(self, session_id):
            return None

        async def update_tag_status(self, session_id, tag_number, status):
            return None

        async def update_tag_drop_mode(self, session_id, tag_number, drop_mode):
            return None

    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="web_search", arguments='{"q":"astrbot"}'),
    )
    assistant_msg = SimpleNamespace(
        role="assistant", id="assistant-1", content="", tool_calls=[tool_call]
    )
    tool_msg = SimpleNamespace(
        role="tool", content="big tool result", tool_call_id="call-1"
    )
    run_context = SimpleNamespace(messages=[assistant_msg, tool_msg])
    event = SimpleNamespace(unified_msg_origin="ses-1")

    cleanup = HeuristicCleanup(LocalDB(), {"drop_tool_structure": True})

    await cleanup.cleanup_phase(event, run_context)

    assert assistant_msg.tool_calls is None
    assert assistant_msg.content == "[dropped]"
    assert tool_msg.content == "[dropped]"


@pytest.mark.asyncio
async def test_injector_replaces_old_prefix_with_summary_block():
    db = FakeDB()
    db.compartments["ses-1"] = [
        {"start_message": 0, "end_message": 1, "title": "Old", "content": "summary"}
    ]
    injector = Injector(db)
    req = SimpleNamespace(
        contexts=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old assistant"},
            {"role": "user", "content": "new user"},
        ]
    )
    event = SimpleNamespace(unified_msg_origin="ses-1")
    await injector.inject_phase(event, req)
    assert req.contexts[0]["role"] == "system"
    assert req.contexts[1]["_magic_context"] is True
    assert req.contexts[2]["content"] == "new user"
    assert len(req.contexts) == 3
