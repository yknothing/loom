"""
test_pipeline_integration.py — Integration tests for the full pipeline

Covers:
  1. enqueue-new.py: scanning raw files → task queue
  2. curator_v2.py: insight-density ranking, dedup, digest output
  3. Pipeline QA: verify raw→queue→ingest→vault integrity
  4. Edge cases: empty queue, already-enqueued files, malformed filenames
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────

@pytest.fixture
def pipeline_env(tmp_path):
    """Create a full pipeline environment: raw/, data/, wiki/."""
    raw_rss = tmp_path / "raw" / "rss"
    raw_papers = tmp_path / "raw" / "papers"
    data_dir = tmp_path / "data"
    wiki = tmp_path / "wiki"
    db_path = data_dir / "task-queue.db"

    # Ensure all directories exist
    for d in [raw_rss, raw_papers, data_dir]:
        d.mkdir(parents=True, exist_ok=True)
    for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
        (wiki / subdir).mkdir(parents=True, exist_ok=True)

    # Verify DB is writable
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.close()

    return {
        "root": tmp_path,
        "raw_rss": raw_rss,
        "raw_papers": raw_papers,
        "data": data_dir,
        "wiki": wiki,
        "db": db_path,
        "db_str": str(db_path),
    }


def _write_raw_file(raw_dir, filename, content=None):
    """Write a minimal raw RSS file."""
    if content is None:
        content = f"""---
source: Test Feed
url: https://example.com/{filename}
url_hash: {filename[:12]}
date: 2026-06-05
fetched: 2026-06-05
category: engineering
priority: medium
---

# {filename}

Test content about machine learning and distributed systems.
"""
    f = raw_dir / filename
    f.write_text(content, encoding="utf-8")
    return f


def _enqueue_files(env, filepaths):
    """Manually add files to the task queue."""
    from ingest.task_queue import TaskQueue
    queue = TaskQueue(str(env["db_str"]))
    queue.init_queue(filepaths)
    stats = queue.stats()
    queue.close()
    return stats


def _insert_result(env, task_id, title_en, quality_score=0.7,
                   category="engineering", insights=None, tags=None,
                   summary="A detailed summary of the article."):
    """Insert a fake ingest result for testing curator."""
    from ingest.task_queue import TaskQueue
    queue = TaskQueue(str(env["db_str"]))
    conn = queue._conn

    insights = insights or ["Key insight about the topic"]
    tags = tags or ["testing"]

    conn.execute("""
        UPDATE ingest_tasks SET status='done', completed_at=datetime('now'),
            llm_model='test-model', input_tokens=100, output_tokens=200
        WHERE id=?
    """, (task_id,))

    conn.execute("""
        INSERT INTO ingest_results (
            task_id, raw_filepath, title_en, title_zh, summary_zh,
            category, tags, key_insights, sentiment, quality_score,
            related_topics, people, orgs, raw_llm_response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id, f"/raw/rss/{title_en}.md", title_en, f"中文{title_en}",
        summary, category,
        json.dumps(tags), json.dumps(insights),
        "positive", quality_score,
        json.dumps(["topic1"]), json.dumps([]), json.dumps([]), ""
    ))

    conn.execute("""
        UPDATE ingest_tasks SET status='done' WHERE id=?
    """, (task_id,))
    queue.close()


# ────────────────────────────────────────────
# enqueue-new tests
# ────────────────────────────────────────────

class TestEnqueueNew:
    """Verify enqueue logic: raw files → task queue."""

    def test_enqueues_new_files(self, pipeline_env):
        """All raw files should be added to the queue."""
        _write_raw_file(pipeline_env["raw_rss"], "2026-06-01-article-a.md")
        _write_raw_file(pipeline_env["raw_rss"], "2026-06-02-article-b.md")

        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        raw_files = [str(f) for f in pipeline_env["raw_rss"].glob("*.md")]
        added, skipped = queue.init_queue(raw_files)
        queue.close()

        assert added == 2
        assert skipped == 0

    def test_idempotent_enqueue(self, pipeline_env):
        """Running enqueue twice should not duplicate tasks."""
        _write_raw_file(pipeline_env["raw_rss"], "2026-06-01-article-a.md")

        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        files = [str(f) for f in pipeline_env["raw_rss"].glob("*.md")]

        added1, _ = queue.init_queue(files)
        added2, skipped2 = queue.init_queue(files)
        queue.close()

        assert added1 == 1
        assert added2 == 0  # INSERT OR IGNORE correctly counts as skipped
        assert skipped2 == 1

        # Verify only 1 task exists
        queue = TaskQueue(str(pipeline_env["db_str"]))
        stats = queue.stats()
        queue.close()
        assert stats["total"] == 1

    def test_incremental_enqueue(self, pipeline_env):
        """New files added later should be enqueued without affecting existing."""
        f1 = _write_raw_file(pipeline_env["raw_rss"], "2026-06-01-old-article.md")

        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        queue.init_queue([str(f1)])
        queue.close()

        # Add new file
        f2 = _write_raw_file(pipeline_env["raw_rss"], "2026-06-05-new-article.md")

        queue = TaskQueue(str(pipeline_env["db_str"]))
        all_files = [str(f) for f in pipeline_env["raw_rss"].glob("*.md")]
        queue.init_queue(all_files)
        stats = queue.stats()
        queue.close()

        assert stats["total"] == 2
        assert stats["pending"] == 2

    def test_handles_malformed_filenames(self, pipeline_env):
        """Files without dates should still be enqueued."""
        _write_raw_file(pipeline_env["raw_rss"], "0001-01-01-no-date.md")
        _write_raw_file(pipeline_env["raw_rss"], "weird-filename.md")

        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        files = [str(f) for f in pipeline_env["raw_rss"].glob("*.md")]
        added, _ = queue.init_queue(files)
        queue.close()

        assert added == 2


# ────────────────────────────────────────────
# curator_v2 tests
# ────────────────────────────────────────────

class TestInsightDensity:
    """Verify insight-density scoring logic."""

    def test_high_insight_count_boosts_score(self):
        from curator import insight_density
        base = {"quality_score": 0.7, "key_insights": ["a", "b", "c"],
                "category": "engineering", "summary_zh": "A good summary"}
        high = {"quality_score": 0.7, "key_insights": ["a", "b", "c", "d", "e", "f"],
                "category": "engineering", "summary_zh": "A good summary"}
        assert insight_density(high) > insight_density(base)

    def test_engineering_category_bonus(self):
        from curator import insight_density
        eng = {"quality_score": 0.7, "key_insights": ["x"],
               "category": "engineering", "summary_zh": "Deep analysis"}
        opinion = {"quality_score": 0.7, "key_insights": ["x"],
                   "category": "opinion", "summary_zh": "Some thoughts"}
        assert insight_density(eng) > insight_density(opinion)

    def test_short_summary_penalty(self):
        from curator import insight_density
        good = {"quality_score": 0.7, "key_insights": ["x"],
                "category": "ai", "summary_zh": "A detailed and thorough analysis of the subject matter with multiple points"}
        short = {"quality_score": 0.7, "key_insights": ["x"],
                 "category": "ai", "summary_zh": "OK"}
        assert insight_density(good) > insight_density(short)


class TestTopicDedup:
    """Verify dedup by topic similarity."""

    def test_near_duplicate_titles_excluded(self, pipeline_env):
        """Two articles about the same topic should not both appear."""
        from curator import get_curated_articles

        # Create two very similar articles
        f1 = _write_raw_file(pipeline_env["raw_rss"], "2026-06-01-ai-safety-deep-dive.md")
        f2 = _write_raw_file(pipeline_env["raw_rss"], "2026-06-02-ai-safety-analysis.md")

        _enqueue_files(pipeline_env, [str(f1), str(f2)])

        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        rows = queue._conn.execute("SELECT id FROM ingest_tasks ORDER BY id").fetchall()
        queue.close()

        _insert_result(env=pipeline_env, task_id=rows[0]["id"],
                       title_en="AI Safety Deep Dive",
                       quality_score=0.8, insights=["AI alignment is critical"])
        _insert_result(env=pipeline_env, task_id=rows[1]["id"],
                       title_en="AI Safety Analysis",
                       quality_score=0.7, insights=["AI alignment matters"])

        with patch("curator.DB_PATH", pipeline_env["db_str"]):
            articles = get_curated_articles(days=30, top_n=5)

        # Should dedup — only one AI safety article
        assert len(articles) <= 1 or all(
            "safety" not in a["title"].lower() for a in articles[1:]
        )


class TestDigestFormat:
    """Verify digest output format."""

    def test_digest_is_nonempty(self, pipeline_env):
        from curator import generate_digest
        from ingest.task_queue import TaskQueue

        f = _write_raw_file(pipeline_env["raw_rss"], "2026-06-01-test.md")

        queue = TaskQueue(str(pipeline_env["db_str"]))
        queue.init_queue([str(f)])
        rows = queue._conn.execute("SELECT id FROM ingest_tasks").fetchall()
        tid = rows[0]["id"]

        # Insert result directly on same connection
        queue._conn.execute("""
            UPDATE ingest_tasks SET status='done', completed_at=datetime('now'),
                llm_model='test', input_tokens=100, output_tokens=200
            WHERE id=?
        """, (tid,))
        queue._conn.execute("""
            INSERT INTO ingest_results (
                task_id, raw_filepath, title_en, title_zh, summary_zh,
                category, tags, key_insights, sentiment, quality_score,
                related_topics, people, orgs, raw_llm_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid, str(f), "Test Article", "测试文章",
            "A detailed summary of this interesting article",
            "engineering",
            json.dumps(["test"]), json.dumps(["Important finding about testing"]),
            "positive", 0.8,
            json.dumps(["topic"]), json.dumps([]), json.dumps([]), ""
        ))
        queue._conn.commit()
        queue.close()

        with patch("curator.DB_PATH", pipeline_env["db_str"]):
            digest = generate_digest(top_n=10, days=30)

        assert "Test Article" in digest
        assert "洞见精选" in digest

    def test_empty_digest(self, pipeline_env):
        from curator import generate_digest
        with patch("curator.DB_PATH", pipeline_env["db_str"]):
            digest = generate_digest(top_n=10, days=30)
        assert "暂无" in digest


# ────────────────────────────────────────────
# Smoke test: Pipeline integrity
# ────────────────────────────────────────────

class TestPipelineSmoke:
    """
    Smoke test: verify end-to-end pipeline integrity.

    Simulates:
      1. Raw files exist
      2. Enqueue them
      3. Simulate ingest (mark done, insert results)
      4. Generate digest
      5. Verify every raw file has a task, every done task has a result
    """

    def test_full_pipeline_integrity(self, pipeline_env):
        # 1. Create raw files (simulating rss-fetch)
        files = []
        for i in range(5):
            f = _write_raw_file(
                pipeline_env["raw_rss"],
                f"2026-06-0{i+1}-article-{i}.md",
            )
            files.append(str(f))

        # 2. Enqueue
        from ingest.task_queue import TaskQueue
        queue = TaskQueue(str(pipeline_env["db_str"]))
        added, _ = queue.init_queue(files)
        assert added == 5

        # 3. Simulate ingest
        rows = queue._conn.execute(
            "SELECT id, filepath FROM ingest_tasks ORDER BY id"
        ).fetchall()

        for row in rows:
            tid = row["id"]
            fp = row["filepath"]
            title = Path(fp).stem

            queue._conn.execute("""
                UPDATE ingest_tasks SET status='done', completed_at=datetime('now'),
                    llm_model='test', input_tokens=100, output_tokens=200
                WHERE id=?
            """, (tid,))

            queue._conn.execute("""
                INSERT INTO ingest_results (
                    task_id, raw_filepath, title_en, title_zh, summary_zh,
                    category, tags, key_insights, sentiment, quality_score,
                    related_topics, people, orgs, raw_llm_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tid, fp, title, f"中文{title}",
                f"这是{title}的摘要", "engineering",
                json.dumps(["test"]), json.dumps(["测试洞察"]),
                "positive", 0.7 + (tid % 3) * 0.1,
                json.dumps(["topic"]), json.dumps([]), json.dumps([]), ""
            ))

        queue._conn.commit()
        stats = queue.stats()
        queue.close()

        # QA Check 1: all tasks done
        assert stats["total"] == 5
        assert stats["done"] == 5
        assert stats["pending"] == 0

        # QA Check 2: every done task has a result
        queue = TaskQueue(str(pipeline_env["db_str"]))
        for row in queue._conn.execute(
            "SELECT id FROM ingest_tasks WHERE status='done'"
        ):
            result = queue.get_result(row["id"])
            assert result is not None, f"Task {row['id']} has no result"
            assert result["title_en"]
            assert result["quality_score"] >= 0.0
        queue.close()

        # QA Check 3: curator can generate digest from these results
        from curator import generate_digest
        with patch("curator.DB_PATH", pipeline_env["db_str"]):
            digest = generate_digest(top_n=10, days=30)

        assert "洞见精选" in digest
        assert len(digest) > 100  # Not empty
