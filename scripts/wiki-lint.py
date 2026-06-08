#!/usr/bin/env python3
"""
wiki-lint.py — Health check for the Cognitive Flywheel wiki.

Scans all wiki/ pages and reports:
  - Broken [[wikilinks]]
  - Orphan pages (no inbound links)
  - Incomplete frontmatter
  - Stale pages (not updated in 30+ days)

Usage:
    python scripts/wiki-lint.py
    python scripts/wiki-lint.py --fix
"""

import argparse
import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "wiki"
SPECIAL_FILES = {"index.md", "log.md"}
# Special files are excluded from orphan checks (they're entry points)
# but their wikilinks ARE checked for broken targets
WIKI_SUBDIRS = ["ideas", "people", "mental-models", "projects", "daily", "code"]

REQUIRED_FRONTMATTER = {
    "ideas": ["title", "created", "updated"],
    "people": ["name", "updated"],
    "mental-models": ["name", "created", "updated"],
    "projects": ["name", "status", "created", "updated"],
    "daily": ["period", "type", "generated"],
    "code": ["title", "created", "updated"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """Return metadata dict from text with optional YAML frontmatter."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = {}
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("'\"")
            return meta
    return {}


def extract_wikilinks(text: str) -> list[str]:
    """Extract [[wikilink]] targets from text."""
    # Match [[target]] or [[target|display]]
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", text)


def get_all_pages(include_special: bool = False) -> dict[str, dict]:
    """Return {relative_path: {meta, links, exists}} for all wiki .md files."""
    pages = {}
    # Scan special files in wiki/ root (index.md, log.md)
    if include_special:
        for name in SPECIAL_FILES:
            p = WIKI_DIR / name
            if p.exists():
                text = p.read_text(encoding="utf-8")
                meta = parse_frontmatter(text)
                links = extract_wikilinks(text)
                pages[name] = {
                    "path": p,
                    "meta": meta,
                    "links": links,
                    "text": text,
                }
    for subdir in WIKI_SUBDIRS:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            rel = f"{subdir}/{p.name}"
            text = p.read_text(encoding="utf-8")
            meta = parse_frontmatter(text)
            links = extract_wikilinks(text)
            pages[rel] = {
                "path": p,
                "meta": meta,
                "links": links,
                "text": text,
            }
    return pages


def page_exists(target: str, pages: dict) -> bool:
    """Check if a wikilink target resolves to an existing page."""
    # Direct match
    if target in pages:
        return True
    # Try adding .md
    if target + ".md" in pages:
        return True
    # Try matching just the filename
    for rel in pages:
        if rel.endswith("/" + target) or rel.endswith("/" + target + ".md"):
            return True
    return False


# ---------------------------------------------------------------------------
# Lint checks
# ---------------------------------------------------------------------------

def check_broken_links(pages: dict, special_pages: dict | None = None) -> list[tuple[str, str]]:
    """Return [(source_page, broken_target), ...]"""
    broken = []
    all_pages = dict(pages)
    if special_pages:
        all_pages.update(special_pages)
    # Check regular pages
    for rel, info in pages.items():
        for target in info["links"]:
            if target in SPECIAL_FILES or target.startswith("raw/"):
                continue
            if not page_exists(target, all_pages):
                broken.append((rel, target))
    # Also check special pages for broken links
    if special_pages:
        for rel, info in special_pages.items():
            for target in info["links"]:
                if target in SPECIAL_FILES or target.startswith("raw/"):
                    continue
                if not page_exists(target, all_pages):
                    broken.append((rel, target))
    return broken


def check_orphan_pages(pages: dict) -> list[str]:
    """Return pages with no inbound links from other wiki pages.
    Special files (index.md, log.md) are excluded — they're entry points."""
    # Build set of all link targets
    all_targets = set()
    for info in pages.values():
        for target in info["links"]:
            all_targets.add(target)
            # Also add the bare filename
            all_targets.add(Path(target).stem)
            all_targets.add(target + ".md")

    orphans = []
    for rel in pages:
        # Never flag special files as orphans
        if Path(rel).name in SPECIAL_FILES:
            continue
        stem = Path(rel).stem
        # Check if this page is linked from anywhere
        is_linked = (
            rel in all_targets
            or stem in all_targets
            or any(Path(t).stem == stem for t in all_targets)
        )
        if not is_linked:
            orphans.append(rel)
    return orphans


def check_frontmatter(pages: dict) -> list[tuple[str, list[str]]]:
    """Return [(page, [missing_fields]), ...]"""
    issues = []
    for rel, info in pages.items():
        subdir = rel.split("/")[0]
        required = REQUIRED_FRONTMATTER.get(subdir, [])
        meta = info["meta"]
        missing = [f for f in required if not meta.get(f)]
        if missing:
            issues.append((rel, missing))
    return issues


def check_stale_pages(pages: dict, threshold_days: int = 30) -> list[tuple[str, str]]:
    """Return [(page, last_updated), ...] for pages older than threshold."""
    stale = []
    cutoff = date.today() - timedelta(days=threshold_days)
    for rel, info in pages.items():
        updated_str = info["meta"].get("updated", "")
        if updated_str:
            try:
                updated = datetime.strptime(updated_str, "%Y-%m-%d").date()
                if updated < cutoff:
                    stale.append((rel, updated_str))
            except ValueError:
                stale.append((rel, updated_str or "(missing)"))
        else:
            stale.append((rel, "(missing)"))
    return stale


# ---------------------------------------------------------------------------
# Fix mode
# ---------------------------------------------------------------------------

def fix_frontmatter(pages: dict, issues: list) -> int:
    """Auto-fix safe frontmatter issues. Returns count of fixed pages."""
    fixed = 0
    today = date.today().isoformat()

    for rel, missing in issues:
        info = pages[rel]
        path = info["path"]
        text = info["text"]

        # Read current frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm_lines = parts[1].strip().splitlines()
                body = parts[2].strip()
            else:
                continue
        else:
            fm_lines = []
            body = text

        # Fix missing fields
        fm_dict = {}
        for line in fm_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                fm_dict[k.strip()] = v.strip()

        for field in missing:
            if field == "updated":
                fm_dict[field] = today
            elif field == "created":
                # Try to infer from filename (YYYY-MM-DD pattern)
                m = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
                fm_dict[field] = m.group(1) if m else today
            elif field == "title":
                fm_dict[field] = path.stem.replace("-", " ").title()
            elif field == "name":
                fm_dict[field] = path.stem.replace("-", " ").title()
            elif field == "period":
                m = re.search(r"(\d{4}-W\d{2})", path.stem)
                fm_dict[field] = m.group(1) if m else ""
            elif field == "type":
                fm_dict[field] = "weekly"
            elif field == "generated":
                fm_dict[field] = today

        # Rebuild frontmatter
        new_fm_lines = []
        for k, v in fm_dict.items():
            new_fm_lines.append(f"{k}: {v}")

        new_text = "---\n" + "\n".join(new_fm_lines) + "\n---\n\n" + body + "\n"
        path.write_text(new_text, encoding="utf-8")
        fixed += 1
        print(f"  🔧 Fixed frontmatter for {rel}")

    return fixed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Lint the Cognitive Flywheel wiki")
    parser.add_argument("--fix", action="store_true", help="Auto-fix safe issues")
    parser.add_argument("--contradictions", action="store_true", help="Run contradiction/tension detection")
    parser.add_argument("--stale-days", type=int, default=30, help="Days before page is stale (default: 30)")
    args = parser.parse_args()

    pages = get_all_pages()
    special_pages = get_all_pages(include_special=True)
    # Separate: special pages only exist in special_pages, regular in pages
    special_only = {k: v for k, v in special_pages.items() if k in SPECIAL_FILES}
    total = len(pages)

    if total == 0:
        print("No wiki pages found. Nothing to lint.")
        return

    print(f"Scanning {total} wiki pages...\n")

    # --- Broken links (checks regular + special pages) ---
    broken = check_broken_links(pages, special_only)
    # --- Orphan pages (special files excluded) ---
    orphans = check_orphan_pages(pages)
    # --- Frontmatter ---
    fm_issues = check_frontmatter(pages)
    # --- Stale pages ---
    stale = check_stale_pages(pages, args.stale_days)

    # --- Healthy pages ---
    pages_with_issues = set()
    for src, _ in broken:
        pages_with_issues.add(src)
    for o in orphans:
        pages_with_issues.add(o)
    for rel, _ in fm_issues:
        pages_with_issues.add(rel)
    for rel, _ in stale:
        pages_with_issues.add(rel)
    healthy = total - len(pages_with_issues)

    # --- Report ---
    if broken:
        print(f"🔴 {len(broken)} broken link(s):")
        for src, target in broken:
            print(f"   {src} → [[{target}]]")
        print()

    if orphans:
        print(f"🟡 {len(orphans)} orphan page(s):")
        for o in orphans:
            print(f"   {o}")
        print()

    if fm_issues:
        print(f"🟡 {len(fm_issues)} incomplete frontmatter:")
        for rel, missing in fm_issues:
            print(f"   {rel} — missing: {', '.join(missing)}")
        print()

    if stale:
        print(f"🟡 {len(stale)} stale page(s) (>{args.stale_days}d):")
        for rel, updated in stale:
            print(f"   {rel} — last updated: {updated}")
        print()

    print(f"🟢 {healthy} healthy page(s)")
    print(f"\n{'='*40}")
    print(f"Total: {total} | 🔴 {len(broken)} broken | 🟡 {len(orphans)+len(fm_issues)+len(stale)} warnings | 🟢 {healthy} ok")

    # --- Fix mode ---
    if args.fix and fm_issues:
        print(f"\n🔧 Fixing {len(fm_issues)} frontmatter issue(s)...")
        fixed = fix_frontmatter(pages, fm_issues)
        print(f"  Fixed {fixed} page(s).")

    # --- Contradiction detection ---
    if args.contradictions:
        print()
        import importlib
        _cd = importlib.import_module("contradiction-detect")
        _cd.run_detection(WIKI_DIR)

    # Exit code
    if broken:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
