# 阶段 1: 标签化阶段 (Tag Phase)

## 目标
为每条消息的每个部分分配唯一的 §N§ 标签，创建可操作的目标对象，为后续的丢弃和压缩提供基础。

## 需要照搬的文件

### 1.1 tagger.ts
**路径**: `packages/plugin/src/hooks/magic-context/tagger.ts`

**需要照搬的函数/类**:
- `class Tagger` - 标签分配器核心类
  - `counters: Map<string, number>` - 每个 session 的单调递增计数器
  - `assignments: Map<string, number>` - messageId → tagNumber 映射
  - `assignTag(sessionId, contentId, type, byteSize, db)` - 分配标签
  - `assignToolTag(sessionId, callId, ownerMsgId, byteSize, db)` - 分配工具标签（复合键）
  - `getTag(sessionId, contentId)` - 获取标签号
  - `resetSession(sessionId)` - 重置 session 标签状态
- `makeToolCompositeKey(ownerMsgId, callId)` - 工具复合键生成
- `TagEntry` 类型定义

### 1.2 tag-messages.ts
**路径**: `packages/plugin/src/hooks/magic-context/tag-messages.ts`

**需要照搬的函数/类**:
- `tagMessages(sessionId, db, messages)` - 主入口函数
  - 遍历每条消息的每个 part
  - 根据类型分配标签（message/tool/file）
  - 注入 §N§ 前缀到文本内容
  - 创建 TagTarget / ToolDropTarget
- `createTagTarget(contentId, text, message, partIndex)` - 创建消息/文件标签目标
  - `setContent(content)` - 修改内容
  - `getContent()` - 获取当前内容
- `createToolDropTarget(compositeKey, thinkingParts, index, batch)` - 创建工具丢弃目标
  - `drop()` - 完全移除工具部分
  - `truncate()` - 截断工具内容
- `TagTarget` 类型定义
- `ToolDropResult` 类型定义
- `isTextPart(part)` - 判断文本部分
- `isToolPartWithOutput(part)` - 判断有输出的工具部分
- `isFilePart(part)` - 判断文件部分

### 1.3 tool-drop-target.ts
**路径**: `packages/plugin/src/hooks/magic-context/tool-drop-target.ts`

**需要照搬的函数/类**:
- `class ToolMutationBatch` - 批量工具变更
  - `markForRemoval(occurrence)` - 标记待移除
  - `finalize()` - 提交变更（过滤 parts + 移除空消息）
- `createToolDropTarget(compositeKey, thinkingParts, index, batch)` - 创建工具丢弃目标
  - `drop(): ToolDropResult` - 移除工具调用和结果
  - `truncate(): ToolDropResult` - 截断工具内容
- `truncateToolPart(part)` - 截断单个工具部分
- `truncateInputValues(input)` - 截断输入参数
- `clearThinkingParts(thinkingParts)` - 清除关联的 thinking 内容
- `hasMeaningfulPart(message)` - 判断消息是否有意义的部分

## 关键设计决策

1. **标签格式**: `§N§` 前缀，N 为单调递增数字
2. **工具复合键**: `${ownerMsgId}\x00${callId}`，避免 callId 冲突
3. **TagTarget 接口**: 提供 setContent/getContent/drop/truncate 操作
4. **批量移除**: ToolMutationBatch 收集所有变更后统一提交

## 数据结构

```typescript
interface TagTarget {
  setContent: (content: string) => boolean;
  getContent?: () => string | null;
  drop?: () => ToolDropResult;
  truncate?: () => ToolDropResult;
  message?: MessageLike;
}

type ToolDropResult = "removed" | "truncated" | "absent" | "incomplete";
```

## 数据库表

```sql
CREATE TABLE tags (
  session_id TEXT,
  tag_number INTEGER,
  message_id TEXT,
  type TEXT,           -- "message" | "tool" | "file"
  status TEXT,         -- "active" | "dropped" | "compacted"
  drop_mode TEXT,      -- "full" | "truncated" | null
  tool_owner_message_id TEXT,
  PRIMARY KEY (session_id, tag_number)
);
```

## AstrBot 实现分析

### 可行
✅ 完全可行。使用 `@on_agent_begin` hook 获取 `run_context.messages: list[Message]`（Pydantic 类型）。
✅ `msg.content` 可以是 `list[ContentPart]`，每个 part 有 `.type` 区分 text/think/image — **可精确到 part 级别标签化**。
✅ `msg.tool_calls[].id` 通过 `tool_call_id` 精确匹配 `role="tool"` 消息 — **复合键不需要了**，ID 本就唯一。
✅ 可用 `StarTools.get_data_dir()` 创建独立 SQLite，存储标签信息。

### 不可行 / 需简化
❌ `@on_agent_begin` 收到的 messages 已被 agent 处理过（system prompt 前置、checkpoint 绑定），不是原始消息。
   → **注意**: 标签化在 agent 处理之后执行，但消息结构完整保留 parts。
❌ 没有 `ToolMutationBatch` / `ToolDropTarget` 的复杂机制。
   → **简化**: 直接在 `Message` Pydantic 对象上修改，不需要单独的对象引用系统。
❌ §N§ 前缀注入无意义（AstrBot 输出文本不会显示给用户看工具调用细节）。
   → **简化**: 不在消息内容中注入 §N§，只存储在 SQLite 的 tags 表中。

### 实现方案（使用 `@on_agent_begin` + `list[Message]`）

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.agent.message import Message, TextPart, ThinkPart

@filter.on_agent_begin()
async def magic_context_tag(
    self,
    event: AstrMessageEvent,
    run_context: ContextWrapper[AstrAgentContext],
):
    """阶段 1: 标签化 — 按 part 和 tool_call_id 精确标记"""
    session_id = event.unified_msg_origin
    self.tags_db.clear_session_tags(session_id)

    for msg in run_context.messages:
        if msg.role == "_checkpoint":
            continue

        # 1. 按 part 分配标签（不在 str content 上）
        if isinstance(msg.content, list):
            # content 是 list[ContentPart] — 可精确到 part 级别
            for part in msg.content:
                part_id = id(part)  # 用对象 ID 作为 part 标识
                tag_number = self.tags_db.assign_tag(
                    session_id, f"part_{part_id}", msg.role,
                    extra={"type": part.type}
                )
                if isinstance(part, TextPart):
                    # 可选: 注入 §N§ 前缀到 TextPart.text
                    pass  # AstrBot 环境下通常不需要
        elif isinstance(msg.content, str):
            # 纯文本消息
            tag_number = self.tags_db.assign_tag(
                session_id, str(id(msg)), msg.role
            )

        # 2. 按 tool_call 分配独立标签
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                self.tags_db.assign_tag(
                    session_id, tc.id, "tool_call",
                    extra={
                        "tool_name": tc.function.name,
                        "owner_msg_id": str(id(msg)),
                    }
                )

        # 3. tool 结果消息 — 通过 tool_call_id 精准匹配
        if msg.role == "tool" and msg.tool_call_id:
            self.tags_db.assign_tag(
                session_id, msg.tool_call_id, "tool_result",
                extra={
                    "call_id": msg.tool_call_id,
                }
            )

# tags_db.py — 增强版支持 tool 复合键和 part 级标记
import aiosqlite
from pathlib import Path

class TagsDatabase:
    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "magic_context.db"

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    session_id TEXT NOT NULL,
                    tag_number INTEGER NOT NULL,
                    content_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    tag_type TEXT DEFAULT 'message',
                    status TEXT DEFAULT 'active',
                    byte_size INTEGER DEFAULT 0,
                    extra_json TEXT DEFAULT '{}',
                    PRIMARY KEY (session_id, tag_number)
                )
            """)
            await db.commit()

    async def assign_tag(
        self, session_id: str, content_id: str, role: str,
        tag_type: str = "message", extra: dict | None = None
    ) -> int:
        import json
        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                "SELECT COALESCE(MAX(tag_number), 0) + 1 FROM tags WHERE session_id = ?",
                (session_id,)
            )
            tag_number = row[0][0] if row else 1
            await db.execute(
                """INSERT OR REPLACE INTO tags
                   (session_id, tag_number, content_id, role, tag_type, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, tag_number, content_id, role, tag_type,
                 json.dumps(extra or {})),
            )
            await db.commit()
            return tag_number
```

### 注意事项
1. `@on_agent_begin` 提供的 `run_context.messages` 是 `list[Message]`（Pydantic），比 `req.contexts` 丰富得多
2. 标签化无需 `@on_llm_request` — 从 `@on_agent_begin` 直接拿到 typed objects
3. 工具标签通过 `tc.id` ↔ `msg.tool_call_id` 精确匹配，不需要 OpenCode 的复合键 (`ownerMsgId\x00callId`)
4. Part 级标签化依赖 `msg.content` 是 `list[ContentPart]` 的情况；纯文本 `str` content 只能消息级标记
5. 标签信息存在 SQLite，不污染消息内容
