#!/usr/bin/env python3
"""
wiki_writer.py — Write LLM ingest results into Cognitive Flywheel wiki
"""

import json
import re
from pathlib import Path
from datetime import date
from typing import Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
WIKI_DIR = Path("/Volumes/t7_shield/ObsidianVault/llmwiki")
# Fallback to project wiki if vault not mounted
if not WIKI_DIR.exists():
    WIKI_DIR = ROOT / "wiki"
LOG_PATH = WIKI_DIR / "log.md"
INDEX_PATH = WIKI_DIR / "index.md"

WIKI_SUBDIRS = ["ideas", "people", "mental-models", "projects", "daily", "code"]

MAX_MERGE_BODY_LENGTH = 5000  # chars — truncate older insights beyond this


def _to_vault_rel(abs_path: str) -> str:
    """Convert an absolute raw-file path to a vault-relative path for Obsidian.

    e.g. /Users/th/.../cognitive-flywheel/raw/rss/2026-04-01-say-the-thing-you-want.md
         → raw/rss/2026-04-01-say-the-thing-you-want
    """
    p = Path(abs_path)
    # Walk up to find 'raw' ancestor
    for parent in p.parents:
        if parent.name == "raw":
            return str(Path("raw") / p.relative_to(parent).with_suffix(""))
    # Fallback: just the filename stem
    return p.stem


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def parse_frontmatter(text: str):
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


def build_frontmatter(meta: dict) -> str:
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


def write_page(path: Path, meta: dict, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = build_frontmatter(meta)
    path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")


def read_page(path: Path):
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def _resolve_idea_action(title: str, tags: list, wiki_dir: Path) -> Tuple[str, str]:
    """Use concept_merger to decide create vs update. Returns (action, path)."""
    try:
        from .concept_merger import resolve_idea_path
        return resolve_idea_path(title, tags, wiki_dir)
    except Exception:
        # Fallback: always create if concept_merger fails
        slug = slugify(title)
        return "create", f"ideas/{slug}.md"


def _merge_into_existing_page(
    idea_path: Path,
    title: str,
    title_zh: str,
    title_en: str,
    summary: str,
    category: str,
    tags: list,
    people: list,
    orgs: list,
    insights: list,
    sentiment: str,
    quality: float,
    related: list,
    filepath: str,
    today: str,
    resolved_path: str,
    idea_slug: str,
) -> str:
    """
    Merge new ingest result into an existing ideas/ page.

    - Preserves existing content
    - Appends new sources to frontmatter
    - Adds new insights without duplicates
    - Updates the `updated` date
    - Increments mention_count
    - Enqueues review entry for tracking

    Returns the relative wiki path that was updated.
    """
    from . import review_queue
    from .concept_merger import _parse_frontmatter_with_lists

    # Use list-aware parser to properly read YAML list values (tags, sources, etc.)
    raw_text = idea_path.read_text(encoding="utf-8") if idea_path.exists() else ""
    if raw_text.startswith("---"):
        parts = raw_text.split("---", 2)
        if len(parts) >= 3:
            existing_meta = _parse_frontmatter_with_lists(raw_text)
            existing_body = parts[2].strip()
        else:
            existing_meta = {}
            existing_body = raw_text
    else:
        existing_meta = {}
        existing_body = raw_text

    # --- Merge frontmatter ---
    existing_source = existing_meta.get("source", "")
    new_source = _to_vault_rel(filepath)
    # Build source list (avoid duplicates)
    sources = [existing_source] if existing_source else []
    if new_source and new_source not in sources:
        sources.append(new_source)

    # Merge tags (union, no duplicates)
    existing_tags_raw = existing_meta.get("tags", [])
    if isinstance(existing_tags_raw, str):
        existing_tags = [t.strip() for t in existing_tags_raw.split(",") if t.strip()]
    elif isinstance(existing_tags_raw, list):
        existing_tags = existing_tags_raw
    else:
        existing_tags = []
    merged_tags = list(dict.fromkeys(existing_tags + tags))[:15]

    # Increment mention_count
    mention_count = int(existing_meta.get("mention_count", 1)) + 1

    merged_meta = {
        "title": existing_meta.get("title", title),
        "title_zh": existing_meta.get("title_zh", "") or title_zh,
        "title_en": existing_meta.get("title_en", "") or title_en,
        "category": existing_meta.get("category", "") or category,
        "sentiment": sentiment,
        "quality_score": quality,
        "created": existing_meta.get("created", today),
        "updated": today,
        "source": sources[0] if sources else "",  # Keep first source as primary
        "sources": sources,  # All sources as list
        "tags": merged_tags,
        "mention_count": mention_count,
        "related_people": list(dict.fromkeys(
            (existing_meta.get("related_people", [])
             if isinstance(existing_meta.get("related_people", []), list) else [])
            + [slugify(p.get("name", "")) for p in people if p.get("name")]
        )),
        "related_orgs": list(dict.fromkeys(
            (existing_meta.get("related_orgs", [])
             if isinstance(existing_meta.get("related_orgs", []), list) else [])
            + [slugify(o) for o in orgs]
        )),
    }

    # --- Merge body ---
    # Find existing insight lines to avoid duplicates
    existing_lines = set(existing_body.splitlines())

    body = existing_body.rstrip()

    # Truncate older content if body exceeds limit to prevent unbounded growth
    if len(body) > MAX_MERGE_BODY_LENGTH:
        lines = body.split("\n")
        # Keep first section (usually the summary) and trim from the middle
        # Find the 核心洞察 section and keep only recent insights
        header_idx = None
        for idx, line in enumerate(lines):
            if line.startswith("## 核心洞察"):
                header_idx = idx
                break
        if header_idx is not None:
            # Count insight lines after the header
            insight_start = header_idx + 1
            insight_lines = []
            other_lines = []
            for i in range(insight_start, len(lines)):
                if lines[i].startswith("- "):
                    insight_lines.append(lines[i])
                elif lines[i].strip() == "":
                    continue
                else:
                    other_lines.extend(lines[i:])
                    break
            # Keep only the last 20 insights (most recent)
            if len(insight_lines) > 20:
                kept = insight_lines[-20:]
                truncated_count = len(insight_lines) - len(kept)
                lines = lines[:insight_start] + ["- ... (截断 {} 条较旧洞察)".format(truncated_count)] + kept + other_lines
                body = "\n".join(lines)

    # Add new insights (avoid duplicates)
    new_insights = [ins for ins in insights if f"- {ins}" not in existing_lines]
    if new_insights:
        # Find or create 核心洞察 section
        if "## 核心洞察" in body:
            # Append after existing insights
            lines = body.split("\n")
            insert_idx = None
            for idx, line in enumerate(lines):
                if line.startswith("## 核心洞察"):
                    # Find last insight line after this heading
                    insert_idx = idx + 1
                    while insert_idx < len(lines) and (lines[insert_idx].startswith("- ") or lines[insert_idx].strip() == ""):
                        if lines[insert_idx].strip() == "" and insert_idx + 1 < len(lines) and not lines[insert_idx + 1].startswith("-"):
                            break
                        insert_idx += 1
                    break
            if insert_idx is not None:
                for ins in new_insights:
                    lines.insert(insert_idx, f"- {ins}")
                    insert_idx += 1
                body = "\n".join(lines)
        else:
            body += "\n\n## 核心洞察\n"
            for ins in new_insights:
                body += f"\n- {ins}"

    # Add merge annotation
    body += f"\n\n---\n> 合并自: {title} ({today})"

    write_page(idea_path, merged_meta, body)

    # Enqueue review for tracking
    try:
        review_queue.enqueue_review(
            "duplicate_concepts",
            {
                "merged_into": resolved_path,
                "merged_from_title": title,
                "merged_from_source": _to_vault_rel(filepath),
                "mention_count": mention_count,
            },
            "wiki_writer.merge",
        )
    except Exception:
        pass  # Don't fail the write if queue fails

    # Return the path relative to wiki (use the resolved path's stem)
    return resolved_path.replace(".md", "") if resolved_path.endswith(".md") else resolved_path


def write_ingest_result(result: dict) -> list[str]:
    """
    Write a single LLM ingest result to wiki.
    Returns list of updated wiki page paths.
    """
    updated = []
    today = date.today().isoformat()
    filepath = result.get("_filepath", "")

    title_zh = result.get("title_zh", "")
    title_en = result.get("title_en", "")
    title = title_en or title_zh or Path(filepath).stem
    summary = result.get("summary_zh", "")
    category = result.get("category", "")
    tags = result.get("tags", [])
    people = result.get("people", [])
    orgs = result.get("orgs", [])
    insights = result.get("key_insights", [])
    sentiment = result.get("sentiment", "")
    quality = result.get("quality_score", 0)
    related = result.get("related_topics", [])

    # --- 1. Update ideas/ page ---
    idea_slug = slugify(title_en) if title_en else slugify(title)
    if idea_slug and idea_slug not in ("index", "log"):
        # Use concept_merger to decide: create new or update existing?
        action, resolved_path = _resolve_idea_action(title_en or title, tags, WIKI_DIR)

        if action == "update":
            # Merge into existing page
            idea_path = WIKI_DIR / resolved_path
            updated.append(_merge_into_existing_page(
                idea_path=idea_path,
                title=title,
                title_zh=title_zh,
                title_en=title_en,
                summary=summary,
                category=category,
                tags=tags,
                people=people,
                orgs=orgs,
                insights=insights,
                sentiment=sentiment,
                quality=quality,
                related=related,
                filepath=filepath,
                today=today,
                resolved_path=resolved_path,
                idea_slug=idea_slug,
            ))
        else:
            # Create new page (original behavior)
            idea_path = WIKI_DIR / "ideas" / f"{idea_slug}.md"
            existing_meta, existing_body = read_page(idea_path)

            idea_meta = {
                "title": title,
                "title_zh": title_zh,
                "title_en": title_en,
                "category": category,
                "sentiment": sentiment,
                "quality_score": quality,
                "created": existing_meta.get("created", today),
                "updated": today,
                "source": _to_vault_rel(filepath),
                "tags": tags[:10],
                "related_people": [slugify(p.get("name", "")) for p in people if p.get("name")],
                "related_orgs": [slugify(o) for o in orgs],
            }

            body_parts = []
            body_parts.append(f"# {title}\n")

            if title_zh and title_en and title_zh != title_en:
                body_parts.append(f"> {title_zh}\n")

            body_parts.append(f"## 深度摘要\n\n{summary}\n")

            if insights:
                body_parts.append("\n## 核心洞察\n")
                for ins in insights:
                    body_parts.append(f"- {ins}")

            if tags:
                body_parts.append(f"\n## 标签\n\n{', '.join(tags)}")

            if people:
                body_parts.append("\n## 相关人物\n")
                for p in people:
                    name = p.get("name", "")
                    role = p.get("role", "")
                    org = p.get("org", "")
                    slug = slugify(name)
                    detail = f" ({role}" + (f", {org}" if org else "") + ")" if role else ""
                    body_parts.append(f"- [[people/{slug}|{name}]]{detail}")

            if orgs:
                body_parts.append("\n## 相关组织\n")
                for o in orgs:
                    body_parts.append(f"- {o}")

            if related:
                body_parts.append("\n## 相关主题\n")
                for r in related:
                    body_parts.append(f"- {r}")

            if sentiment:
                body_parts.append(f"\n> 情感倾向: {sentiment} | 质量评分: {quality:.1f}/1.0")

            new_body = "\n".join(body_parts)
            write_page(idea_path, idea_meta, new_body)
            updated.append(f"ideas/{idea_slug}")

    # --- 2. Update people/ pages ---
    for person in people:
        name = person.get("name", "")
        if not name or len(name.split()) < 2:
            continue  # Skip non-person names

        person_slug = slugify(name)
        person_path = WIKI_DIR / "people" / f"{person_slug}.md"
        existing_meta, existing_body = read_page(person_path)

        role = person.get("role", existing_meta.get("role", ""))
        org = person.get("org", "")

        person_meta = {
            "name": name,
            "role": role,
            "org": org,
            "updated": today,
        }

        if existing_body and "（待补充）" not in existing_body:
            # Append mention
            body = existing_body.rstrip()
            body += f"\n\n- 在 [[ideas/{idea_slug}|{title}]] 中被提及 ({today})\n"
        else:
            body = f"## 简介\n\n**{name}**"
            if role:
                body += f" · {role}"
            if org:
                body += f" · {org}"
            body += "\n\n## 相关文章\n\n"
            body += f"- [[ideas/{idea_slug}|{title}]] ({today})\n"

        write_page(person_path, person_meta, body)
        updated.append(f"people/{person_slug}")

    return updated


def rebuild_index():
    """Rebuild wiki/index.md from all wiki pages."""
    pages = {}
    for subdir in WIKI_SUBDIRS:
        d = WIKI_DIR / subdir
        if d.exists():
            for p in d.glob("*.md"):
                rel = f"{subdir}/{p.name}"
                meta, _ = read_page(p)
                title = meta.get("title") or meta.get("name") or p.stem
                pages[rel] = title

    today = date.today().isoformat()

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
    lines.append("_此索引由 LLM Ingest V2 自动维护。_\n")

    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


def append_log(source: str, title: str, updated_pages: list[str],
               tokens_in: int = 0, tokens_out: int = 0, model: str = ""):
    today = date.today().isoformat()
    pages_str = ", ".join(updated_pages)
    entry = (
        f"\n## [{today}] llm-ingest-v2 | {title}\n"
        f"来源: {source}\n"
        f"模型: {model}\n"
        f"Token: in={tokens_in}, out={tokens_out}\n"
        f"更新页面: {pages_str}\n"
    )
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
