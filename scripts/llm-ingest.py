#!/usr/bin/env python3
"""
llm-ingest.py — Ingest raw files into the Cognitive Flywheel wiki.

V1: Pure Python extraction (no external LLM API needed).
Reads raw files, extracts keywords/entities, creates/updates wiki pages.

Usage:
    python scripts/llm-ingest.py --file raw/rss/2026-05-30-example.md
    python scripts/llm-ingest.py --all-unprocessed
    python scripts/llm-ingest.py --all-unprocessed --dry-run
"""

import argparse
import re
import os
import sys
from datetime import datetime, date
from pathlib import Path
from collections import Counter

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
WIKI_DIR = ROOT / "wiki"
LOG_PATH = WIKI_DIR / "log.md"
INDEX_PATH = WIKI_DIR / "index.md"

WIKI_SUBDIRS = ["ideas", "people", "mental-models", "projects", "daily", "code"]


# ---------------------------------------------------------------------------
# Helpers — frontmatter
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata_dict, body) from text with optional YAML frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = {}
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("'\"")
            return meta, parts[2].strip()
    return {}, text


def build_frontmatter(meta: dict) -> str:
    """Build a YAML frontmatter block."""
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


def read_page(path: Path) -> tuple[dict, str]:
    """Read a wiki page, returning (meta, body)."""
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def write_page(path: Path, meta: dict, body: str):
    """Write a wiki page with frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = build_frontmatter(meta)
    path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers — text extraction
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset("""
the a an is are was were be been being have has had do does did will would
could should may might shall can need to of in for on with at by from as
into through during before after above below between out off over under again
further then once here there when where why how all both each few more most
other some such no nor not only own same so than too very and but or if while
about against between through during before after up down just also this that
these those it its he she they them their we our you your which what who whom
""".split())


def extract_keywords(text: str, top_n: int = 15) -> list[str]:
    """Simple keyword extraction via word frequency (no NLP lib)."""
    # Tokenise: lowercase, strip non-alpha
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    # Remove stop words
    words = [w for w in words if w not in STOP_WORDS]
    counter = Counter(words)
    return [w for w, _ in counter.most_common(top_n)]


def extract_title(text: str, filepath: Path) -> str:
    """Try to extract a title from the content, fall back to filename."""
    # Look for first markdown heading
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)", line)
        if m:
            return m.group(1).strip()
    # Look for 'title:' in frontmatter
    for line in text.splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    # Fall back to filename stem
    return filepath.stem


def extract_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    return re.findall(r"https?://[^\s\]\)>\"']+", text)


def guess_people(text: str, keywords: list[str]) -> list[str]:
    """Heuristic: look for proper nouns that could be person names.
    
    Very simple V1: look for capitalized word pairs and known patterns.
    """
    people = []
    # Look for patterns like "First Last" or "First Last said/wrote/argues"
    matches = re.findall(
        r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", text
    )
    # Deduplicate and filter common false positives
    false_positives = {
        "The Wiki", "In This", "Of The", "In The", "For The", "To The",
        "With The", "On The", "Is A", "Is An", "Are The", "Was A",
        "Markdown File", "New York", "San Francisco",
    }
    for name in dict.fromkeys(matches):
        if name not in false_positives and len(name.split()) == 2:
            people.append(name)
    return people[:5]  # Cap at 5


def slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ---------------------------------------------------------------------------
# Helpers — log & index
# ---------------------------------------------------------------------------

def get_processed_files() -> set[str]:
    """Parse log.md to find already-processed raw files."""
    processed = set()
    if not LOG_PATH.exists():
        return processed
    text = LOG_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"来源:\s*(raw/\S+)", line)
        if m:
            processed.add(m.group(1))
    return processed


def append_log(source: str, title: str, updated_pages: list[str]):
    """Append an entry to log.md."""
    today = date.today().isoformat()
    pages_str = ", ".join(updated_pages)
    entry = (
        f"\n## [{today}] ingest | {title}\n"
        f"来源: {source}\n"
        f"更新页面: {pages_str}\n"
    )
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def scan_wiki_pages() -> dict[str, str]:
    """Return {relative_path: title} for all wiki .md pages."""
    pages = {}
    for subdir in WIKI_SUBDIRS:
        d = WIKI_DIR / subdir
        if d.exists():
            for p in d.glob("*.md"):
                rel = f"{subdir}/{p.name}"
                meta, _ = read_page(p)
                title = meta.get("title") or meta.get("name") or p.stem
                pages[rel] = title
    return pages


def rebuild_index():
    """Rebuild wiki/index.md from current wiki pages."""
    pages = scan_wiki_pages()
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
        f"> 页面总数: {len(pages)} | 来源总数: {len(get_processed_files())}\n",
    ]

    for subdir, (heading, entries) in sections.items():
        lines.append(f"## {heading}")
        if entries:
            for rel, title in entries:
                stem = Path(rel).stem
                lines.append(f"- [[{rel}|{title}]]")
        else:
            lines.append("_暂无页面_")
        lines.append("")

    lines.append("---\n")
    lines.append("_此索引由 LLM 自动维护。每次 Ingest/Query/Lint 后更新。_\n")

    INDEX_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Ingest logic for a single file
# ---------------------------------------------------------------------------

def detect_source_type(filepath: Path) -> str:
    """Determine source sub-type from path (rss/papers/web/code/journal)."""
    parts = filepath.parts
    for t in ("rss", "papers", "web", "code", "journal"):
        if t in parts:
            return t
    return "unknown"


def ingest_file(filepath: Path, dry_run: bool = False) -> list[str]:
    """Process a single raw file. Returns list of updated wiki page paths."""
    if not filepath.exists():
        print(f"  ⚠️  File not found: {filepath}")
        return []

    text = filepath.read_text(encoding="utf-8")
    title = extract_title(text, filepath)
    keywords = extract_keywords(text)
    urls = extract_urls(text)
    people = guess_people(text, keywords)
    source_type = detect_source_type(filepath)
    today = date.today().isoformat()

    print(f"  📄 {filepath.name} → title=\"{title}\"")
    print(f"     keywords: {', '.join(keywords[:8])}")
    if people:
        print(f"     people:   {', '.join(people)}")

    updated_pages = []

    if dry_run:
        print(f"     (dry-run, skipping updates)")
        return updated_pages

    # --- 1. Create/update ideas/ page from title/keywords -----------------
    idea_slug = slugify(title)
    if idea_slug and idea_slug not in ("index", "log"):
        idea_path = WIKI_DIR / "ideas" / f"{idea_slug}.md"
        existing_meta, existing_body = read_page(idea_path)

        # Normalize existing sources to a list
        existing_sources = existing_meta.get("sources", [])
        if isinstance(existing_sources, str):
            existing_sources = [s.strip() for s in existing_sources.split(",") if s.strip()]
        elif not isinstance(existing_sources, list):
            existing_sources = []

        idea_meta = {
            "title": title,
            "created": existing_meta.get("created", today),
            "updated": today,
            "sources": list(set(existing_sources + [str(filepath.relative_to(ROOT))])),
            "related": [f"people/{slugify(p)}" for p in people],
            "tags": keywords[:5],
        }

        # Build body
        body_parts = []
        if existing_body:
            body_parts.append(existing_body)
            body_parts.append(f"\n### {today} — 补充\n")

        summary_lines = []
        # Use first few non-heading, non-frontmatter lines as summary
        content_lines = []
        in_fm = False
        for line in text.splitlines():
            if line.strip() == "---":
                in_fm = not in_fm
                continue
            if in_fm:
                continue
            if line.startswith("#"):
                continue
            if line.strip():
                content_lines.append(line.strip())
                if len(content_lines) >= 5:
                    break

        if content_lines:
            summary_lines.append("## 核心观点\n")
            for cl in content_lines:
                summary_lines.append(f"- {cl}")

        if urls:
            summary_lines.append("\n## 来源链接\n")
            for url in urls[:3]:
                summary_lines.append(f"- {url}")

        if keywords:
            summary_lines.append(f"\n## 关键词\n")
            summary_lines.append(", ".join(keywords))

        if people:
            summary_lines.append("\n## 相关人物\n")
            for p in people:
                summary_lines.append(f"- [[people/{slugify(p)}|{p}]]")

        new_body = "\n".join(body_parts + summary_lines)
        write_page(idea_path, idea_meta, new_body)
        updated_pages.append(f"ideas/{idea_slug}")
        print(f"     ✅ ideas/{idea_slug}.md")

    # --- 2. Create/update people/ pages -----------------------------------
    for person in people:
        person_slug = slugify(person)
        person_path = WIKI_DIR / "people" / f"{person_slug}.md"
        existing_meta, existing_body = read_page(person_path)

        # Normalize existing sources to a list
        existing_person_sources = existing_meta.get("sources", [])
        if isinstance(existing_person_sources, str):
            existing_person_sources = [s.strip() for s in existing_person_sources.split(",") if s.strip()]
        elif not isinstance(existing_person_sources, list):
            existing_person_sources = []

        person_meta = {
            "name": person,
            "role": existing_meta.get("role", ""),
            "sources": list(set(existing_person_sources + [str(filepath.relative_to(ROOT))])),
            "updated": today,
        }

        body = existing_body or ""
        if not body:
            body = f"## 核心思想\n\n（待补充）\n"
        # Add reference to this article
        body += f"\n- 在 [[ideas/{idea_slug}|{title}]] 中被提及 ({today})\n"
        body += f"- [[ideas/{idea_slug}]]\n"

        write_page(person_path, person_meta, body)
        updated_pages.append(f"people/{person_slug}")
        print(f"     ✅ people/{person_slug}.md")

    # --- 3. Append to log -------------------------------------------------
    source_rel = str(filepath.relative_to(ROOT))
    append_log(source_rel, title, updated_pages)

    return updated_pages


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ingest raw files into the Cognitive Flywheel wiki"
    )
    parser.add_argument(
        "--file", "-f", action="append", dest="files",
        help="Path to a raw file to ingest (repeatable)",
    )
    parser.add_argument(
        "--all-unprocessed", action="store_true",
        help="Process all raw/ files not yet in log.md",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--llm", default="openclaw",
        help="LLM to use (reserved for future use, default: openclaw)",
    )

    args = parser.parse_args()

    if not args.files and not args.all_unprocessed:
        parser.error("Specify --file or --all-unprocessed")

    # Collect files to process
    target_files: list[Path] = []
    if args.files:
        for f in args.files:
            p = Path(f)
            if not p.is_absolute():
                p = ROOT / p
            target_files.append(p)

    if args.all_unprocessed:
        processed = get_processed_files()
        for subdir in ("rss", "papers", "web", "code", "journal"):
            d = RAW_DIR / subdir
            if d.exists():
                for p in sorted(d.glob("*.md")):
                    rel = str(p.relative_to(ROOT))
                    if rel not in processed:
                        target_files.append(p)

    if not target_files:
        print("No files to process.")
        return

    print(f"Found {len(target_files)} file(s) to ingest.\n")

    all_updated = []
    for fp in target_files:
        updated = ingest_file(fp, dry_run=args.dry_run)
        all_updated.extend(updated)

    if not args.dry_run and all_updated:
        # Rebuild index
        rebuild_index()
        print(f"\n✅ Ingested {len(target_files)} file(s), updated {len(all_updated)} wiki page(s).")
        print(f"📝 wiki/index.md and wiki/log.md updated.")
    elif args.dry_run:
        print(f"\n🏃 Dry run complete. {len(target_files)} file(s) would be processed.")
    else:
        print("\nNothing to update.")


if __name__ == "__main__":
    main()
