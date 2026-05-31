# 阶段 6: 后处理阶段 (Post-processing Phase)

## 目标
应用所有待处理的丢弃操作，清理空消息，重新标签化，为下一轮压缩做准备。

## 需要照搬的文件

### 6.1 transform-postprocess-phase.ts
**路径**: `packages/plugin/src/hooks/magic-context/transform-postprocess-phase.ts`

**需要照搬的函数/类**:
- `runPostprocessPhase(sessionId, db, messages, config)` - 主入口函数
  - 应用待处理的 drop 操作
  - 应用已持久化的 drop 状态
  - 清理空消息
  - 重新计算标签
- `applyPendingOperations(sessionId, db, messages)` - 应用 pending drop
  - 从 pending_ops 表读取
  - 调用 target.drop() 或 target.truncate()
  - 更新 tags 表状态
- `applyFlushedStatuses(sessionId, db, messages)` - 应用已持久化状态
  - 从 tags 表读取已 drop 的标签
  - 重放 drop 操作
- `removeEmptyMessages(messages)` - 移除空消息
  - 检查消息是否有有意义的 part
  - 移除没有有效内容的 message
- `retagMessages(sessionId, db, messages)` - 重新标签化
  - 清除旧标签
  - 重新分配标签

### 6.2 apply-operations.ts
**路径**: `packages/plugin/src/hooks/magic-context/apply-operations.ts`

**需要照搬的函数/类**:
- `buildReplacementContent(tagId, target)` - 构建替换内容
  - 用户消息: `[truncated §N§]\n{预览}`
  - 助手消息: `[dropped §N§]`
  - 预览保留前 250 字符或截断到最近单词边界
- `queuePendingOp(db, sessionId, tagId, op)` - 排队操作
  - 插入 pending_ops 表
- `getPendingOps(db, sessionId)` - 获取待执行操作
- `clearPendingOps(db, sessionId)` - 清除待执行操作
- `updateTagStatus(db, sessionId, tagId, status)` - 更新标签状态

### 6.3 tag-messages.ts (重新标签化部分)
**路径**: `packages/plugin/src/hooks/magic-context/tag-messages.ts`

**需要照搬的函数/类**:
- `clearSessionTags(db, sessionId)` - 清除会话标签
  - 从 tags 表删除该会话的所有标签
- `tagMessages(sessionId, db, messages)` - 重新标签化
  - 与阶段 1 相同，但此时部分消息已被 drop

## 关键设计决策

1. **两阶段应用**:
   - `applyPendingOperations`: 应用本次 pass 新产生的 drop
   - `applyFlushedStatuses`: 应用之前已持久化的 drop

2. **替换内容策略**:
   - 用户消息保留预览（避免破坏 turn boundary）
   - 助手消息完全替换为占位符
   - 预览截断到最近单词边界

3. **空消息清理**:
   - 移除没有有意义 part 的消息
   - 从后向前遍历，避免索引问题

4. **重新标签化**:
   - 清除旧标签（从数据库删除）
   - 重新分配标签（新的单调递增序列）
   - 保持与原始消息结构的对应关系

## 替换内容构建

```typescript
function buildReplacementContent(tagId, target) {
  if (role !== "user") {
    // 助手消息: 完全丢弃
    return `[dropped §${tagId}§]`;
  }
  
  // 用户消息: 保留预览
  const originalText = stripTagPrefix(currentContent);
  if (originalText.length <= 250) {
    return `[truncated §${tagId}§]\n${originalText}`;
  }
  
  // 截断到最近单词边界
  const preview = originalText.slice(0, 250);
  const lastSpace = preview.lastIndexOf(' ');
  const truncated = lastSpace > 0 ? preview.slice(0, lastSpace) : preview;
  return `[truncated §${tagId}§]\n${truncated}...`;
}
```

## 数据库表

```sql
-- 待执行操作
CREATE TABLE pending_ops (
  session_id TEXT,
  tag_id INTEGER,
  op TEXT,             -- "drop" | "truncate"
  created_at INTEGER,
  PRIMARY KEY (session_id, tag_id)
);

-- 标签状态
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

## 状态流转

```
active → dropped (通过 applyPendingOperations)
active → compacted (通过 Historian compartment 化)
dropped → active (不支持，重新创建新标签)
```

## AstrBot 实现分析

### 可行
✅ 完全可行。在 `@filter.on_llm_response()` hook 中执行后处理。
✅ `LLMResponse` 有 `usage.token_usage` 字段，可记录 token 使用量。
✅ `@filter.after_message_sent()` 可用于最终归档。

### 不可行 / 需简化
❌ AstrBot 没有 `pending_ops` 表和两阶段 drop 机制。
   → **简化**: 在 hook 中同步执行所有清理，不需要延迟。
❌ 没有 `buildReplacementContent()` 的复杂替换逻辑。
   → **简化**: 直接标记 tags 表状态，不再重建消息内容。
❌ 不需要在消息内容中注入 `[dropped §N§]`。
   → **简化**: 后处理只做数据清理（标记状态、移除注入内容、记录日志）。

### 实现方案

```python
@filter.on_llm_response(priority=90)
async def magic_context_postprocess(self, event: AstrMessageEvent, response: LLMResponse):
    """阶段 6: 后处理 - 清理和归档"""
    session_id = event.unified_msg_origin
    
    # 1. 记录 token 使用
    if response.usage:
        await self.tags_db.record_token_usage(
            session_id,
            response.usage.total,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
    
    # 2. 清理注入的 _magic_context 标记消息
    # （移除本插件注入的临时内容，避免污染对话历史）
    # 注意: 这需要在 conversation_manager 保存前执行
    # 由于 on_llm_response 在 agent 完成后触发，注入的内容可能已保存
    # 更好的方案: 使用 TextPart.mark_as_temp() 确保不保存

@filter.after_message_sent(priority=50)
async def magic_context_archive(self, event: AstrMessageEvent):
    """阶段 6 补充: 最终归档"""
    session_id = event.unified_msg_origin
    
    # 清理会话标签（下次请求会重新分配）
    # 保留 compartments 和 facts（跨轮次持久化）
    await self.tags_db.clear_session_tags(session_id)
    
    # 检查是否需要启动 Historian Agent（异步）
    conv_mgr = self.context.conversation_manager
    cid = await conv_mgr.get_curr_conversation_id(session_id)
    if cid:
        conv = await conv_mgr.get_conversation(session_id, cid)
        if conv:
            history = json.loads(conv.history)
            history_tokens = self._estimate_tokens(history)
            if history_tokens > self.config.historian_threshold:
                asyncio.create_task(self._run_historian_async(session_id))
```

### 注意事项
1. 后处理在 `@on_llm_response` 中执行（agent 完成后）
2. `@after_message_sent` 在所有输出处理完成后触发，适合最终清理
3. 不需要重建消息内容（AstrBot 无需在消息中显示 drop 标记）
4. tags 表在每轮后清除，compartments 和 facts 持久化
5. 标记 `_magic_context: True` 的消息在 conversation_manager 写入数据库前应清理
