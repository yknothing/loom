#!/usr/bin/env python3
"""
daily-digest.py — Generate weekly/monthly digest for Cognitive Flywheel.

Scans wiki/log.md for recent activity and generates a summary page.

Usage:
    python scripts/daily-digest.py
    python scripts/daily-digest.py --week 2026-W22
    python scripts/daily-digest.py --month 2026-05
"""

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, date, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.config import wiki_dir

WIKI_DIR = wiki_dir()
LOG_PATH = WIKI_DIR / "log.md"
DAILY_DIR = WIKI_DIR / "daily"


def get_current_week() -> str:
    """Return current ISO week string like '2026-W22'."""
    return date.today().strftime("%G-W%V")


def get_current_month() -> str:
    """Return current month string like '2026-05'."""
    return date.today().strftime("%Y-%m")


def parse_log_entries(since: date | None = None) -> list[dict]:
    """Parse log.md entries, optionally filtering by date."""
    if not LOG_PATH.exists():
        return []

    text = LOG_PATH.read_text(encoding="utf-8")
    entries = []

    # Split by entry headers: ## [YYYY-MM-DD] action | title
    parts = re.split(r"(?=^## \[)", text, flags=re.MULTILINE)

    for part in parts:
        m = re.match(r"## \[(\d{4}-\d{2}-\d{2})\]\s+(\w+)\s*\|\s*(.+)", part)
        if not m:
            continue

        entry_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        if since and entry_date < since:
            continue

        action = m.group(2)
        title = m.group(3).strip()

        # Extract source and updated pages
        source = ""
        updated = []
        for line in part.splitlines():
            sm = re.match(r"来源:\s*(.+)", line)
            if sm:
                source = sm.group(1).strip()
            um = re.match(r"更新页面:\s*(.+)", line)
            if um:
                updated = [p.strip() for p in um.group(1).split(",")]

        entries.append({
            "date": m.group(1),
            "action": action,
            "title": title,
            "source": source,
            "updated": updated,
        })

    return entries


def scan_wiki_pages() -> dict[str, dict]:
    """Quick scan of wiki pages for stats."""
    pages = {}
    for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
        d = WIKI_DIR / subdir
        if d.exists():
            for p in d.glob("*.md"):
                pages[f"{subdir}/{p.name}"] = {"path": p, "stem": p.stem}
    return pages


def generate_weekly_digest(week_str: str) -> str:
    """Generate a weekly digest page."""
    # Parse week to get date range
    # week_str format: 2026-W22
    parts = week_str.split("-W")
    year = int(parts[0])
    week = int(parts[1])

    # ISO week start (Monday)
    jan4 = date(year, 1, 4)
    week_start = jan4 + timedelta(weeks=week - 1, days=-jan4.weekday())
    week_end = week_start + timedelta(days=6)

    entries = parse_log_entries(since=week_start)
    pages = scan_wiki_pages()

    # Collect stats
    new_ideas = [e for e in entries if any("ideas/" in p for p in e.get("updated", []))]
    new_people = [e for e in entries if any("people/" in p for p in e.get("updated", []))]
    all_updated_pages = set()
    for e in entries:
        all_updated_pages.update(e.get("updated", []))

    # Build digest
    today = date.today().isoformat()
    lines = [
        f"---",
        f"period: {week_str}",
        f"type: weekly",
        f"generated: {today}",
        f"sources_count: {len(entries)}",
        f"---",
        f"",
        f"# Weekly Digest: {week_str}",
        f"",
        f"**Period**: {week_start.isoformat()} — {week_end.isoformat()}",
        f"",
        f"## 📊 统计",
        f"",
        f"- 处理条目: {len(entries)}",
        f"- 更新页面: {len(all_updated_pages)}",
        f"- 新概念页: {len(new_ideas)}",
        f"- 人物页更新: {len(new_people)}",
        f"- Wiki 总页面: {len(pages)}",
        f"",
    ]

    # This week's highlights
    if entries:
        lines.append("## 📰 本周精华")
        lines.append("")
        for e in entries:
            lines.append(f"- **{e['title']}** ({e['date']})")
            if e.get("updated"):
                lines.append(f"  → 更新: {', '.join(e['updated'][:5])}")
        lines.append("")

    # New discoveries
    ideas_pages = [p for p in all_updated_pages if p.startswith("ideas/")]
    people_pages = [p for p in all_updated_pages if p.startswith("people/")]

    if ideas_pages or people_pages:
        lines.append("## 🔍 新发现")
        lines.append("")
        if ideas_pages:
            lines.append("### 新概念")
            for p in sorted(ideas_pages):
                stem = p.split("/", 1)[1].replace(".md", "")
                lines.append(f"- [[{p}|{stem.replace('-', ' ').title()}]]")
            lines.append("")
        if people_pages:
            lines.append("### 人物动态")
            for p in sorted(people_pages):
                stem = p.split("/", 1)[1].replace(".md", "")
                lines.append(f"- [[{p}|{stem.replace('-', ' ').title()}]]")
            lines.append("")

    # Contradictions / tensions (placeholder for V2)
    lines.append("## ⚡ 矛盾/张力")
    lines.append("")
    lines.append("_（V2 将自动检测不同观点之间的矛盾）_")
    lines.append("")

    # Next week focus
    lines.append("## 🎯 下周关注")
    lines.append("")
    lines.append("_（根据本周内容自动建议）_")
    lines.append("")

    return "\n".join(lines)


def generate_monthly_digest(month_str: str) -> str:
    """Generate a monthly digest page."""
    # Parse month_str ("2026-05") to date range
    year, month = int(month_str.split("-")[0]), int(month_str.split("-")[1])
    month_start = date(year, month, 1)
    # Last day of month
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    entries = parse_log_entries(since=month_start)
    # Filter to only entries within the month
    entries = [
        e for e in entries
        if datetime.strptime(e["date"], "%Y-%m-%d").date() <= month_end
    ]

    pages = scan_wiki_pages()

    # Stats
    all_updated_pages = set()
    for e in entries:
        all_updated_pages.update(e.get("updated", []))
    new_ideas = [e for e in entries if any("ideas/" in p for p in e.get("updated", []))]
    new_people = [e for e in entries if any("people/" in p for p in e.get("updated", []))]

    # Group entries by ISO week
    weeks: dict[str, list] = {}
    for e in entries:
        ed = datetime.strptime(e["date"], "%Y-%m-%d").date()
        week_label = ed.strftime("%G-W%V")
        weeks.setdefault(week_label, []).append(e)

    # Top Topics: count keyword frequency from page stems in updated pages
    topic_counter: Counter[str] = Counter()
    for e in entries:
        # Count from title words (skip short/common)
        words = re.findall(r"[\w]{3,}", e["title"].lower())
        stop = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "how", "why", "what", "not", "but", "can", "has", "its", "our"}
        topic_counter.update(w for w in words if w not in stop)
        # Count from updated page stems
        for p in e.get("updated", []):
            stem = p.split("/", 1)[-1].replace(".md", "").replace("-", " ")
            topic_counter.update(stem.split())
    top_topics = topic_counter.most_common(10)

    # Active People: count mentions of people pages
    people_counter: Counter[str] = Counter()
    for e in entries:
        for p in e.get("updated", []):
            if p.startswith("people/"):
                name = p.split("/", 1)[1].replace(".md", "").replace("-", " ").title()
                people_counter[name] += 1
    top_people = people_counter.most_common(5)

    # Trend: compare with previous month
    prev_month_str = ""
    if month == 1:
        prev_month_str = f"{year - 1}-12"
    else:
        prev_month_str = f"{year}-{month - 1:02d}"

    prev_start = date.fromisoformat(prev_month_str + "-01")
    if prev_start.month == 12:
        prev_end = date(prev_start.year + 1, 1, 1) - timedelta(days=1)
    else:
        prev_end = date(prev_start.year, prev_start.month + 1, 1) - timedelta(days=1)
    prev_entries = parse_log_entries(since=prev_start)
    prev_entries = [
        e for e in prev_entries
        if datetime.strptime(e["date"], "%Y-%m-%d").date() <= prev_end
    ]

    prev_topic_counter: Counter[str] = Counter()
    for e in prev_entries:
        words = re.findall(r"[\w]{3,}", e["title"].lower())
        stop = {"the", "and", "for", "with", "this", "that", "from", "are", "was", "how", "why", "what", "not", "but", "can", "has", "its", "our"}
        prev_topic_counter.update(w for w in words if w not in stop)
        for p in e.get("updated", []):
            stem = p.split("/", 1)[-1].replace(".md", "").replace("-", " ")
            prev_topic_counter.update(stem.split())

    today = date.today().isoformat()
    lines = [
        "---",
        f"period: {month_str}",
        "type: monthly",
        f"generated: {today}",
        f"sources_count: {len(entries)}",
        "---",
        "",
        f"# Monthly Digest: {month_str}",
        "",
        f"**Period**: {month_start.isoformat()} — {month_end.isoformat()}",
        "",
        "## 📊 月度统计",
        "",
        f"- 处理条目: {len(entries)}",
        f"- 更新页面: {len(all_updated_pages)}",
        f"- 新概念页: {len(new_ideas)}",
        f"- 人物页更新: {len(new_people)}",
        f"- Wiki 总页面: {len(pages)}",
        "",
    ]

    # Monthly highlights grouped by week
    lines.append("## 📰 月度精华")
    lines.append("")
    if weeks:
        for wk in sorted(weeks.keys()):
            lines.append(f"### {wk}")
            for e in weeks[wk]:
                lines.append(f"- **{e['title']}** ({e['date']})")
                if e.get("updated"):
                    lines.append(f"  → 更新: {', '.join(e['updated'][:5])}")
            lines.append("")

    # Top Topics
    lines.append("## 🔥 Top Topics")
    lines.append("")
    if top_topics:
        for topic, count in top_topics:
            trend = ""
            prev_count = prev_topic_counter.get(topic, 0)
            if prev_count > 0:
                diff = count - prev_count
                if diff > 0:
                    trend = f" ↑{diff}"
                elif diff < 0:
                    trend = f" ↓{abs(diff)}"
                else:
                    trend = " →"
            lines.append(f"- **{topic}**: {count}{trend}")
    else:
        lines.append("_（本月暂无热门话题）_")
    lines.append("")

    # Active People
    lines.append("## 👤 活跃人物")
    lines.append("")
    if top_people:
        for name, count in top_people:
            lines.append(f"- **{name}**: {count} 次")
    else:
        lines.append("_（本月暂无活跃人物）_")
    lines.append("")

    # New discoveries
    ideas_pages = sorted(p for p in all_updated_pages if p.startswith("ideas/"))
    people_pages = sorted(p for p in all_updated_pages if p.startswith("people/"))
    lines.append("## 🔍 新发现")
    lines.append("")
    if ideas_pages:
        for p in ideas_pages:
            stem = p.split("/", 1)[1].replace(".md", "")
            lines.append(f"- [[{p}|{stem.replace('-', ' ').title()}]]")
    if people_pages:
        for p in people_pages:
            stem = p.split("/", 1)[1].replace(".md", "")
            lines.append(f"- [[{p}|{stem.replace('-', ' ').title()}]]")
    if not ideas_pages and not people_pages:
        lines.append("_（本月暂无新发现）_")
    lines.append("")

    # Contradictions placeholder
    lines.append("## ⚡ 矛盾/张力")
    lines.append("")
    lines.append("_（由 contradiction-detect.py 自动检测）_")
    lines.append("")

    # Trends
    lines.append("## 📈 趋势")
    lines.append("")
    if prev_entries:
        delta = len(entries) - len(prev_entries)
        direction = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        lines.append(f"- 条目数: {len(entries)} ({direction}{abs(delta)} vs 上月 {len(prev_entries)})")
        growing = []
        for topic, count in top_topics[:5]:
            prev_c = prev_topic_counter.get(topic, 0)
            if prev_c > 0 and count > prev_c:
                growing.append(f"{topic} ({prev_c}→{count})")
            elif prev_c == 0 and count >= 2:
                growing.append(f"{topic} (新)")
        if growing:
            lines.append(f"- 上升话题: {', '.join(growing)}")
    else:
        lines.append(f"- 本月条目: {len(entries)}（上月无数据）")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate Cognitive Flywheel digest")
    parser.add_argument("--week", help="Week string (e.g., 2026-W22). Default: current week")
    parser.add_argument("--month", help="Month string (e.g., 2026-05). Not yet implemented.")
    args = parser.parse_args()

    if args.month:
        month_str = args.month
        print(f"Generating monthly digest for {month_str}...")

        content = generate_monthly_digest(month_str)

        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DAILY_DIR / f"{month_str}-monthly.md"
        output_path.write_text(content, encoding="utf-8")

        print(f"✅ Written to wiki/daily/{month_str}-monthly.md")

        # Append to log
        today = date.today().isoformat()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n## [{today}] digest | Monthly digest {month_str}\n")
            f.write(f"生成: wiki/daily/{month_str}-monthly.md\n")
        return

    week_str = args.week or get_current_week()
    print(f"Generating weekly digest for {week_str}...")

    content = generate_weekly_digest(week_str)

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DAILY_DIR / f"{week_str}.md"
    output_path.write_text(content, encoding="utf-8")

    print(f"✅ Written to wiki/daily/{week_str}.md")

    # Append to log
    today = date.today().isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n## [{today}] digest | Weekly digest {week_str}\n")
        f.write(f"生成: wiki/daily/{week_str}.md\n")


if __name__ == "__main__":
    main()
