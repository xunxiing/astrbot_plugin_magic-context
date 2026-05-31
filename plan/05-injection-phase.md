# 阶段 5: 注入阶段 (Injection Phase)

## 目标
将 compartments、facts 和项目记忆注入到消息流中，为 LLM 提供压缩后的上下文。

## 需要照搬的文件

### 5.1 inject-compartments.ts
**路径**: `packages/plugin/src/hooks/magic-context/inject-compartments.ts`

**需要照搬的函数/类**:
- `prepareCompartmentInjection(db, sessionId, messages, isCacheBusting, projectPath, injectionBudgetTokens, temporalAwareness)` - 准备注入
  - 检查缓存
  - 获取 compartments + facts
  - 获取项目记忆
  - 按预算裁剪记忆
  - 构建注入块
  - 裁剪已覆盖的消息
- `renderCompartmentInjection(sessionId, messages, prepared)` - 渲染注入
  - 构建 `<session-history>` 块
  - 插入到 message[0]
  - 记录日志
- `renderMemoryBlock(memories)` - 渲染记忆块
  - 按分类分组
  - 按优先级排序
  - 生成 XML
- `trimMemoriesToBudget(sessionId, memories, budgetTokens)` - 按预算裁剪记忆
  - 优先级排序
  - Token 预算控制
- `utilityTier(memory)` - 计算记忆效用层级
  - Tier 0: 被检索过
  - Tier 1: 包含约束关键词
  - Tier 2: 其他
- `getVisibleMemoryIds(db, sessionId)` - 获取已显示的记忆 ID
  - 用于 ctx_search 过滤
- `clearInjectionCache(sessionId)` - 清除注入缓存

### 5.2 compartment-storage.ts
**路径**: `packages/plugin/src/features/magic-context/compartment-storage.ts`

**需要照搬的函数/类**:
- `getCompartments(db, sessionId)` - 获取 compartments
- `getSessionFacts(db, sessionId)` - 获取会话 facts
- `appendCompartments(db, sessionId, compartments)` - 追加 compartments
- `replaceSessionFacts(db, sessionId, facts)` - 替换 facts
- `replaceAllCompartmentState(db, sessionId, compartments, facts)` - 替换所有状态
- `buildCompartmentBlock(compartments, facts, memoryBlock, dateRanges)` - 构建 compartment 块
- `clearCompartmentState(db, sessionId)` - 清除 compartment 状态

## 关键设计决策

1. **缓存机制**: 
   - 注入缓存避免重复构建
   - 记忆块缓存避免背景变化导致缓存失效
   - 缓存在 historian/compressor/recomp 写入后失效

2. **预算控制**:
   - `injectionBudgetTokens` 控制记忆注入大小
   - 按优先级排序，先注入高优先级记忆
   - 使用真实 tokenizer 估算 token 数

3. **消息裁剪**:
   - 找到最后一个 compartment 覆盖的消息
   - 裁剪该消息之前的所有消息
   - 保留未覆盖的消息供 LLM 直接处理

4. **子会话隔离**:
   - 子会话不注入记忆（避免递归）
   - 通过 `isSubagent` 标记判断

## 注入位置

```typescript
// 找到第一个未覆盖的消息
const cutoffIndex = messages.findIndex(m => m.info.id === lastEndMessageId);

// 裁剪已覆盖的消息
messages.splice(0, cutoffIndex + 1);

// 在 message[0] 前注入 <session-history>
const historyBlock = `<session-history>\n${block}\n</session-history>`;
```

## 记忆优先级排序

```typescript
// 1. 永久记忆优先
if (a.status === "permanent" && b.status !== "permanent") return -1;

// 2. 效用层级（被检索过 > 约束关键词 > 其他）
const tierDiff = utilityTier(a) - utilityTier(b);

// 3. 看到次数降序
const seenDiff = b.seenCount - a.seenCount;

// 4. 内容短的优先
const lenDiff = a.content.length - b.content.length;

// 5. ID 作为确定性平局决胜
return a.id - b.id;
```

## 记忆渲染格式

```xml
<project-memory>
  <USER_DIRECTIVES>
    - Always use English for comments
    - Use pathlib.Path instead of string paths
  </USER_DIRECTIVES>
  <CONSTRAINTS>
    - Dashboard Tauri build needs RGBA PNGs, not grayscale
  </CONSTRAINTS>
  <WORKFLOW_RULES>
    - Always use scripts/release.sh for releases
  </WORKFLOW_RULES>
</project-memory>
```

## 完整注入块格式

```xml
<session-history>
  <compartment start="1" end="50" title="Initial setup">
    压缩后的内容...
  </compartment>
  
  <compartment start="51" end="100" title="API implementation">
    压缩后的内容...
  </compartment>
  
  <WORKFLOW_RULES>
    * Always use ruff for formatting
    * Use conventional commits
  </WORKFLOW_RULES>
  
  <project-memory>
    <USER_DIRECTIVES>
      - Always use English for comments
    </USER_DIRECTIVES>
  </project-memory>
</session-history>
```

## 缓存策略

### 注入缓存
```typescript
const injectionCache = new BoundedSessionMap<InjectionCacheEntry>(100);

// 缓存命中条件
if (!isCacheBusting && cached) {
  return cached;
}

// 缓存失效时机
function clearInjectionCache(sessionId) {
  injectionCache.delete(sessionId);
}
```

### 记忆块缓存
```typescript
// 存储在 session_meta 表中
UPDATE session_meta SET 
  memory_block_cache = ?,
  memory_block_count = ?,
  memory_block_ids = ?
WHERE session_id = ?;

// 用途
// 1. 避免每轮都重新构建
// 2. ctx_search 可以过滤已显示的记忆
```

## 配置项

```typescript
interface InjectionConfig {
  injectionBudgetTokens: number;  // 记忆注入预算
  temporalAwareness: boolean;     // 是否启用时间感知
}
```

## AstrBot 实现分析

### 可行
✅ 完全可行。在 `@filter.on_llm_request()` hook 中插入消息到 `req.contexts`。
✅ 可修改 `req.system_prompt` 或 `req.extra_user_content_parts`。
✅ AstrBot 有 `TextPart.mark_as_temp()` 标记临时内容（不保存到对话历史）。

### 不可行 / 需简化
❌ AstrBot 没有 `<compartment>` XML 格式。
   → **简化**: 使用 `"role": "system"` 消息，包含结构化的纯文本摘要。
❌ 无法精确控制注入位置（在第一个 uncovered 用户消息前）。
   → **简化**: 在所有 system 消息之后、第一个用户/助手消息之前插入。

### 实现方案

```python
@filter.on_llm_request(priority=40)
async def magic_context_inject(self, event: AstrMessageEvent, req: ProviderRequest):
    """阶段 5: 注入 - 插入压缩后的 compartments 到上下文"""
    
    injection_parts = []
    
    compartments = await self.compartment_db.get_compartments(event.unified_msg_origin)
    if compartments:
        injection_parts.append("## Conversation History Summary:")
        for comp in compartments:
            depth = comp.get("depth", 1)
            start = comp.get("start_tag", "?")
            end = comp.get("end_tag", "?")
            title = comp.get("title", "")
            summary = comp.get("summary", "")
            indent = "  " * (depth - 1)
            injection_parts.append(f"{indent}### §{start}-§{end}: {title}")
            injection_parts.append(f"{indent}{summary}")
    
    facts = await self.facts_db.get_session_facts(event.unified_msg_origin)
    if facts:
        injection_parts.append("## Session Facts:")
        for fact in facts:
            injection_parts.append(f"- {fact['content']}")
    
    if not injection_parts:
        return
    
    injection_text = "\n".join(injection_parts)
    system_indices = [
        i for i, ctx in enumerate(req.contexts) 
        if ctx.get("role") == "system"
    ]
    insert_at = system_indices[-1] + 1 if system_indices else 0
    
    req.contexts.insert(insert_at, {
        "role": "system",
        "content": injection_text,
        "_magic_context": True
    })
    
    # 记忆注入使用 extra_user_content_parts（不保存到历史）
    memories = await self.memory_db.get_relevant_memories(event.unified_msg_origin)
    if memories:
        from astrbot.core.agent.message import TextPart
        memory_text = "\n".join(f"- {m['content']}" for m in memories)
        part = TextPart(
            text=f"<relevant_memories>\n{memory_text}\n</relevant_memories>"
        )
        part.mark_as_temp()
        req.extra_user_content_parts.append(part)
```

### 注意事项
1. 不要修改 `req.system_prompt` 来注入动态内容（会破坏 provider 缓存）
2. 使用 `req.extra_user_content_parts` + `TextPart.mark_as_temp()` 注入动态记忆
3. 使用 `req.contexts.insert()` 在 system 消息后插入 compartments
4. 标记 `_magic_context: True` 以便后处理阶段识别和清理
5. 子会话不注入记忆，避免递归和上下文膨胀
