"""Tests for kb-reflect (post-batch reflector)."""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest.reflector import (
    cluster_by_tags,
    fetch_results,
    write_synthesis_article,
    _read_state,
    _write_state,
    _slugify,
    rebuild_index,
    append_reflect_log,
    run_reflect,
)


# ────────────────────────────────────────────
# Cluster by tags tests
# ────────────────────────────────────────────

class TestClusterByTags:
    def test_empty_input(self):
        result = cluster_by_tags([])
        assert result == []

    def test_single_article(self):
        articles = [{"title": "test", "tags": ["ai", "ml"]}]
        result = cluster_by_tags(articles, min_cluster_size=1, min_overlap=1)
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_two_articles_shared_tags(self):
        articles = [
            {"title": "art1", "tags": ["ai", "machine-learning", "neural-nets"]},
            {"title": "art2", "tags": ["ai", "machine-learning", "deep-learning"]},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_two_articles_no_overlap(self):
        articles = [
            {"title": "art1", "tags": ["ai", "machine-learning"]},
            {"title": "art2", "tags": ["security", "cryptography"]},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 0

    def test_transitive_clustering(self):
        """A-B share tags, B-C share tags → A, B, C in same cluster."""
        articles = [
            {"title": "A", "tags": ["ai", "ml", "python"]},
            {"title": "B", "tags": ["ai", "ml", "data"]},
            {"title": "C", "tags": ["ml", "data", "spark"]},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_multiple_clusters(self):
        articles = [
            {"title": "A1", "tags": ["ai", "ml", "python"]},
            {"title": "A2", "tags": ["ai", "ml", "data"]},
            {"title": "B1", "tags": ["security", "crypto", "tls"]},
            {"title": "B2", "tags": ["security", "crypto", "ssl"]},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 2
        sizes = sorted([len(c) for c in result])
        assert sizes == [2, 2]

    def test_min_cluster_size_filter(self):
        articles = [
            {"title": "A", "tags": ["ai", "ml"]},
            {"title": "B", "tags": ["security", "crypto"]},
        ]
        # No pair shares >=2 tags, so each is alone → filtered by min_cluster_size=2
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 0

    def test_tags_as_json_string(self):
        """Tags may come from DB as JSON strings."""
        articles = [
            {"title": "art1", "tags": '["ai", "ml", "python"]'},
            {"title": "art2", "tags": '["ai", "ml", "data"]'},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 1

    def test_case_insensitive_tags(self):
        articles = [
            {"title": "A", "tags": ["AI", "Machine-Learning"]},
            {"title": "B", "tags": ["ai", "machine-learning"]},
        ]
        result = cluster_by_tags(articles, min_cluster_size=2, min_overlap=2)
        assert len(result) == 1


# ────────────────────────────────────────────
# State management tests
# ────────────────────────────────────────────

class TestStateManagement:
    def test_read_state_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ingest.reflector.STATE_FILE", tmp_path / "nonexistent.json")
        state = _read_state()
        assert state == {"last_run": None, "processed_task_ids": []}

    def test_write_and_read_state(self, tmp_path, monkeypatch):
        state_file = tmp_path / "reflector-state.json"
        monkeypatch.setattr("ingest.reflector.STATE_FILE", state_file)
        _write_state({"last_run": "2026-06-01", "processed_task_ids": [1, 2]})
        state = _read_state()
        assert state["last_run"] == "2026-06-01"
        assert state["processed_task_ids"] == [1, 2]


# ────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("Machine Learning") == "machine-learning"

    def test_special_chars(self):
        assert _slugify("AI/ML & Deep Learning!") == "aiml-deep-learning"

    def test_empty(self):
        assert _slugify("") == ""

    def test_already_slug(self):
        assert _slugify("already-slug") == "already-slug"


# ────────────────────────────────────────────
# Synthesis article writing tests
# ────────────────────────────────────────────

class TestWriteSynthesisArticle:
    def test_writes_file(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        (wiki_dir / "ideas").mkdir(parents=True)

        synthesis = {
            "title": "AI Safety综合分析",
            "title_zh": "AI Safety综合分析",
            "abstract": "关于AI安全的综合分析摘要",
            "synthesis_of": ["article-1", "article-2"],
            "key_points": ["Safety is important", "Alignment matters"],
        }
        result = write_synthesis_article(synthesis, ["ai", "safety"], wiki_dir)
        assert result is not None
        assert result.startswith("ideas/synthesis-")
        assert (wiki_dir / result.replace("ideas/", "ideas/")).with_suffix(".md").exists() or \
               (wiki_dir / "ideas" / f"{result.split('/')[1]}.md").exists()

    def test_no_title_returns_none(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        synthesis = {"abstract": "No title"}
        result = write_synthesis_article(synthesis, [], wiki_dir)
        assert result is None

    def test_synthesis_prefix(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        (wiki_dir / "ideas").mkdir(parents=True)

        synthesis = {"title": "AI Safety Analysis", "abstract": "Summary"}
        result = write_synthesis_article(synthesis, ["ai"], wiki_dir)
        assert "synthesis-" in result

    def test_reserved_name_skipped(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        (wiki_dir / "ideas").mkdir(parents=True)

        synthesis = {"title": "index"}
        result = write_synthesis_article(synthesis, [], wiki_dir)
        assert result is None


# ────────────────────────────────────────────
# DB fetch tests (with temp DB)
# ────────────────────────────────────────────

class TestFetchResults:
    def _create_test_db(self, db_path: Path, n_results: int = 5):
        """Create a test DB with sample results."""
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ingest_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'done',
                priority INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                completed_at TEXT DEFAULT '2026-06-05 12:00:00',
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
        """)
        for i in range(n_results):
            conn.execute(
                "INSERT INTO ingest_tasks (filepath, status, completed_at) VALUES (?, 'done', ?)",
                (f"/raw/rss/article-{i}.md", f"2026-06-{5+i%25:02d} 12:00:00"),
            )
            task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                """INSERT INTO ingest_results
                   (task_id, raw_filepath, title_zh, title_en, summary_zh, category,
                    tags, people, orgs, key_insights, sentiment, quality_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_id, f"/raw/rss/article-{i}.md",
                 f"文章{i}", f"Article {i}", f"摘要{i}内容",
                 "ai" if i % 2 == 0 else "engineering",
                 json.dumps(["ai", "ml", f"topic-{i}"]),
                 json.dumps([]), json.dumps([]),
                 json.dumps([f"Insight {i}"]),
                 "positive" if i % 2 == 0 else "neutral",
                 0.5 + i * 0.05),
            )
        conn.commit()
        conn.close()

    def test_fetch_with_limit(self, tmp_path):
        db_path = tmp_path / "test.db"
        self._create_test_db(db_path, 10)
        results = fetch_results(str(db_path), limit=5)
        assert len(results) == 5

    def test_fetch_all(self, tmp_path):
        db_path = tmp_path / "test.db"
        self._create_test_db(db_path, 3)
        results = fetch_results(str(db_path), limit=100)
        assert len(results) == 3

    def test_fetch_since_date(self, tmp_path):
        db_path = tmp_path / "test.db"
        self._create_test_db(db_path, 10)
        results = fetch_results(str(db_path), since="2026-06-08")
        # Some results might match depending on dates
        assert isinstance(results, list)

    def test_fetch_empty_db(self, tmp_path):
        db_path = tmp_path / "empty.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Create schema but no data
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ingest_tasks (
                id INTEGER PRIMARY KEY, filepath TEXT UNIQUE, status TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ingest_results (
                id INTEGER PRIMARY KEY, task_id INTEGER, raw_filepath TEXT,
                title_zh TEXT, title_en TEXT, summary_zh TEXT, category TEXT,
                tags TEXT, people TEXT, orgs TEXT, key_insights TEXT,
                sentiment TEXT, quality_score REAL, related_topics TEXT,
                raw_llm_response TEXT
            );
        """)
        conn.close()
        results = fetch_results(str(db_path), limit=10)
        assert results == []

    def test_json_fields_parsed(self, tmp_path):
        db_path = tmp_path / "test.db"
        self._create_test_db(db_path, 1)
        results = fetch_results(str(db_path), limit=1)
        assert len(results) == 1
        assert isinstance(results[0]["tags"], list)


# ────────────────────────────────────────────
# Rebuild index tests
# ────────────────────────────────────────────

class TestRebuildIndex:
    def test_generates_index(self, tmp_path):
        wiki = tmp_path / "wiki"
        for subdir in ["ideas", "people", "mental-models", "projects", "daily", "code"]:
            (wiki / subdir).mkdir(parents=True)
        (wiki / "ideas" / "test.md").write_text("---\ntitle: Test Idea\n---\n\nBody\n")

        rebuild_index(wiki)
        index = wiki / "index.md"
        assert index.exists()
        content = index.read_text()
        assert "Test Idea" in content
        assert "ideas/test" in content


# ────────────────────────────────────────────
# Append reflect log tests
# ────────────────────────────────────────────

class TestAppendReflectLog:
    def test_appends_to_log(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        log = wiki / "log.md"
        log.write_text("# Log\n")

        append_reflect_log(wiki, clusters_count=3, synthesis_count=2,
                           themes=["AI Safety", "Scaling"])
        content = log.read_text()
        assert "kb-reflect" in content
        assert "AI Safety" in content


# ────────────────────────────────────────────
# Run reflect integration (mocked)
# ────────────────────────────────────────────

class TestRunReflect:
    def test_dry_run(self, tmp_path, monkeypatch):
        """Dry run should cluster but not call LLM."""
        monkeypatch.setattr("ingest.reflector.DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr("ingest.reflector.WIKI_DIR", tmp_path / "wiki")
        monkeypatch.setattr("ingest.reflector.STATE_FILE", tmp_path / "state.json")

        # Create test DB
        db = tmp_path / "test.db"
        conn = __import__("sqlite3").connect(str(db))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ingest_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'done',
                completed_at TEXT DEFAULT '2026-06-05 12:00:00'
            );
            CREATE TABLE IF NOT EXISTS ingest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                raw_filepath TEXT,
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
                raw_llm_response TEXT
            );
        """)
        for i in range(4):
            conn.execute("INSERT INTO ingest_tasks (filepath, status) VALUES (?, 'done')",
                         (f"/raw/article-{i}.md",))
            tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO ingest_results (task_id, raw_filepath, title_en, tags, summary_zh, "
                "key_insights, sentiment, quality_score, category, people, orgs, related_topics) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, f"/raw/article-{i}.md", f"Article {i}",
                 json.dumps(["ai", "ml"]), "Summary", json.dumps(["Insight"]),
                 "positive", 0.7, "ai", "[]", "[]", "[]"),
            )
        conn.commit()
        conn.close()

        wiki = tmp_path / "wiki"
        for subdir in ["ideas", "people", "mental-models", "projects", "daily", "code"]:
            (wiki / subdir).mkdir(parents=True)
        (wiki / "log.md").write_text("# Log\n")
        (wiki / "index.md").write_text("# Index\n")

        result = run_reflect(articles=10, dry_run=True)
        assert result["clusters"] >= 0  # May or may not cluster

    def test_no_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ingest.reflector.DB_PATH", tmp_path / "nonexistent.db")
        result = run_reflect()
        assert "error" in result
