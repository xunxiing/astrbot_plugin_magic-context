import json
from datetime import datetime
from pathlib import Path

import aiosqlite


class TagsDatabase:
    def __init__(self, data_dir: str | Path):
        self.db_path = Path(data_dir) / "magic_context.db"

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS tags (
                    session_id TEXT NOT NULL,
                    tag_number INTEGER NOT NULL,
                    content_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    tag_type TEXT DEFAULT 'message',
                    status TEXT DEFAULT 'active',
                    byte_size INTEGER DEFAULT 0,
                    extra_json TEXT DEFAULT '{}',
                    PRIMARY KEY (session_id, tag_number)
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_session ON tags(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_message ON tags(content_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS compartments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    start_tag INTEGER NOT NULL,
                    end_tag INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    depth INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_compartments_session ON compartments(session_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS session_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    content TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_facts_session ON session_facts(session_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS session_meta (
                    session_id TEXT PRIMARY KEY,
                    last_historian_run TEXT,
                    historian_failure_count INTEGER DEFAULT 0,
                    total_tokens_used INTEGER DEFAULT 0,
                    total_messages_processed INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )"""
            )
            await db.commit()

    async def assign_tag(
        self,
        session_id: str,
        content_id: str,
        role: str,
        tag_type: str = "message",
        extra: dict | None = None,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            row = await db.execute_fetchall(
                "SELECT MAX(tag_number) FROM tags WHERE session_id = ?",
                (session_id,),
            )
            max_tag = row[0][0]
            tag_number = (max_tag + 1) if max_tag is not None else 0

            await db.execute(
                """INSERT OR REPLACE INTO tags
                    (session_id, tag_number, content_id, role, tag_type, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    tag_number,
                    content_id,
                    role,
                    tag_type,
                    json.dumps(extra or {}),
                ),
            )
            await db.commit()
        return tag_number

    async def batch_assign(self, session_id: str, items: list[dict]) -> list[int]:
        tag_numbers = []
        for item in items:
            tag_num = await self.assign_tag(
                session_id=session_id,
                content_id=item["content_id"],
                role=item["role"],
                tag_type=item.get("tag_type", "message"),
                extra=item.get("extra"),
            )
            tag_numbers.append(tag_num)
        return tag_numbers

    async def get_session_tags(
        self, session_id: str, status: str | None = None
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if status is not None:
                cursor = await db.execute(
                    "SELECT * FROM tags WHERE session_id = ? AND status = ? ORDER BY tag_number ASC",
                    (session_id, status),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM tags WHERE session_id = ? ORDER BY tag_number ASC",
                    (session_id,),
                )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_tag_status(
        self,
        session_id: str,
        tag_number: int,
        status: str,
        drop_mode: str | None = None,
    ):
        async with aiosqlite.connect(self.db_path) as db:
            if drop_mode is not None:
                await db.execute(
                    """UPDATE tags SET status = ?, extra_json = json_set(extra_json, '$.drop_mode', ?)
                       WHERE session_id = ? AND tag_number = ?""",
                    (status, drop_mode, session_id, tag_number),
                )
            else:
                await db.execute(
                    "UPDATE tags SET status = ? WHERE session_id = ? AND tag_number = ?",
                    (status, session_id, tag_number),
                )
            await db.commit()

    async def clear_session_tags(self, session_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM tags WHERE session_id = ?", (session_id,))
            await db.commit()

    async def record_token_usage(
        self, session_id: str, total: int, prompt: int, completion: int
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO session_meta (session_id, total_tokens_used, total_messages_processed, created_at)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       total_tokens_used = total_tokens_used + ?,
                       total_messages_processed = total_messages_processed + 1""",
                (
                    session_id,
                    total,
                    datetime.now().isoformat(),
                    total,
                ),
            )
            await db.commit()

    async def save_compartment(
        self,
        session_id: str,
        start_tag: int,
        end_tag: int,
        title: str,
        summary: str,
        depth: int = 1,
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO compartments
                    (session_id, start_tag, end_tag, title, summary, depth, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    start_tag,
                    end_tag,
                    title,
                    summary,
                    depth,
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

    async def get_compartments(self, session_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM compartments WHERE session_id = ? ORDER BY start_tag ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_fact(self, session_id: str, content: str, category: str = "general"):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO session_facts
                    (session_id, category, content, created_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, category, content, datetime.now().isoformat()),
            )
            await db.commit()

    async def get_session_facts(self, session_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_facts WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_session_meta(self, session_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_meta WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None
