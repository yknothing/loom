#!/usr/bin/env python3
"""
llm-ingest-v2.py — Serial LLM Deep Ingest Pipeline (V3)

Design choices:
  - Serial execution (no parallelism) to avoid rate limits
  - Configurable delay between requests (default: 3s)
  - Structured error logging to data/error-log.jsonl
  - Error-type-aware retry in worker (see worker.py)

Usage:
    python3 scripts/llm-ingest-v2.py --resume --delay 3
    python3 scripts/llm-ingest-v2.py --resume --max 10 --delay 5
    python3 scripts/llm-ingest-v2.py --status
    python3 scripts/llm-ingest-v2.py --errors
"""

import argparse
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue
from ingest.worker import call_llm
from ingest.wiki_writer import write_ingest_result, append_log
from ingest.error_logger import log_failure, summarize_errors

ROOT = SCRIPTS_DIR.parent
DB_PATH = ROOT / "data" / "task-queue.db"


def main():
    parser = argparse.ArgumentParser(description="Serial LLM Deep Ingest V3")
    parser.add_argument("--resume", action="store_true", help="Resume from queue")
    parser.add_argument("--init", action="store_true", help="Init queue with all raw files")
    parser.add_argument("--incremental", action="store_true", help="Only new files")
    parser.add_argument("--status", action="store_true", help="Show progress")
    parser.add_argument("--errors", action="store_true", help="Show error summary")
    parser.add_argument("--model", default="",
                        help="Model override (default: auto from provider)")
    parser.add_argument("--base-url", default="",
                        help="Base URL override (default: auto from provider)")
    parser.add_argument("--provider", default="kimi",
                        choices=["kimi", "mimo", "deepseek"],
                        help="LLM provider (default: kimi)")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max", type=int, default=0, help="Max articles (0=all)")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Seconds between requests (default: 3)")
    parser.add_argument("--two-stage", action="store_true", default=True,
                        help="Enable two-stage compile+reflect mode (default: True)")
    parser.add_argument("--single", action="store_true",
                        help="Force single-shot mode (backward compat)")
    args = parser.parse_args()

    queue = TaskQueue(str(DB_PATH))

    # --- Status ---
    if args.status:
        s = queue.stats()
        total_tokens = s["input_tokens"] + s["output_tokens"]
        cost = s["input_tokens"] / 1e6 * 0.435 + s["output_tokens"] / 1e6 * 0.87
        print(f"📊 Ingest V3 Status")
        print(f"  ✅ Done: {s['done']}")
        print(f"  ⏳ Pending: {s['pending']}")
        print(f"  ❌ Failed: {s['failed']}")
        print(f"  🚫 Rejected: {s['rejected']}")
        print(f"  💰 Tokens: {total_tokens:,} (${cost:.3f})")
        queue.close()
        return

    # --- Error summary ---
    if args.errors:
        print(summarize_errors())
        queue.close()
        return

    # --- Init ---
    if args.init or args.incremental:
        from ingest.task_queue import TaskQueue as _Q
        files = []
        for subdir in ("rss", "papers", "web", "code", "journal"):
            d = ROOT / "raw" / subdir
            if d.exists():
                for p in sorted(d.glob("*.md")):
                    files.append(str(p))

        added, skipped = queue.init_queue(files)
        print(f"📁 Queue: {added} added, {skipped} existed")

    # --- Reset stuck tasks ---
    queue.reset_stuck_tasks()

    stats = queue.stats()
    pending = stats["pending"]
    if pending == 0:
        print("✅ No pending tasks.")
        queue.close()
        return

    if not (args.init or args.resume or args.incremental):
        print("Use --init, --resume, or --incremental. See --help.")
        queue.close()
        return

    start_time = time.time()
    processed = 0
    total_done = stats["done"]
    total_failed = stats["failed"]
    max_articles = args.max if args.max > 0 else pending
    two_stage_flag = not getattr(args, 'single', False)

    print(f"\n🧠 Serial LLM Ingest V3")
    print(f"   Provider: {args.provider} | Model: {args.model or '(auto)'}")
    print(f"   Pending: {pending} | Already done: {total_done}")
    print(f"   Max this run: {max_articles}")
    print(f"   Delay between requests: {args.delay}s")
    print(f"   Max retries per article: {args.max_retries}")
    print()

    while processed < max_articles:
        task = queue.claim_next()
        if not task:
            print("✅ Queue empty.")
            break

        filepath = task["filepath"]
        result = call_llm(
            filepath=filepath,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            max_retries=args.max_retries,
            provider=args.provider,
            two_stage=two_stage_flag,
        )

        if result["success"]:
            stage = result.get("stage", "single")
            segment_count = result.get("segment_count", 1)
            segments_json = result.get("segments_json", None)
            merge_action = result.get("merge_action", None)

            queue.complete_task(
                task_id=task["id"],
                result=result["result"],
                model=args.model,
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                stage=stage,
                merge_action=merge_action,
                segment_count=segment_count,
                segments_json=segments_json,
            )

            updated_pages = write_ingest_result(result["result"])
            append_log(
                source=filepath,
                title=result["result"].get("title_en", ""),
                updated_pages=updated_pages,
                tokens_in=result["input_tokens"],
                tokens_out=result["output_tokens"],
                model=args.model,
            )

            total_done += 1
            title = result["result"].get("title_en", Path(filepath).stem)[:60]
            print(f"  ✅ [{total_done}] {title} "
                  f"({result['latency_ms']:.0f}ms, "
                  f"{result['input_tokens']}+{result['output_tokens']}tok)")
        else:
            queue.fail_task(task_id=task["id"], error=result["error"])
            total_failed += 1
            fname = Path(filepath).name[:60]

            # Structured error log
            log_failure(
                filepath=filepath,
                error=result["error"],
                error_log=result.get("error_log", []),
                title=fname,
            )

            print(f"  ❌ [{total_done + total_failed}] {fname}")
            print(f"      → {result['error'][:120]}")
            attempts = len(result.get("error_log", []))
            if attempts > 1:
                print(f"      → {attempts} attempts, "
                      f"last phase: {result['error_log'][-1].get('phase', '?')}")

        processed += 1

        # Progress every batch
        if processed % args.batch_size == 0:
            elapsed = time.time() - start_time
            rate = processed / (elapsed / 60) if elapsed > 60 else 0
            print(f"\n  📊 Batch: {processed} done this run | "
                  f"Rate: {rate:.1f}/min | "
                  f"Failed: {total_failed}\n")

        # Rate limiting delay
        if processed < max_articles:
            time.sleep(args.delay)

    # --- Final ---
    elapsed = time.time() - start_time
    rate = processed / (elapsed / 60) if elapsed > 60 else 0
    print(f"\n{'='*60}")
    print(f"🏁 Session complete")
    print(f"   Processed: {processed} | Done: {total_done} | Failed: {total_failed}")
    print(f"   Elapsed: {elapsed/60:.1f}min | Rate: {rate:.1f}/min")

    if total_failed > 0:
        print(f"\n   ⚠️  {total_failed} failures. Run --errors for details.")

    from ingest.wiki_writer import rebuild_index
    rebuild_index()
    print("   🔄 Wiki index rebuilt")

    queue.close()


if __name__ == "__main__":
    main()
