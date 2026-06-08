"""Tests for wiki-lint.py"""

import pytest
from pathlib import Path
from importlib import import_module

wiki_lint = import_module("wiki-lint")


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ntitle: Test\nupdated: 2026-05-30\n---\n\nBody"
        meta = wiki_lint.parse_frontmatter(text)
        assert meta["title"] == "Test"
        assert meta["updated"] == "2026-05-30"

    def test_no_frontmatter(self):
        meta = wiki_lint.parse_frontmatter("Just text")
        assert meta == {}

    def test_body_with_dashes(self):
        text = "---\ntitle: Test\n---\n\n---\nMore text"
        meta = wiki_lint.parse_frontmatter(text)
        assert meta["title"] == "Test"


class TestExtractWikilinks:
    def test_simple_link(self):
        result = wiki_lint.extract_wikilinks("See [[ideas/test]] for more")
        assert "ideas/test" in result

    def test_link_with_display(self):
        result = wiki_lint.extract_wikilinks("See [[ideas/test|display text]] here")
        assert "ideas/test" in result
        assert "display text" not in result[0]

    def test_multiple_links(self):
        result = wiki_lint.extract_wikilinks("[[a]] and [[b]] and [[c]]")
        assert len(result) == 3

    def test_no_links(self):
        assert wiki_lint.extract_wikilinks("no links here") == []

    def test_nested_brackets(self):
        """Make sure we don't confuse nested brackets."""
        result = wiki_lint.extract_wikilinks("[[ideas/test]]")
        assert result == ["ideas/test"]


class TestCheckBrokenLinks:
    def _make_pages(self, wiki_dir, pages_data):
        """Helper to create wiki pages for testing."""
        pages = {}
        for rel, content in pages_data.items():
            subdir = rel.split("/")[0]
            (wiki_dir / subdir).mkdir(parents=True, exist_ok=True)
            path = wiki_dir / f"{rel}.md"
            path.write_text(content)
            meta = wiki_lint.parse_frontmatter(content)
            links = wiki_lint.extract_wikilinks(content)
            pages[rel] = {"path": path, "meta": meta, "links": links, "text": content}
        return pages

    def test_no_broken(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        pages = self._make_pages(wiki, {
            "ideas/a": "---\ntitle: A\n---\n\n[[ideas/b]]",
            "ideas/b": "---\ntitle: B\n---\n\nContent",
        })
        broken = wiki_lint.check_broken_links(pages)
        assert broken == []

    def test_broken_link(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        pages = self._make_pages(wiki, {
            "ideas/a": "---\ntitle: A\n---\n\n[[ideas/nonexistent]]",
        })
        broken = wiki_lint.check_broken_links(pages)
        assert len(broken) == 1
        assert broken[0][0] == "ideas/a"
        assert broken[0][1] == "ideas/nonexistent"


class TestCheckOrphanPages:
    def test_linked_page_not_orphan(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        pages = {
            "ideas/a": {"path": wiki / "ideas" / "a.md", "meta": {}, "links": ["ideas/b"], "text": ""},
            "ideas/b": {"path": wiki / "ideas" / "b.md", "meta": {}, "links": [], "text": ""},
        }
        orphans = wiki_lint.check_orphan_pages(pages)
        assert "ideas/b" not in orphans  # b is linked from a

    def test_orphan_detected(self, tmp_path):
        wiki = tmp_path / "wiki"
        pages = {
            "ideas/a": {"path": wiki / "ideas" / "a.md", "meta": {}, "links": [], "text": ""},
        }
        orphans = wiki_lint.check_orphan_pages(pages)
        assert "ideas/a" in orphans


class TestCheckFrontmatter:
    def test_missing_fields(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        pages = {
            "ideas/test": {"path": wiki / "ideas" / "test.md", "meta": {"title": "Test"}, "links": [], "text": ""},
        }
        issues = wiki_lint.check_frontmatter(pages)
        assert len(issues) == 1
        assert "created" in issues[0][1] or "updated" in issues[0][1]

    def test_all_fields_present(self, tmp_path):
        wiki = tmp_path / "wiki"
        pages = {
            "ideas/test": {
                "path": wiki / "ideas" / "test.md",
                "meta": {"title": "Test", "created": "2026-05-01", "updated": "2026-05-30"},
                "links": [],
                "text": "",
            },
        }
        issues = wiki_lint.check_frontmatter(pages)
        assert issues == []


class TestFixFrontmatter:
    def test_adds_updated(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True)
        page_path = wiki / "ideas" / "test.md"
        content = "---\ntitle: Test\ncreated: 2026-05-01\n---\n\nBody\n"
        page_path.write_text(content)

        pages = {
            "ideas/test": {
                "path": page_path,
                "meta": {"title": "Test", "created": "2026-05-01"},
                "links": [],
                "text": content,
            },
        }
        issues = [("ideas/test", ["updated"])]
        fixed = wiki_lint.fix_frontmatter(pages, issues)
        assert fixed == 1

        updated = page_path.read_text()
        assert "updated:" in updated
