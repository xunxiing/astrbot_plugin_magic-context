# 阶段 2: 启发式清理 (Heuristic Cleanup)

## 目标
通过预设规则自动清理低价值内容，减少 token 消耗，为后续的 Historian Agent 压缩做准备。

## 需要照搬的文件

### 2.1 heuristic-cleanup.ts
**路径**: `packages/plugin/src/hooks/magic-context/heuristic-cleanup.ts`

**需要照搬的函数/类**:
- `applyHeuristicCleanup(sessionId, db, targets, messageTagNumbers, config, tags)` - 主入口
  - 自动丢弃旧工具调用
  - 工具去重
  - 空消息清理
- `buildToolFingerprints(messages)` - 构建工具指纹
  - 基于 toolName + input 的哈希
  - 用于识别重复的工具调用
- `isEmptyAssistantMessage(message)` - 判断空助手消息
- `isToolOnlyMessage(message)` - 判断纯工具消息
- `getToolAgeThreshold(maxTag, config)` - 计算工具年龄阈值

### 2.2 apply-operations.ts
**路径**: `packages/plugin/src/hooks/magic-context/apply-operations.ts`

**需要照搬的函数/类**:
- `applyPendingOperations(sessionId, db, messages)` - 应用待处理的 drop 操作
  - 从 pending_ops 表读取待执行操作
  - 调用 target.drop() 或 target.truncate()
  - 更新 tags 表状态为 "dropped"
- `applyFlushedStatuses(sessionId, db, messages)` - 应用已持久化的 drop 状态
  - 从 tags 表读取已 drop 的标签
  - 重放 drop 操作（用于跨 pass 恢复）
- `buildReplacementContent(tagId, target)` - 构建丢弃后的替换内容
  - 用户消息: `[truncated §N§]\n{预览}`
  - 助手消息: `[dropped §N§]`
- `queuePendingOp(db, sessionId, tagId, op)` - 排队待执行操作
- `getPendingOps(db, sessionId)` - 获取待执行操作列表
- `clearPendingOps(db, sessionId)` - 清除待执行操作

## 关键设计决策

1. **自动丢弃规则**:
   - 工具调用超过 `autoDropToolAge` 个标签自动丢弃
   - 相同 fingerprint 的工具调用只保留最新
   - 空的 assistant 消息（不含有效 part）直接移除

2. **保护机制**:
   - 最近的 `protectedTags` 个标签不被自动丢弃
   - 有结果的工具调用才能被完全丢弃（无结果的只能截断）

3. **丢弃模式**:
   - `dropToolStructure=true`: 完全移除工具调用和结果
   - `dropToolStructure=false`: 截断内容，保留结构

## 配置项

```typescript
interface HeuristicConfig {
  autoDropToolAge: number;      // 自动丢弃工具的年龄阈值
  protectedTags: number;        // 保护最近的标签数量
  dropToolStructure: boolean;   // 是否完全移除工具结构
}
```

## 工具指纹算法

```typescript
function buildToolFingerprints(messages): Map<string, ToolFingerprint[]> {
  // 基于 toolName + normalizedInput 的哈希
  // 相同 fingerprint 的工具调用被认为是重复的
}
```

## AstrBot 实现分析

### 可行
✅ 完全可行。使用 `@on_agent_begin` hook 获取 `run_context.messages: list[Message]`。
✅ **工具精确匹配**: `msg.tool_calls[].id` ↔ `msg.tool_call_id` — 可精确移除某个 tool call 及其结果。
✅ **Part 级操作**: `msg.content` 作为 `list[ContentPart]` 时，可删除具体的 part 而非整条消息。
✅ **工具指纹去重**: 基于 `tc.function.name + tc.function.arguments` 的哈希，完全相同者保留最新。

### 不可行 / 需简化
❌ 没有 `pending_ops` 表机制。
   → **简化**: 直接在 hook 中修改 `run_context.messages`，不需要延迟执行。
❌ AstrBot 的 parts 没有 OpenCode 的 `ToolDropResult` 状态机。
   → **简化**: 直接 `del msg.content[i]` 或设置 `msg.content = None`。

### 实现方案（使用 `@on_agent_begin` + 精确 tool 匹配）

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.agent.message import Message, ToolCall, TextPart
import hashlib

@filter.on_agent_begin()
async def magic_context_heuristic_cleanup(
    self,
    event: AstrMessageEvent,
    run_context: ContextWrapper[AstrAgentContext],
):
    """阶段 2: 启发式清理 — 精确 tool drop + 去重"""
    session_id = event.unified_msg_origin
    messages = run_context.messages
    tags = await self.tags_db.get_session_tags(session_id)
    max_tag = max((t["tag_number"] for t in tags), default=len(tags))
    age_threshold = max_tag - self.config.auto_drop_tool_age

    # 1. 工具去重：收集所有 tool fingerprints，标记重复的
    fingerprints: dict[str, int] = {}  # fp_str → 最新 tag_number
    to_drop_tags: set[int] = set()

    for i, msg in enumerate(messages):
        tag_info = tags[i] if i < len(tags) else {}
        tag_num = tag_info.get("tag_number", i)

        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                fp = build_tool_fingerprint(tc)
                if fp in fingerprints and tag_num > age_threshold:
                    to_drop_tags.add(tag_num)  # 旧重复，丢弃
                fingerprints[fp] = tag_num

    # 2. 执行清理：遍历消息，执行精确 drop
    to_remove: list[int] = []  # 消息索引，标记待删除

    for i, msg in enumerate(messages):
        tag_info = tags[i] if i < len(tags) else {}
        tag_num = tag_info.get("tag_number", i)

        # 跳过 system 和 _checkpoint
        if msg.role in ("system", "_checkpoint"):
            continue

        # 丢弃旧的 tool 消息
        if msg.role == "tool" and tag_num < age_threshold:
            to_remove.append(i)
            continue

        # 丢弃重复的工具消息
        if tag_num in to_drop_tags:
            to_remove.append(i)
            continue

        # 丢弃空的 assistant 消息（无 tool_calls 且无意义 content）
        if msg.role == "assistant":
            has_tool_calls = bool(msg.tool_calls)
            has_content = bool(msg.content) if msg.content else False
            if not has_tool_calls and not has_content:
                to_remove.append(i)
                continue
            # Part 级检查：content 为 parts 时所有 part 为空
            if isinstance(msg.content, list):
                has_meaningful = any(
                    isinstance(p, TextPart) and p.text.strip()
                    for p in msg.content
                )
                if not has_meaningful and not has_tool_calls:
                    to_remove.append(i)
                    continue

        # 丢弃极短的 text 消息
        if msg.role in ("user", "assistant"):
            if isinstance(msg.content, str) and len(msg.content.strip()) < 2:
                if not msg.tool_calls:
                    to_remove.append(i)

    # 从后往前删除，避免索引错乱
    for i in reversed(to_remove):
        del messages[i]

    # 更新 run_context（直接修改已经生效）
    run_context.messages = messages


def build_tool_fingerprint(tc: ToolCall) -> str:
    """基于 toolName + input 的哈希（与 OpenCode 一致）"""
    raw = f"{tc.function.name}:{tc.function.arguments}"
    return hashlib.md5(raw.encode()).hexdigest()
```

### 注意事项
1. `msg.tool_calls[].id` 通过 `msg.tool_call_id` 精确匹配 tool 结果 — **不需要复合键**
2. 工具去重基于 `name + arguments` 的 MD5，与 OpenCode 算法一致
3. 从后往前删除避免索引错乱
4. `@on_agent_begin` 中直接修改 `messages` 即可生效
5. Tool 消息的 tag number 来自阶段 1 的 tags 表，需确保阶段 1 先于阶段 2 运行
