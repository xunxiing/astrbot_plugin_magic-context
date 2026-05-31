# 阶段 4: Historian Agent (Compartment Phase)

## 目标
通过 LLM 智能压缩历史对话，生成结构化的 compartments 和 facts，实现长期上下文的语义压缩。

## 需要照搬的文件

### 4.1 compartment-runner-incremental.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-incremental.ts`

**需要照搬的函数/类**:
- `runCompartmentAgent(deps)` - 主入口函数
  - 步骤 1: 读取现有 compartments 和 facts
  - 步骤 2: 验证现有 compartments 有效性
  - 步骤 3: 读取新消息 chunk
  - 步骤 4: 构建 prompt
  - 步骤 5: 调用 Historian Agent
  - 步骤 6: 验证输出
  - 步骤 7: 追加新 compartments 和替换 facts
  - 步骤 8: 为 compartment 化消息排队 drop
  - 步骤 9: 运行压缩器 pass（如果需要）
- `getActiveCompartmentRun(sessionId)` - 获取活跃的 compartment 运行
- `setActiveCompartmentRun(sessionId, promise)` - 设置活跃的 compartment 运行
- `clearActiveCompartmentRun(sessionId)` - 清除活跃的 compartment 运行

### 4.2 compartment-runner-historian.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-historian.ts`

**需要照搬的函数/类**:
- `runValidatedHistorianPass(args)` - 带验证的 Historian 运行
  - 首次运行 → 验证 → 成功/修复/失败
  - 修复运行（如果首次失败）
  - fallback 模型（如果修复也失败）
- `runHistorianPrompt(args)` - 执行 Historian prompt
  - 创建子会话
  - 发送 prompt（synthetic: true）
  - 获取助手回复
  - 保存响应到调试文件
- `runEditorPassOrFallback(args)` - 两阶段编辑
- `runFallbackHistorianPass(args)` - Fallback 模型运行
- `MAX_RETRIES` - 最大重试次数
- `RETRY_BACKOFF_MS` - 重试退避时间

### 4.3 compartment-prompt.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-prompt.ts`

**需要照搬的函数/类**:
- `buildCompartmentAgentPrompt(existingState, newChunk, options)` - 构建 Historian prompt
  - 系统指令（角色、任务、约束）
  - 现有状态 XML
  - 新消息 chunk
  - 输出格式要求
- `buildHistorianRepairPrompt(originalPrompt, failedOutput, error)` - 构建修复 prompt
  - 原始 prompt
  - 失败的输出
  - 错误信息

### 4.4 compartment-runner-validation.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-validation.ts`

**需要照搬的函数/类**:
- `validateHistorianOutput(text, sessionId, chunk, priorCompartments, sequenceOffset)` - 验证输出
  - 解析 XML
  - 修复 gaps
  - 映射到 chunk lines
  - 验证结构
- `validateStoredCompartments(priorCompartments)` - 验证已存储的 compartments
  - 检查连续性
  - 检查无重叠
- `validateParsedCompartments(compartments, startIndex, endIndex, unprocessedFrom)` - 验证解析结果
  - 范围有效性
  - 连续性
- `healCompartmentGaps(compartments, toolOnlyRanges)` - 修复 gaps
  - 工具-only gap 自动修复
  - 小 gap (<=15) 安全修复

### 4.5 compartment-parser.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-parser.ts`

**需要照搬的函数/类**:
- `parseCompartmentOutput(text)` - 解析 Historian XML 输出
  - 提取 compartments
  - 提取 facts
  - 提取 unprocessed_from
- `parseCompartmentTag(text)` - 解析单个 compartment 标签
- `parseFacts(text)` - 解析 facts 部分

### 4.6 compartment-runner-mapping.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-mapping.ts`

**需要照搬的函数/类**:
- `mapParsedCompartmentsToChunk(parsedCompartments, chunk, sequenceOffset)` - 映射到原始消息
  - 将 compartment 的序号映射到 messageId
  - 验证映射有效性

### 4.7 compartment-runner-state-xml.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-state-xml.ts`

**需要照搬的函数/类**:
- `buildExistingStateXml(priorCompartments, priorFacts, memoryBlock)` - 构建现有状态 XML
  - compartments XML
  - facts XML
  - 记忆块 XML

### 4.8 read-session-chunk.ts
**路径**: `packages/plugin/src/hooks/magic-context/read-session-chunk.ts`

**需要照搬的函数/类**:
- `readSessionChunk(sessionId, maxTokens, offset, protectedTailStart)` - 读取消息 chunk
  - 读取原始消息
  - 按 token 预算截断
  - 格式化消息
  - 识别 tool-only ranges
- `formatMessage(message)` - 格式化单条消息
- `mergeConsecutiveMessages(messages)` - 合并连续同角色消息
- `identifyToolOnlyRanges(lines)` - 识别纯工具区间

### 4.9 compartment-runner-drop-queue.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-drop-queue.ts`

**需要照搬的函数/类**:
- `queueDropsForCompartmentalizedMessages(db, sessionId, upToMessageIndex)` - 排队 drop
  - 获取 compartment 范围内的消息
  - 为对应标签排队 drop 操作
  - 工具标签使用复合键匹配

### 4.10 compartment-runner-compressor.ts
**路径**: `packages/plugin/src/hooks/magic-context/compartment-runner-compressor.ts`

**需要照搬的函数/类**:
- `runCompressionPassIfNeeded(deps)` - 压缩器入口
  - 估算当前 token 数
  - 选择压缩带
  - 运行 LLM 压缩
  - 应用 caveman 后处理
- `selectCompressionBand(scored, options)` - 选择压缩带
  - 深度优先，同深度最旧优先
  - 保护最新的 compartments
- `COMPRESSOR_MERGE_RATIO_BY_DEPTH` - 深度合并比例
- `cavemanLevelForDepth(depth)` - 深度对应的 caveman 级别

## 关键设计决策

1. **增量压缩**: 只压缩新消息，保留已有的 compartments
2. **子会话隔离**: Historian 在子会话中运行，不影响主会话
3. **验证修复**: 首次失败 → 修复 prompt → fallback 模型
4. **压缩梯度**: 旧历史深度压缩，新历史轻度压缩
5. **工具-only gap**: 允许任意大小的 gap（纯工具调用无叙事文本）

## 输出格式

```xml
<compartment start="1" end="50" title="Initial setup">
  压缩后的内容...
</compartment>

<compartment start="51" end="100" title="API implementation">
  压缩后的内容...
</compartment>

<facts>
  <fact category="WORKFLOW_RULES">Always use ruff for formatting</fact>
  <fact category="CONSTRAINTS">Dashboard needs RGBA PNGs</fact>
</facts>

<unprocessed_from>101</unprocessed_from>
```

## 配置项

```typescript
interface HistorianConfig {
  historianChunkTokens: number;      // Historian 处理的 chunk 大小
  historianTimeoutMs: number;        // Historian 超时时间
  historyBudgetTokens: number;       // 历史预算
  compressorMinCompartmentRatio: number; // 压缩器最小 compartment 比例
  compressorMaxMergeDepth: number;   // 压缩器最大合并深度
  fallbackModels: string[];          // Fallback 模型列表
}
```

## 压缩带选择策略

```
eligible scope = [0, length - graceCompartments)  // 保护最新的 10 个

1. 找到 eligible scope 中的最小深度层
2. 锚定该层最旧的 compartment
3. 向前扩展同深度 compartment
4. 要求 runLen >= 2
```

## 深度梯度设计

| 深度 | 合并比例 | Caveman 级别 | 适用场景 |
|------|---------|-------------|---------|
| 1 | 1.33x | lite | 较新的历史 |
| 2 | 1.5x | lite | 中等历史 |
| 3 | 2.0x | full | 较旧的历史 |
| 4 | 2.5x | full | 旧历史 |
| 5 | title-only | ultra | 最古老的 history |

## AstrBot 实现分析

### 可行
✅ AstrBot 有现成的 `ContextManager` + `ContextCompressor` + `LLMSummaryCompressor`，可直接用！
✅ `ctx.llm_generate(chat_provider_id=..., prompt=..., system_prompt=..., contexts=...)` 调用 LLM 生成摘要。
✅ `ctx.tool_loop_agent()` 可运行带工具的多轮 Agent。
✅ `asyncio.create_task()` + cron (`context.cron_manager`) 支持后台异步执行。
✅ `conversation_manager.get_conversation(umo, cid)` 可读取完整对话历史。
✅ AstrBot 已有 `_checkpoint` 消息机制，在发送 LLM 前自动剥离。

### 不可行 / 需简化
❌ AstrBot 没有真正的子会话（sub-session）概念。
   → **简化**: 直接调用 `ctx.llm_generate()` 传入自定义 system_prompt + 历史，不创建子会话。
❌ 没有 OpenCode 的 `MessageLike.parts` 和 `ToolMutationBatch`。
   → **简化**: 直接操作 `list[dict]`，以消息为粒度标记哪些需要 drop。
❌ 不需要 `<compartment>` XML 格式和复杂的 compartment-parser。
   → **简化**: 使用纯文本 Markdown/JSON 格式，让 LLM 输出结构化摘要。

### 实现方案 (利用 AstrBot 现有基础设施)

```python
# 方案 A: 复用 AstrBot 的 ContextCompressor（推荐）

class MagicContextCompressor:
    """实现 ContextCompressor 协议，接入 AstrBot 的压缩 pipeline"""
    
    def should_compress(self, messages, current_tokens, max_tokens):
        return current_tokens > max_tokens * 0.82  # AstrBot 默认阈值
    
    async def __call__(self, messages):
        """核心压缩逻辑"""
        # 复用 AstrBot 的 LLMSummaryCompressor 逻辑：
        # 1. 分离 system 消息、旧消息、最新消息
        # 2. 将旧消息发送给 LLM 生成摘要
        # 3. 返回 [system] + [摘要] + [最新消息]
        
        system_msgs = [m for m in messages if m.role == "system"]
        recent = messages[-self.keep_recent:]
        old = messages[len(system_msgs):-self.keep_recent]
        
        if not old:
            return messages
        
        # 格式化旧消息
        formatted = self._format_for_historian(old)
        
        # 调用 LLM 生成摘要
        provider_id = self._get_provider_id()
        resp = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=f"Summarize:\n\n{formatted}",
            system_prompt=(
                "You are a conversation historian. Analyze the conversation and produce:\n"
                "1. A concise summary of key topics and decisions\n"
                "2. A list of facts (constraints, preferences, workflow rules learned)\n"
                "Output as JSON: {\"summary\": \"...\", \"facts\": [...]}"
            ),
        )
        
        summary = self._parse_historian_output(resp.completion_text)
        
        # 构建新的消息列表
        from astrbot.core.agent.message import Message
        new_messages = system_msgs.copy()
        new_messages.append(Message(
            role="user",
            content=f"[Conversation history summary]\n{summary['summary']}"
        ))
        new_messages.append(Message(
            role="assistant", 
            content="Acknowledged."
        ))
        new_messages.extend(recent)
        
        return new_messages

    def _format_for_historian(self, messages):
        lines = []
        for msg in messages:
            role = msg.role
            if role in ("_checkpoint",):
                continue
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"[{role}]: {content[:500]}")
        return "\n".join(lines)

# 方案 B: 后台 cron 方式 - 定期运行 Historian
async def register_historian_cron(self):
    await self.context.cron_manager.add_basic_job(
        name="magic_context_historian",
        cron_expression="*/15 * * * *",  # 每15分钟
        handler=self.run_historian_job,
        payload={},
        persistent=False,
        description="Run Historian Agent to compress conversations",
    )

async def run_historian_job(self, **kwargs):
    """后台任务：压缩所有活跃会话"""
    # 1. 遍历活跃会话
    # 2. 对每个会话:
    #    - 读取完整对话历史 (conversation_manager.get_conversation)
    #    - 去掉 _checkpoint 消息
    #    - 调用 LLM 生成 compartments 和 facts
    #    - 存储到 compartment_db
    #    - 排队 drop 已经压缩的消息

# 方案 C: 在 on_llm_request 中同步执行（简单版）
@filter.on_llm_request(priority=50)
async def magic_context_historian(self, event: AstrMessageEvent, req: ProviderRequest):
    """阶段 4: Historian - 同步压缩（在 LLM 请求前）"""
    if len(req.contexts) < self.config.min_messages_for_historian:
        return
    
    session_id = event.unified_msg_origin
    max_tokens = self.config.historian_chunk_tokens
    history_tokens = self._estimate_tokens(req.contexts)
    
    if history_tokens < max_tokens:
        return
    
    # 分离 system、旧消息、新消息
    system_msgs = [c for c in req.contexts if c.get("role") == "system"]
    conversation = [c for c in req.contexts if c.get("role") not in ("system",)]
    
    keep_recent = self.config.llm_compress_keep_recent
    old_messages = conversation[:max(1, len(conversation) - keep_recent)]
    recent_messages = conversation[-keep_recent:] if keep_recent else []
    
    # 格式化旧消息为文本
    formatted = []
    for ctx in old_messages:
        content = ctx.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        formatted.append(f"[{ctx.get('role', '?')}]: {content[:500]}")
    
    prompt = "\n".join(formatted[-50:])  # 最多 50 条消息
    
    # 调用 LLM 生成摘要
    provider_id = await self.context.get_current_chat_provider_id(session_id)
    try:
        resp = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=f"Summarize this conversation. Cover key topics, decisions, constraints, and facts:\n\n{prompt}",
            system_prompt=(
                "You are a conversation historian. Output a JSON:\n"
                '{"summary": "<concise summary>", "facts": ["fact1", "fact2"], '
                '"topics": ["topic1"]}'
            ),
            timeout=30000,  # 30s timeout
        )
        
        summary = self._parse_json_safe(resp.completion_text)
        
        # 存储 compartments 和 facts
        await self.compartment_db.save_compartment(session_id, {
            "summary": summary.get("summary", ""),
            "topics": summary.get("topics", []),
            "tag_range": f"0-{len(old_messages)}",
            "depth": 1,
            "created_at": datetime.now().isoformat(),
        })
        for fact in summary.get("facts", []):
            await self.facts_db.upsert_fact(session_id, fact)
        
        # 替换 contexts
        new_contexts = system_msgs.copy()
        new_contexts.append({
            "role": "system",
            "content": f"[Compressed history]: {summary.get('summary', '')}"
        })
        new_contexts.extend(recent_messages)
        req.contexts = new_contexts
        
    except Exception as e:
        logger.warning(f"Historian failed, keeping original context: {e}")

def _estimate_tokens(self, contexts):
    """简化 token 估算: 1 字符 ≈ 0.5 token"""
    total_chars = sum(
        len(c.get("content", "")) if isinstance(c.get("content"), str) else 0
        for c in contexts
    )
    return int(total_chars * 0.5)
```

### 注意事项
1. **推荐方案 A**: 复用 AstrBot 的 `ContextCompressor` 协议，自动接入压缩 pipeline
2. 压缩器在 `ToolLoopAgentRunner.step()` 中每轮都会被调用
3. Historian prompt 应要求 JSON 输出（便于解析），而非 XML
4. 超时处理: `ctx.llm_generate()` 支持超时，Historically 不应阻塞主流程
5. AstrBot 已有 `LLMSummaryCompressor` 参考实现，位于 `astrbotcore/core/agent/context/compressor.py`
