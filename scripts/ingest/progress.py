#!/usr/bin/env python3
"""
progress.py — Progress tracker for LLM ingest V2
"""

import time
from .task_queue import TaskQueue


class ProgressTracker:
    def __init__(self, queue: TaskQueue, start_time: float):
        self.queue = queue
        self.start_time = start_time

    def report(self) -> str:
        s = self.queue.stats()
        elapsed = time.time() - self.start_time
        done = s["done"]
        total = s["total"]
        pending = s["pending"]
        failed = s["failed"]

        # ETA
        if done > 0 and pending > 0:
            avg_seconds = elapsed / done
            eta_min = (pending + s["running"]) * avg_seconds / 60
        else:
            eta_min = 0

        pct = done / total * 100 if total > 0 else 0
        total_tokens = s["input_tokens"] + s["output_tokens"]

        # Cost estimate (Mimo Pro: $1/M input, $3/M output)
        cost = s["input_tokens"] / 1e6 * 1.0 + s["output_tokens"] / 1e6 * 3.0

        return (
            f"📊 Ingest V2 Progress\n"
            f"  ✅ {done}/{total} ({pct:.1f}%)\n"
            f"  ⏳ Pending: {pending} | 🔄 Running: {s['running']}\n"
            f"  ❌ Failed: {failed} | 🚫 Rejected: {s['rejected']}\n"
            f"  💰 Tokens: {total_tokens:,} (${cost:.2f})\n"
            f"  ⏱️  Elapsed: {elapsed/60:.0f}min | ETA: {eta_min:.0f}min"
        )
