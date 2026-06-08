#!/usr/bin/env python3
"""
queue.py — SQLite-based task queue for LLM ingest V2

Provides persistent task queue with:
- Atomic state transitions
- Dedup by filepath
- Priority ordering
- Retry tracking
- Progress stats
"""

import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    llm_model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    result_hash TEXT,
    stage TEXT DEFAULT 'single',
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS ingest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES ingest_tasks(id),
    raw_filepath TEXT NOT NULL,
    title_zh TEXT,
    title_en TEXT,
    summary_zh TEXT,
    category TEXT,
    tags TEXT,
    people TEXT,
    orgs TEXT,
    key_insights TEXT,
    sentiment TEXT,
    quality_score REAL,
    related_topics TEXT,
    raw_llm_response TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    merge_action TEXT,
    segment_count INTEGER DEFAULT 1,
    segments_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_status ON ingest_tasks(status);
CREATE INDEX IF NOT EXISTS idx_filepath ON ingest_tasks(filepath);

-- Pipeline run metadata
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    model TEXT,
    total_tasks INTEGER DEFAULT 0,
    completed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);
"""


class TaskQueue:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(DB_SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add new columns to existing databases (idempotent)."""
        # Ensure pipeline_runs table exists (may be missing in old DBs)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT DEFAULT (datetime('now')),
                finished_at TEXT,
                model TEXT,
                total_tasks INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running'
            )
        """)
        migrations = [
            ("ingest_tasks", "stage", "TEXT DEFAULT 'single'"),
            ("ingest_tasks", "content_hash", "TEXT"),
            ("ingest_results", "merge_action", "TEXT"),
            ("ingest_results", "segment_count", "INTEGER DEFAULT 1"),
            ("ingest_results", "segments_json", "TEXT"),
        ]
        for table, column, col_type in migrations:
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists
        self._conn.commit()

    def init_queue(self, filepaths: list[str], priority_fn=None):
        """Populate queue with filepaths. Skip existing. Apply priority if fn given."""
        added = 0
        skipped = 0
        for fp in filepaths:
            prio = priority_fn(fp) if priority_fn else 0
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO ingest_tasks (filepath, priority) VALUES (?, ?)",
                (fp, prio),
            )
            if cur.rowcount > 0:
                added += 1
            else:
                skipped += 1
        self._conn.commit()
        return added, skipped

    def claim_next(self) -> Optional[dict]:
        """Atomically claim the next pending task (highest priority first)."""
        cur = self._conn.execute("""
            UPDATE ingest_tasks
            SET status = 'running', started_at = datetime('now')
            WHERE id = (
                SELECT id FROM ingest_tasks
                WHERE status = 'pending'
                ORDER BY priority DESC, id ASC
                LIMIT 1
            )
            RETURNING *
        """)
        row = cur.fetchone()
        self._conn.commit()
        return dict(row) if row else None

    def complete_task(self, task_id: int, result: dict, model: str,
                      input_tokens: int, output_tokens: int,
                      stage: str = 'single', content_hash: str = None,
                      merge_action: str = None,
                      segment_count: int = 1, segments_json: str = None):
        """Mark task done and store result."""
        self._conn.execute("""
            UPDATE ingest_tasks
            SET status = 'done',
                completed_at = datetime('now'),
                llm_model = ?,
                input_tokens = ?,
                output_tokens = ?,
                error_message = NULL,
                stage = ?,
                content_hash = ?
            WHERE id = ?
        """, (model, input_tokens, output_tokens, stage, content_hash, task_id))

        self._conn.execute("""
            INSERT INTO ingest_results (
                task_id, raw_filepath,
                title_zh, title_en, summary_zh, category,
                tags, people, orgs, key_insights,
                sentiment, quality_score, related_topics,
                raw_llm_response,
                merge_action, segment_count, segments_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id, result.get("_filepath", ""),
            result.get("title_zh"), result.get("title_en"),
            result.get("summary_zh"), result.get("category"),
            json.dumps(result.get("tags", []), ensure_ascii=False),
            json.dumps(result.get("people", []), ensure_ascii=False),
            json.dumps(result.get("orgs", []), ensure_ascii=False),
            json.dumps(result.get("key_insights", []), ensure_ascii=False),
            result.get("sentiment"),
            result.get("quality_score", 0),
            json.dumps(result.get("related_topics", []), ensure_ascii=False),
            result.get("_raw_response", ""),
            merge_action,
            segment_count,
            segments_json,
        ))
        self._conn.commit()

    def fail_task(self, task_id: int, error: str):
        """Mark task failed, increment retry count. If under max, reset to pending."""
        row = self._conn.execute(
            "SELECT retry_count, max_retries FROM ingest_tasks WHERE id = ?",
            (task_id,)
        ).fetchone()

        if row and row["retry_count"] < row["max_retries"]:
            self._conn.execute("""
                UPDATE ingest_tasks
                SET status = 'pending',
                    retry_count = retry_count + 1,
                    error_message = ?,
                    started_at = NULL
                WHERE id = ?
            """, (error, task_id))
        else:
            self._conn.execute("""
                UPDATE ingest_tasks
                SET status = 'failed',
                    error_message = ?,
                    completed_at = datetime('now')
                WHERE id = ?
            """, (error, task_id))
        self._conn.commit()

    def reject_task(self, task_id: int, reason: str):
        """Mark task as rejected (quality check failed, won't retry)."""
        self._conn.execute("""
            UPDATE ingest_tasks
            SET status = 'rejected',
                error_message = ?,
                completed_at = datetime('now')
            WHERE id = ?
        """, (reason, task_id))
        self._conn.commit()

    def get_result(self, task_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM ingest_results WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        rows = self._conn.execute("""
            SELECT status, COUNT(*) as cnt FROM ingest_tasks GROUP BY status
        """).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}

        token_row = self._conn.execute("""
            SELECT COALESCE(SUM(input_tokens), 0) as inp,
                   COALESCE(SUM(output_tokens), 0) as out
            FROM ingest_tasks WHERE status = 'done'
        """).fetchone()

        total = sum(counts.values())
        return {
            "total": total,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
            "rejected": counts.get("rejected", 0),
            "input_tokens": token_row["inp"],
            "output_tokens": token_row["out"],
        }

    def reset_stuck_tasks(self):
        """Reset any tasks stuck in 'running' state back to 'pending'."""
        self._conn.execute(
            "UPDATE ingest_tasks SET status='pending', started_at=NULL WHERE status='running'"
        )
        self._conn.commit()

    def bulk_update_priority(self, filepath_pattern: str, priority: int):
        """Update priority for tasks matching a filepath LIKE pattern."""
        self._conn.execute(
            "UPDATE ingest_tasks SET priority=? WHERE filepath LIKE ?",
            (priority, filepath_pattern),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
