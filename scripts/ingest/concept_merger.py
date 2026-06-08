#!/usr/bin/env python3
"""
concept_merger.py — Detect similar/dupe ideas before creating wiki pages.

Before wiki_writer creates a new ideas/ page, check if a similar page already
exists. If so, return "update" instead of "create". Uses lightweight heuristics
(difflib.SequenceMatcher + tag Jaccard) — no LLM calls needed.
"""

from difflib import SequenceMatcher
from pathlib import Path
from typing import Tuple

from .wiki_writer import slugify, read_page, WIKI_DIR

MERGE_SIMILARITY_THRESHOLD = 0.8


def _title_similarity(a: str, b: str) -> float:
    """Normalized similarity between two titles (case-insensitive)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _tag_jaccard(tags_a: list, tags_b: list) -> float:
    """Jaccard similarity between two tag sets."""
    set_a = {t.lower().strip() for t in (tags_a or []) if t}
    set_b = {t.lower().strip() for t in (tags_b or []) if t}
    if not set_a and not set_b:
        return 0.0  # No tags → no tag-based similarity signal
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _combined_similarity(title_sim: float, tag_jaccard: float) -> float:
    """Weighted combination: title similarity is primary signal, tags secondary.
    
    If title similarity is very high (>= 0.85), boost to at least that level
    so exact/near-exact matches always pass threshold regardless of tags.
    """
    combined = 0.6 * title_sim + 0.4 * tag_jaccard
    if title_sim >= 0.85:
        combined = max(combined, title_sim)
    return combined


def _parse_frontmatter_with_lists(text: str) -> dict:
    """Parse YAML frontmatter, handling list values (tags as YAML array)."""
    meta = {}
    if not text.startswith("---"):
        return meta
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta
    fm = parts[1].strip()
    current_key = None
    current_list = None

    for line in fm.splitlines():
        stripped = line.strip()
        # List item: "- value"
        if stripped.startswith("- ") and current_key is not None:
            val = stripped[2:].strip().strip("'\"")
            if current_list is not None:
                current_list.append(val)
            continue
        # Key: value
        if ":" in line and not line.startswith(" "):
            # Flush previous list
            if current_list is not None:
                meta[current_key] = current_list
                current_list = None
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if v == "":
                # This key has a list value on subsequent lines
                current_key = k
                current_list = []
            else:
                meta[k] = v
                current_key = None
                current_list = None
        elif current_list is not None:
            # continuation of some kind
            pass

    # Flush final list
    if current_list is not None:
        meta[current_key] = current_list

    return meta


def _read_existing_ideas(wiki_dir: Path) -> list[dict]:
    """Scan wiki/ideas/*.md and return list of page metadata."""
    ideas_dir = wiki_dir / "ideas"
    if not ideas_dir.exists():
        return []

    results = []
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

        results.append({
            "path": path,
            "title": title,
            "title_en": title_en,
            "tags": tags,
        })
    return results


def find_similar_ideas(title: str, tags: list, wiki_dir: Path) -> list[dict]:
    """
    Scan wiki/ideas/*.md for pages with similar title or high tag overlap.
    Returns list of dicts: {path, title, title_en, tags, similarity_score}
    Only returns candidates with similarity >= MERGE_SIMILARITY_THRESHOLD
    """
    if not wiki_dir or not (wiki_dir / "ideas").exists():
        return []

    existing = _read_existing_ideas(wiki_dir)
    candidates = []

    for page in existing:
        # Compare against both title and title_en
        page_title = page["title_en"] or page["title"]
        title_sim = max(
            _title_similarity(title, page["title"]),
            _title_similarity(title, page_title) if page_title != page["title"] else 0.0,
        )
        tag_sim = _tag_jaccard(tags, page["tags"])
        combined = _combined_similarity(title_sim, tag_sim)

        if combined >= MERGE_SIMILARITY_THRESHOLD:
            candidates.append({
                "path": page["path"],
                "title": page["title"],
                "title_en": page["title_en"],
                "tags": page["tags"],
                "similarity_score": round(combined, 4),
            })

    # Sort by similarity descending
    candidates.sort(key=lambda x: x["similarity_score"], reverse=True)
    return candidates


def should_merge(new_title: str, new_tags: list, existing: dict) -> Tuple[bool, str]:
    """
    Decide whether new article should merge into existing page.
    Returns (should_merge: bool, reason: str)

    Uses the same _combined_similarity (60/40 weighted) as find_similar_ideas
    as the gate, then applies finer-grained rules for the reason.
    If combined similarity >= MERGE_SIMILARITY_THRESHOLD, merge is approved.
    """
    existing_title = existing.get("title_en", "") or existing.get("title", "")
    existing_tags = existing.get("tags", [])

    title_sim = max(
        _title_similarity(new_title, existing.get("title", "")),
        _title_similarity(new_title, existing_title) if existing_title != existing.get("title", "") else 0.0,
    )
    tag_jac = _tag_jaccard(new_tags, existing_tags)
    combined = _combined_similarity(title_sim, tag_jac)

    if combined < MERGE_SIMILARITY_THRESHOLD:
        return False, f"Different topic (combined={combined:.2f}, title_sim={title_sim:.2f}, tag_jac={tag_jac:.2f})"

    # Approved — determine reason
    if title_sim > 0.85:
        return True, f"High title similarity ({title_sim:.2f})"

    return True, f"Tag overlap ({tag_jac:.2f}) + moderate title similarity ({title_sim:.2f})"


def resolve_idea_path(title_en: str, tags: list, wiki_dir: Path) -> Tuple[str, str]:
    """
    Determine the target path for an idea.
    Returns (action, path) where action is "create" or "update"
    """
    slug = slugify(title_en)
    default_path = f"ideas/{slug}.md"

    # Check for similar existing ideas
    candidates = find_similar_ideas(title_en, tags, wiki_dir)

    if candidates:
        best = candidates[0]
        merge, reason = should_merge(title_en, tags, best)
        if merge:
            # Return relative path to the existing page
            rel = best["path"].relative_to(wiki_dir)
            return "update", str(rel)

    return "create", default_path
