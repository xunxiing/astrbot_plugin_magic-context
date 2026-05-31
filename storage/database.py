import asyncio
import time
from pathlib import Path

import aiosqlite


class MagicContextDB:
    def __init__(self, data_dir: str | Path):
        self.db_path = Path(data_dir) / "magic_context.db"
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def init(self):
        async with self._init_lock:
            await self._init_unlocked()
            self._initialized = True

    async def _ensure_initialized(self):
        if not self._initialized:
            await self.init()

    async def _init_unlocked(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA foreign_keys = ON")
            await self._rename_incompatible_tables(db)

            await db.execute(
                """CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tag_number INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'message',
                    status TEXT NOT NULL DEFAULT 'active',
                    byte_size INTEGER DEFAULT 0,
                    drop_mode TEXT DEFAULT 'full',
                    tool_name TEXT,
                    input_byte_size INTEGER DEFAULT 0,
                    reasoning_byte_size INTEGER DEFAULT 0,
                    caveman_depth INTEGER DEFAULT 0,
                    tool_owner_message_id TEXT DEFAULT NULL,
                    original_text TEXT DEFAULT NULL,
                    harness TEXT NOT NULL DEFAULT 'astrbot',
                    UNIQUE(session_id, tag_number)
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_session_tag_number ON tags(session_id, tag_number)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_session_message_id ON tags(session_id, message_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_session_type_tool_name ON tags(session_id, type, tool_name)"
            )

            await db.execute(
                """CREATE TABLE IF NOT EXISTS pending_ops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tag_id INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    queued_at INTEGER NOT NULL,
                    harness TEXT NOT NULL DEFAULT 'astrbot'
                )"""
            )

            await db.execute(
                """CREATE TABLE IF NOT EXISTS compartments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    start_message INTEGER NOT NULL,
                    end_message INTEGER NOT NULL,
                    start_message_id TEXT DEFAULT '',
                    end_message_id TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    harness TEXT NOT NULL DEFAULT 'astrbot',
                    created_at INTEGER NOT NULL,
                    UNIQUE(session_id, sequence)
                )"""
            )

            await db.execute(
                """CREATE TABLE IF NOT EXISTS session_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    harness TEXT NOT NULL DEFAULT 'astrbot',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS compaction_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'idle',
                    input_tokens INTEGER DEFAULT 0,
                    saved_tokens INTEGER DEFAULT 0,
                    context_limit INTEGER DEFAULT 0,
                    ratio REAL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_compaction_events_created_at ON compaction_events(created_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_compaction_events_session_id ON compaction_events(session_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS context_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'request',
                    input_tokens INTEGER DEFAULT 0,
                    context_limit INTEGER DEFAULT 0,
                    ratio REAL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_context_samples_created_at ON context_samples(created_at)"
            )

            await db.execute(
                """CREATE TABLE IF NOT EXISTS session_meta (
                    session_id TEXT PRIMARY KEY,
                    harness TEXT NOT NULL DEFAULT 'astrbot',
                    historian_last_error TEXT,
                    historian_last_failure_at INTEGER,
                    historian_failure_count INTEGER DEFAULT 0,
                    compartment_in_progress INTEGER DEFAULT 0,
                    total_tokens_used INTEGER DEFAULT 0,
                    total_messages_processed INTEGER DEFAULT 0,
                    recent_24h_message_count INTEGER DEFAULT 0,
                    recent_24h_window_start INTEGER,
                    last_compaction_at INTEGER,
                    last_compaction_mode TEXT,
                    last_compaction_input_tokens INTEGER DEFAULT 0,
                    last_compaction_ratio REAL DEFAULT 0,
                    last_compaction_source_end_message INTEGER,
                    last_compaction_context_limit INTEGER DEFAULT 0,
                    last_request_input_tokens INTEGER DEFAULT 0,
                    last_request_context_limit INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )

            await self._migrate_columns(db)

            await db.commit()

    async def _table_exists(self, db: aiosqlite.Connection, table_name: str) -> bool:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
        return bool(rows)

    async def _table_columns(
        self, db: aiosqlite.Connection, table_name: str
    ) -> set[str]:
        cursor = await db.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        return {row[1] for row in rows}

    async def _rename_if_incompatible(
        self,
        db: aiosqlite.Connection,
        table_name: str,
        required_columns: set[str],
    ):
        if not await self._table_exists(db, table_name):
            return
        columns = await self._table_columns(db, table_name)
        if required_columns.issubset(columns):
            return
        legacy_name = f"{table_name}_legacy_{int(time.time())}"
        await db.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_name}")

    async def _rename_incompatible_tables(self, db: aiosqlite.Connection):
        await self._rename_if_incompatible(
            db,
            "tags",
            {"id", "session_id", "tag_number", "message_id", "type", "status"},
        )
        await self._rename_if_incompatible(
            db,
            "compartments",
            {"id", "session_id", "sequence", "start_message", "end_message"},
        )
        await self._rename_if_incompatible(
            db,
            "session_meta",
            {"session_id", "created_at", "updated_at"},
        )

    async def _add_column_if_missing(
        self,
        db: aiosqlite.Connection,
        table_name: str,
        column_name: str,
        ddl: str,
    ):
        columns = await self._table_columns(db, table_name)
        if column_name not in columns:
            await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")

    async def _migrate_columns(self, db: aiosqlite.Connection):
        await self._add_column_if_missing(
            db, "tags", "original_text", "original_text TEXT DEFAULT NULL"
        )
        await self._add_column_if_missing(
            db,
            "tags",
            "tool_owner_message_id",
            "tool_owner_message_id TEXT DEFAULT NULL",
        )
        await self._add_column_if_missing(
            db, "tags", "caveman_depth", "caveman_depth INTEGER DEFAULT 0"
        )
        await self._add_column_if_missing(
            db, "session_meta", "last_response_time", "last_response_time INTEGER"
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "recent_24h_message_count",
            "recent_24h_message_count INTEGER DEFAULT 0",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "recent_24h_window_start",
            "recent_24h_window_start INTEGER",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_at",
            "last_compaction_at INTEGER",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_mode",
            "last_compaction_mode TEXT",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_input_tokens",
            "last_compaction_input_tokens INTEGER DEFAULT 0",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_ratio",
            "last_compaction_ratio REAL DEFAULT 0",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_source_end_message",
            "last_compaction_source_end_message INTEGER",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_compaction_context_limit",
            "last_compaction_context_limit INTEGER DEFAULT 0",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_request_input_tokens",
            "last_request_input_tokens INTEGER DEFAULT 0",
        )
        await self._add_column_if_missing(
            db,
            "session_meta",
            "last_request_context_limit",
            "last_request_context_limit INTEGER DEFAULT 0",
        )

    # ── Tags ──────────────────────────────────────────────────────────

    async def assign_tag(
        self,
        session_id: str,
        tag_number: int,
        message_id: str,
        tag_type: str = "message",
        **kwargs,
    ) -> int:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO tags (
                    session_id, tag_number, message_id, type, status,
                    byte_size, drop_mode, tool_name, input_byte_size,
                    reasoning_byte_size, caveman_depth, tool_owner_message_id, original_text, harness
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, tag_number) DO UPDATE SET
                    message_id = excluded.message_id,
                    type = excluded.type,
                    status = excluded.status,
                    byte_size = excluded.byte_size,
                    drop_mode = excluded.drop_mode,
                    tool_name = excluded.tool_name,
                    input_byte_size = excluded.input_byte_size,
                    reasoning_byte_size = excluded.reasoning_byte_size,
                    caveman_depth = MAX(tags.caveman_depth, excluded.caveman_depth),
                    tool_owner_message_id = COALESCE(tags.tool_owner_message_id, excluded.tool_owner_message_id),
                    original_text = COALESCE(tags.original_text, excluded.original_text),
                    harness = excluded.harness
                WHERE tags.message_id = excluded.message_id
                  AND tags.type = excluded.type
                  AND (
                    tags.tool_owner_message_id IS excluded.tool_owner_message_id
                    OR tags.tool_owner_message_id = excluded.tool_owner_message_id
                  )""",
                (
                    session_id,
                    tag_number,
                    message_id,
                    tag_type,
                    kwargs.get("status", "active"),
                    kwargs.get("byte_size", 0),
                    kwargs.get("drop_mode", "full"),
                    kwargs.get("tool_name"),
                    kwargs.get("input_byte_size", 0),
                    kwargs.get("reasoning_byte_size", 0),
                    kwargs.get("caveman_depth", 0),
                    kwargs.get("tool_owner_message_id"),
                    kwargs.get("original_text"),
                    kwargs.get("harness", "astrbot"),
                ),
            )
            await db.commit()
        return tag_number

    async def get_active_tags(self, session_id: str) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tags WHERE session_id = ? AND status = 'active' ORDER BY tag_number ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_tags_by_session(self, session_id: str) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tags WHERE session_id = ? ORDER BY tag_number ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def session_has_tool_call(self, session_id: str, tool_name: str) -> bool:
        await self._ensure_initialized()
        if not session_id or not tool_name:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                """SELECT 1
                   FROM tags
                   WHERE session_id = ?
                     AND type = 'tool_call'
                     AND tool_name = ?
                   LIMIT 1""",
                (session_id, tool_name),
            )
        return bool(row)

    async def get_max_tag_number(self, session_id: str) -> int:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                "SELECT COALESCE(MAX(tag_number), 0) FROM tags WHERE session_id = ?",
                (session_id,),
            )
        return row[0][0]

    async def get_tag_number_by_identity(
        self,
        session_id: str,
        message_id: str,
        tag_type: str,
        tool_owner_message_id: str | None = None,
    ) -> int | None:
        await self._ensure_initialized()
        if tool_owner_message_id is None:
            where = (
                "session_id = ? AND message_id = ? AND type = ? "
                "AND tool_owner_message_id IS NULL"
            )
            params = (session_id, message_id, tag_type)
        else:
            where = (
                "session_id = ? AND message_id = ? AND type = ? "
                "AND tool_owner_message_id = ?"
            )
            params = (session_id, message_id, tag_type, tool_owner_message_id)

        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                f"SELECT tag_number FROM tags WHERE {where} ORDER BY tag_number ASC LIMIT 1",
                params,
            )
        if not row:
            return None
        return row[0][0]

    async def update_tag_status(self, session_id: str, tag_number: int, status: str):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tags SET status = ? WHERE session_id = ? AND tag_number = ?",
                (status, session_id, tag_number),
            )
            await db.commit()

    async def update_tag_drop_mode(
        self, session_id: str, tag_number: int, drop_mode: str
    ):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tags SET drop_mode = ? WHERE session_id = ? AND tag_number = ?",
                (drop_mode, session_id, tag_number),
            )
            await db.commit()

    async def update_tag_byte_size(
        self, session_id: str, tag_number: int, byte_size: int
    ):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tags SET byte_size = ? WHERE session_id = ? AND tag_number = ?",
                (byte_size, session_id, tag_number),
            )
            await db.commit()

    async def update_tag_message_id(
        self, session_id: str, tag_number: int, new_message_id: str
    ):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tags SET message_id = ? WHERE session_id = ? AND tag_number = ?",
                (new_message_id, session_id, tag_number),
            )
            await db.commit()

    async def update_caveman_depth(self, session_id: str, tag_number: int, depth: int):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tags SET caveman_depth = ? WHERE session_id = ? AND tag_number = ?",
                (depth, session_id, tag_number),
            )
            await db.commit()

    async def update_caveman_depths(
        self, session_id: str, updates: list[tuple[int, int]]
    ):
        """Batch-update caveman depths in a single transaction."""
        if not updates:
            return
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            for tag_number, depth in updates:
                await db.execute(
                    "UPDATE tags SET caveman_depth = ? WHERE session_id = ? AND tag_number = ?",
                    (depth, session_id, tag_number),
                )
            await db.commit()

    async def delete_tags_by_message_id(self, session_id: str, message_id: str):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM tags WHERE session_id = ? AND message_id = ?",
                (session_id, message_id),
            )
            await db.commit()
        return cursor.rowcount

    async def clear_session_tags(self, session_id: str):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM tags WHERE session_id = ?", (session_id,))
            await db.commit()

    async def get_source_contents(
        self, session_id: str, tag_numbers: list[int]
    ) -> dict[int, str]:
        """Batch-load pristine original text for given tag numbers."""
        if not tag_numbers:
            return {}
        await self._ensure_initialized()
        placeholders = ",".join("?" * len(tag_numbers))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT tag_number, original_text FROM tags WHERE session_id = ? AND tag_number IN ({placeholders})",
                (session_id, *tag_numbers),
            )
            rows = await cursor.fetchall()
        return {row["tag_number"]: row["original_text"] or "" for row in rows}

    # ── Pending Ops ───────────────────────────────────────────────────

    async def queue_pending_op(self, session_id: str, tag_id: int, operation: str):
        await self._ensure_initialized()
        now_ms = int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO pending_ops (session_id, tag_id, operation, queued_at) VALUES (?, ?, ?, ?)",
                (session_id, tag_id, operation, now_ms),
            )
            await db.commit()

    async def get_pending_ops(self, session_id: str) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pending_ops WHERE session_id = ?",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def clear_pending_ops(self, session_id: str):
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM pending_ops WHERE session_id = ?", (session_id,)
            )
            await db.commit()

    # ── Compartments ──────────────────────────────────────────────────

    async def save_compartments(self, session_id: str, compartments: list[dict]):
        await self._ensure_initialized()
        now_ms = int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM compartments WHERE session_id = ?", (session_id,)
            )
            for c in compartments:
                await db.execute(
                    """INSERT INTO compartments (
                        session_id, sequence, start_message, end_message,
                        start_message_id, end_message_id, title, content, harness, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        c["sequence"],
                        c["start_message"],
                        c["end_message"],
                        c.get("start_message_id", ""),
                        c.get("end_message_id", ""),
                        c["title"],
                        c["content"],
                        c.get("harness", "astrbot"),
                        c.get("created_at", now_ms),
                    ),
                )
            await db.commit()

    async def get_compartments(self, session_id: str) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM compartments WHERE session_id = ? ORDER BY sequence ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def append_compartments(self, session_id: str, compartments: list[dict]):
        await self._ensure_initialized()
        now_ms = int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            for c in compartments:
                await db.execute(
                    """INSERT INTO compartments (
                        session_id, sequence, start_message, end_message,
                        start_message_id, end_message_id, title, content, harness, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        c["sequence"],
                        c["start_message"],
                        c["end_message"],
                        c.get("start_message_id", ""),
                        c.get("end_message_id", ""),
                        c["title"],
                        c["content"],
                        c.get("harness", "astrbot"),
                        c.get("created_at", now_ms),
                    ),
                )
            await db.commit()

    async def get_last_compartment_end_message(self, session_id: str) -> int | None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                "SELECT MAX(end_message) FROM compartments WHERE session_id = ?",
                (session_id,),
            )
        return row[0][0]

    # ── Session Facts ─────────────────────────────────────────────────

    async def replace_session_facts(self, session_id: str, facts: list[dict]):
        await self._ensure_initialized()
        now_ms = int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM session_facts WHERE session_id = ?", (session_id,)
            )
            for f in facts:
                await db.execute(
                    """INSERT INTO session_facts (
                        session_id, category, content, harness, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        f["category"],
                        f["content"],
                        f.get("harness", "astrbot"),
                        f.get("created_at", now_ms),
                        f.get("updated_at", now_ms),
                    ),
                )
            await db.commit()

    async def get_session_facts(self, session_id: str) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_facts WHERE session_id = ? ORDER BY category, id ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Session Meta ──────────────────────────────────────────────────

    async def get_or_create_session_meta(self, session_id: str) -> dict:
        await self._ensure_initialized()
        now_ms = int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_meta WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                return dict(row)
            await db.execute(
                """INSERT INTO session_meta (session_id, created_at, updated_at)
                   VALUES (?, ?, ?)""",
                (session_id, now_ms, now_ms),
            )
            await db.commit()
        return await self.get_or_create_session_meta(session_id)

    async def update_session_meta(self, session_id: str, **kwargs):
        if not kwargs:
            return
        await self._ensure_initialized()
        kwargs.setdefault("updated_at", int(time.time() * 1000))
        keys = list(kwargs)
        sets = [f"{k} = ?" for k in keys]
        values = [kwargs[k] for k in keys]
        values.append(session_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE session_meta SET {', '.join(sets)} WHERE session_id = ?",
                values,
            )
            await db.commit()

    async def list_session_meta(self) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_meta ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def record_compaction_event(
        self,
        session_id: str,
        mode: str,
        *,
        source: str = "idle",
        input_tokens: int = 0,
        saved_tokens: int = 0,
        context_limit: int = 0,
        ratio: float = 0,
        created_at: int | None = None,
    ) -> None:
        await self._ensure_initialized()
        created_at = created_at or int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO compaction_events (
                    session_id, mode, source, input_tokens, saved_tokens,
                    context_limit, ratio, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    mode,
                    source,
                    int(input_tokens),
                    int(saved_tokens),
                    int(context_limit),
                    float(ratio),
                    int(created_at),
                ),
            )
            await db.commit()

    async def get_compaction_events_since(self, start_ms: int) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM compaction_events
                   WHERE created_at >= ?
                   ORDER BY created_at ASC""",
                (int(start_ms),),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def record_context_sample(
        self,
        session_id: str,
        *,
        source: str = "request",
        input_tokens: int = 0,
        context_limit: int = 0,
        ratio: float = 0,
        created_at: int | None = None,
    ) -> None:
        await self._ensure_initialized()
        created_at = created_at or int(time.time() * 1000)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO context_samples (
                    session_id, source, input_tokens, context_limit, ratio, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source,
                    int(input_tokens),
                    int(context_limit),
                    float(ratio),
                    int(created_at),
                ),
            )
            await db.commit()

    async def get_context_samples_since(
        self, start_ms: int, *, limit: int = 240
    ) -> list[dict]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM context_samples
                   WHERE created_at >= ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (int(start_ms), int(limit)),
            )
            rows = await cursor.fetchall()
        items = [dict(row) for row in rows]
        items.reverse()
        return items
