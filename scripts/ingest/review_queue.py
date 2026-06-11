#!/usr/bin/env python3
"""
review_queue.py — JSON-based review queue for the Cognitive Flywheel.

Manages items that need human or automated review:
- duplicate_concepts, contradiction, thin_page, gap, stale_page

Thread-safe via write-to-temp-then-rename pattern.
"""

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

def _default_queue_path() -> Path:
    """Resolve the queue file via central config (was a cwd-relative path)."""
    try:
        from .config import data_dir
        return data_dir() / "review-queue.json"
    except Exception:
        return Path("data/review-queue.json")


REVIEW_QUEUE_PATH = _default_queue_path()


def _load(path: Path) -> dict:
    """Load the queue file, creating an empty one if needed."""
    if not path.exists():
        return {"version": 1, "updated": datetime.now().isoformat(), "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: dict):
    """Atomic write via temp file + rename."""
    data["updated"] = datetime.now().isoformat()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def enqueue_review(item_type: str, data: dict, source: str):
    """Add a review item. Types: duplicate_concepts, contradiction, thin_page, gap, stale_page."""
    path = REVIEW_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    queue = _load(path)
    item = {
        "id": f"rev_{uuid.uuid4().hex[:8]}",
        "type": item_type,
        "status": "pending",
        "created": datetime.now().isoformat(),
        "source": source,
        "data": data,
    }
    queue["items"].append(item)
    _save(path, queue)
    return item["id"]


def list_pending(item_type: str = None) -> list[dict]:
    """List pending items, optionally filtered by type."""
    path = REVIEW_QUEUE_PATH
    if not path.exists():
        return []
    queue = _load(path)
    items = [i for i in queue["items"] if i["status"] == "pending"]
    if item_type:
        items = [i for i in items if i["type"] == item_type]
    return items


def mark_resolved(item_id: str, resolution: str):
    """Mark an item as resolved."""
    path = REVIEW_QUEUE_PATH
    queue = _load(path)
    for item in queue["items"]:
        if item["id"] == item_id:
            item["status"] = "resolved"
            item["resolution"] = resolution
            item["resolved_at"] = datetime.now().isoformat()
            _save(path, queue)
            return
    raise ValueError(f"Item {item_id} not found")


def get_stats() -> dict:
    """Return counts by type and status."""
    path = REVIEW_QUEUE_PATH
    if not path.exists():
        return {}
    queue = _load(path)
    stats: dict[str, dict[str, int]] = {}
    for item in queue["items"]:
        t = item["type"]
        s = item["status"]
        if t not in stats:
            stats[t] = {}
        stats[t][s] = stats[t].get(s, 0) + 1
    return stats


def clear_resolved(older_than_days: int = 30):
    """Remove resolved items older than N days."""
    path = REVIEW_QUEUE_PATH
    if not path.exists():
        return
    queue = _load(path)
    cutoff = datetime.now() - timedelta(days=older_than_days)
    before = len(queue["items"])
    queue["items"] = [
        i for i in queue["items"]
        if not (
            i["status"] == "resolved"
            and i.get("resolved_at")
            and datetime.fromisoformat(i["resolved_at"]) < cutoff
        )
    ]
    if len(queue["items"]) < before:
        _save(path, queue)
