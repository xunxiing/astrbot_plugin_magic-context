# 阶段 3: 内容压缩 (Content Compression)

## 目标
通过文本压缩算法和推理内容剥离，进一步减少 token 消耗。

## 需要照搬的文件

### 3.1 caveman.ts
**路径**: `packages/plugin/src/hooks/magic-context/caveman.ts`

**需要照搬的函数/类**:
- `cavemanCompress(text, level)` - 主入口函数
  - `level: 'lite' | 'full' | 'ultra'` - 压缩级别
  - 返回压缩后的文本
- `liteCompress(text)` - 轻度压缩
  - 保留前 3 个词 + "..."
  - 用于快速预览
- `fullCompress(text)` - 完全压缩
  - 简化冠词、代词、标点
  - 保留关键信息
- `ultraCompress(text)` - 极致压缩
  - 极简模式
  - 只保留最核心的词
- `stripArticles(text)` - 去除冠词
- `stripPronouns(text)` - 去除代词
- `simplifyPunctuation(text)` - 简化标点

### 3.2 strip-content.ts
**路径**: `packages/plugin/src/hooks/magic-context/strip-content.ts`

**需要照搬的函数/类**:
- `stripContent(message, config)` - 主入口函数
  - 剥离推理内容
  - 压缩思考过程
- `stripThinkingTags(text)` - 剥离 `<thinking>` 标签
  - 替换为摘要 `[thinking: ...]`
- `stripAntThinking(content)` - 压缩 `<antThinking>` 结构化推理
  - 保留 key observations
  - 压缩 reasoning chain
- `simplifyReasoning(reasoning)` - 简化 reasoning 对象
  - 保留 toolUse / toolResult / exit 关键信息
  - 压缩 intermediate steps
- `isReasoningContent(part)` - 判断是否为推理内容
- `shouldStrip(part, config)` - 判断是否应剥离

## 压缩级别对比

| 级别 | 处理方式 | 适用场景 |
|------|---------|---------|
| **lite** | 保留前 3 个词 + "..." | 快速预览、大文本摘要 |
| **full** | 简化冠词、代词、标点 | 标准压缩 |
| **ultra** | 极简模式，只保留核心词 | 深度压缩旧历史 |

## Caveman 压缩规则

### Lite 模式
```typescript
function liteCompress(text: string): string {
  const words = text.split(/\s+/);
  if (words.length <= 4) return text;
  return words.slice(0, 3).join(' ') + ' ...';
}
```

### Full 模式
```typescript
function fullCompress(text: string): string {
  return text
    .replace(/\b(the|a|an)\b/gi, '')      // 去除冠词
    .replace(/\b(I|you|we|they)\b/gi, '') // 去除代词
    .replace(/[,;]/g, ' ')                // 简化标点
    .replace(/\s+/g, ' ')                 // 规范化空格
    .trim();
}
```

### Ultra 模式
```typescript
function ultraCompress(text: string): string {
  // 只保留名词、动词、关键形容词
  // 去除所有虚词和冗余
}
```

## 推理内容剥离规则

### Thinking 标签处理
```typescript
// 原始
<thinking>
1. 首先分析需求
2. 然后设计方案
3. 最后实现代码
</thinking>

// 剥离后
[thinking: analyzed requirements, designed solution, implemented code]
```

### AntThinking 结构化处理
```typescript
// 原始
<antThinking>
  <observation>用户需要格式化功能</observation>
  <reasoning>应该使用 ruff 因为它更快</reasoning>
  <conclusion>使用 ruff 进行格式化</conclusion>
</antThinking>

// 压缩后
[thinking: use ruff for formatting (faster)]
```

### Reasoning 对象简化
```typescript
// 原始
{
  "toolUse": { "name": "read_file", "args": { "path": "src/main.py" } },
  "reasoning": [
    { "step": 1, "thought": "需要读取主文件" },
    { "step": 2, "thought": "分析文件结构" },
    { "step": 3, "thought": "确定修改位置" }
  ],
  "toolResult": { "status": "success", "content": "..." }
}

// 简化后
{
  "toolUse": { "name": "read_file", "args": { "path": "src/main.py" } },
  "toolResult": { "status": "success" }
}
```

## 配置项

```typescript
interface CompressionConfig {
  cavemanLevel: 'none' | 'lite' | 'full' | 'ultra';  // Caveman 压缩级别
  stripThinking: boolean;                             // 是否剥离 thinking 标签
  stripReasoning: boolean;                            // 是否简化 reasoning 对象
  maxThinkingLength: number;                          // thinking 内容最大长度
}
```

## AstrBot 实现分析

### 可行
✅ 完全可行。在 `@filter.on_llm_request()` hook 中修改 `req.contexts` 的内容。
✅ Caveman 压缩是纯文本操作，Python 正则即可实现。
✅ AstrBot 有 `reasoning_content` 字段（`LLMResponse.reasoning_content`），可在 `@on_llm_response` 中剥离。

### 不可行 / 需简化
❌ AstrBot 没有 `<antThinking>` 结构化标签。
   → **简化**: 只实现 `<thinking>` XML 标签剥离。
❌ reasoning 对象在 AstrBot 中对应 `ThinkPart`，不在消息内容中。
   → **简化**: 在 `@filter.on_agent_begin` hook 中访问 `run_context.messages`，剥离 `ThinkPart` 为简短摘要。

### 实现方案

```python
# cave_man.py - 纯文本压缩
import re

def cave_man_compress(text: str, level: str) -> str:
    """Caveman 文本压缩"""
    if level == "lite":
        # 保留前5个词
        words = text.split()
        if len(words) <= 5:
            return text
        return " ".join(words[:5]) + " ..."
    elif level == "full":
        # 简化虚词和标点
        text = re.sub(r'\b(the|a|an)\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\b(I|you|we|they|he|she|it)\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[,;:\'"()\[\]{}]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    elif level == "ultra":
        # 极简：只保留名词动词（简单启发式）
        words = text.split()
        keywords = [w for w in words if len(w) > 3]
        return " ".join(keywords[:10])
    return text

# strip_reasoning.py - 推理内容剥离
def strip_thinking_tags(text: str) -> str:
    """剥离 <thinking> XML 标签"""
    return re.sub(r'<thinking>.*?</thinking>', '[thinking: summarized]', text, flags=re.DOTALL)

# main.py
@filter.on_llm_request(priority=70)
async def magic_context_compress(self, event: AstrMessageEvent, req: ProviderRequest):
    """阶段 3: 内容压缩 - Caveman + Reasoning 剥离"""
    config = self.config
    if config.caveman_level == "none":
        return
    
    session_id = event.unified_msg_origin
    tags = await self.tags_db.get_session_tags(session_id)
    
    for idx, ctx in enumerate(req.contexts):
        tag_info = tags[idx] if idx < len(tags) else {}
        tag_number = tag_info.get("tag_number", 0)
        
        # 只为旧消息进行压缩
        if tag_number < config.caveman_age_threshold:
            content = ctx.get("content", "")
            if isinstance(content, str):
                # 先剥离 thinking
                content = strip_thinking_tags(content)
                # 再 Caveman 压缩
                ctx["content"] = cave_man_compress(content, config.caveman_level)
```

### 注意事项
1. Caveman 压缩仅用于**旧历史**（超过 caveman_age_threshold），当前对话不压缩
2. Python 的正则 `re.DOTALL` 对应 JS 的 `/s` flag
3. AstrBot 有 `LLMResponse.reasoning_content` 字段，可在 response 侧剥离
4. 剥离后需保留一定可读性，否则 Historian Agent 无法理解
