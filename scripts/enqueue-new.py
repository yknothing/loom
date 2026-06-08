#!/usr/bin/env python3
"""
enqueue-new.py — Scan raw/ for files not yet in ingest_tasks and enqueue them.

Ensures every raw file has a corresponding task in the queue.
Idempotent: uses INSERT OR IGNORE (dedup by filepath).

Usage:
    python scripts/enqueue-new.py                  # Enqueue all untracked raw files
    python scripts/enqueue-new.py --dry-run        # Show what would be enqueued
    python scripts/enqueue-new.py --json            # JSON output
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue
from ingest.config import db_path, raw_dir

DB_PATH = db_path()
RAW_DIR = raw_dir()


def find_raw_files() -> list[str]:
    """Find all .md files under raw/ (all subdirs)."""
    files = []
    for subdir in ("rss", "papers", "web", "code", "journal"):
        d = RAW_DIR / subdir
        if d.exists():
            files.extend(sorted(str(f.resolve()) for f in d.glob("*.md")))
    return files


def enqueue_new(dry_run: bool = False) -> dict:
    """Enqueue raw files not yet in the task queue.

    Returns stats dict.
    """
    raw_files = find_raw_files()
    stats = {"total_raw": len(raw_files), "new": 0, "existing": 0}

    if dry_run:
        queue = TaskQueue(str(DB_PATH))
        conn = queue._conn
        existing = set(
            row[0] for row in conn.execute("SELECT filepath FROM ingest_tasks")
        )
        queue.close()

        new_files = [f for f in raw_files if f not in existing]
        stats["new"] = len(new_files)
        stats["existing"] = len(raw_files) - len(new_files)
        stats["new_files"] = new_files
        return stats

    queue = TaskQueue(str(DB_PATH))
    added, skipped = queue.init_queue(raw_files)
    queue_stats = queue.stats()
    queue.close()

    stats["new"] = added
    stats["existing"] = skipped
    stats["queue_pending"] = queue_stats["pending"]
    stats["queue_done"] = queue_stats["done"]
    stats["queue_failed"] = queue_stats["failed"]
    stats["queue_total"] = queue_stats["total"]

    return stats


def main():
    parser = argparse.ArgumentParser(description="Enqueue new raw files for ingest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    stats = enqueue_new(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"Raw files found: {stats['total_raw']}")
        print(f"  New to queue:  {stats['new']}")
        print(f"  Already known: {stats['existing']}")
        if not args.dry_run:
            print(f"  Queue: pending={stats.get('queue_pending', '?')}, "
                  f"done={stats.get('queue_done', '?')}, "
                  f"total={stats.get('queue_total', '?')}")
            if stats["new"] > 0:
                print(f"\n✅ {stats['new']} new articles enqueued for ingest")
            else:
                print("\n✅ No new articles to enqueue")


if __name__ == "__main__":
    main()
