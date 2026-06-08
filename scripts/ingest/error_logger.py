#!/usr/bin/env python3
"""
error_logger.py — Structured error logging for ingest pipeline.

Writes JSONL to data/error-log.jsonl, one entry per failed article.
Each entry contains enough context to diagnose and fix the issue.

Log entry schema:
{
  "ts": "2026-06-03T12:34:56",       # ISO timestamp
  "filepath": "/full/path/to/file",   # source article path
  "filename": "2026-06-01-slug.md",   # just the filename
  "title": "...",                      # article title if known
  "error": "...",                      # final error summary
  "error_type": "HTTP_429|TimeoutError|...",  # classified type
  "phase": "read|http|network|json_parse|validation|unknown",
  "attempts": 4,                       # total attempts made
  "total_latency_ms": 12345,           # cumulative time spent
  "details": [                         # per-attempt breakdown
    {"attempt": 1, "phase": "network", "error_type": "URLError",
     "message": "...", "latency_ms": 3000}
  ]
}
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


from ingest.config import data_dir

LOG_PATH = data_dir() / "error-log.jsonl"


def log_failure(
    filepath: str,
    error: str,
    error_log: list[dict],
    title: str = "",
) -> None:
    """Append a structured failure entry to the error log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Classify the dominant error type
    phase = "unknown"
    error_type = "unknown"
    total_latency = 0

    if error_log:
        last = error_log[-1]
        phase = last.get("phase", "unknown")
        error_type = last.get("error_type", "unknown")
        for entry in error_log:
            total_latency += entry.get("latency_ms", 0)

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "title": title or os.path.basename(filepath),
        "error": error[:300],
        "error_type": error_type,
        "phase": phase,
        "attempts": len(error_log),
        "total_latency_ms": round(total_latency),
        "details": error_log,
    }

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_error_log(limit: int = 20) -> list[dict]:
    """Read last N entries from error log."""
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def summarize_errors() -> str:
    """Produce a human-readable summary of all logged errors."""
    entries = read_error_log(limit=1000)
    if not entries:
        return "📭 No errors logged."

    # Group by error_type
    from collections import Counter
    type_counts = Counter()
    phase_counts = Counter()
    for e in entries:
        type_counts[e.get("error_type", "unknown")] += 1
        phase_counts[e.get("phase", "unknown")] += 1

    lines = [
        f"📋 Error Log Summary ({len(entries)} failures)",
        "",
        "By error type:",
    ]
    for et, cnt in type_counts.most_common():
        lines.append(f"  {et}: {cnt}")

    lines.append("\nBy phase:")
    for ph, cnt in phase_counts.most_common():
        lines.append(f"  {ph}: {cnt}")

    # Recent failures (last 5)
    lines.append("\nRecent failures:")
    for e in entries[-5:]:
        lines.append(
            f"  [{e['ts']}] {e['filename'][:50]}\n"
            f"    {e['error_type']} @ {e['phase']} "
            f"({e['attempts']} attempts, {e['total_latency_ms']}ms)"
        )

    return "\n".join(lines)
