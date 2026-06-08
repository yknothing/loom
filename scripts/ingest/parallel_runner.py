#!/usr/bin/env python3
"""
parallel_runner.py — Multi-worker ingest runner (optional).

⚠️  DEFAULT: workers=1 (serial). Increase only when rate limits allow.
Each worker claims one task at a time from the shared SQLite queue.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue
from ingest.error_logger import log_failure
from ingest.config import db_path

DB_PATH = db_path()

_print_lock = Lock()
_claim_lock = Lock()


def work_one_task(
    base_url: str,
    model: str,
    timeout: int,
    max_retries: int,
    provider: str = "mimo",
) -> dict:
    """Process a single task. Called from worker threads."""
    from ingest.worker import call_llm
    from ingest.wiki_writer import write_ingest_result, append_log

    queue = TaskQueue(str(DB_PATH))

    try:
        with _claim_lock:
            task = queue.claim_next()
        if not task:
            queue.close()
            return {"claimed": False}

        filepath = task["filepath"]
        result = call_llm(
            filepath=filepath,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            provider=provider,
        )

        if result["success"]:
            queue.complete_task(
                task_id=task["id"],
                result=result["result"],
                model=model,
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
            )
            updated_pages = write_ingest_result(result["result"])
            append_log(
                source=filepath,
                title=result["result"].get("title_en", ""),
                updated_pages=updated_pages,
                tokens_in=result["input_tokens"],
                tokens_out=result["output_tokens"],
                model=model,
            )
            title = result["result"].get("title_en", Path(filepath).stem)[:60]
            return {
                "claimed": True, "success": True,
                "title": title,
                "latency": result["latency_ms"],
                "tokens": f"{result['input_tokens']}+{result['output_tokens']}",
            }
        else:
            queue.fail_task(task_id=task["id"], error=result["error"])
            log_failure(
                filepath=filepath,
                error=result["error"],
                error_log=result.get("error_log", []),
                title=Path(filepath).name[:60],
            )
            return {
                "claimed": True, "success": False,
                "title": Path(filepath).name[:60],
                "error": result["error"][:120],
                "error_log": result.get("error_log", []),
            }
    finally:
        queue.close()


def main():
    parser = argparse.ArgumentParser(description="Parallel LLM Ingest Runner")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers (default: 1 = serial)")
    parser.add_argument("--model", default="mimo-v2.5-pro")
    parser.add_argument("--base-url",
                        default="https://token-plan-sgp.xiaomimimo.com/v1")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--rpm", type=int, default=15)
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Delay between requests in seconds (serial mode)")
    parser.add_argument("--provider", default="mimo",
                        choices=["mimo", "kimi", "deepseek"],
                        help="LLM provider (default: mimo)")
    args = parser.parse_args()

    # Set defaults per provider
    if args.provider == "kimi":
        args.model = args.model if args.model != "mimo-v2.5-pro" else "kimi-for-coding"
        args.base_url = args.base_url if args.base_url != "https://token-plan-sgp.xiaomimimo.com/v1" else "https://api.kimi.com/coding"
    elif args.provider == "deepseek":
        args.model = args.model if args.model != "mimo-v2.5-pro" else "deepseek-chat"
        args.base_url = args.base_url if args.base_url != "https://token-plan-sgp.xiaomimimo.com/v1" else "https://api.deepseek.com/v1"

    queue = TaskQueue(str(DB_PATH))
    queue.reset_stuck_tasks()
    stats = queue.stats()
    pending = stats["pending"]

    if pending == 0:
        print("✅ No pending tasks.")
        queue.close()
        return

    print(f"🧠 Ingest Runner (workers={args.workers})")
    print(f"   Provider: {args.provider} | Model: {args.model} | Pending: {pending} | RPM limit: {args.rpm}")
    print()

    start_time = time.time()
    initial_done = stats["done"]
    initial_failed = stats["failed"]
    done_count = 0
    fail_count = 0
    max_articles = args.max if args.max > 0 else pending

    if args.workers == 1:
        # --- Serial mode with delay ---
        from ingest.rate_limiter import RateLimiter
        rate_limiter = RateLimiter(rpm=args.rpm, tpm=10_000_000)

        for i in range(max_articles):
            rate_limiter.acquire_sync(estimated_tokens=2000)
            result = work_one_task(
                args.base_url, args.model, args.timeout, args.max_retries,
                args.provider,
            )

            if not result.get("claimed"):
                break

            if result["success"]:
                done_count += 1
                print(f"  ✅ [{initial_done + done_count}] {result['title']} "
                      f"({result['latency']:.0f}ms, {result['tokens']}tok)")
            else:
                fail_count += 1
                print(f"  ❌ [{initial_done + done_count + fail_count}] "
                      f"{result['title']}")
                print(f"      → {result.get('error', '?')[:120]}")

            if done_count + fail_count >= max_articles:
                break

            if args.delay > 0:
                time.sleep(args.delay)
    else:
        # --- Parallel mode ---
        from ingest.rate_limiter import RateLimiter
        rate_limiter = RateLimiter(rpm=args.rpm, tpm=10_000_000)

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            submitted = 0

            # Initial submit
            batch = min(args.workers * 2, max_articles)
            while submitted < batch:
                rate_limiter.acquire_sync(estimated_tokens=2000)
                fut = executor.submit(
                    work_one_task,
                    args.base_url, args.model, args.timeout, args.max_retries,
                    args.provider,
                )
                futures[fut] = submitted
                submitted += 1

            while futures:
                for fut in as_completed(futures):
                    try:
                        result = fut.result()
                    except Exception as e:
                        fail_count += 1
                        print(f"  💥 Worker crashed: {e}")
                        del futures[fut]
                        continue

                    del futures[fut]

                    if not result.get("claimed"):
                        continue

                    if result["success"]:
                        done_count += 1
                        with _print_lock:
                            print(f"  ✅ [{initial_done + done_count}] "
                                  f"{result['title']} ({result['latency']:.0f}ms)")
                    else:
                        fail_count += 1
                        with _print_lock:
                            print(f"  ❌ {result['title']}: "
                                  f"{result.get('error', '?')[:100]}")

                    if done_count + fail_count >= max_articles:
                        break

                    # Submit next
                    rate_limiter.acquire_sync(estimated_tokens=2000)
                    new_fut = executor.submit(
                        work_one_task,
                        args.base_url, args.model, args.timeout, args.max_retries,
                        args.provider,
                    )
                    futures[new_fut] = submitted
                    submitted += 1

                if done_count + fail_count >= max_articles:
                    break

    elapsed = time.time() - start_time
    rate = (done_count + fail_count) / (elapsed / 60) if elapsed > 60 else 0
    print(f"\n🏁 Done: {done_count} | Failed: {fail_count} | "
          f"Elapsed: {elapsed/60:.1f}min | Rate: {rate:.1f}/min")

    if fail_count > 0:
        print(f"⚠️  {fail_count} failures logged. Run --errors for details.")

    from ingest.wiki_writer import rebuild_index
    rebuild_index()
    queue.close()
    print("✅ Complete.")


if __name__ == "__main__":
    main()
