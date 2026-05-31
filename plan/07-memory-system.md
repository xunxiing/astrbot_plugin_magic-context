# 阶段 7: 长期记忆系统 (Memory System)

## 目标
实现跨会话的长期记忆存储、检索和注入，支持语义搜索和全文搜索。

## 需要照搬的文件

### 7.1 types.ts
**路径**: `packages/plugin/src/features/magic-context/memory/types.ts`

**需要照搬的类型定义**:
- `MemoryCategory` - 记忆分类枚举
  - ARCHITECTURE_DECISIONS, CONSTRAINTS, CONFIG_DEFAULTS
  - NAMING, USER_PREFERENCES, USER_DIRECTIVES
  - ENVIRONMENT, WORKFLOW_RULES, KNOWN_ISSUES
- `MemoryStatus` - 记忆状态: active | permanent | archived
- `VerificationStatus` - 验证状态: unverified | verified | stale | flagged
- `MemorySourceType` - 来源类型: historian | agent | dreamer | user
- `Memory` 接口 - 完整记忆对象
- `MemoryInput` 接口 - 创建记忆输入

### 7.2 storage-memory.ts
**路径**: `packages/plugin/src/features/magic-context/memory/storage-memory.ts`

**需要照搬的函数/类**:
- `insertMemory(db, input)` - 插入记忆
  - 计算 normalized_hash
  - 设置默认状态 (active, unverified)
  - 返回插入的记忆对象
- `getMemoryByHash(db, projectPath, category, normalizedHash)` - 按哈希查找
  - 用于快速去重
- `getMemoriesByProject(db, projectPath, statuses)` - 按项目获取记忆
  - 默认获取 active 和 permanent
  - 过滤过期记忆
- `getMemoryById(db, id)` - 按 ID 获取
- `updateMemorySeenCount(db, id)` - 增加看到次数
- `updateMemoryRetrievalCount(db, id)` - 增加检索次数
- `updateMemoryStatus(db, id, status)` - 更新状态
- `updateMemoryVerification(db, id, verificationStatus)` - 更新验证状态
- `updateMemoryContent(db, id, content, normalizedHash)` - 更新内容
- `supersededMemory(db, id, supersededById)` - 标记被替代
- `mergeMemoryStats(db, id, seenCount, retrievalCount, mergedFrom, status)` - 合并统计
- `archiveMemory(db, id, reason)` - 归档记忆
- `deleteMemory(db, id)` - 删除记忆
- `getMemoryCount(db, projectPath?)` - 获取记忆数量
- `getMemoryCountsByStatus(db, projectPath)` - 按状态统计

### 7.3 storage-memory-embeddings.ts
**路径**: `packages/plugin/src/features/magic-context/memory/storage-memory-embeddings.ts`

**需要照搬的函数/类**:
- `saveEmbedding(db, memoryId, embedding, modelId)` - 保存嵌入
  - 使用 Buffer.from 转换 Float32Array
  - UPSERT 语义
- `loadAllEmbeddings(db, projectPath)` - 加载所有嵌入
  - 返回 Map<memoryId, Float32Array>
- `deleteEmbedding(db, memoryId)` - 删除嵌入
- `getStoredModelId(db, projectPath)` - 获取存储的模型 ID
- `clearEmbeddingsForProject(db, projectPath)` - 清除项目嵌入

### 7.4 storage-memory-fts.ts
**路径**: `packages/plugin/src/features/magic-context/memory/storage-memory-fts.ts`

**需要照搬的函数/类**:
- `searchMemoriesFTS(db, projectPath, query, limit)` - 全文搜索
  - 使用 FTS5 MATCH 语法
  - BM25 排序
  - 过滤过期记忆
- `sanitizeFtsQuery(query)` - 清理查询
  - 转义特殊字符
  - 包装为引号 token

### 7.5 embedding.ts
**路径**: `packages/plugin/src/features/magic-context/memory/embedding.ts`

**需要照搬的函数/类**:
- `initializeEmbedding(config)` - 初始化嵌入配置
- `isEmbeddingEnabled()` - 检查是否启用
- `ensureEmbeddingModel()` - 确保模型就绪
- `embedText(text, signal?)` - 嵌入单条文本
- `embedBatch(texts, signal?)` - 批量嵌入
- `embedUnembeddedMemories(db, projectPath, config, batchSize)` - 嵌入未嵌入的记忆
- `embedAllUnembeddedMemories(db, config, batchSize)` - 全量嵌入
  - 按项目分组
  - 优先处理最近更新的项目
  - 墙钟时间限制 (10分钟)
  - 连续空批次停止 (3次)
- `getEmbeddingModelId()` - 获取模型 ID
- `disposeEmbeddingModel()` - 释放模型

### 7.6 embedding-local.ts
**路径**: `packages/plugin/src/features/magic-context/memory/embedding-local.ts`

**需要照搬的函数/类**:
- `LocalEmbeddingProvider` 类
  - `initialize()` - 加载本地模型
  - `embed(text)` - 单条嵌入
  - `embedBatch(texts)` - 批量嵌入
  - `dispose()` - 释放资源
  - `modelId` - 模型标识

### 7.7 embedding-openai.ts
**路径**: `packages/plugin/src/features/magic-context/memory/embedding-openai.ts`

**需要照搬的函数/类**:
- `OpenAICompatibleEmbeddingProvider` 类
  - `initialize()` - 检查 API 可用性
  - `embed(text)` - 调用 API
  - `embedBatch(texts)` - 批量调用
  - `dispose()` - 清理

### 7.8 cosine-similarity.ts
**路径**: `packages/plugin/src/features/magic-context/memory/cosine-similarity.ts`

**需要照搬的函数**:
- `cosineSimilarity(a, b)` - 计算余弦相似度
  - 点积 / (范数a * 范数b)
  - 处理零向量

### 7.9 normalize-hash.ts
**路径**: `packages/plugin/src/features/magic-context/memory/normalize-hash.ts`

**需要照搬的函数**:
- `computeNormalizedHash(content)` - 计算规范化哈希
  - 小写化
  - 去除多余空格
  - 标准化标点

### 7.10 promotion.ts
**路径**: `packages/plugin/src/features/magic-context/memory/promotion.ts`

**需要照搬的函数/类**:
- `promoteSessionFactsToMemory(db, sessionId, projectPath, facts)` - 提升 facts 到记忆
  - 过滤可提升的分类
  - 检查去重
  - 插入记忆
  - 异步嵌入
- `embedAndStoreMemory(db, sessionId, memoryId, content)` - 嵌入并存储
  - fire-and-forget 模式

### 7.11 constants.ts
**路径**: `packages/plugin/src/features/magic-context/memory/constants.ts`

**需要照搬的常量**:
- `PROMOTABLE_CATEGORIES` - 可提升的分类列表
- `CATEGORY_PRIORITY` - 分类优先级
- `CATEGORY_DEFAULT_TTL` - 分类默认 TTL

### 7.12 project-identity.ts
**路径**: `packages/plugin/src/features/magic-context/memory/project-identity.ts`

**需要照搬的函数/类**:
- `resolveProjectIdentity(sessionId, messages)` - 解析项目标识
  - 从 git 仓库推断
  - 从消息内容推断
  - 返回 projectPath

## 数据库表

```sql
-- 记忆表
CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path TEXT NOT NULL,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  normalized_hash TEXT NOT NULL,
  source_session_id TEXT,
  source_type TEXT NOT NULL,
  seen_count INTEGER DEFAULT 1,
  retrieval_count INTEGER DEFAULT 0,
  first_seen_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  last_retrieved_at INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  expires_at INTEGER,
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  verified_at INTEGER,
  superseded_by_memory_id INTEGER,
  merged_from TEXT,
  metadata_json TEXT
);

-- 嵌入表
CREATE TABLE memory_embeddings (
  memory_id INTEGER PRIMARY KEY,
  embedding BLOB NOT NULL,
  model_id TEXT NOT NULL
);

-- FTS5 虚拟表
CREATE VIRTUAL TABLE memories_fts USING fts5(
  content,
  content_rowid=rowid,
  content=memories
);

-- 触发器保持 FTS 同步
CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER memories_fts_update AFTER UPDATE ON memories BEGIN
  UPDATE memories_fts SET content = new.content WHERE rowid = new.id;
END;

CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN
  DELETE FROM memories_fts WHERE rowid = old.id;
END;
```

## 关键设计决策

1. **项目隔离**: 通过 `projectPath` 隔离不同项目的记忆
2. **规范化哈希**: 快速去重，避免重复记忆
3. **分类系统**: 结构化存储，便于优先级排序
4. **混合搜索**: FTS5 全文 + 向量语义搜索
5. **异步嵌入**: 不阻塞主流程，后台生成
6. **自动晋升**: Historian facts 自动变为跨会话记忆
7. **TTL 机制**: 部分分类有过期时间，自动清理

## 记忆生命周期

```
创建 (insertMemory)
  │
  ├──► active ──► 被看到 (updateSeenCount)
  │     │
  │     ├──► 被检索 (updateRetrievalCount)
  │     │
  │     ├──► 被验证 (updateVerification: verified)
  │     │
  │     ├──► 过期 (archiveMemory)
  │     │
  │     └──► 被替代 (supersededMemory)
  │
  └──► permanent (永久保留)
```

## 搜索策略

```typescript
function searchMemories(db, projectPath, query, options) {
  // 1. FTS5 全文搜索
  const ftsResults = searchMemoriesFTS(db, projectPath, query, limit);
  
  // 2. 语义搜索（如果启用嵌入）
  if (isEmbeddingEnabled()) {
    const queryEmbedding = await embedText(query);
    const allEmbeddings = loadAllEmbeddings(db, projectPath);
    const semanticResults = rankBySimilarity(queryEmbedding, allEmbeddings);
  }
  
  // 3. 混合排序
  return mergeAndRank(ftsResults, semanticResults);
}
```

## 配置项

```typescript
interface MemoryConfig {
  enabled: boolean;                    // 是否启用记忆
  autoPromote: boolean;                // 是否自动晋升 facts
  injectionBudgetTokens: number;       // 注入预算
  embedding: EmbeddingConfig;          // 嵌入配置
}

interface EmbeddingConfig {
  provider: 'local' | 'openai-compatible' | 'off';
  model: string;
  endpoint?: string;
  api_key?: string;
}
```

## ⚠️ 跳过：AstrBot 已有其他插件实现长期记忆

AstrBot 已有独立的记忆插件（如 long_term_memory 等），magic-context 不需要重复实现此功能。

> **决策**: 跳过阶段 7 的实现。`ctx_memory` / `ctx_search` 工具和记忆注入由 AstrBot 现有记忆插件提供。

---

## AstrBot 实现分析（已废弃）

### 可行
✅ AstrBot 已有 `EmbeddingProvider` 抽象层（OpenAI/Ollama/NVIDIA/Gemini）。通过 `context.get_all_embedding_providers()` 获取。
✅ `StarTools.get_data_dir()` 返回 `Path`，可创建独立 SQLite 存储记忆。
✅ FTS5 在 AstrBot 中已验证可用（`document_storage.py` 已使用）。
✅ `@filter.on_llm_request()` 可注入记忆到 context。
✅ `@filter.llm_tool()` 或 `FunctionTool` dataclass 注册 `ctx_memory`/`ctx_search` 工具。

### 不可行 / 需简化
❌ AstrBot 没有项目路径概念（没有 projectPath）。
   → **简化**: 用 `event.unified_msg_origin` 中的 platform 部分或配置中的 project_id 替代。
❌ AstrBot 没有 session facts（`ctx_note`）的自动晋升机制。
   → **简化**: 手动实现 cron 任务定期扫描 facts 表，按规则晋升。

### 实现方案

```python
# memory_system.py
import aiosqlite
from pathlib import Path

class MemoryStore:
    """长期记忆系统"""
    
    def __init__(self, data_dir: Path):
        self.db_path = data_dir / "magic_context.db"
    
    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN (
                        'USER_DIRECTIVES','CONSTRAINTS','ENVIRONMENT',
                        'WORKFLOW_RULES','CONFIG_DEFAULTS','USER_PREFERENCES',
                        'ARCHITECTURE_DECISIONS','NAMING','KNOWN_ISSUES'
                    )),
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    seen_count INTEGER DEFAULT 0,
                    retrieval_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            try:
                await db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts 
                    USING fts5(content, tokenize='unicode61')
                """)
            except Exception:
                logger.warning("FTS5 not available, falling back to LIKE search")
            await db.commit()
    
    async def insert(self, project_id, category, content):
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO memories (project_id, category, content, content_hash) VALUES (?, ?, ?, ?)",
                (project_id, category, content, content_hash)
            )
            await db.commit()
            return cursor.lastrowid

# 注册 ctx_memory / ctx_search 工具
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

@dataclass
class CtxMemoryTool(FunctionTool[AstrAgentContext]):
    name = "ctx_memory"
    description = "Save a cross-session project memory"
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Memory content"},
            "category": {"type": "string", "enum": ["USER_DIRECTIVES","CONSTRAINTS","ENVIRONMENT","WORKFLOW_RULES"]}
        },
        "required": ["content", "category"]
    }
    async def call(self, context, **kwargs):
        await plugin.memory_store.insert(plugin.project_id, kwargs["category"], kwargs["content"])
        return ToolExecResult(result=f"Memory saved: {kwargs['content'][:100]}")

# 记忆注入 hook
@filter.on_llm_request(priority=40)
async def memory_inject(self, event, req):
    mems = await self.memory_store.search_fts(self.project_id, req.prompt or "", 5)
    if mems:
        from astrbot.core.agent.message import TextPart
        text = "\n".join(f"- [{m['category']}] {m['content']}" for m in mems)
        part = TextPart(text=f"<relevant_memories>\n{text}\n</relevant_memories>")
        part.mark_as_temp()
        req.extra_user_content_parts.append(part)
```

### 注意事项
1. 用 `event.unified_msg_origin` 提取 platform 或配置中的 project_id 作为项目隔离
2. AstrBot 已有 `EmbeddingProvider`，直接复用
3. 参照 `document_storage.py` 实现 FTS5 降级策略
4. 用 `req.extra_user_content_parts` + `TextPart.mark_as_temp()` 注入记忆
