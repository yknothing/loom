#!/usr/bin/env python3
"""
curator.py — Smart digest curator for Cognitive Flywheel

Ranks articles by insight density (not just quality_score):
  1. key_insights count boosts the score (more insights = richer article)
  2. Category bonus: engineering/science/ai/hardware > opinion/news
  3. Penalty for very short summaries (likely shallow extraction)
  4. Dedup by topic similarity (tags + title), not just title

Usage:
    python scripts/curator.py                      # This week's Top 10
    python scripts/curator.py --top 5              # Top 5
    python scripts/curator.py --days 14            # Last 2 weeks
    python scripts/curator.py --days 30 --json     # Monthly JSON export
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue
from ingest.config import db_path, data_dir

DB_PATH = db_path()
DIGEST_STATE_PATH = data_dir() / "digest-state.json"

# Categories that tend to be news-heavy, not insight-rich → pull them down
NEWSY_CATEGORIES = {"opinion", "other"}
# Categories that tend to carry lasting insight → push them up
INSIGHT_CATEGORIES = {"engineering", "science", "ai", "hardware"}


def parse_date_from_filepath(filepath: str) -> Optional[date]:
    """Extract publication date from RSS filename like '2026-06-01-article-slug.md'."""
    basename = Path(filepath).name
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-", basename)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def title_similarity(a: str, b: str) -> float:
    """Detect near-duplicate titles."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def topic_similarity(a: dict, b: dict) -> float:
    """Compare two articles by tags overlap and title similarity."""
    title_sim = SequenceMatcher(
        None,
        (a.get("title_en") or a.get("title", "")).lower(),
        (b.get("title_en") or b.get("title", "")).lower(),
    ).ratio()

    tags_a = set(a.get("tags", []) or [])
    tags_b = set(b.get("tags", []) or [])
    if tags_a and tags_b:
        tag_sim = len(tags_a & tags_b) / max(len(tags_a | tags_b), 1)
    else:
        tag_sim = 0

    return max(title_sim, tag_sim)


def insight_density(article: dict) -> float:
    """Score an article by insight richness.

    Base = quality_score.
    + key_insights count × 0.05 (capped at +0.3)
    + 0.1 if category is insight-rich (engineering/science/ai/hardware)
    - 0.1 if category is newsy (opinion/other)
    - 0.15 if summary is trivially short (< 20 chars)
    """
    score = article.get("quality_score", 0)

    insights = article.get("key_insights", [])
    if isinstance(insights, str):
        try:
            insights = json.loads(insights)
        except (json.JSONDecodeError, TypeError):
            insights = []
    insight_count = len(insights)
    score += min(insight_count * 0.05, 0.3)

    cat = article.get("category", "")
    if cat in INSIGHT_CATEGORIES:
        score += 0.1
    elif cat in NEWSY_CATEGORIES:
        score -= 0.1

    summary = article.get("summary_zh", "") or ""
    if len(summary) < 20:
        score -= 0.15

    return score


def _parse_json_field(raw, default=None) -> list:
    """Parse a JSON field that may be a list, a JSON string, or None."""
    if default is None:
        default = []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


def load_digest_state() -> Optional[str]:
    """Read last digest timestamp from state file. Returns ISO string or None."""
    try:
        with open(DIGEST_STATE_PATH, "r") as f:
            data = json.load(f)
        return data.get("last_digest_ts")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def save_digest_state(timestamp_str: str) -> None:
    """Write current timestamp to digest state file."""
    DIGEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DIGEST_STATE_PATH, "w") as f:
        json.dump({"last_digest_ts": timestamp_str}, f, indent=2)
        f.write("\n")


def get_curated_articles_since(
    since_ts: str,
    min_quality: float = 0.4,
    top_n: int = 10,
    dedup_threshold: float = 0.75,
) -> list[dict]:
    """Fetch and rank articles completed after a given timestamp."""

    queue = TaskQueue(str(DB_PATH))
    conn = queue._conn

    rows = conn.execute("""
        SELECT
            t.filepath,
            t.completed_at,
            r.title_en,
            r.title_zh,
            r.summary_zh,
            r.category,
            r.tags,
            r.key_insights,
            r.sentiment,
            r.quality_score,
            r.related_topics,
            r.people,
            r.orgs
        FROM ingest_tasks t
        JOIN ingest_results r ON r.task_id = t.id
        WHERE t.status = 'done'
          AND r.quality_score >= ?
          AND t.completed_at > ?
        ORDER BY t.completed_at DESC
    """, (min_quality, since_ts))

    candidates = []

    for row in rows:
        title = row["title_en"] or row["title_zh"] or "Untitled"

        article = {
            "title": title,
            "title_zh": row["title_zh"],
            "summary": row["summary_zh"],
            "category": row["category"],
            "tags": _parse_json_field(row["tags"]),
            "key_insights": _parse_json_field(row["key_insights"]),
            "sentiment": row["sentiment"],
            "quality_score": row["quality_score"],
            "related_topics": _parse_json_field(row["related_topics"]),
            "people": _parse_json_field(row["people"]),
            "orgs": _parse_json_field(row["orgs"]),
            "pub_date": row["completed_at"][:10] if row["completed_at"] else str(date.today()),
            "filepath": row["filepath"],
        }
        candidates.append(article)

    queue.close()

    # Score by insight density
    for art in candidates:
        art["_density"] = insight_density(art)
    candidates.sort(key=lambda a: a["_density"], reverse=True)

    # Dedup by topic similarity
    selected = []
    for art in candidates:
        is_dup = False
        for existing in selected:
            if topic_similarity(art, existing) > dedup_threshold:
                is_dup = True
                break
        if not is_dup:
            selected.append(art)
        if len(selected) >= top_n:
            break

    for art in selected:
        art.pop("_density", None)

    return selected


def get_curated_articles(
    days: int = 7,
    min_quality: float = 0.4,
    top_n: int = 10,
    dedup_threshold: float = 0.75,
) -> list[dict]:
    """Fetch and rank articles by insight density."""

    queue = TaskQueue(str(DB_PATH))
    conn = queue._conn

    rows = conn.execute("""
        SELECT
            t.filepath,
            t.completed_at,
            r.title_en,
            r.title_zh,
            r.summary_zh,
            r.category,
            r.tags,
            r.key_insights,
            r.sentiment,
            r.quality_score,
            r.related_topics,
            r.people,
            r.orgs
        FROM ingest_tasks t
        JOIN ingest_results r ON r.task_id = t.id
        WHERE t.status = 'done'
          AND r.quality_score >= ?
        ORDER BY t.completed_at DESC
    """, (min_quality,))

    today = date.today()
    cutoff = today - timedelta(days=days)
    candidates = []

    for row in rows:
        pub_date = parse_date_from_filepath(row["filepath"])
        effective_date = pub_date or (
            datetime.strptime(row["completed_at"][:10], "%Y-%m-%d").date()
            if row["completed_at"] else today
        )

        if pub_date and pub_date < cutoff:
            continue
        if not pub_date and effective_date < (today - timedelta(days=max(days, 14))):
            continue

        title = row["title_en"] or row["title_zh"] or "Untitled"

        article = {
            "title": title,
            "title_zh": row["title_zh"],
            "summary": row["summary_zh"],
            "category": row["category"],
            "tags": _parse_json_field(row["tags"]),
            "key_insights": _parse_json_field(row["key_insights"]),
            "sentiment": row["sentiment"],
            "quality_score": row["quality_score"],
            "related_topics": _parse_json_field(row["related_topics"]),
            "people": _parse_json_field(row["people"]),
            "orgs": _parse_json_field(row["orgs"]),
            "pub_date": str(pub_date) if pub_date else str(effective_date),
            "filepath": row["filepath"],
        }

        candidates.append(article)

    queue.close()

    # Score by insight density
    for art in candidates:
        art["_density"] = insight_density(art)

    candidates.sort(key=lambda a: a["_density"], reverse=True)

    # Dedup by topic similarity
    selected = []
    for art in candidates:
        is_dup = False
        for existing in selected:
            if topic_similarity(art, existing) > dedup_threshold:
                is_dup = True
                break
        if not is_dup:
            selected.append(art)
        if len(selected) >= top_n:
            break

    for art in selected:
        art.pop("_density", None)

    return selected


def _format_digest(articles: list[dict], subtitle: str) -> str:
    """Format a list of articles into a human-readable digest string."""
    if not articles:
        return "📭 暂无新文章。"

    today_str = date.today().isoformat()
    lines = [f"🧠 **Cognitive Flywheel 洞见精选** ({today_str})", ""]
    lines.append(f"_{subtitle} | Top {len(articles)} | 按洞见密度排序_")
    lines.append("")

    for i, art in enumerate(articles, 1):
        score = art["quality_score"]
        score_emoji = "⭐" if score >= 0.8 else "📌" if score >= 0.6 else "📎"

        lines.append(f"**{i}. {art['title']}** {score_emoji}")
        if art.get("title_zh") and art["title_zh"] != art["title"]:
            lines.append(f"   _{art['title_zh']}_")

        insights = art.get("key_insights", [])
        if insights:
            lines.append(f"   💡 {insights[0]}")

        summary = art.get("summary", "")
        if summary:
            if len(summary) > 150:
                summary = summary[:150] + "…"
            lines.append(f"   📝 {summary}")

        meta = [
            f"质量: {score:.1%}",
            f"分类: {art.get('category', '?')}",
            f"日期: {art.get('pub_date', '?')}",
        ]
        if art.get("tags"):
            meta.append(f"🏷️ {', '.join(art['tags'][:4])}")
        lines.append(f"   {' | '.join(meta)}")
        lines.append("")

    return "\n".join(lines)


def generate_digest(days: int = 7, top_n: int = 10) -> str:
    """Generate a human-readable insight digest."""
    articles = get_curated_articles(days=days, top_n=top_n)

    if not articles:
        return f"📭 最近 {days} 天暂无高质量洞见文章。"

    return _format_digest(articles, subtitle=f"最近 {days} 天")


def main():
    parser = argparse.ArgumentParser(
        description="Insight-driven Daily Digest for Cognitive Flywheel"
    )
    parser.add_argument("--top", type=int, default=10, help="Number of articles (default: 10)")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--min-quality", type=float, default=0.4,
                        help="Minimum quality_score (default: 0.4)")
    parser.add_argument("--since-last", action="store_true",
                        help="Query articles since last digest timestamp")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.since_last:
        since_ts = load_digest_state()
        if since_ts is None:
            # No state file → fall back to days=1
            days = 1
            articles = get_curated_articles(
                days=days, min_quality=args.min_quality, top_n=args.top
            )
            if args.json:
                print(json.dumps(articles, ensure_ascii=False, indent=2))
            else:
                print(generate_digest(days=days, top_n=args.top))
            if articles:
                save_digest_state(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        else:
            if args.json:
                articles = get_curated_articles_since(
                    since_ts, min_quality=args.min_quality, top_n=args.top
                )
                print(json.dumps(articles, ensure_ascii=False, indent=2))
            else:
                articles = get_curated_articles_since(
                    since_ts, min_quality=args.min_quality, top_n=args.top
                )
                if not articles:
                    print(f"📭 自上次摘要以来暂无新文章 (since {since_ts})")
                else:
                    print(_format_digest(articles, subtitle=f"Since {since_ts}"))

            # Update state with latest completed_at from returned articles
            if articles:
                latest_ts = max(a.get("pub_date", "") for a in articles)
                # Use current time if we can't determine a better one
                save_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_digest_state(save_ts)
    else:
        if args.json:
            articles = get_curated_articles(
                days=args.days, min_quality=args.min_quality, top_n=args.top
            )
            print(json.dumps(articles, ensure_ascii=False, indent=2))
        else:
            print(generate_digest(days=args.days, top_n=args.top))


if __name__ == "__main__":
    main()
