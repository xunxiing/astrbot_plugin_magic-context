# 阶段 9: 数据库架构

## 目标
设计完整的数据库架构，支持所有阶段的数据持久化需求。

## 数据库文件

```
~/.config/opencode/magic-context.db
或
<project>/.opencode/magic-context.db
```

## 完整表结构

### 9.1 tags 表
```sql
CREATE TABLE tags (
  session_id TEXT NOT NULL,
  tag_number INTEGER NOT NULL,
  message_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN ('message', 'tool', 'file')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'dropped', 'compacted')),
  drop_mode TEXT CHECK(drop_mode IN ('full', 'truncated')),
  tool_owner_message_id TEXT,
  byte_size INTEGER,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
  PRIMARY KEY (session_id, tag_number)
);

CREATE INDEX idx_tags_session ON tags(session_id);
CREATE INDEX idx_tags_message ON tags(message_id);
CREATE INDEX idx_tags_status ON tags(session_id, status);
```

### 9.2 pending_ops 表
```sql
CREATE TABLE pending_ops (
  session_id TEXT NOT NULL,
  tag_id INTEGER NOT NULL,
  op TEXT NOT NULL CHECK(op IN ('drop', 'truncate')),
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
  PRIMARY KEY (session_id, tag_id)
);

CREATE INDEX idx_pending_ops_session ON pending_ops(session_id);
```

### 9.3 compartments 表
```sql
CREATE TABLE compartments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  start_message INTEGER NOT NULL,
  end_message INTEGER NOT NULL,
  start_message_id TEXT,
  end_message_id TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  depth INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
);

CREATE INDEX idx_compartments_session ON compartments(session_id);
CREATE INDEX idx_compartments_range ON compartments(session_id, start_message, end_message);
```

### 9.4 session_facts 表
```sql
CREATE TABLE session_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
);

CREATE INDEX idx_session_facts_session ON session_facts(session_id);
```

### 9.5 session_meta 表
```sql
CREATE TABLE session_meta (
  session_id TEXT PRIMARY KEY,
  project_path TEXT,
  memory_block_cache TEXT,
  memory_block_count INTEGER DEFAULT 0,
  memory_block_ids TEXT,
  last_historian_run INTEGER,
  historian_failure_count INTEGER DEFAULT 0,
  historian_failure_reason TEXT,
  emergency_recovery INTEGER DEFAULT 0,
  pending_compaction_marker TEXT,
  is_subagent INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
);
```

### 9.6 memories 表
```sql
CREATE TABLE memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path TEXT NOT NULL,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  normalized_hash TEXT NOT NULL,
  source_session_id TEXT,
  source_type TEXT NOT NULL DEFAULT 'historian',
  seen_count INTEGER NOT NULL DEFAULT 1,
  retrieval_count INTEGER NOT NULL DEFAULT 0,
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

CREATE INDEX idx_memories_project ON memories(project_path);
CREATE INDEX idx_memories_hash ON memories(project_path, category, normalized_hash);
CREATE INDEX idx_memories_status ON memories(project_path, status);
```

### 9.7 memory_embeddings 表
```sql
CREATE TABLE memory_embeddings (
  memory_id INTEGER PRIMARY KEY,
  embedding BLOB NOT NULL,
  model_id TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
);

CREATE INDEX idx_embeddings_model ON memory_embeddings(model_id);
```

### 9.8 memories_fts 表 (FTS5 虚拟表)
```sql
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

### 9.9 metrics 表
```sql
CREATE TABLE metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  pass_number INTEGER NOT NULL,
  original_tokens INTEGER,
  compressed_tokens INTEGER,
  compartment_count INTEGER,
  fact_count INTEGER,
  memory_count INTEGER,
  dropped_count INTEGER,
  historian_time_ms INTEGER,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
);

CREATE INDEX idx_metrics_session ON metrics(session_id);
```

## 数据库初始化

```typescript
function initializeDatabase(db: Database): void {
  db.exec(`
    PRAGMA foreign_keys = ON;
    PRAGMA journal_mode = WAL;
    
    -- 创建所有表
    CREATE TABLE IF NOT EXISTS tags (...);
    CREATE TABLE IF NOT EXISTS pending_ops (...);
    CREATE TABLE IF NOT EXISTS compartments (...);
    CREATE TABLE IF NOT EXISTS session_facts (...);
    CREATE TABLE IF NOT EXISTS session_meta (...);
    CREATE TABLE IF NOT EXISTS memories (...);
    CREATE TABLE IF NOT EXISTS memory_embeddings (...);
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(...);
    CREATE TABLE IF NOT EXISTS metrics (...);
  `);
}
```

## 注意事项

1. 使用 WAL 模式提高并发性能
2. FTS5 虚拟表需要触发器保持同步
3. 所有时间戳使用毫秒级 Unix 时间
4. 索引设计针对常见查询模式
5. 考虑使用迁移脚本管理 schema 变更
