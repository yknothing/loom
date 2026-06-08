"""Shared fixtures for Cognitive Flywheel tests."""

import os
import sys
import pytest
from pathlib import Path

# Add scripts dir to path so we can import modules
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def tmp_wiki(tmp_path):
    """Create a temporary wiki directory structure."""
    wiki = tmp_path / "wiki"
    for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
        (wiki / subdir).mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# Index\n")
    (wiki / "log.md").write_text("# Log\n")
    return wiki


@pytest.fixture
def tmp_raw(tmp_path):
    """Create a temporary raw directory."""
    raw = tmp_path / "raw"
    for subdir in ("rss", "papers", "web", "code", "journal"):
        (raw / subdir).mkdir(parents=True, exist_ok=True)
    return raw


@pytest.fixture
def sample_rss_page(tmp_raw):
    """Create a sample raw RSS file."""
    content = """---
source: Test Feed
url: https://example.com/article-1
url_hash: abc123def456
date: 2026-05-30
fetched: 2026-05-30
category: general
priority: medium
---

# Test Article Title

This is the first paragraph about interesting things.
It mentions machine learning and neural networks.

Some more content about AI safety and alignment.
"""
    f = tmp_raw / "rss" / "2026-05-30-test-article.md"
    f.write_text(content)
    return f


@pytest.fixture
def sample_wiki_pages(tmp_wiki):
    """Create a few sample wiki pages."""
    pages = {
        "ideas/test-idea": {
            "meta": {"title": "Test Idea", "created": "2026-05-01", "updated": "2026-05-30"},
            "body": "# Test Idea\n\nA test idea page.\n\nSee also [[ideas/another-idea]]."
        },
        "ideas/another-idea": {
            "meta": {"title": "Another Idea", "created": "2026-05-15", "updated": "2026-05-30"},
            "body": "# Another Idea\n\nAnother page.\n\nRelated: [[ideas/test-idea]]."
        },
        "people/john-doe": {
            "meta": {"name": "John Doe", "updated": "2026-05-20"},
            "body": "## Profile\n\nA researcher.\n\n- [[ideas/test-idea]]"
        },
    }
    for rel, data in pages.items():
        path = tmp_wiki / f"{rel}.md"
        lines = ["---"]
        for k, v in data["meta"].items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
        lines.append(data["body"])
        path.write_text("\n".join(lines))
    return pages
