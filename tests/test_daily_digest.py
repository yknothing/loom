"""Tests for daily-digest.py"""

import pytest
from datetime import date, timedelta
from pathlib import Path
from importlib import import_module

daily_digest = import_module("daily-digest")


class TestGetCurrentWeek:
    def test_format(self):
        result = daily_digest.get_current_week()
        assert "W" in result
        assert len(result) == 8  # e.g. "2026-W22"

    def test_is_string(self):
        result = daily_digest.get_current_week()
        assert isinstance(result, str)


class TestGetCurrentMonth:
    def test_format(self):
        result = daily_digest.get_current_month()
        assert "-" in result
        assert len(result) == 7  # e.g. "2026-05"

    def test_is_string(self):
        result = daily_digest.get_current_month()
        assert isinstance(result, str)


class TestParseLogEntries:
    def test_basic_parsing(self, tmp_path):
        log = tmp_path / "log.md"
        log.write_text(
            "## [2026-05-30] ingest | Test Article\n"
            "来源: raw/rss/test.md\n"
            "更新页面: ideas/test, people/john\n"
        )
        original = daily_digest.LOG_PATH
        daily_digest.LOG_PATH = log
        try:
            entries = daily_digest.parse_log_entries()
            assert len(entries) >= 1
            assert entries[0]["action"] == "ingest"
            assert entries[0]["title"] == "Test Article"
            assert entries[0]["source"] == "raw/rss/test.md"
        finally:
            daily_digest.LOG_PATH = original

    def test_date_filtering(self, tmp_path):
        log = tmp_path / "log.md"
        log.write_text(
            "## [2026-05-01] ingest | Old Article\n"
            "来源: raw/rss/old.md\n"
            "更新页面: ideas/old\n"
            "\n"
            "## [2026-05-30] ingest | New Article\n"
            "来源: raw/rss/new.md\n"
            "更新页面: ideas/new\n"
        )
        original = daily_digest.LOG_PATH
        daily_digest.LOG_PATH = log
        try:
            cutoff = date(2026, 5, 15)
            entries = daily_digest.parse_log_entries(since=cutoff)
            assert len(entries) == 1
            assert entries[0]["title"] == "New Article"
        finally:
            daily_digest.LOG_PATH = original

    def test_no_log_file(self, tmp_path):
        log = tmp_path / "nonexistent.md"
        original = daily_digest.LOG_PATH
        daily_digest.LOG_PATH = log
        try:
            entries = daily_digest.parse_log_entries()
            assert entries == []
        finally:
            daily_digest.LOG_PATH = original


class TestGenerateWeeklyDigest:
    def test_output_format(self, tmp_path, monkeypatch):
        wiki = tmp_path / "wiki"
        (wiki / "ideas").mkdir(parents=True, exist_ok=True)
        (wiki / "people").mkdir(parents=True, exist_ok=True)
        (wiki / "mental-models").mkdir(parents=True, exist_ok=True)
        (wiki / "projects").mkdir(parents=True, exist_ok=True)
        (wiki / "daily").mkdir(parents=True, exist_ok=True)
        (wiki / "code").mkdir(parents=True, exist_ok=True)

        log = tmp_path / "log.md"
        log.write_text(
            "## [2026-05-26] ingest | Test Article\n"
            "来源: raw/rss/test.md\n"
            "更新页面: ideas/test\n"
        )

        monkeypatch.setattr(daily_digest, "WIKI_DIR", wiki)
        monkeypatch.setattr(daily_digest, "LOG_PATH", log)
        monkeypatch.setattr(daily_digest, "DAILY_DIR", wiki / "daily")

        result = daily_digest.generate_weekly_digest("2026-W22")
        assert "Weekly Digest" in result
        assert "2026-W22" in result
        assert "统计" in result
        assert "period:" in result


class TestGenerateMonthlyDigest:
    @pytest.fixture
    def wiki_env(self, tmp_path, monkeypatch):
        """Set up a wiki directory with log for monthly digest tests."""
        wiki = tmp_path / "wiki"
        for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
            (wiki / subdir).mkdir(parents=True, exist_ok=True)

        log = tmp_path / "log.md"
        log.write_text(
            "## [2026-05-03] ingest | AI Research Breakthrough\n"
            "来源: raw/rss/ai.md\n"
            "更新页面: ideas/ai-research, people/sarah-chen\n"
            "\n"
            "## [2026-05-10] ingest | Rust Language Update\n"
            "来源: raw/rss/rust.md\n"
            "更新页面: ideas/rust-lang\n"
            "\n"
            "## [2026-05-17] ingest | Quantum Computing News\n"
            "来源: raw/rss/quantum.md\n"
            "更新页面: ideas/quantum-computing, people/bob-smith\n"
            "\n"
            "## [2026-05-25] ingest | AI Ethics Debate\n"
            "来源: raw/rss/ethics.md\n"
            "更新页面: ideas/ai-ethics, people/sarah-chen\n"
        )

        monkeypatch.setattr(daily_digest, "WIKI_DIR", wiki)
        monkeypatch.setattr(daily_digest, "LOG_PATH", log)
        monkeypatch.setattr(daily_digest, "DAILY_DIR", wiki / "daily")
        return wiki, log

    def test_monthly_digest_output_format(self, wiki_env):
        result = daily_digest.generate_monthly_digest("2026-05")
        assert "# Monthly Digest: 2026-05" in result
        assert "period: 2026-05" in result
        assert "type: monthly" in result
        assert "## 📊 月度统计" in result
        assert "## 📰 月度精华" in result
        assert "## 🔥 Top Topics" in result
        assert "## 👤 活跃人物" in result
        assert "## 🔍 新发现" in result
        assert "## ⚡ 矛盾/张力" in result
        assert "## 📈 趋势" in result

    def test_monthly_date_range(self, wiki_env):
        result = daily_digest.generate_monthly_digest("2026-05")
        assert "2026-05-01" in result
        assert "2026-05-31" in result

    def test_monthly_groups_by_week(self, wiki_env):
        result = daily_digest.generate_monthly_digest("2026-05")
        # Entries span multiple weeks; check at least one week heading present
        assert "2026-W" in result
        assert "AI Research Breakthrough" in result
        assert "AI Ethics Debate" in result

    def test_monthly_top_topics(self, wiki_env):
        result = daily_digest.generate_monthly_digest("2026-05")
        # "ai" appears in multiple titles, should be a top topic
        lines = result.split("\n")
        in_topics = False
        found_ai = False
        for line in lines:
            if "🔥 Top Topics" in line:
                in_topics = True
            elif line.startswith("## ") and in_topics:
                break
            elif in_topics and "ai" in line.lower():
                found_ai = True
        assert found_ai, "Expected 'ai' to appear in top topics"
