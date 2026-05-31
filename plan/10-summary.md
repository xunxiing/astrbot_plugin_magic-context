# 复刻总结

## 粒度问题已解决

### Part 级标签化 ✅
`@filter.on_agent_begin` hook 提供 `run_context.messages: list[Message]`（Pydantic）。
`msg.content` 可以是 `list[ContentPart]`，每个 part 有 `.type` 字段区分 text/think/image。
→ 可以按 **part 级别**分配标签，与 OpenCode 的 `parts` 数组等价。

```python
if isinstance(msg.content, list):
    for part in msg.content:
        if isinstance(part, TextPart):    # type: text
            ...
        elif isinstance(part, ThinkPart):  # type: think
            ...
```

### Tool 精确 drop ✅
`msg.tool_calls[].id` 通过 `role="tool"` 消息的 `tool_call_id` 精确匹配。
→ **不需要** OpenCode 的复合键 (`ownerMsgId\x00callId`)，ID 本身已全局唯一。

```python
# Assistant 调用工具
assistant_msg = Message(role="assistant", tool_calls=[
    ToolCall(id="call_123", function=FunctionBody(name="read", arguments="{}"))
])
# Tool 返回结果 — 精确匹配
tool_msg = Message(role="tool", tool_call_id="call_123", content="result")
```

## AstrBot 流程图

```
会话消息到达
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ @filter.on_agent_begin → run_context.messages: list[Message]       │
│   (Pydantic 类型，有 parts、tool_calls、tool_call_id)        │
│                                                             │
│   阶段 1: 标签化     → 按 part + tool_call_id 精确标记        │
│   阶段 2: 启发式清理  → 精确 tool drop + 去重                │
│   阶段 3: 内容压缩    → Caveman + Reasoning 剥离             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ @on_llm_request → req.contexts: list[dict]                  │
│   (OpenAI format，历史注入)                                  │
│                                                             │
│   阶段 4: Historian   → LLM 压缩生成 compartments + facts    │
│   阶段 5: 注入        → 插入 system 消息 + extra_user_parts  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
LLM 调用 & Agent 运行
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ @on_llm_response + @after_message_sent                      │
│                                                             │
│   阶段 6: 后处理    → Token 记录 + 清理 + 异步 Historian     │
└─────────────────────────────────────────────────────────────┘
```

## 文件清单

### 核心流程 (8个文件)
1. `transform.ts` - 主控流程
2. `tag-messages.ts` - 标签化
3. `heuristic-cleanup.ts` - 启发式清理
4. `caveman.ts` - 文本压缩
5. `strip-content.ts` - 推理剥离
6. `inject-compartments.ts` - 注入
7. `transform-postprocess-phase.ts` - 后处理
8. `apply-operations.ts` - 操作应用

### Historian Agent (9个文件)
9. `compartment-runner-incremental.ts` - 主入口
10. `compartment-runner-historian.ts` - LLM 调用
11. `compartment-prompt.ts` - Prompt 构建
12. `compartment-runner-validation.ts` - 验证
13. `compartment-parser.ts` - 解析
14. `compartment-runner-mapping.ts` - 映射
15. `compartment-runner-state-xml.ts` - 状态 XML
16. `read-session-chunk.ts` - 读取 chunk
17. `compartment-runner-drop-queue.ts` - 排队 drop
18. `compartment-runner-compressor.ts` - 压缩器

### 记忆系统 (12个文件) — ⚠️ 全部跳过，AstrBot 已有其他插件实现
~~19-30. 记忆系统相关文件~~

### 辅助文件 (6个文件)
31. `tagger.ts` - 标签分配器
32. `tool-drop-target.ts` - 工具丢弃目标
33. `tokenizer-calibration.ts` - Tokenizer
34. `read-session-formatting.ts` - 格式化
35. `read-session-db.ts` - 数据库读取
36. `system-prompt-hash.ts` - 哈希计算
37. `temporal-awareness.ts` - 时间感知
38. `note-nudger.ts` - 笔记提醒
39. `transform-cache-busting-signals.ts` - 缓存信号

**总计: 39 个核心文件**

## 关键数据结构

### MessageLike
```typescript
interface MessageLike {
  info: {
    id: string;
    role: 'user' | 'assistant' | 'system';
    sessionID: string;
    [key: string]: unknown;
  };
  parts: Array<{
    type: string;
    text?: string;
    [key: string]: unknown;
  }>;
}
```

### TagTarget
```typescript
interface TagTarget {
  setContent: (content: string) => boolean;
  getContent?: () => string | null;
  drop?: () => ToolDropResult;
  truncate?: () => ToolDropResult;
  message?: MessageLike;
}
```

### Compartment
```typescript
interface Compartment {
  id: number;
  sessionId: string;
  startMessage: number;
  endMessage: number;
  startMessageId: string;
  endMessageId: string;
  title: string;
  content: string;
  depth: number;
}
```

### Memory
```typescript
interface Memory {
  id: number;
  projectPath: string;
  category: MemoryCategory;
  content: string;
  normalizedHash: string;
  status: MemoryStatus;
  seenCount: number;
  retrievalCount: number;
  // ...
}
```

## 配置项汇总

```typescript
interface MagicContextConfig {
  enabled: boolean;
  historyBudgetTokens: number;
  historianChunkTokens: number;
  historianTimeoutMs: number;
  fallbackModels: string[];
  autoDropToolAge: number;
  protectedTags: number;
  dropToolStructure: boolean;
  cavemanLevel: 'none' | 'lite' | 'full' | 'ultra';
  stripThinking: boolean;
  stripReasoning: boolean;
  compressorMinCompartmentRatio: number;
  compressorMaxMergeDepth: number;
  injectionBudgetTokens: number;
  temporalAwareness: boolean;
  memory: {
    enabled: boolean;
    autoPromote: boolean;
    injectionBudgetTokens: number;
    embedding: {
      provider: 'local' | 'openai-compatible' | 'off';
      model: string;
      endpoint?: string;
      api_key?: string;
    };
  };
}
```

## AstrBot 适配总结

### 核心文件映射（AstrBot 实现只需 ~7 个文件）

| OpenCode TypeScript | AstrBot Python | 状态 |
|---|---|---|
| `transform.ts` | `main.py` (plugin entry) | ✅ 直接实现 |
| `tag-messages.ts` | `tags_db.py` | ✅ 简化（无 parts） |
| `tagger.ts` | 合并到 `tags_db.py` | ✅ SQLite 存储 |
| `heuristic-cleanup.ts` | `main.py` (hook) | ✅ 直接操作 dict |
| `caveman.ts` | `cave_man.py` | ✅ 纯文本 regex |
| `strip-content.ts` | `strip_reasoning.py` | ✅ 简化 |
| `compartment-runner-*.ts` | `historian.py` | ✅ 复用 `LLMSummaryCompressor` |
| `inject-compartments.ts` | `main.py` (hook) | ✅ 用 system 消息替代 |
| `compartment-storage.ts` | `compartment_db.py` | ✅ SQLite |
| `transform-postprocess-phase.ts` | `main.py` (hook) | ✅ 简化 |
| ~~`storage-memory.ts`~~ | ~~跳过~~ | ⚠️ AstrBot 已有其他插件 |
| ~~`storage-memory-fts.ts`~~ | ~~跳过~~ | ⚠️ AstrBot 已有其他插件 |
| ~~`embedding.ts`~~ | ~~跳过~~ | ⚠️ AstrBot 已有其他插件 |
| ~~`cosine-similarity.ts`~~ | ~~跳过~~ | ⚠️ AstrBot 已有其他插件 |

### 可以跳过的文件

| 文件 | 原因 |
|---|---|
| `tool-drop-target.ts` | AstrBot 无 parts 概念 |
| `apply-operations.ts` | 直接修改 `req.contexts` |
| `compartment-parser.ts` | 不用 XML output |
| `compartment-runner-validation.ts` | 不用复杂验证 |
| `compartment-runner-mapping.ts` | 不用 message id 映射 |
| `compartment-runner-state-xml.ts` | 用纯文本替代 |
| `read-session-chunk.ts` | 从 `conversation_manager` 读取 |
| `read-session-formatting.ts` | 简化格式化 |
| `read-session-db.ts` | 用 `conversation_manager` |
| `system-prompt-hash.ts` | 不需要缓存机制 |
| `transform-cache-busting-signals.ts` | 不需要 |
| `tokenizer-calibration.ts` | 简单字符估算 |
| `note-nudger.ts` | 可选 |
| `temporal-awareness.ts` | 可选 |
| 所有 `storage-memory*.ts` | AstrBot 已有记忆插件 |
| `embedding*.ts` | 复用现有记忆插件 |
| `cosine-similarity.ts` | 复用现有记忆插件 |
| `promotion.ts` | 复用现有记忆插件 |
| `constants.ts` | 复用现有记忆插件 |
| `project-identity.ts` | 复用现有记忆插件 |

### 实现优先级（AstrBot 适配版）

### P0（核心功能，1周）
1. ✅ `main.py` - 插件入口 + `@filter.on_llm_request()` hooks
2. ✅ `tags_db.py` - SQLite 标签存储
3. ✅ Phase 2: 启发式清理（在 hook 中直接删除 `req.contexts` 条目）
4. ✅ Phase 5+6: 注入 + 后处理（system 消息插入 + 清理）

### P1（重要功能，1-2周）
5. ✅ `historian.py` - 复用 `LLMSummaryCompressor` 或 `ctx.llm_generate()`
6. ✅ `cave_man.py` - 纯文本压缩
7. ✅ `compartment_db.py` - Compartment 持久化
8. ~~`memory_store.py`~~ - **跳过**（已有其他插件实现）

### P2（增强功能，可选）
9. ~~`ctx_memory` / `ctx_search` 工具注册~~ → **跳过**，AstrBot 已有其他插件实现
10. ~~向量嵌入 + 语义搜索~~ → **跳过**，复用现有记忆插件
11. ✅ Cron 定时 Historian Agent

### 推荐的文件结构
```
astrbot_plugin_magic_context/
├── main.py               # 插件入口 + 所有 hook
├── metadata.yaml          # 插件元数据
├── _conf_schema.json      # 配置模式
├── requirements.txt       # aiosqlite
├── tags_db.py             # 标签数据库
├── historian.py           # Historian Agent
├── cave_man.py            # 文本压缩
├── strip_reasoning.py     # 推理剥离
├── compartment_db.py      # Compartment + Facts 存储
└── README.md
```
> 注意：`memory_store.py` 省略（已有其他插件实现长期记忆）

### 不需要的文件（相对于 OpenCode 原版）
- `tagger.ts` → 合并到 `tags_db.py`
- `tool-drop-target.ts` → 不需要（AstrBot 无 parts）
- `apply-operations.ts` → 不需要（直接修改 `req.contexts`）
- `compartment-parser.ts` → 不需要（不用 XML）
- `compartment-runner-validation.ts` → 简化（不需要复杂验证）
- `tokenizer-calibration.ts` → 简化（字符估算）
- 所有 `read-session-*.ts` → 用 `conversation_manager` 替代
- 所有 `embedding-*.ts` → 复用 AstrBot 的 `EmbeddingProvider`
