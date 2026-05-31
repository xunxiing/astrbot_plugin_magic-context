# 阶段 8: 主控流程 (Main Transform)

## 目标
整合所有阶段，实现完整的上下文压缩流程。

## 需要照搬的文件

### 8.1 transform.ts
**路径**: `packages/plugin/src/hooks/magic-context/transform.ts`

**需要照搬的函数/类**:
- `createTransform(deps)` - 主入口，创建转换函数
  - 返回 `MagicContextTransform` 函数
  - 整合所有阶段的调用
- `MagicContextTransform` - 主转换函数
  - 参数: `sessionId`, `messages`, `options`
  - 返回: 转换后的消息数组
- `runTransformPass(deps)` - 执行单次转换 pass
  - 阶段 1: 标签化
  - 阶段 2: 启发式清理
  - 阶段 3: 内容压缩
  - 阶段 4: Historian Agent（异步触发）
  - 阶段 5: 注入
  - 阶段 6: 后处理
- `shouldRunHistorian(deps)` - 判断是否运行 Historian
  - 检查消息数量
  - 检查 token 预算
  - 检查是否已有进行中的 Historian
- `getSessionDirectory(sessionId)` - 获取会话目录
- `loadContextUsage(sessionId)` - 加载上下文使用情况

### 8.2 transform-cache-busting-signals.ts
**路径**: `packages/plugin/src/hooks/magic-context/transform-cache-busting-signals.ts`

**需要照搬的函数/类**:
- `isCacheBustingPass(signals)` - 判断是否为缓存刷新 pass
  - 检查信号标志
- `getCacheBustingSignals()` - 获取缓存刷新信号
- `clearCacheBustingSignals()` - 清除信号

### 8.3 tokenizer-calibration.ts
**路径**: `packages/plugin/src/hooks/magic-context/tokenizer-calibration.ts`

**需要照搬的函数/类**:
- `estimateTokens(text)` - 估算 token 数
  - 使用 Claude tokenizer
  - 或字符数/4 估算
- `calibrateTokenizer()` - 校准 tokenizer
- `getTokenizerStats()` - 获取统计信息

### 8.4 read-session-formatting.ts
**路径**: `packages/plugin/src/hooks/magic-context/read-session-formatting.ts`

**需要照搬的函数/类**:
- `formatMessageForChunk(message)` - 格式化消息为 chunk
  - 合并连续同角色消息
  - 清理系统提醒
  - 提取工具调用摘要
- `estimateMessageTokens(message)` - 估算消息 token 数
- `mergeConsecutiveSameRole(messages)` - 合并连续同角色

### 8.5 read-session-db.ts
**路径**: `packages/plugin/src/hooks/magic-context/read-session-db.ts`

**需要照搬的函数/类**:
- `getMessageTimesFromOpenCodeDb(sessionId, messageIds)` - 获取消息时间
  - 用于时间感知
- `readMessagesFromOpenCodeDb(sessionId, offset, limit)` - 读取消息

### 8.6 system-prompt-hash.ts
**路径**: `packages/plugin/src/hooks/magic-context/system-prompt-hash.ts`

**需要照搬的函数/类**:
- `computeSystemPromptHash(messages)` - 计算系统提示哈希
  - 用于缓存一致性检查
- `getActiveUserMemories()` - 获取活跃用户记忆

### 8.7 temporal-awareness.ts
**路径**: `packages/plugin/src/hooks/magic-context/temporal-awareness.ts`

**需要照搬的函数/类**:
- `formatDate(timestamp)` - 格式化日期
  - 相对时间（今天、昨天）
  - 绝对时间（2024-01-15）
- `getTemporalContext(sessionId)` - 获取时间上下文

### 8.8 note-nudger.ts
**路径**: `packages/plugin/src/hooks/magic-context/note-nudger.ts`

**需要照搬的函数/类**:
- `shouldNudgeNotes(sessionId)` - 是否应该提醒笔记
  - 基于消息数量
  - 基于时间间隔
- `getNoteNudgeMessage()` - 获取提醒消息

## 主控流程

```typescript
async function MagicContextTransform(sessionId, messages, options) {
  // 1. 初始化检查
  if (!isEnabled()) return messages;
  
  // 2. 获取配置
  const config = getConfig(sessionId);
  
  // 3. 获取数据库连接
  const db = getDatabase(sessionId);
  
  // 4. 判断是否为缓存刷新 pass
  const isCacheBusting = isCacheBustingPass(options.signals);
  
  // 5. 阶段 1: 标签化
  const targets = tagMessages(sessionId, db, messages);
  
  // 6. 阶段 2: 启发式清理
  applyHeuristicCleanup(sessionId, db, targets, config);
  
  // 7. 阶段 3: 内容压缩
  applyContentCompression(messages, config);
  
  // 8. 阶段 4: Historian Agent（异步）
  if (shouldRunHistorian({ sessionId, messages, config })) {
    runCompartmentAgent({
      sessionId,
      db,
      messages,
      config,
      // ...
    }).catch(error => {
      logError('Historian failed', error);
    });
  }
  
  // 9. 阶段 5: 注入
  const injection = prepareCompartmentInjection(
    db, sessionId, messages, isCacheBusting,
    projectPath, config.injectionBudgetTokens
  );
  
  if (injection) {
    renderCompartmentInjection(sessionId, messages, injection);
  }
  
  // 10. 阶段 6: 后处理
  runPostprocessPhase(sessionId, db, messages, config);
  
  // 11. 记录统计
  recordMetrics(sessionId, messages, injection);
  
  return messages;
}
```

## 配置系统

### 配置文件位置
```
项目级: <project>/.opencode/magic-context.jsonc
用户级: ~/.config/opencode/magic-context.jsonc
```

### 完整配置项
```typescript
interface MagicContextConfig {
  // 启用开关
  enabled: boolean;
  
  // 历史预算
  historyBudgetTokens: number;
  
  // Historian 配置
  historianChunkTokens: number;
  historianTimeoutMs: number;
  fallbackModels: string[];
  
  // 启发式清理
  autoDropToolAge: number;
  protectedTags: number;
  dropToolStructure: boolean;
  
  // 内容压缩
  cavemanLevel: 'none' | 'lite' | 'full' | 'ultra';
  stripThinking: boolean;
  stripReasoning: boolean;
  
  // 压缩器
  compressorMinCompartmentRatio: number;
  compressorMaxMergeDepth: number;
  
  // 注入
  injectionBudgetTokens: number;
  temporalAwareness: boolean;
  
  // 记忆
  memory: {
    enabled: boolean;
    autoPromote: boolean;
    injectionBudgetTokens: number;
    embedding: EmbeddingConfig;
  };
  
  // 调试
  debug: {
    dumpPrompts: boolean;
    dumpResponses: boolean;
    logLevel: 'error' | 'warn' | 'info' | 'debug';
  };
}
```

## 事件监听

```typescript
// 会话创建
onSessionCreate(sessionId => {
  initializeDatabase(sessionId);
});

// 消息发送前
onBeforeSendMessage((sessionId, messages) => {
  return MagicContextTransform(sessionId, messages);
});

// 会话结束
onSessionEnd(sessionId => {
  clearInjectionCache(sessionId);
  clearTaggerState(sessionId);
});

// 配置变更
onConfigChange(() => {
  clearAllCaches();
});
```

## 性能优化

1. **缓存策略**:
   - 注入缓存: 100 个会话 LRU
   - 记忆块缓存: 每会话持久化到 SQLite
   - Tagger 状态: 每会话内存缓存

2. **异步处理**:
   - Historian Agent 异步运行
   - 嵌入生成后台执行
   - 不阻塞用户交互

3. **批量操作**:
   - 工具变更批量提交
   - 嵌入批量生成
   - 数据库事务

## 错误处理

```typescript
// Historian 失败
if (historianError) {
  // 1. 记录错误
  logError('Historian failed', historianError);
  
  // 2. 设置失败状态
  setHistorianFailureState(db, sessionId, error);
  
  // 3. 降级处理
  // - 使用现有 compartments
  // - 跳过本次压缩
  
  // 4. 下次重试
  // - 指数退避
  // - 最多重试 3 次
}

// 嵌入失败
if (embeddingError) {
  // 1. 记录错误
  logError('Embedding failed', embeddingError);
  
  // 2. 禁用嵌入
  // - 回退到 FTS5 搜索
  // - 标记嵌入为不可用
}
```

## AstrBot 实现分析

### 总体架构
AstrBot 有**两个**关键 hook 可用于上下文处理：
- `@on_agent_begin` → `run_context.messages: list[Message]`（Pydantic 类型，有 parts/tool_calls）— 用于**阶段 1-3**
- `@on_llm_request` → `req.contexts: list[dict]`（OpenAI dict）— 用于**阶段 4-5**

### AstrBot 主控流程

```python
# main.py - 主插件入口
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest, LLMResponse

class MagicContextPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = StarTools.get_data_dir()
        self.tags_db = TagsDatabase(self.data_dir)
        self.compartment_db = CompartmentDatabase(self.data_dir)
        self.facts_db = FactsDatabase(self.data_dir)
        self.config = self._load_config()

    # ═══════ 阶段 1-3: 使用 @filter.on_agent_begin（list[Message]）═══════
    # 原因: Message 是 Pydantic 对象，有 content parts、tool_calls、tool_call_id
    @filter.on_agent_begin()
    async def phase_1_tag(self, event, run_context):
        """标签化 — 按 part + tool_call_id 精确标记"""
        ...

    @filter.on_agent_begin()
    async def phase_2_cleanup(self, event, run_context):
        """启发式清理 — 精确 tool 匹配 + 工具去重"""
        ...

    @filter.on_agent_begin()
    async def phase_3_compress(self, event, run_context):
        """内容压缩 — Caveman + Reasoning 剥离"""
        ...

    # ═══════ 阶段 4-5: 使用 @on_llm_request（list[dict]）═══════
    @filter.on_llm_request(priority=50)
    async def phase_4_historian(self, event, req):
        """Historian Agent — 在 dict 层面操作更方便"""
        ...

    @filter.on_llm_request(priority=40)
    async def phase_5_inject(self, event, req):
        """注入 Compartments + Facts 到 system 消息"""
        ...

    # ═══════ 阶段 6: 后处理 ═══════
    @filter.on_llm_response(priority=90)
    async def phase_6_postprocess(self, event, response):
        """后处理 — 记录 token，清理临时内容"""
        ...

    @filter.after_message_sent(priority=50)
    async def phase_6_archive(self, event):
        """最终归档 — 清理会话标签，触发异步 Historian"""
        ...
```

### 关键区别：AstrBot vs OpenCode

| 特性 | OpenCode | AstrBot |
|------|----------|---------|
| 拦截点 | `onBeforeSendMessage` | `@on_agent_begin` + `@on_llm_request` |
| 消息格式 | `MessageLike` (info + parts) | `Message` (Pydantic, parts + tool_calls + tool_call_id) |
| 子会话 | 支持 | 不支持（用 `ctx.llm_generate()` 替代） |
| Part 级别访问 | ✅ parts 数组 | ✅ `msg.content` as `list[ContentPart]` |
| Tool 精确匹配 | 复合键 `ownerMsgId\x00callId` | `tool_call_id` 直接匹配（更简单） |
| Compartment | XML 格式 + parser | 纯文本/JSON + markdown |
| §N§ 标签 | 注入到消息内容 | 仅存储在 DB |
| Drop queue | `pending_ops` 延迟执行 | 直接修改 `run_context.messages` |
| 内存缓存 | LRU `BoundedSessionMap` | 可用 `functools.lru_cache` |
| 项目隔离 | `projectPath` | `event.unified_msg_origin` + config project_id |

### 注意事项
1. 所有 `on_llm_request` hook 按 priority 顺序执行，前一个的修改对后一个可见
2. `req.contexts` 是 `list[dict]`（OpenAI format），直接操作即可
3. 不需要 transform.ts 的复杂缓存机制（AstrBot 已经内置对话缓存）
4. 配置用 `_conf_schema.json`，不用 JSONC 文件
