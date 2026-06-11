#!/usr/bin/env python3
"""
contradiction-detect.py — Detect contradictions and tensions across wiki pages.

Scans wiki pages in ideas/, people/, mental-models/ and finds:
  - Opposing claims about the same entity
  - Evolving viewpoints for the same person over time

Usage:
    python scripts/contradiction-detect.py
    python scripts/contradiction-detect.py --verbose
    python scripts/contradiction-detect.py --wiki-dir /path/to/wiki
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.config import wiki_dir

DEFAULT_WIKI_DIR = wiki_dir()

SCAN_SUBDIRS = ["ideas", "people", "mental-models"]

# Section headers that contain claims/opinions
CLAIM_SECTIONS = {"核心观点", "核心思想", "核心洞察", "深度摘要", "详细内容",
                  "争议", "局限与挑战", "观点演变"}

# ── Antonym pairs for contradiction detection ──────────────────────────────
# Each tuple: (positive_pattern, negative_pattern, tension_label)
ANTONYM_PAIRS = [
    # English
    (r"\bwill\b", r"\bwon't\b|\bwill not\b", "optimistic vs pessimistic"),
    (r"\bshould\b", r"\bshouldn't\b|\bshould not\b", "prescriptive vs cautious"),
    (r"\bis the future\b", r"\bis (?:overhyped|dead|dying|declining)\b", "optimistic vs skeptical"),
    (r"\bimportant\b", r"\bunimportant\b|\btrivial\b", "essential vs trivial"),
    (r"\bgood\b", r"\bbad\b", "positive vs negative"),
    (r"\bnecessary\b", r"\bunnecessary\b", "required vs optional"),
    (r"\bsafe\b", r"\bdangerous\b|\bunsafe\b", "safe vs dangerous"),
    (r"\buseful\b", r"\buseless\b", "useful vs useless"),
    (r"\bbeneficial\b", r"\bharmful\b|\bdetrimental\b", "beneficial vs harmful"),
    (r"\bsimple\b", r"\bcomplex\b|\bcomplicated\b", "simple vs complex"),
    (r"\bpowerful\b", r"\bweak\b|\blimited\b", "powerful vs limited"),
    (r"\binnovative\b", r"\boutdated\b|\bobsolete\b", "innovative vs outdated"),
    (r"\bshould embrace\b|\bshould adopt\b", r"\bshould (?:avoid|reject|resist)\b", "embrace vs resist"),
    # Chinese
    (r"是.*?未来", r"不是.*?未来|已经.*?过时", "乐观 vs 怀疑"),
    (r"是.*?重要", r"不是.*?重要|不重要", "重要 vs 不重要"),
    (r"应该", r"不应该|不该", "倡导 vs 谨慎"),
    (r"安全", r"危险|不安全", "安全 vs 危险"),
    (r"有用", r"无用|没用", "有用 vs 无用"),
    (r"必要", r"不必要|没必要", "必要 vs 不必要"),
    (r"好", r"坏|糟糕", "正面 vs 负面"),
]

# Tension labels for specific keyword pairs
TENSION_KEYWORDS = {
    ("未来", "过时"): "乐观 vs 过时论",
    ("未来", "质疑"): "乐观 vs 质疑",
    ("创新", "风险"): "创新 vs 风险",
    ("创新", "安全"): "创新 vs 安全",
    ("信任", "不信任"): "信任 vs 不信任",
    ("信任", "黑箱"): "信任 vs 透明",
    ("拥抱", "避免"): "拥抱 vs 避免",
    ("未来", "批评"): "乐观 vs 批评",
    ("支持", "批评"): "支持 vs 批评",
    ("支持者", "批评者"): "支持 vs 批评",
    ("优势", "局限"): "优势 vs 局限",
    ("适用", "不适用"): "适用 vs 不适用",
    ("复利", "风险"): "复利效应 vs 风险",
}


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


def extract_sections(text: str) -> dict[str, list[str]]:
    """Split text into sections by ## headers. Returns {section_name: [lines]}."""
    sections: dict[str, list[str]] = {}
    current_section = ""
    for line in text.splitlines():
        m = re.match(r"^##+\s+(.+)", line)
        if m:
            current_section = m.group(1).strip()
            if current_section not in sections:
                sections[current_section] = []
        else:
            if current_section not in sections:
                sections[current_section] = []
            sections[current_section].append(line)
    return sections


def extract_claims_from_line(line: str) -> list[str]:
    """Extract claim-like content from a single line."""
    claims = []
    stripped = line.strip()

    # Bullet points
    if stripped.startswith("- "):
        content = stripped[2:].strip()
        if len(content) > 5:
            claims.append(content)
    # Blockquotes
    elif stripped.startswith(">"):
        content = stripped.lstrip("> ").strip()
        if len(content) > 5:
            claims.append(content)
    # Lines in claim sections that are substantial
    elif len(stripped) > 15 and not stripped.startswith("#") and not stripped.startswith("|"):
        claims.append(stripped)

    return claims


def extract_entity_claims(wiki_dir: Path) -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, list[tuple[str, str, str]]]]:
    """
    Scan all wiki pages and extract claims mapped to entities.

    Returns:
        entity_claims: {entity_name: [(source_page, claim_text, section_name), ...]}
        person_timeline: {person_name: [(date_context, source_page, claim_text), ...]}
    """
    entity_claims: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    person_timeline: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    for subdir in SCAN_SUBDIRS:
        d = wiki_dir / subdir
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            page_name = p.stem.replace("-", " ")
            text = p.read_text(encoding="utf-8")
            meta = parse_frontmatter(text)
            rel_path = f"{subdir}/{p.name}"
            sections = extract_sections(text)

            # Extract claims from key sections
            for section_name, lines in sections.items():
                # Only process relevant sections
                is_claim_section = any(
                    kw in section_name for kw in CLAIM_SECTIONS
                )
                if not is_claim_section:
                    continue

                for line in lines:
                    for claim in extract_claims_from_line(line):
                        # Map claim to the page itself as an entity
                        entity_claims[page_name].append(
                            (rel_path, claim, section_name)
                        )
                        # Also map to entities mentioned in the claim via wikilinks
                        for link in re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", claim):
                            link_name = Path(link).stem.replace("-", " ")
                            entity_claims[link_name].append(
                                (rel_path, claim, section_name)
                            )

            # For people pages, extract timeline entries (dated subsections)
            if subdir == "people":
                # Look for ### YYYY: patterns
                date_pattern = re.compile(r"^###\s+(\d{4}(?:-\d{2})?(?:-\d{2})?):\s*(.*)", re.MULTILINE)
                for m in date_pattern.finditer(text):
                    date_ctx = m.group(1)
                    summary = m.group(2).strip()
                    # Collect lines until next ### or ##
                    start = m.end()
                    rest = text[start:]
                    next_header = re.search(r"\n##", rest)
                    block = rest[: next_header.start()] if next_header else rest
                    for line in block.splitlines():
                        for claim in extract_claims_from_line(line):
                            person_timeline[page_name].append(
                                (date_ctx, rel_path, claim)
                            )
                    # Also treat the summary line itself as a claim
                    if len(summary) > 5:
                        person_timeline[page_name].append(
                            (date_ctx, rel_path, summary)
                        )

    return dict(entity_claims), dict(person_timeline)


def extract_tension_keywords(text: str) -> list[str]:
    """Extract tension-related keywords from text."""
    found = []
    for kw_pair in TENSION_KEYWORDS:
        for kw in kw_pair:
            if kw in text:
                found.append(kw)
    return found


def detect_contradiction_pair(claim_a: str, claim_b: str) -> str | None:
    """
    Check if two claims contradict each other using antonym patterns.
    Returns a tension label if contradiction found, None otherwise.
    """
    for pos_pat, neg_pat, label in ANTONYM_PAIRS:
        a_pos = bool(re.search(pos_pat, claim_a, re.IGNORECASE))
        b_neg = bool(re.search(neg_pat, claim_b, re.IGNORECASE))
        a_neg = bool(re.search(neg_pat, claim_a, re.IGNORECASE))
        b_pos = bool(re.search(pos_pat, claim_b, re.IGNORECASE))

        if (a_pos and b_neg) or (a_neg and b_pos):
            return label

    # Check tension keyword pairs
    kw_a = set(extract_tension_keywords(claim_a))
    kw_b = set(extract_tension_keywords(claim_b))
    for kw_pair, label in TENSION_KEYWORDS.items():
        if (kw_pair[0] in kw_a and kw_pair[1] in kw_b) or (
            kw_pair[1] in kw_a and kw_pair[0] in kw_b
        ):
            return label

    return None


def find_contradictions(
    entity_claims: dict[str, list[tuple[str, str, str]]],
) -> list[dict]:
    """
    Find contradictions by comparing claims about the same entity.

    Returns list of contradiction dicts:
        {entity, page_a, claim_a, page_b, claim_b, tension}
    """
    contradictions = []
    seen_pairs = set()

    for entity, claims in entity_claims.items():
        if len(claims) < 2:
            continue

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                src_a, claim_a, _ = claims[i]
                src_b, claim_b, _ = claims[j]

                # Skip same-claim comparison
                if claim_a == claim_b:
                    continue

                # Deduplicate by claim pair
                pair_key = tuple(sorted([claim_a[:50], claim_b[:50]]))
                if pair_key in seen_pairs:
                    continue

                tension = detect_contradiction_pair(claim_a, claim_b)
                if tension:
                    seen_pairs.add(pair_key)
                    contradictions.append({
                        "entity": entity,
                        "page_a": src_a,
                        "claim_a": claim_a,
                        "page_b": src_b,
                        "claim_b": claim_b,
                        "tension": tension,
                    })

    return contradictions


def find_evolution(
    person_timeline: dict[str, list[tuple[str, str, str]]],
) -> list[dict]:
    """
    Detect viewpoint evolution for a person over time.

    Returns list of evolution dicts:
        {person, date_a, claim_a, date_b, claim_b, evolution_label}
    """
    evolutions = []

    # Evolution signal words
    shift_signals = {
        ("乐观", "审慎"): "从乐观转向审慎",
        ("未来", "挑战"): "从乐观转向关注挑战",
        ("好", "坏"): "从正面转向负面",
        ("拥抱", "避免"): "从拥抱转向避免",
        ("great", "concern"): "从热情转向关注",
        ("trust", "verify"): "从信任转向验证",
        ("simple", "complex"): "从简单认知转向复杂理解",
    }

    for person, entries in person_timeline.items():
        if len(entries) < 2:
            continue

        # Sort by date
        sorted_entries = sorted(entries, key=lambda x: x[0])

        for i in range(len(sorted_entries) - 1):
            date_a, src_a, claim_a = sorted_entries[i]
            date_b, src_b, claim_b = sorted_entries[i + 1]

            # Check for shift signals
            combined = f"{claim_a} ||| {claim_b}"
            for (kw_a, kw_b), label in shift_signals.items():
                if kw_a.lower() in claim_a.lower() and kw_b.lower() in claim_b.lower():
                    evolutions.append({
                        "person": person,
                        "date_a": date_a,
                        "claim_a": claim_a,
                        "date_b": date_b,
                        "claim_b": claim_b,
                        "evolution": label,
                    })
                    break

            # Also use antonym detection for evolution
            tension = detect_contradiction_pair(claim_a, claim_b)
            if tension and not any(
                e["date_a"] == date_a and e["date_b"] == date_b
                for e in evolutions
                if e["person"] == person
            ):
                evolutions.append({
                    "person": person,
                    "date_a": date_a,
                    "claim_a": claim_a,
                    "date_b": date_b,
                    "claim_b": claim_b,
                    "evolution": f"观点转变 ({tension})",
                })

    return evolutions


def format_contradictions(contradictions: list[dict]) -> str:
    """Format contradiction results for display."""
    if not contradictions:
        return "✅ 未检测到明显矛盾"

    lines = ["⚡ 矛盾检测", "────────────────"]
    for c in contradictions:
        lines.append(f"📌 [{c['entity']}]:")
        lines.append(f'  • [{c["page_a"]}] claims: "{c["claim_a"][:80]}"')
        lines.append(f'  • [{c["page_b"]}] claims: "{c["claim_b"][:80]}"')
        lines.append(f'  → 张力: {c["tension"]}')
        lines.append("")

    return "\n".join(lines)


def format_evolution(evolutions: list[dict]) -> str:
    """Format evolution results for display."""
    if not evolutions:
        return ""

    lines = ["", "🔄 观点演变", "────────────────"]
    for e in evolutions:
        lines.append(f"📌 [{e['person']}] 观点演变:")
        lines.append(f'  • [{e["date_a"]}] believed: "{e["claim_a"][:80]}"')
        lines.append(f'  • [{e["date_b"]}] argued: "{e["claim_b"][:80]}"')
        lines.append(f'  → 演变: {e["evolution"]}')
        lines.append("")

    return "\n".join(lines)


def run_detection(wiki_dir: Path, verbose: bool = False) -> list[dict]:
    """
    Run full contradiction detection.

    Returns list of all findings (contradictions + evolutions).
    """
    entity_claims, person_timeline = extract_entity_claims(wiki_dir)
    contradictions = find_contradictions(entity_claims)
    evolutions = find_evolution(person_timeline)

    if verbose:
        print("📋 所有实体及其主张:")
        for entity, claims in sorted(entity_claims.items()):
            if len(claims) > 1:
                print(f"\n  [{entity}] ({len(claims)} claims)")
                for src, claim, section in claims:
                    print(f'    {src} ({section}): "{claim[:60]}"')
        if person_timeline:
            print("\n📋 人物时间线:")
            for person, entries in sorted(person_timeline.items()):
                print(f"\n  [{person}]")
                for date_ctx, src, claim in entries:
                    print(f'    [{date_ctx}] {src}: "{claim[:60]}"')
        print()

    # Print formatted results
    print(format_contradictions(contradictions))
    print(format_evolution(evolutions))

    total = len(contradictions) + len(evolutions)
    print(f"\n{'='*40}")
    print(f"Total: {len(contradictions)} contradiction(s) | {len(evolutions)} evolution(s)")

    return contradictions + evolutions


def main():
    parser = argparse.ArgumentParser(
        description="Detect contradictions and tensions across wiki pages"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all comparisons, not just contradictions",
    )
    parser.add_argument(
        "--wiki-dir",
        type=Path,
        default=DEFAULT_WIKI_DIR,
        help="Path to wiki directory",
    )
    args = parser.parse_args()

    wiki_dir = args.wiki_dir
    if not wiki_dir.exists():
        print(f"Error: wiki directory not found: {wiki_dir}")
        sys.exit(1)

    results = run_detection(wiki_dir, verbose=args.verbose)
    sys.exit(0)


if __name__ == "__main__":
    main()
