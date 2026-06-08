"""Tests for rss-fetch.py"""

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from importlib import import_module
rss_fetch = import_module("rss-fetch")


class TestSlugify:
    def test_basic(self):
        assert rss_fetch.slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert rss_fetch.slugify("AI & Machine Learning!") == "ai-machine-learning"

    def test_multiple_spaces(self):
        assert rss_fetch.slugify("foo   bar   baz") == "foo-bar-baz"

    def test_underscores(self):
        assert rss_fetch.slugify("foo_bar_baz") == "foo-bar-baz"

    def test_leading_trailing_hyphens(self):
        assert rss_fetch.slugify("--hello--") == "hello"

    def test_long_title_truncated(self):
        long_title = "a" * 200
        result = rss_fetch.slugify(long_title)
        assert len(result) <= 80

    def test_empty(self):
        assert rss_fetch.slugify("") == ""

    def test_unicode(self):
        result = rss_fetch.slugify("café résumé")
        assert "caf" in result


class TestUrlHash:
    def test_deterministic(self):
        url = "https://example.com/article"
        assert rss_fetch.url_hash(url) == rss_fetch.url_hash(url)

    def test_different_urls(self):
        assert rss_fetch.url_hash("https://a.com") != rss_fetch.url_hash("https://b.com")

    def test_length(self):
        result = rss_fetch.url_hash("https://example.com")
        assert len(result) == 12

    def test_matches_sha256(self):
        url = "https://example.com"
        expected = hashlib.sha256(url.encode()).hexdigest()[:12]
        assert rss_fetch.url_hash(url) == expected


class TestGetExistingHashes:
    def test_empty_dir(self, tmp_path):
        result = rss_fetch.get_existing_hashes(tmp_path / "nonexistent")
        assert result == set()

    def test_reads_hashes(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        content = "---\nurl_hash: abc123\n---\n\n# Test\n"
        (raw / "test.md").write_text(content)
        result = rss_fetch.get_existing_hashes(raw)
        assert "abc123" in result

    def test_no_frontmatter(self, tmp_path):
        raw = tmp_path
        (raw / "plain.md").write_text("Just plain text")
        result = rss_fetch.get_existing_hashes(raw)
        assert result == set()


class TestStripHtml:
    def test_basic_tags(self):
        assert rss_fetch.strip_html("<p>Hello</p>") == "Hello"

    def test_nested_tags(self):
        result = rss_fetch.strip_html("<div><p>Hello <b>World</b></p></div>")
        assert result == "Hello World"

    def test_entities(self):
        assert rss_fetch.strip_html("a &amp; b &lt; c") == "a & b < c"

    def test_quotes(self):
        assert rss_fetch.strip_html("&quot;hello&quot; &#39;world&#39;") == '"hello" \'world\''

    def test_nbsp(self):
        assert rss_fetch.strip_html("hello&nbsp;world") == "hello world"

    def test_collapses_whitespace(self):
        result = rss_fetch.strip_html("hello    world")
        assert result == "hello world"

    def test_collapses_newlines(self):
        result = rss_fetch.strip_html("hello\n\n\n\nworld")
        assert "\n\n\n" not in result


class TestFetchFeed:
    def _make_entry(self, title, url, published=None, content=""):
        entry = MagicMock()
        entry.title = title
        entry.link = url
        entry.published = published or "2026-05-30"
        entry.published_parsed = None
        entry.updated_parsed = None
        entry.content = [{"value": content}] if content else None
        entry.summary_detail = None
        entry.summary = content if not content else None
        return entry

    def _mock_feed_data(self, entries):
        """Build a serialized feed string that feedparser can parse."""
        feed = MagicMock()
        feed.entries = entries
        feed.bozo = False
        return feed

    def _mock_urlopen(self, feed_obj):
        """Mock urllib.request.urlopen to return dummy bytes that feedparser.parse() will process."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<rss/>"  # dummy bytes
        mock_urlopen = MagicMock(return_value=mock_resp)
        return mock_urlopen, feed_obj

    def test_new_article(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        entry = self._make_entry("Test Article", "https://example.com/test", content="<p>Content here</p>")
        feed_obj = self._mock_feed_data([entry])

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<dummy/>"
        with patch.object(rss_fetch.urllib.request, "urlopen", return_value=mock_resp), \
             patch.object(rss_fetch.feedparser, "parse", return_value=feed_obj):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss", "category": "tech"}
            stats = rss_fetch.fetch_feed(config, set(), raw)

        assert stats["new"] == 1
        assert stats["existing"] == 0
        files = list(raw.glob("*.md"))
        assert len(files) == 1

    def test_existing_article_skipped(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        url = "https://example.com/test"
        h = rss_fetch.url_hash(url)
        entry = self._make_entry("Test Article", url)
        feed_obj = self._mock_feed_data([entry])

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<dummy/>"
        with patch.object(rss_fetch.urllib.request, "urlopen", return_value=mock_resp), \
             patch.object(rss_fetch.feedparser, "parse", return_value=feed_obj):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss"}
            stats = rss_fetch.fetch_feed(config, {h}, raw)

        assert stats["new"] == 0
        assert stats["existing"] == 1

    def test_dry_run_no_files(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        entry = self._make_entry("Test Article", "https://example.com/test")
        feed_obj = self._mock_feed_data([entry])

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<dummy/>"
        with patch.object(rss_fetch.urllib.request, "urlopen", return_value=mock_resp), \
             patch.object(rss_fetch.feedparser, "parse", return_value=feed_obj):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss"}
            stats = rss_fetch.fetch_feed(config, set(), raw, dry_run=True)

        assert stats["new"] == 1
        assert list(raw.glob("*.md")) == []

    def test_filename_collision(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "2026-05-30-test-article.md").write_text("---\n---\nExisting")

        entry = self._make_entry("Test Article", "https://example.com/test", published="2026-05-30")
        import time
        entry.published_parsed = time.strptime("2026-05-30", "%Y-%m-%d")
        feed_obj = self._mock_feed_data([entry])

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<dummy/>"
        with patch.object(rss_fetch.urllib.request, "urlopen", return_value=mock_resp), \
             patch.object(rss_fetch.feedparser, "parse", return_value=feed_obj):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss"}
            stats = rss_fetch.fetch_feed(config, set(), raw)

        assert stats["new"] == 1
        files = sorted(raw.glob("*.md"))
        assert len(files) == 2
        assert any("-1.md" in f.name for f in files)

    def test_feed_error(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        feed_obj = MagicMock()
        feed_obj.bozo = True
        feed_obj.entries = []
        feed_obj.bozo_exception = "bad xml"

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<dummy/>"
        with patch.object(rss_fetch.urllib.request, "urlopen", return_value=mock_resp), \
             patch.object(rss_fetch.feedparser, "parse", return_value=feed_obj):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss"}
            stats = rss_fetch.fetch_feed(config, set(), raw)

        assert len(stats["errors"]) > 0

    def test_retry_on_network_error(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        # urlopen always raises
        with patch.object(rss_fetch.urllib.request, "urlopen", side_effect=ConnectionError("refused")):
            config = {"name": "TestFeed", "url": "https://feed.example.com/rss"}
            stats = rss_fetch.fetch_feed(config, set(), raw)

        assert len(stats["errors"]) > 0
        assert "3 attempts" in stats["errors"][0]
