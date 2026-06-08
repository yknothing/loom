"""Tests for --since-last mode in curator.py."""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue
import curator


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Set up a temp DB and state file path."""
    db_path = tmp_path / "data" / "task-queue.db"
    state_path = tmp_path / "data" / "digest-state.json"
    monkeypatch.setattr(curator, "DB_PATH", db_path)
    monkeypatch.setattr(curator, "DIGEST_STATE_PATH", state_path)
    return {"db": db_path, "state": state_path, "root": tmp_path}


def _insert_article(queue, filepath, completed_at, title_en="Test Article",
                    category="ai", quality_score=0.7, tags=None, key_insights=None):
    """Helper to insert a done task with result."""
    conn = queue._conn
    conn.execute(
        "INSERT INTO ingest_tasks (filepath, status, completed_at) VALUES (?, 'done', ?)",
        (filepath, completed_at),
    )
    task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO ingest_results
           (task_id, raw_filepath, title_en, summary_zh, category, tags,
            key_insights, sentiment, quality_score, related_topics, people, orgs)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, filepath, title_en, "Some summary text that is longer than twenty characters",
         category, json.dumps(tags or ["tech"]),
         json.dumps(key_insights or ["insight 1"]), "neutral",
         quality_score, json.dumps([]), json.dumps([]), json.dumps([])),
    )
    conn.commit()
    return task_id


def test_state_file_created(tmp_env, capsys):
    """When --since-last is used and no state file exists, falls back to days=1 and creates state file."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    queue = TaskQueue(str(db_path))
    today = date.today()
    # Insert article from today
    _insert_article(queue, f"{today.isoformat()}-new-article.md",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    title_en="Fresh Article", quality_score=0.8)
    queue.close()

    # No state file yet
    assert not state_path.exists()

    with patch("sys.argv", ["curator.py", "--since-last", "--json"]):
        curator.main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) >= 1
    assert data[0]["title"] == "Fresh Article"

    # State file should now exist
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert "last_digest_ts" in state


def test_state_file_updated(tmp_env, capsys):
    """After generating digest, state file timestamp is updated."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    # Create initial state file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_digest_ts": "2026-06-01 08:00:00"}))

    queue = TaskQueue(str(db_path))
    _insert_article(queue, "2026-06-05-article.md", "2026-06-05 10:00:00",
                    title_en="New Article", quality_score=0.8)
    queue.close()

    with patch("sys.argv", ["curator.py", "--since-last", "--json"]):
        curator.main()

    state = json.loads(state_path.read_text())
    # Timestamp should have been updated (not the old one)
    assert state["last_digest_ts"] != "2026-06-01 08:00:00"


def test_filters_by_completed_at(tmp_env, capsys):
    """Articles with completed_at before the state timestamp are excluded."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    # State says last digest was at this time
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_digest_ts": "2026-06-03 12:00:00"}))

    queue = TaskQueue(str(db_path))
    # Old article - should be excluded
    _insert_article(queue, "2026-06-01-old.md", "2026-06-01 10:00:00",
                    title_en="Old Article", quality_score=0.9)
    # New article - should be included
    _insert_article(queue, "2026-06-04-new.md", "2026-06-04 10:00:00",
                    title_en="New Article", quality_score=0.7)
    queue.close()

    with patch("sys.argv", ["curator.py", "--since-last", "--json"]):
        curator.main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    titles = [a["title"] for a in data]
    assert "New Article" in titles
    assert "Old Article" not in titles


def test_no_articles_found(tmp_env, capsys):
    """When no new articles, state file is NOT updated."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    old_ts = "2026-06-01 08:00:00"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_digest_ts": old_ts}))

    queue = TaskQueue(str(db_path))
    # Only old articles
    _insert_article(queue, "2026-05-30-old.md", "2026-05-30 10:00:00",
                    title_en="Old Article", quality_score=0.9)
    queue.close()

    with patch("sys.argv", ["curator.py", "--since-last", "--json"]):
        curator.main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data == []

    # State file should still have the old timestamp
    state = json.loads(state_path.read_text())
    assert state["last_digest_ts"] == old_ts


def test_compatible_with_json(tmp_env, capsys):
    """--since-last --json works correctly and outputs valid JSON."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_digest_ts": "2026-06-01 08:00:00"}))

    queue = TaskQueue(str(db_path))
    _insert_article(queue, "2026-06-03-a.md", "2026-06-03 10:00:00",
                    title_en="JSON Article", quality_score=0.75)
    queue.close()

    with patch("sys.argv", ["curator.py", "--since-last", "--json"]):
        curator.main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "JSON Article"


def test_compatible_with_top(tmp_env, capsys):
    """--since-last --top 3 limits output."""
    db_path = tmp_env["db"]
    state_path = tmp_env["state"]

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_digest_ts": "2026-06-01 08:00:00"}))

    queue = TaskQueue(str(db_path))
    for i in range(5):
        _insert_article(queue, f"2026-06-03-art{i}.md", f"2026-06-03 10:0{i}:00",
                        title_en=f"Article {i}", quality_score=0.5 + i * 0.1)
    queue.close()

    with patch("sys.argv", ["curator.py", "--since-last", "--top", "3", "--json"]):
        curator.main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) <= 3
