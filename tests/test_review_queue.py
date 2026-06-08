"""Tests for review_queue.py"""

import json
import time
import pytest
from pathlib import Path
from ingest.review_queue import (
    enqueue_review, list_pending, mark_resolved, get_stats, clear_resolved,
    REVIEW_QUEUE_PATH,
)


@pytest.fixture(autouse=True)
def tmp_queue(tmp_path, monkeypatch):
    """Point review queue to a temp file for each test."""
    p = tmp_path / "review-queue.json"
    monkeypatch.setattr("ingest.review_queue.REVIEW_QUEUE_PATH", p)
    return p


class TestEnqueueReview:
    def test_creates_file_on_first_use(self, tmp_queue):
        enqueue_review("duplicate_concepts", {"a": 1}, "reflector")
        assert tmp_queue.exists()

    def test_stores_item_correctly(self, tmp_queue):
        enqueue_review("thin_page", {"url": "http://x.com"}, "reflector")
        data = json.loads(tmp_queue.read_text())
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["type"] == "thin_page"
        assert item["status"] == "pending"
        assert item["source"] == "reflector"
        assert item["data"] == {"url": "http://x.com"}
        assert "id" in item
        assert "created" in item

    def test_multiple_enqueues(self, tmp_queue):
        enqueue_review("duplicate_concepts", {"x": 1}, "a")
        enqueue_review("gap", {"y": 2}, "b")
        data = json.loads(tmp_queue.read_text())
        assert len(data["items"]) == 2

    def test_idempotent_file_writes(self, tmp_queue):
        enqueue_review("stale_page", {"p": 1}, "reflector")
        enqueue_review("stale_page", {"p": 2}, "reflector")
        data = json.loads(tmp_queue.read_text())
        assert data["version"] == 1
        assert len(data["items"]) == 2


class TestListPending:
    def test_empty_queue(self, tmp_queue):
        assert list_pending() == []

    def test_lists_all_pending(self, tmp_queue):
        enqueue_review("duplicate_concepts", {}, "a")
        enqueue_review("gap", {}, "b")
        items = list_pending()
        assert len(items) == 2

    def test_filter_by_type(self, tmp_queue):
        enqueue_review("duplicate_concepts", {}, "a")
        enqueue_review("gap", {}, "b")
        items = list_pending(item_type="gap")
        assert len(items) == 1
        assert items[0]["type"] == "gap"

    def test_excludes_resolved(self, tmp_queue):
        enqueue_review("thin_page", {}, "a")
        data = json.loads(tmp_queue.read_text())
        item_id = data["items"][0]["id"]
        mark_resolved(item_id, "fixed")
        assert list_pending() == []


class TestMarkResolved:
    def test_resolves_item(self, tmp_queue):
        enqueue_review("contradiction", {"x": 1}, "a")
        data = json.loads(tmp_queue.read_text())
        item_id = data["items"][0]["id"]
        mark_resolved(item_id, "merged concepts")
        data = json.loads(tmp_queue.read_text())
        assert data["items"][0]["status"] == "resolved"
        assert data["items"][0]["resolution"] == "merged concepts"

    def test_nonexistent_id_raises(self, tmp_queue):
        with pytest.raises(ValueError):
            mark_resolved("nonexistent", "x")


class TestGetStats:
    def test_empty_stats(self, tmp_queue):
        stats = get_stats()
        assert stats == {}

    def test_counts_by_type_and_status(self, tmp_queue):
        enqueue_review("duplicate_concepts", {}, "a")
        enqueue_review("duplicate_concepts", {}, "b")
        enqueue_review("gap", {}, "c")
        data = json.loads(tmp_queue.read_text())
        mark_resolved(data["items"][0]["id"], "ok")

        stats = get_stats()
        assert stats["duplicate_concepts"]["pending"] == 1
        assert stats["duplicate_concepts"]["resolved"] == 1
        assert stats["gap"]["pending"] == 1


class TestClearResolved:
    def test_clears_old_resolved(self, tmp_queue):
        enqueue_review("stale_page", {}, "a")
        data = json.loads(tmp_queue.read_text())
        item_id = data["items"][0]["id"]
        mark_resolved(item_id, "updated")

        # Manually set resolved_at to old date
        data = json.loads(tmp_queue.read_text())
        data["items"][0]["resolved_at"] = "2020-01-01T00:00:00"
        tmp_queue.write_text(json.dumps(data))

        clear_resolved(older_than_days=30)
        data = json.loads(tmp_queue.read_text())
        assert len(data["items"]) == 0

    def test_keeps_recent_resolved(self, tmp_queue):
        enqueue_review("stale_page", {}, "a")
        data = json.loads(tmp_queue.read_text())
        item_id = data["items"][0]["id"]
        mark_resolved(item_id, "updated")
        clear_resolved(older_than_days=30)
        data = json.loads(tmp_queue.read_text())
        assert len(data["items"]) == 1
