"""
test_wiki_writer.py — Tests for wiki_writer.py

Covers:
  - _to_vault_rel path conversion (BUG FIX: source must be vault-relative)
  - write_ingest_result: ideas page structure + source field
  - write_ingest_result: people pages + wikilinks
  - append_log: log format
  - rebuild_index: index generation
  - Smoke test: end-to-end ingest → verify all three layers link correctly
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Ensure scripts.ingest is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Must import AFTER path setup
from ingest.wiki_writer import (
    _to_vault_rel,
    slugify,
    parse_frontmatter,
    build_frontmatter,
    write_page,
    read_page,
    write_ingest_result,
    rebuild_index,
    append_log,
)


# ────────────────────────────────────────────
# _to_vault_rel — path conversion
# ────────────────────────────────────────────

class TestToVaultRel:
    """BUG FIX: source field must be vault-relative, not absolute."""

    def test_rss_article(self):
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/rss/2026-04-01-say-the-thing-you-want.md"
        assert _to_vault_rel(abs_path) == "raw/rss/2026-04-01-say-the-thing-you-want"

    def test_papers_article(self):
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/papers/some-paper.md"
        assert _to_vault_rel(abs_path) == "raw/papers/some-paper"

    def test_web_article(self):
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/web/clipped-page.md"
        assert _to_vault_rel(abs_path) == "raw/web/clipped-page"

    def test_no_extension(self):
        """Edge case: path without .md suffix."""
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/rss/article"
        assert _to_vault_rel(abs_path) == "raw/rss/article"

    def test_no_raw_ancestor(self):
        """Fallback when path has no 'raw' ancestor."""
        assert _to_vault_rel("/some/random/path.md") == "path"

    def test_never_returns_absolute_path(self):
        """The original BUG: must NEVER return /Users/... paths."""
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/rss/test.md"
        result = _to_vault_rel(abs_path)
        assert not result.startswith("/"), f"Got absolute path: {result}"

    def test_never_returns_md_extension(self):
        """Obsidian wikilinks should NOT have .md extension."""
        abs_path = "/Users/th/.openclaw/workspace-leader/cognitive-flywheel/raw/rss/test.md"
        result = _to_vault_rel(abs_path)
        assert not result.endswith(".md"), f"Got .md extension: {result}"


# ────────────────────────────────────────────
# slugify
# ────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("What's the Deal with Euler's Identity?") == "whats-the-deal-with-eulers-identity"

    def test_cjk_fallback(self):
        """CJK text without ASCII → slugify should still return something."""
        result = slugify("深度学习")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty(self):
        assert slugify("") == ""


# ────────────────────────────────────────────
# write_ingest_result — ideas page
# ────────────────────────────────────────────

@pytest.fixture
def wiki_env(tmp_path):
    """Set up a temporary wiki directory and patch WIKI_DIR."""
    wiki = tmp_path / "wiki"
    for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
        (wiki / subdir).mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# Index\n")
    (wiki / "log.md").write_text("# Log\n")
    return wiki


def _sample_result(filepath="/Users/th/project/raw/rss/2026-05-30-test-article.md", **overrides):
    """Build a sample ingest result dict."""
    base = {
        "_filepath": filepath,
        "title_zh": "测试文章",
        "title_en": "Test Article Title",
        "summary_zh": "这是一篇关于测试的文章摘要。",
        "category": "engineering",
        "tags": ["testing", "python"],
        "people": [
            {"name": "Alice Smith", "role": "Engineer", "org": "Acme Corp"},
        ],
        "orgs": ["Acme Corp"],
        "key_insights": ["Insight one", "Insight two"],
        "sentiment": "positive",
        "quality_score": 0.85,
        "related_topics": ["software testing"],
    }
    base.update(overrides)
    return base


class TestWriteIngestResultIdeas:
    """Verify ideas/ pages are correctly written with vault-relative source."""

    def test_source_is_vault_relative(self, wiki_env):
        """BUG FIX: source must be relative, not absolute."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        assert idea_file.exists(), "ideas page should be created"

        text = idea_file.read_text()
        meta, body = parse_frontmatter(text)

        # THE BUG FIX ASSERTION
        assert meta["source"] == "raw/rss/2026-05-30-test-article"
        assert not meta["source"].startswith("/"), \
            f"source is absolute path: {meta['source']}"
        assert not meta["source"].endswith(".md"), \
            f"source has .md extension: {meta['source']}"

    def test_source_no_raw_ancestor(self, wiki_env):
        """If filepath has no raw/ ancestor, source falls back to stem."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(filepath="/weird/path/article-x.md"))

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        meta, _ = parse_frontmatter(idea_file.read_text())
        assert meta["source"] == "article-x"

    def test_frontmatter_fields(self, wiki_env):
        """Verify all expected frontmatter fields."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        meta, _ = parse_frontmatter(idea_file.read_text())

        assert meta["title"] == "Test Article Title"
        assert meta["title_zh"] == "测试文章"
        assert meta["title_en"] == "Test Article Title"
        assert meta["category"] == "engineering"
        assert meta["sentiment"] == "positive"
        assert "quality_score" in meta
        assert "created" in meta
        assert "updated" in meta

    def test_body_structure(self, wiki_env):
        """Verify body has required sections."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        _, body = parse_frontmatter(idea_file.read_text())

        assert "# Test Article Title" in body
        assert "## 深度摘要" in body
        assert "这是一篇关于测试的文章摘要" in body
        assert "## 核心洞察" in body
        assert "Insight one" in body
        assert "## 标签" in body
        assert "## 相关人物" in body

    def test_people_wikilinks_in_body(self, wiki_env):
        """People must be linked with [[people/slug|Name]] format."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        _, body = parse_frontmatter(idea_file.read_text())

        assert "[[people/alice-smith|Alice Smith]]" in body

    def test_no_people_for_short_names(self, wiki_env):
        """Single-word names should not create people pages."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                people=[{"name": "Alice", "role": "Engineer"}]
            ))

        # Only the ideas page should be created, no people page
        assert not (wiki_env / "people" / "alice.md").exists()

    def test_idempotent_update(self, wiki_env):
        """Running twice should update, not duplicate."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            updated1 = write_ingest_result(_sample_result())
            updated2 = write_ingest_result(_sample_result())

        idea_file = wiki_env / "ideas" / "test-article-title.md"
        assert idea_file.exists()

        # Both calls should report the same pages
        assert "ideas/test-article-title" in updated1
        assert "ideas/test-article-title" in updated2


# ────────────────────────────────────────────
# write_ingest_result — people pages
# ────────────────────────────────────────────

class TestWriteIngestResultPeople:
    """Verify people/ pages and their wikilinks."""

    def test_creates_person_page(self, wiki_env):
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        person_file = wiki_env / "people" / "alice-smith.md"
        assert person_file.exists()
        meta, body = parse_frontmatter(person_file.read_text())
        assert meta["name"] == "Alice Smith"
        assert meta["role"] == "Engineer"

    def test_person_links_to_idea(self, wiki_env):
        """Person page must contain wikilink to the idea."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())

        person_file = wiki_env / "people" / "alice-smith.md"
        body = person_file.read_text()
        assert "[[ideas/test-article-title|Test Article Title]]" in body

    def test_person_appends_on_second_mention(self, wiki_env):
        """Second ingest mentioning same person should append, not overwrite."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result())
            write_ingest_result(_sample_result(
                title_en="Another Article",
                filepath="/Users/th/project/raw/rss/2026-06-01-another.md",
            ))

        person_file = wiki_env / "people" / "alice-smith.md"
        body = person_file.read_text()
        # Both articles should be mentioned
        assert "test-article-title" in body
        assert "another-article" in body


# ────────────────────────────────────────────
# append_log
# ────────────────────────────────────────────

class TestAppendLog:
    def test_appends_entry(self, wiki_env):
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env), \
             patch("ingest.wiki_writer.LOG_PATH", wiki_env / "log.md"):
            append_log(
                source="raw/rss/test.md",
                title="Test Article",
                updated_pages=["ideas/test-article", "people/alice-smith"],
                tokens_in=500, tokens_out=1000,
                model="mimo-v2.5-pro",
            )

        log_text = (wiki_env / "log.md").read_text()
        assert "Test Article" in log_text
        assert "raw/rss/test.md" in log_text
        assert "mimo-v2.5-pro" in log_text
        assert "ideas/test-article" in log_text


# ────────────────────────────────────────────
# rebuild_index
# ────────────────────────────────────────────

class TestRebuildIndex:
    def test_includes_ideas(self, wiki_env):
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env), \
             patch("ingest.wiki_writer.INDEX_PATH", wiki_env / "index.md"):
            write_ingest_result(_sample_result())
            rebuild_index()

        index_text = (wiki_env / "index.md").read_text()
        assert "概念 (ideas/)" in index_text
        assert "test-article-title" in index_text

    def test_includes_people(self, wiki_env):
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env), \
             patch("ingest.wiki_writer.INDEX_PATH", wiki_env / "index.md"):
            write_ingest_result(_sample_result())
            rebuild_index()

        index_text = (wiki_env / "index.md").read_text()
        assert "人物 (people/)" in index_text
        assert "alice-smith" in index_text


# ────────────────────────────────────────────
# SMOKE TEST: End-to-end three-layer verification
# ────────────────────────────────────────────

class TestSmokeThreeLayerIntegrity:
    """
    Smoke test: verify the three-layer architecture holds end-to-end.

    Layer 1: raw/ (immutable source)
    Layer 2: wiki/ (LLM-generated knowledge, links back to raw/)
    Layer 3: schema (AGENTS.md — not tested here)

    Assertions:
    1. Every ideas page has a source field that is vault-relative
    2. Every ideas page source references a raw/ path
    3. People pages use correct [[ideas/xxx]] wikilinks
    4. No absolute paths leak into any wiki page
    """

    def test_three_layer_integrity(self, wiki_env):
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env), \
             patch("ingest.wiki_writer.INDEX_PATH", wiki_env / "index.md"):
            # Simulate multiple ingest results
            results = [
                _sample_result(
                    filepath="/Users/th/project/raw/rss/2026-05-01-article-alpha.md",
                    title_en="Article Alpha",
                    tags=["testing", "python"],
                    people=[{"name": "Bob Jones", "role": "Researcher"}],
                ),
                _sample_result(
                    filepath="/Users/th/project/raw/rss/2026-05-02-article-beta.md",
                    title_en="Article Beta",
                    tags=["beta", "release"],
                    people=[
                        {"name": "Bob Jones", "role": "Researcher"},
                        {"name": "Carol White", "role": "Writer"},
                    ],
                ),
                _sample_result(
                    filepath="/Users/th/project/raw/papers/deep-paper.md",
                    title_en="Deep Paper",
                    tags=["research", "academic"],
                    people=[],
                ),
            ]

            for r in results:
                write_ingest_result(r)

            rebuild_index()

        # ── Verify ideas pages ──
        ideas_files = list((wiki_env / "ideas").glob("*.md"))
        assert len(ideas_files) == 3, f"Expected 3 ideas, got {len(ideas_files)}"

        for idea_file in ideas_files:
            text = idea_file.read_text()
            meta, body = parse_frontmatter(text)

            # 1. Source MUST be vault-relative
            source = meta.get("source", "")
            assert source, f"{idea_file.name}: missing source field"
            assert not source.startswith("/"), \
                f"{idea_file.name}: source is absolute: {source}"
            assert source.startswith("raw/"), \
                f"{idea_file.name}: source doesn't start with raw/: {source}"
            assert not source.endswith(".md"), \
                f"{idea_file.name}: source has .md extension: {source}"

            # 2. No absolute paths anywhere in the file
            assert "/Users/" not in text, \
                f"{idea_file.name}: contains absolute path"

        # ── Verify people pages ──
        people_files = list((wiki_env / "people").glob("*.md"))
        assert len(people_files) >= 2, f"Expected >=2 people, got {len(people_files)}"

        for person_file in people_files:
            text = person_file.read_text()

            # 3. People must use [[ideas/xxx]] wikilinks
            if "[[ideas/" in text:
                # Links should be relative, not absolute
                assert "[[ideas/" in text

            # 4. No absolute paths
            assert "/Users/" not in text, \
                f"{person_file.name}: contains absolute path"

        # ── Verify Bob Jones has mentions from both articles ──
        bob_file = wiki_env / "people" / "bob-jones.md"
        assert bob_file.exists(), "Bob Jones should have a page"
        bob_text = bob_file.read_text()
        assert "article-alpha" in bob_text
        assert "article-beta" in bob_text

        # ── Verify index ──
        index_text = (wiki_env / "index.md").read_text()
        assert "页面总数: 5" in index_text  # 3 ideas + 2 people
