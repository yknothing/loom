#!/usr/bin/env python3
"""
reflector.py — Post-batch kb-reflect for Cognitive Flywheel

After a batch of articles has been ingested, this module:
  1. Reads the last N completed ingest results from DB
  2. Groups by topic similarity (tag overlap)
  3. For each cluster, calls LLM to find cross-cutting themes, contradictions,
     implicit relationships, and synthesis opportunities
  4. Writes synthesis articles to wiki/ideas/synthesis-*.md
  5. Updates wiki/index.md
  6. Logs to wiki/log.md

Usage:
    python3 scripts/ingest/reflector.py --articles 20
    python3 scripts/ingest/reflector.py --since 2026-06-05
    python3 scripts/ingest/reflector.py --provider kimi
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.prompts import REFLECT_SYSTEM_PROMPT, build_reflect_prompt

ROOT = SCRIPTS_DIR.parent
DB_PATH = ROOT / "data" / "task-queue.db"
WIKI_DIR = ROOT / "wiki"
STATE_FILE = ROOT / "data" / "reflector-state.json"


def _read_api_key(provider: str = "xiaomimimo") -> str:
    import pathlib
    auth_path = pathlib.Path.home() / ".openclaw/agents/leader/agent/auth-profiles.json"
    with open(auth_path) as f:
        auth = json.load(f)
    return auth["profiles"][f"{provider}:default"]["key"]


PROVIDERS = {
    "mimo": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "max_tokens": 131072,
        "api": "openai",
        "key_profile": "xiaomimimo",
    },
    "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "model": "kimi-for-coding",
        "max_tokens": 32768,
        "api": "anthropic",
        "key_profile": "kimi",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "max_tokens": 8192,
        "api": "openai",
        "key_profile": "deepseek",
    },
}


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _parse_frontmatter(text: str) -> tuple:
    meta = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("'\"")
            return meta, parts[2].strip()
    return {}, text


def _build_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _read_state() -> dict:
    """Read reflector state (tracks last run for incremental mode)."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_run": None, "processed_task_ids": []}


def _write_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_results(db_path: str, limit: int = 20,
                  since: Optional[str] = None) -> list[dict]:
    """Fetch completed ingest results from DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if since:
        rows = conn.execute("""
            SELECT r.*, t.filepath, t.completed_at
            FROM ingest_results r
            JOIN ingest_tasks t ON r.task_id = t.id
            WHERE t.status = 'done' AND t.completed_at >= ?
            ORDER BY t.completed_at DESC
        """, (since,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT r.*, t.filepath, t.completed_at
            FROM ingest_results r
            JOIN ingest_tasks t ON r.task_id = t.id
            WHERE t.status = 'done'
            ORDER BY t.completed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        # Parse JSON fields
        for field in ("tags", "people", "orgs", "key_insights", "related_topics"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except json.JSONDecodeError:
                    d[field] = []
        results.append(d)

    conn.close()
    return results


def cluster_by_tags(articles: list[dict],
                    min_cluster_size: int = 2,
                    min_overlap: int = 2) -> list[list[dict]]:
    """
    Group articles by tag overlap.

    Two articles are in the same cluster if they share >= min_overlap tags.
    Uses union-find for transitive clustering.
    """
    n = len(articles)
    if n == 0:
        return []

    # Build tag sets
    tag_sets = []
    for art in articles:
        tags = art.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = []
        tag_sets.append(set(str(t).lower() for t in tags))

    # Union-Find
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            overlap = len(tag_sets[i] & tag_sets[j])
            if overlap >= min_overlap:
                union(i, j)

    # Group by root
    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(articles[i])

    # Only return clusters with enough articles
    return [articles for articles in clusters.values()
            if len(articles) >= min_cluster_size]


def call_reflect_llm(articles: list[dict], provider: str = "kimi",
                     timeout: int = 120) -> Optional[dict]:
    """Call LLM with reflect prompt for a cluster of articles."""
    prov = PROVIDERS.get(provider, PROVIDERS["kimi"])
    api_key = _read_api_key(prov["key_profile"])
    base_url = prov["base_url"]
    model = prov["model"]
    max_tokens = prov["max_tokens"]
    api_format = prov["api"]

    # Build article summaries for the prompt
    art_summaries = []
    for art in articles:
        summary = {
            "title": art.get("title_en") or art.get("title_zh") or "",
            "summary": art.get("summary_zh", ""),
            "tags": art.get("tags", []),
            "key_insights": art.get("key_insights", []),
        }
        art_summaries.append(summary)

    user_prompt = build_reflect_prompt(art_summaries)

    # Build request
    import urllib.request
    import urllib.error

    if api_format == "anthropic":
        url = f"{base_url}/v1/messages"
        payload = {
            "model": model,
            "system": REFLECT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
        }
    else:
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": REFLECT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        }

    # Opener for proxy bypass
    opener = None
    if "xiaomimimo" in base_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_format == "anthropic":
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
    else:
        req.add_header("Authorization", f"Bearer {api_key}")

    data = json.dumps(payload).encode("utf-8")
    _urlopen = opener.open if opener else urllib.request.urlopen

    try:
        with _urlopen(req, data, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            result_json = json.loads(raw)

        if api_format == "anthropic":
            content = "\n".join(
                b["text"] for b in result_json.get("content", [])
                if b.get("type") == "text"
            )
        else:
            content = result_json.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Clean markdown fences
        content = re.sub(r"^```(?:json)?\s*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
        content = content.strip()

        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        return parsed

    except Exception as e:
        print(f"  ⚠️ Reflect LLM call failed: {e}")
        return None


def write_synthesis_article(synthesis: dict, cluster_tags: list[str],
                            wiki_dir: Path) -> Optional[str]:
    """Write a synthesis article to wiki/ideas/synthesis-*.md. Returns relative path or None."""
    title = synthesis.get("title_zh") or synthesis.get("title", "")
    if not title:
        return None

    slug = _slugify(title)
    if not slug or slug in ("index", "log"):
        return None

    # Prefix with synthesis-
    if not slug.startswith("synthesis-"):
        slug = f"synthesis-{slug}"

    path = wiki_dir / "ideas" / f"{slug}.md"
    today = datetime.now().strftime("%Y-%m-%d")

    meta = {
        "title": title,
        "title_en": synthesis.get("title", ""),
        "type": "synthesis",
        "created": today,
        "updated": today,
        "tags": cluster_tags[:10],
    }

    body_parts = [f"# {title}\n"]

    en_title = synthesis.get("title", "")
    if en_title and en_title != title:
        body_parts.append(f"> {en_title}\n")

    abstract = synthesis.get("abstract", "")
    if abstract:
        body_parts.append(f"## 综合摘要\n\n{abstract}\n")

    sources = synthesis.get("synthesis_of", [])
    if sources:
        body_parts.append("\n## 综合来源\n")
        for src in sources:
            body_parts.append(f"- {src}")

    key_points = synthesis.get("key_points", [])
    if key_points:
        body_parts.append("\n## 核心观点\n")
        for pt in key_points:
            body_parts.append(f"- {pt}")

    if cluster_tags:
        body_parts.append(f"\n## 标签\n\n{', '.join(cluster_tags)}")

    body = "\n".join(body_parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = _build_frontmatter(meta)
    path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")

    return f"ideas/{slug}"


def rebuild_index(wiki_dir: Path):
    """Rebuild wiki/index.md."""
    index_path = wiki_dir / "index.md"
    subdirs = ["ideas", "people", "mental-models", "projects", "daily", "code"]

    pages = {}
    for subdir in subdirs:
        d = wiki_dir / subdir
        if d.exists():
            for p in d.glob("*.md"):
                rel = f"{subdir}/{p.name}"
                meta, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
                title = meta.get("title") or meta.get("name") or p.stem
                pages[rel] = title

    today = datetime.now().strftime("%Y-%m-%d")

    sections = {
        "ideas": ("🧠 概念 (ideas/)", []),
        "people": ("👤 人物 (people/)", []),
        "projects": ("🚀 项目 (projects/)", []),
        "mental-models": ("🧩 思维模型 (mental-models/)", []),
        "daily": ("📅 每日摘要 (daily/)", []),
        "code": ("💻 技术文档 (code/)", []),
    }

    for rel, title in sorted(pages.items()):
        subdir = rel.split("/")[0]
        if subdir in sections:
            sections[subdir][1].append((rel, title))

    lines = [
        "# Cognitive Flywheel — 知识索引\n",
        f"> 最后更新: {today}",
        f"> 页面总数: {len(pages)}\n",
    ]

    for subdir, (heading, entries) in sections.items():
        lines.append(f"## {heading}")
        if entries:
            for rel, title in entries:
                lines.append(f"- [[{rel}|{title}]]")
        else:
            lines.append("_暂无页面_")
        lines.append("")

    lines.append("---\n")
    lines.append("_此索引由 LLM Ingest V2 + Reflector 自动维护。_\n")

    index_path.write_text("\n".join(lines), encoding="utf-8")


def append_reflect_log(wiki_dir: Path, clusters_count: int,
                       synthesis_count: int, themes: list[str]):
    """Append reflector run to wiki/log.md."""
    log_path = wiki_dir / "log.md"
    today = datetime.now().strftime("%Y-%m-%d")
    themes_str = ", ".join(themes[:10]) if themes else "无"

    entry = (
        f"\n## [{today}] kb-reflect | 知识反思\n"
        f"集群数: {clusters_count}\n"
        f"综合文章: {synthesis_count}\n"
        f"发现主题: {themes_str}\n"
    )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def run_reflect(articles: int = 20, since: Optional[str] = None,
                provider: str = "kimi", timeout: int = 120,
                min_cluster_size: int = 2, min_tag_overlap: int = 2,
                dry_run: bool = False) -> dict:
    """
    Main reflector logic.

    Returns summary dict with clusters, synthesis_count, themes.
    """
    if not DB_PATH.exists():
        print("❌ DB not found. Run ingest first.")
        return {"error": "db_not_found"}

    # Fetch results
    results = fetch_results(str(DB_PATH), limit=articles, since=since)
    if not results:
        print("📭 No completed ingest results to reflect on.")
        return {"clusters": 0, "synthesis_count": 0, "themes": []}

    print(f"📊 Found {len(results)} completed ingest results")

    # Cluster
    clusters = cluster_by_tags(results, min_cluster_size=min_cluster_size,
                               min_overlap=min_tag_overlap)
    print(f"🔗 Formed {len(clusters)} clusters (sizes: {[len(c) for c in clusters]})")

    if not clusters:
        print("📭 No significant clusters found for reflection.")
        return {"clusters": 0, "synthesis_count": 0, "themes": []}

    all_themes = []
    synthesis_count = 0
    all_updated = []

    for i, cluster in enumerate(clusters):
        cluster_tags = set()
        for art in cluster:
            tags = art.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except json.JSONDecodeError:
                    tags = []
            cluster_tags.update(str(t).lower() for t in tags)

        cluster_names = [
            art.get("title_en") or art.get("title_zh") or f"article-{j}"
            for j, art in enumerate(cluster)
        ]
        print(f"\n🔄 Cluster {i+1}/{len(clusters)}: {len(cluster)} articles")
        print(f"   Tags: {', '.join(sorted(cluster_tags)[:10])}")
        print(f"   Articles: {', '.join(cluster_names[:5])}{'...' if len(cluster_names) > 5 else ''}")

        if dry_run:
            print(f"   (dry-run, skipping LLM call)")
            continue

        # Call LLM
        reflection = call_reflect_llm(cluster, provider=provider, timeout=timeout)
        if not reflection:
            print(f"   ⚠️ No reflection result for cluster {i+1}")
            continue

        # Extract themes
        themes = reflection.get("cross_cutting_themes", [])
        for theme in themes:
            theme_name = theme.get("theme", "")
            if theme_name:
                all_themes.append(theme_name)

        # Write synthesis articles
        for synth in reflection.get("synthesis_opportunities", []):
            rel_path = write_synthesis_article(
                synth, sorted(cluster_tags), WIKI_DIR
            )
            if rel_path:
                synthesis_count += 1
                all_updated.append(rel_path)
                print(f"   ✅ Synthesis: {rel_path}")

        # Report gaps and contradictions
        gaps = reflection.get("gaps", [])
        if gaps:
            print(f"   📋 Gaps found: {len(gaps)}")
            for gap in gaps[:3]:
                print(f"      - {gap.get('topic', '?')}")

        contradictions = reflection.get("contradictions", [])
        if contradictions:
            print(f"   ⚡ Contradictions: {len(contradictions)}")
            for c in contradictions[:3]:
                print(f"      - {c.get('topic', '?')}")

    # Update state
    state = _read_state()
    state["last_run"] = datetime.now().isoformat()
    state["last_clusters"] = len(clusters)
    state["last_synthesis_count"] = synthesis_count
    _write_state(state)

    # Update wiki
    if not dry_run:
        append_reflect_log(WIKI_DIR, len(clusters), synthesis_count, all_themes)
        rebuild_index(WIKI_DIR)
        print(f"\n📝 Wiki log + index updated")

    print(f"\n{'='*50}")
    print(f"🏁 Reflect complete")
    print(f"   Clusters: {len(clusters)}")
    print(f"   Synthesis articles: {synthesis_count}")
    print(f"   Cross-cutting themes: {len(all_themes)}")

    return {
        "clusters": len(clusters),
        "synthesis_count": synthesis_count,
        "themes": all_themes,
        "updated_pages": all_updated,
    }


def detect_duplicate_candidates(wiki_dir: Path, threshold: float = 0.8) -> list[dict]:
    """
    Scan all ideas/ pages and find pairs that might be duplicates.
    Uses concept_merger similarity logic to compare all pairs.

    Returns list of duplicate candidate dicts.
    Each candidate: {id, page_a, page_b, similarity, reason}
    Also enqueues each pair into review_queue as "duplicate_concepts" type.
    """
    from .concept_merger import (
        _title_similarity,
        _tag_jaccard,
        _combined_similarity,
        _parse_frontmatter_with_lists,
    )
    from . import review_queue

    ideas_dir = wiki_dir / "ideas"
    if not ideas_dir.exists():
        return []

    # Read all idea pages
    pages = []
    for path in sorted(ideas_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = _parse_frontmatter_with_lists(text)
        title = meta.get("title", "")
        title_en = meta.get("title_en", "")
        tags_raw = meta.get("tags", [])
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = tags_raw
        else:
            tags = []
        pages.append({
            "path": path,
            "rel": str(path.relative_to(wiki_dir)),
            "title": title,
            "title_en": title_en,
            "tags": tags,
        })

    candidates = []
    n = len(pages)

    for i in range(n):
        for j in range(i + 1, n):
            a = pages[i]
            b = pages[j]

            # Compare titles (both directions: title and title_en)
            a_title = a["title_en"] or a["title"]
            b_title = b["title_en"] or b["title"]
            title_sim = max(
                _title_similarity(a["title"], b["title"]),
                _title_similarity(a["title"], b_title) if b_title != b["title"] else 0.0,
                _title_similarity(a_title, b["title"]) if a_title != a["title"] else 0.0,
                _title_similarity(a_title, b_title) if (a_title != a["title"] or b_title != b["title"]) else 0.0,
            )
            tag_sim = _tag_jaccard(a["tags"], b["tags"])
            combined = _combined_similarity(title_sim, tag_sim)

            if combined >= threshold:
                reason = f"Title sim: {title_sim:.2f}, Tag Jaccard: {tag_sim:.2f}"
                candidate_id = f"dup_{i}_{j}"
                candidate = {
                    "id": candidate_id,
                    "page_a": a["rel"],
                    "page_b": b["rel"],
                    "similarity": round(combined, 4),
                    "reason": reason,
                }
                candidates.append(candidate)

                # Enqueue for review
                try:
                    review_queue.enqueue_review(
                        "duplicate_concepts",
                        {
                            "page_a": a["rel"],
                            "page_b": b["rel"],
                            "similarity": round(combined, 4),
                            "reason": reason,
                            "title_a": a["title"],
                            "title_b": b["title"],
                        },
                        "reflector.detect_duplicate_candidates",
                    )
                except Exception:
                    pass  # Don't fail detection if queue write fails

    # Sort by similarity descending
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates


def main():
    parser = argparse.ArgumentParser(description="Cognitive Flywheel — KB Reflector")
    parser.add_argument("--articles", type=int, default=20,
                        help="Number of recent articles to reflect on (default: 20)")
    parser.add_argument("--since", type=str, default=None,
                        help="Reflect on articles since date (YYYY-MM-DD)")
    parser.add_argument("--provider", default="kimi",
                        choices=["kimi", "mimo", "deepseek"],
                        help="LLM provider (default: kimi)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="LLM call timeout in seconds (default: 120)")
    parser.add_argument("--min-cluster", type=int, default=2,
                        help="Minimum articles per cluster (default: 2)")
    parser.add_argument("--min-overlap", type=int, default=2,
                        help="Minimum tag overlap for clustering (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without LLM calls")
    parser.add_argument("--detect-duplicates", action="store_true",
                        help="Scan ideas/ pages for potential duplicates")
    parser.add_argument("--threshold", type=float, default=0.8,
                        help="Similarity threshold for duplicate detection (default: 0.8)")
    args = parser.parse_args()

    if args.detect_duplicates:
        wiki = Path(__file__).resolve().parent.parent.parent / "wiki"
        if not wiki.exists():
            print("❌ Wiki directory not found.")
            sys.exit(1)
        candidates = detect_duplicate_candidates(wiki, threshold=args.threshold)
        if not candidates:
            print("✅ No duplicate candidates found.")
        else:
            print(f"⚠️  Found {len(candidates)} duplicate candidate(s):")
            for c in candidates:
                print(f"   {c['page_a']} ↔ {c['page_b']}  (sim={c['similarity']}, {c['reason']})")
        sys.exit(0)

    run_reflect(
        articles=args.articles,
        since=args.since,
        provider=args.provider,
        timeout=args.timeout,
        min_cluster_size=args.min_cluster,
        min_tag_overlap=args.min_overlap,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
