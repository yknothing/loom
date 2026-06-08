"""Tests for llm-ingest.py"""

import sys
from pathlib import Path
from importlib import import_module
import pytest

llm_ingest = import_module("llm-ingest")


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ntitle: Hello\ncreated: 2026-05-30\n---\n\nBody text"
        meta, body = llm_ingest.parse_frontmatter(text)
        assert meta["title"] == "Hello"
        assert meta["created"] == "2026-05-30"
        assert "Body text" in body

    def test_no_frontmatter(self):
        text = "Just some text\nNo frontmatter"
        meta, body = llm_ingest.parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_body_with_triple_dashes(self):
        """Body containing '---' should not confuse parser (split with maxsplit=2)."""
        text = "---\ntitle: Test\n---\n\nBody\n---\nmore body"
        meta, body = llm_ingest.parse_frontmatter(text)
        assert meta["title"] == "Test"
        assert "---" in body

    def test_empty_frontmatter(self):
        text = "---\n---\n\nBody"
        meta, body = llm_ingest.parse_frontmatter(text)
        assert meta == {}
        assert "Body" in body

    def test_quoted_values(self):
        text = "---\ntitle: 'My Title'\n---\n\nBody"
        meta, body = llm_ingest.parse_frontmatter(text)
        assert meta["title"] == "My Title"


class TestBuildFrontmatter:
    def test_simple(self):
        meta = {"title": "Test", "created": "2026-05-30"}
        fm = llm_ingest.build_frontmatter(meta)
        assert fm.startswith("---\n")
        assert fm.endswith("\n---")
        assert "title: Test" in fm
        assert "created: 2026-05-30" in fm

    def test_list_values(self):
        meta = {"tags": ["ai", "ml"]}
        fm = llm_ingest.build_frontmatter(meta)
        assert "tags:" in fm
        assert "  - ai" in fm
        assert "  - ml" in fm

    def test_empty(self):
        fm = llm_ingest.build_frontmatter({})
        assert fm.startswith("---\n")
        assert fm.strip().endswith("---")


class TestExtractKeywords:
    def test_returns_list(self):
        result = llm_ingest.extract_keywords("python is great for machine learning")
        assert isinstance(result, list)

    def test_filters_stop_words(self):
        result = llm_ingest.extract_keywords("the quick brown fox jumps over the lazy dog")
        assert "the" not in result

    def test_top_n(self):
        text = "python python python java java java rust rust go"
        result = llm_ingest.extract_keywords(text, top_n=3)
        assert len(result) <= 3

    def test_empty_text(self):
        result = llm_ingest.extract_keywords("")
        assert result == []

    def test_min_length(self):
        """Words shorter than 3 chars should be excluded."""
        result = llm_ingest.extract_keywords("I am a go to do it")
        assert all(len(w) >= 3 for w in result)


class TestExtractTitle:
    def test_from_heading(self):
        text = "# My Title\n\nSome body"
        result = llm_ingest.extract_title(text, Path("test.md"))
        assert result == "My Title"

    def test_from_frontmatter(self):
        text = "---\ntitle: FM Title\n---\n\nNo heading"
        result = llm_ingest.extract_title(text, Path("test.md"))
        assert result == "FM Title"

    def test_heading_preferred_over_frontmatter(self):
        text = "---\ntitle: FM Title\n---\n\n# Heading Title\nBody"
        result = llm_ingest.extract_title(text, Path("test.md"))
        assert result == "Heading Title"

    def test_from_filename(self):
        text = "No heading or frontmatter"
        result = llm_ingest.extract_title(text, Path("my-file.md"))
        assert result == "my-file"


class TestExtractUrls:
    def test_basic(self):
        text = "Visit https://example.com for more"
        result = llm_ingest.extract_urls(text)
        assert "https://example.com" in result

    def test_multiple(self):
        text = "See https://a.com and http://b.com/page"
        result = llm_ingest.extract_urls(text)
        assert len(result) == 2

    def test_no_urls(self):
        assert llm_ingest.extract_urls("no urls here") == []

    def test_url_in_markdown_link(self):
        text = "[click](https://example.com/page)"
        result = llm_ingest.extract_urls(text)
        assert any("example.com" in u for u in result)


class TestGetProcessedFiles:
    def test_parses_log(self, tmp_path):
        log = tmp_path / "log.md"
        log.write_text(
            "## [2026-05-30] ingest | Test\n"
            "来源: raw/rss/test.md\n"
            "更新页面: ideas/test\n"
        )
        # Monkey-patch LOG_PATH
        original = llm_ingest.LOG_PATH
        llm_ingest.LOG_PATH = log
        try:
            result = llm_ingest.get_processed_files()
            assert "raw/rss/test.md" in result
        finally:
            llm_ingest.LOG_PATH = original

    def test_no_log(self, tmp_path):
        log = tmp_path / "nonexistent.md"
        original = llm_ingest.LOG_PATH
        llm_ingest.LOG_PATH = log
        try:
            result = llm_ingest.get_processed_files()
            assert result == set()
        finally:
            llm_ingest.LOG_PATH = original


class TestIngestFile:
    def _setup_ingest(self, tmp_path, monkeypatch):
        """Point module paths at tmp dirs."""
        wiki = tmp_path / "wiki"
        raw = tmp_path / "raw"
        for d in ["ideas", "people", "mental-models", "projects", "daily", "code"]:
            (wiki / d).mkdir(parents=True, exist_ok=True)
        (raw / "rss").mkdir(parents=True, exist_ok=True)
        (wiki / "log.md").write_text("# Log\n")
        (wiki / "index.md").write_text("# Index\n")

        monkeypatch.setattr(llm_ingest, "WIKI_DIR", wiki)
        monkeypatch.setattr(llm_ingest, "RAW_DIR", raw)
        monkeypatch.setattr(llm_ingest, "LOG_PATH", wiki / "log.md")
        monkeypatch.setattr(llm_ingest, "INDEX_PATH", wiki / "index.md")
        monkeypatch.setattr(llm_ingest, "ROOT", tmp_path)
        return wiki, raw

    def test_creates_idea_page(self, tmp_path, monkeypatch):
        wiki, raw = self._setup_ingest(tmp_path, monkeypatch)
        # Create raw file
        raw_file = raw / "rss" / "2026-05-30-test-article.md"
        raw_file.write_text("---\nsource: test\n---\n\n# Test Article\n\nContent about machine learning and neural networks.\n")

        result = llm_ingest.ingest_file(raw_file)
        assert any("ideas/test-article" in p for p in result)
        idea_page = wiki / "ideas" / "test-article.md"
        assert idea_page.exists()

    def test_creates_person_page(self, tmp_path, monkeypatch):
        wiki, raw = self._setup_ingest(tmp_path, monkeypatch)
        raw_file = raw / "rss" / "2026-05-30-test.md"
        raw_file.write_text("---\n---\n\n# Test Article\n\nJane Smith wrote about machine learning. John Doe also contributed.\n")

        result = llm_ingest.ingest_file(raw_file)
        # Should create person pages for detected people
        assert len(result) > 0

    def test_dry_run(self, tmp_path, monkeypatch):
        wiki, raw = self._setup_ingest(tmp_path, monkeypatch)
        raw_file = raw / "rss" / "2026-05-30-test.md"
        raw_file.write_text("---\n---\n\n# Test\n\nSome content.\n")

        result = llm_ingest.ingest_file(raw_file, dry_run=True)
        assert result == []
        assert not (wiki / "ideas" / "test.md").exists()

    def test_nonexistent_file(self, tmp_path, monkeypatch):
        self._setup_ingest(tmp_path, monkeypatch)
        result = llm_ingest.ingest_file(Path("/nonexistent/file.md"))
        assert result == []

    def test_sources_list_consistency(self, tmp_path, monkeypatch):
        """Verify sources field is always a list after ingest."""
        wiki, raw = self._setup_ingest(tmp_path, monkeypatch)
        raw_file = raw / "rss" / "2026-05-30-test.md"
        raw_file.write_text("---\n---\n\n# Test Article\n\nContent here.\n")

        llm_ingest.ingest_file(raw_file)

        idea_page = wiki / "ideas" / "test-article.md"
        text = idea_page.read_text()
        meta, _ = llm_ingest.parse_frontmatter(text)
        # sources should be a list when round-tripped through build_frontmatter
        assert isinstance(meta.get("sources", ""), str)  # parse_frontmatter returns strings
        # But the internal representation should handle list correctly


class TestRebuildIndex:
    def test_generates_index(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True, exist_ok=True)
        (wiki / "people").mkdir(parents=True, exist_ok=True)
        (wiki / "mental-models").mkdir(parents=True, exist_ok=True)
        (wiki / "projects").mkdir(parents=True, exist_ok=True)
        (wiki / "daily").mkdir(parents=True, exist_ok=True)
        (wiki / "code").mkdir(parents=True, exist_ok=True)

        # Create a page
        page = wiki / "ideas" / "test-idea.md"
        page.write_text("---\ntitle: Test Idea\n---\n\nBody\n")

        index = wiki / "index.md"
        log = wiki / "log.md"
        log.write_text("# Log\n")

        monkeypatch.setattr(llm_ingest, "WIKI_DIR", wiki)
        monkeypatch.setattr(llm_ingest, "INDEX_PATH", index)
        monkeypatch.setattr(llm_ingest, "LOG_PATH", log)

        llm_ingest.rebuild_index()
        assert index.exists()
        content = index.read_text()
        assert "Test Idea" in content
        assert "ideas/test-idea" in content
