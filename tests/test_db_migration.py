"""Tests for DB migration — new columns in task_queue.py"""

import sqlite3
import pytest
from pathlib import Path
from ingest.task_queue import TaskQueue, DB_SCHEMA


@pytest.fixture
def q(tmp_path):
    """Create a fresh TaskQueue."""
    return TaskQueue(str(tmp_path / "test.db"))


class TestMigrationNewDB:
    """New databases should have all columns from the start."""

    def test_ingest_tasks_has_stage_column(self, q):
        row = q._conn.execute("PRAGMA table_info(ingest_tasks)").fetchall()
        col_names = [r[1] for r in row]
        assert "stage" in col_names

    def test_ingest_tasks_has_content_hash_column(self, q):
        row = q._conn.execute("PRAGMA table_info(ingest_tasks)").fetchall()
        col_names = [r[1] for r in row]
        assert "content_hash" in col_names

    def test_ingest_tasks_stage_default(self, q):
        q._conn.execute("INSERT INTO ingest_tasks (filepath) VALUES (?)", ("test.md",))
        row = q._conn.execute("SELECT stage FROM ingest_tasks WHERE filepath='test.md'").fetchone()
        assert row[0] == "single"

    def test_ingest_results_has_merge_action(self, q):
        row = q._conn.execute("PRAGMA table_info(ingest_results)").fetchall()
        col_names = [r[1] for r in row]
        assert "merge_action" in col_names

    def test_ingest_results_has_segment_count(self, q):
        row = q._conn.execute("PRAGMA table_info(ingest_results)").fetchall()
        col_names = [r[1] for r in row]
        assert "segment_count" in col_names

    def test_ingest_results_has_segments_json(self, q):
        row = q._conn.execute("PRAGMA table_info(ingest_results)").fetchall()
        col_names = [r[1] for r in row]
        assert "segments_json" in col_names


class TestMigrationExistingDB:
    """Migration on existing DBs without new columns."""

    def test_migration_adds_columns_without_data_loss(self, tmp_path):
        """Create old-schema DB, add data, migrate, verify data intact."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        # Use OLD schema without the new columns
        OLD_SCHEMA = """
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
            result_hash TEXT
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
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO ingest_tasks (filepath, status) VALUES ('a.md', 'done')")
        conn.execute("INSERT INTO ingest_results (task_id, raw_filepath, title_zh) VALUES (1, 'a.md', '标题')")
        conn.commit()
        conn.close()

        # Open with TaskQueue (triggers migration)
        q = TaskQueue(str(db_path))

        # Old data still there
        row = q._conn.execute("SELECT filepath, status FROM ingest_tasks WHERE id=1").fetchone()
        assert row[0] == "a.md"
        assert row[1] == "done"

        # New columns exist
        row = q._conn.execute("SELECT stage, content_hash FROM ingest_tasks WHERE id=1").fetchone()
        assert row[0] == "single"  # default
        assert row[1] is None

        row2 = q._conn.execute("SELECT merge_action, segment_count FROM ingest_results WHERE id=1").fetchone()
        assert row2[0] is None
        assert row2[1] == 1  # default

    def test_migration_is_idempotent(self, tmp_path):
        """Opening the DB twice doesn't crash."""
        db_path = tmp_path / "idem.db"
        q1 = TaskQueue(str(db_path))
        q1.close()
        q2 = TaskQueue(str(db_path))
        # Verify columns still there
        row = q2._conn.execute("PRAGMA table_info(ingest_tasks)").fetchall()
        col_names = [r[1] for r in row]
        assert "stage" in col_names
        q2.close()


class TestCompleteTaskNewFields:
    """complete_task should accept and store new fields."""

    def test_complete_task_with_stage(self, q):
        q.init_queue(["x.md"])
        task = q.claim_next()
        result = {"_filepath": "x.md", "title_zh": "测试", "_raw_response": "resp"}
        q.complete_task(task["id"], result, "gpt-4", 100, 200,
                        stage="two_stage")
        row = q._conn.execute("SELECT stage FROM ingest_tasks WHERE id=?", (task["id"],)).fetchone()
        assert row[0] == "two_stage"

    def test_complete_task_with_merge_action(self, q):
        q.init_queue(["y.md"])
        task = q.claim_next()
        result = {"_filepath": "y.md", "title_zh": "测试"}
        q.complete_task(task["id"], result, "gpt-4", 50, 100,
                        merge_action="create")
        row = q._conn.execute("SELECT merge_action FROM ingest_results WHERE task_id=?", (task["id"],)).fetchone()
        assert row[0] == "create"

    def test_complete_task_with_segments(self, q):
        q.init_queue(["z.md"])
        task = q.claim_next()
        result = {"_filepath": "z.md", "title_zh": "测试"}
        q.complete_task(task["id"], result, "gpt-4", 50, 100,
                        segment_count=3, segments_json="[0,1,2]")
        row = q._conn.execute("SELECT segment_count, segments_json FROM ingest_results WHERE task_id=?", (task["id"],)).fetchone()
        assert row[0] == 3
        assert row[1] == "[0,1,2]"

    def test_complete_task_backwards_compatible(self, q):
        """Calling without new fields should still work (defaults)."""
        q.init_queue(["old.md"])
        task = q.claim_next()
        result = {"_filepath": "old.md", "title_zh": "老数据"}
        q.complete_task(task["id"], result, "gpt-4", 10, 20)
        task_row = q._conn.execute("SELECT stage FROM ingest_tasks WHERE id=?", (task["id"],)).fetchone()
        assert task_row[0] == "single"
        res_row = q._conn.execute("SELECT merge_action, segment_count FROM ingest_results WHERE task_id=?", (task["id"],)).fetchone()
        assert res_row[0] is None
        assert res_row[1] == 1

    def test_content_hash_stored(self, q):
        q.init_queue(["h.md"])
        task = q.claim_next()
        result = {"_filepath": "h.md", "title_zh": "哈希"}
        q.complete_task(task["id"], result, "gpt-4", 10, 20,
                        content_hash="abc123")
        row = q._conn.execute("SELECT content_hash FROM ingest_tasks WHERE id=?", (task["id"],)).fetchone()
        assert row[0] == "abc123"
