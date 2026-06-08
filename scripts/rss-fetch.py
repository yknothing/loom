#!/usr/bin/env python3
"""
RSS Fetcher for Cognitive Flywheel
Fetches articles from configured RSS/Atom feeds and saves as markdown.
"""

import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ssl

import feedparser
import urllib.request
import yaml
from http.client import IncompleteRead
from socket import timeout as SocketTimeout
from urllib.error import URLError, HTTPError


# Module-level timeout, set from CLI
_timeout = 30


def load_config(config_path: str) -> dict:
    """Load RSS feeds configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def url_hash(url: str) -> str:
    """Generate a short hash from a URL for dedup."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def get_existing_hashes(raw_dir: Path) -> set:
    """Scan raw/rss/ for existing files and collect URL hashes from frontmatter."""
    hashes = set()
    if not raw_dir.exists():
        return hashes
    for f in raw_dir.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            # Extract url from frontmatter
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    frontmatter = content[3:end]
                    for line in frontmatter.strip().split("\n"):
                        if line.startswith("url_hash:"):
                            hashes.add(line.split(":", 1)[1].strip())
        except Exception:
            continue
    return hashes


def extract_content(entry) -> str:
    """Extract the best available content from a feed entry."""
    # Try content fields in order of preference
    for field in ["content", "summary_detail", "summary"]:
        value = getattr(entry, field, None)
        if value:
            if isinstance(value, list) and value:
                return value[0].get("value", "")
            elif isinstance(value, dict):
                return value.get("value", "")
            elif isinstance(value, str):
                return value
    return ""


def strip_html(html: str) -> str:
    """Basic HTML to text conversion."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", html)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def fetch_feed(feed_config: dict, existing_hashes: set, raw_dir: Path, dry_run: bool = False) -> dict:
    """Fetch and process a single feed."""
    name = feed_config["name"]
    url = feed_config["url"]
    category = feed_config.get("category", "general")
    priority = feed_config.get("priority", "medium")

    stats = {"name": name, "new": 0, "existing": 0, "errors": []}

    max_retries = 3
    retry_delay = 2  # seconds
    feed = None
    timeout_secs = _timeout  # module-level, set from CLI

    headers = {
        "User-Agent": "CognitiveFlywheel/1.0 (+https://github.com/cognitive-flywheel)",
        "Accept": "application/rss+xml, application/atom+xml, text/xml, application/xml, */*;q=0.1",
    }

    # Build SSL contexts: first try verified, then fallback to unverified
    ssl_contexts = [ssl.create_default_context()]
    # Only add unverified context as fallback
    try:
        ctx_unverified = ssl.create_default_context()
        ctx_unverified.check_hostname = False
        ctx_unverified.verify_mode = ssl.CERT_NONE
        ssl_contexts.append(ctx_unverified)
    except Exception:
        pass

    for attempt in range(1, max_retries + 1):
        ssl_ctx = ssl_contexts[min(attempt - 1, len(ssl_contexts) - 1)]
        try:
            request = urllib.request.Request(url, headers=headers)
            response = urllib.request.urlopen(request, timeout=timeout_secs, context=ssl_ctx)
            raw_data = response.read()
            feed = feedparser.parse(raw_data)
            break
        except HTTPError as e:
            # Don't retry 404s — feed is gone
            if e.code == 404:
                stats["errors"].append(f"Feed returned 404 (feed may be discontinued): {e}")
                return stats
            if attempt < max_retries:
                print(f"  ⚠ Attempt {attempt}/{max_retries} HTTP {e.code} for {name}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                stats["errors"].append(f"Failed after {max_retries} attempts: HTTP {e.code}")
                return stats
        except (IncompleteRead, ConnectionError, SocketTimeout, URLError, OSError) as e:
            if attempt < max_retries:
                print(f"  ⚠ Attempt {attempt}/{max_retries} failed for {name}: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                # Last resort: try feedparser.parse(url) directly (uses its own HTTP)
                print(f"  ⚠ urllib failed for {name}. Trying feedparser direct...")
                try:
                    feed = feedparser.parse(url)
                    if feed.entries:
                        break
                    else:
                        stats["errors"].append(f"Failed after {max_retries} attempts + feedparser fallback: {e}")
                        return stats
                except Exception as fb_err:
                    stats["errors"].append(f"Failed after {max_retries} attempts + fallback: {e} / {fb_err}")
                    return stats
        except Exception as e:
            stats["errors"].append(f"Unexpected error: {e}")
            return stats

    if feed is None:
        stats["errors"].append("No feed data retrieved")
        return stats

    # Log bozo warnings but don't skip feeds that still have entries
    if feed.bozo:
        print(f"  ⚠ Parse warning for {name}: {feed.bozo_exception}")
        if not feed.entries:
            stats["errors"].append(f"Parse error (no entries): {feed.bozo_exception}")
            return stats

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for entry in feed.entries:
        entry_url = getattr(entry, "link", None)
        if not entry_url:
            continue

        h = url_hash(entry_url)

        if h in existing_hashes:
            stats["existing"] += 1
            continue

        title = getattr(entry, "title", "Untitled")
        published = getattr(entry, "published", today)
        # Try to extract date from entry
        date_str = today
        published_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if published_parsed:
            try:
                date_str = time.strftime("%Y-%m-%d", published_parsed)
            except Exception:
                pass
        elif isinstance(published, str) and len(published) >= 10:
            date_str = published[:10]

        content = extract_content(entry)
        text_content = strip_html(content) if content else title

        slug = slugify(title)
        filename = f"{date_str}-{slug}.md"

        if dry_run:
            print(f"  [NEW] {title}")
            print(f"        {entry_url}")
            stats["new"] += 1
        else:
            filepath = raw_dir / filename
            # Avoid overwriting
            counter = 1
            while filepath.exists():
                filepath = raw_dir / f"{date_str}-{slug}-{counter}.md"
                counter += 1

            md_content = f"""---
source: {name}
url: {entry_url}
url_hash: {h}
date: {date_str}
fetched: {today}
category: {category}
priority: {priority}
---

# {title}

{text_content}
"""
            filepath.write_text(md_content, encoding="utf-8")
            stats["new"] += 1
            existing_hashes.add(h)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Fetch RSS feeds for Cognitive Flywheel")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched without saving")
    parser.add_argument("--feed", type=str, help="Only fetch a specific feed by name (case-insensitive substring match)")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    parser.add_argument("--raw-dir", type=str, default=None, help="Path to raw/rss directory")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout per request in seconds (default: 30)")
    args = parser.parse_args()

    # Resolve paths relative to script location
    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parent

    config_path = Path(args.config) if args.config else project_dir / "config" / "rss-feeds.yml"
    raw_dir = Path(args.raw_dir) if args.raw_dir else project_dir / "raw" / "rss"

    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config(str(config_path))
    feeds = config.get("feeds", [])

    if not feeds:
        print("No feeds found in config.", file=sys.stderr)
        sys.exit(1)

    # Filter by name if --feed specified
    if args.feed:
        feeds = [f for f in feeds if args.feed.lower() in f["name"].lower()]
        if not feeds:
            print(f"No feeds matching '{args.feed}' found.", file=sys.stderr)
            sys.exit(1)

    global _timeout
    _timeout = args.timeout

    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print("=== DRY RUN ===\n")

    existing_hashes = get_existing_hashes(raw_dir)
    print(f"Found {len(existing_hashes)} existing articles in {raw_dir}\n")

    total_feeds = len(feeds)
    total_new = 0
    total_existing = 0
    total_errors = 0

    for i, feed_config in enumerate(feeds, 1):
        name = feed_config["name"]
        url = feed_config["url"]
        print(f"[{i}/{total_feeds}] {name} ({url})")

        stats = fetch_feed(feed_config, existing_hashes, raw_dir, dry_run=args.dry_run)

        total_new += stats["new"]
        total_existing += stats["existing"]

        if stats["errors"]:
            for err in stats["errors"]:
                print(f"  ⚠ {err}")
                total_errors += 1

        print(f"  → {stats['new']} new, {stats['existing']} existing")

    print(f"\n{'='*40}")
    print(f"Feeds checked: {total_feeds}")
    print(f"New articles:  {total_new}")
    print(f"Already known: {total_existing}")
    print(f"Errors:        {total_errors}")

    if args.dry_run:
        print(f"\n(Dry run - no files written)")


if __name__ == "__main__":
    main()
