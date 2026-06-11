"""
Tests for contradiction-detect.py
"""

import pytest
from pathlib import Path
import tempfile
import os

# Add scripts dir to path so we can import
import importlib

_cd = importlib.import_module("contradiction-detect")

extract_sections = _cd.extract_sections
extract_claims_from_line = _cd.extract_claims_from_line
extract_entity_claims = _cd.extract_entity_claims
detect_contradiction_pair = _cd.detect_contradiction_pair
find_contradictions = _cd.find_contradictions
find_evolution = _cd.find_evolution
format_contradictions = _cd.format_contradictions
format_evolution = _cd.format_evolution
run_detection = _cd.run_detection
ANTONYM_PAIRS = _cd.ANTONYM_PAIRS
TENSION_KEYWORDS = _cd.TENSION_KEYWORDS


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def wiki_tmp(tmp_path):
    """Create a temporary wiki with test pages."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    # ideas/
    ideas = wiki / "ideas"
    ideas.mkdir()

    (ideas / "alpha.md").write_text(
        """---
title: Alpha
created: 2026-01-01
updated: 2026-01-01
---

## 核心观点

Alpha is the future of everything. It is important and powerful.
- Alpha will change the world
- Alpha is safe and beneficial

## 争议

Some say Alpha is overhyped and dangerous.
""",
        encoding="utf-8",
    )

    (ideas / "beta.md").write_text(
        """---
title: Beta
created: 2026-01-01
updated: 2026-01-01
---

## 核心观点

Beta is a competing concept. It is complex but necessary.
- Beta is necessary for success
- Beta is good and useful

## 详细内容

- Alpha is overhyped
- Alpha is dangerous for society
""",
        encoding="utf-8",
    )

    # people/
    people = wiki / "people"
    people.mkdir()

    (people / "alice.md").write_text(
        """---
name: Alice
updated: 2026-01-01
---

## 观点演变

### 2020:
Alice was optimistic about AI. AI is great and simple.

### 2024:
AI is complex and we need more rigor. The challenges are real.

## 核心思想

- Technology is the future
""",
        encoding="utf-8",
    )

    return wiki


# ── Test extract_sections ───────────────────────────────────────────────────


def test_extract_sections_basic():
    text = "# Title\n\n## Section A\nline1\nline2\n\n## Section B\nline3"
    sections = extract_sections(text)
    assert "Section A" in sections
    assert "Section B" in sections
    assert "line1" in sections["Section A"]
    assert "line3" in sections["Section B"]


def test_extract_sections_empty():
    text = "Just a paragraph with no headers"
    sections = extract_sections(text)
    # Default section (empty string key)
    assert "" in sections
    assert sections[""] == ["Just a paragraph with no headers"]


# ── Test extract_claims_from_line ───────────────────────────────────────────


def test_extract_claims_bullet():
    claims = extract_claims_from_line("- This is a bullet claim")
    assert len(claims) == 1
    assert claims[0] == "This is a bullet claim"


def test_extract_claims_blockquote():
    claims = extract_claims_from_line("> This is a quoted claim")
    assert len(claims) == 1
    assert "quoted claim" in claims[0]


def test_extract_claims_short_line_skipped():
    claims = extract_claims_from_line("- hi")
    assert len(claims) == 0


def test_extract_claims_long_line():
    claims = extract_claims_from_line(
        "This is a substantial line that is long enough to be a claim"
    )
    assert len(claims) == 1


# ── Test detect_contradiction_pair ──────────────────────────────────────────


def test_contradiction_will_wont():
    result = detect_contradiction_pair("AI will succeed", "AI won't succeed")
    assert result is not None
    assert "optimistic" in result.lower() or "pessimistic" in result.lower()


def test_contradiction_future_overhyped():
    result = detect_contradiction_pair(
        "Alpha is the future", "Alpha is overhyped"
    )
    assert result is not None


def test_contradiction_good_bad():
    result = detect_contradiction_pair("This is good", "This is bad")
    assert result is not None


def test_contradiction_safe_dangerous():
    result = detect_contradiction_pair("AI is safe", "AI is dangerous")
    assert result is not None


def test_no_contradiction():
    result = detect_contradiction_pair("AI is fast", "AI is efficient")
    assert result is None


def test_contradiction_chinese():
    result = detect_contradiction_pair("AI是未来", "AI不是未来")
    assert result is not None


def test_contradiction_tension_keywords():
    result = detect_contradiction_pair(
        "支持者认为这是好事", "批评者说这是坏事"
    )
    assert result is not None


# ── Test extract_entity_claims ──────────────────────────────────────────────


def test_extract_entity_claims(wiki_tmp):
    entity_claims, person_timeline = extract_entity_claims(wiki_tmp)

    # alpha page should have claims
    assert "alpha" in entity_claims
    assert len(entity_claims["alpha"]) > 0

    # beta page should have claims too
    assert "beta" in entity_claims
    assert len(entity_claims["beta"]) > 0

    # person timeline
    assert "alice" in person_timeline
    assert len(person_timeline["alice"]) >= 2


# ── Test find_contradictions ────────────────────────────────────────────────


def test_find_contradictions(wiki_tmp):
    entity_claims, _ = extract_entity_claims(wiki_tmp)
    contradictions = find_contradictions(entity_claims)

    # Should find contradictions between alpha (future/safe) and beta (overhyped/dangerous)
    assert len(contradictions) > 0

    # Check structure
    for c in contradictions:
        assert "entity" in c
        assert "page_a" in c
        assert "claim_a" in c
        assert "page_b" in c
        assert "claim_b" in c
        assert "tension" in c


# ── Test find_evolution ─────────────────────────────────────────────────────


def test_find_evolution(wiki_tmp):
    _, person_timeline = extract_entity_claims(wiki_tmp)
    evolutions = find_evolution(person_timeline)

    # Alice should have evolution from optimistic to complex/rigor
    assert len(evolutions) > 0
    alice_evs = [e for e in evolutions if e["person"] == "alice"]
    assert len(alice_evs) > 0


# ── Test format functions ───────────────────────────────────────────────────


def test_format_contradictions_empty():
    result = format_contradictions([])
    assert "未检测到明显矛盾" in result


def test_format_contradictions_with_data():
    contradictions = [
        {
            "entity": "Test",
            "page_a": "ideas/a.md",
            "claim_a": "X is the future",
            "page_b": "ideas/b.md",
            "claim_b": "X is overhyped",
            "tension": "optimistic vs skeptical",
        }
    ]
    result = format_contradictions(contradictions)
    assert "矛盾检测" in result
    assert "Test" in result
    assert "optimistic vs skeptical" in result


def test_format_evolution_empty():
    result = format_evolution([])
    assert result == ""


def test_format_evolution_with_data():
    evolutions = [
        {
            "person": "Alice",
            "date_a": "2020",
            "claim_a": "AI is great",
            "date_b": "2024",
            "claim_b": "AI is complex",
            "evolution": "从乐观转向审慎",
        }
    ]
    result = format_evolution(evolutions)
    assert "观点演变" in result
    assert "Alice" in result
    assert "从乐观转向审慎" in result


# ── Test run_detection integration ──────────────────────────────────────────


def test_run_detection_integration(wiki_tmp, capsys):
    results = run_detection(wiki_tmp, verbose=False)
    captured = capsys.readouterr()

    # Should produce output
    assert len(results) > 0
    assert "矛盾检测" in captured.out or "观点演变" in captured.out or "未检测到" in captured.out


def test_run_detection_verbose(wiki_tmp, capsys):
    results = run_detection(wiki_tmp, verbose=True)
    captured = capsys.readouterr()

    # Verbose should show entity listing
    assert "所有实体" in captured.out


# ── Test with real wiki pages ──────────────────────────────────────────────


def test_real_wiki_contradictions():
    """Test against the actual wiki content."""
    wiki_dir = Path(__file__).resolve().parent.parent / "wiki"
    if not wiki_dir.exists():
        pytest.skip("No wiki directory")
    ideas_dir = wiki_dir / "ideas"
    if not ideas_dir.exists() or not any(ideas_dir.glob("*.md")):
        pytest.skip("Wiki has no idea pages yet")

    entity_claims, person_timeline = extract_entity_claims(wiki_dir)

    # Should have extracted claims from multiple pages
    assert len(entity_claims) > 0

    # Vibe coding should have claims
    assert "vibe coding" in entity_claims

    # Andrej Karpathy should have timeline entries
    assert "andrej karpathy" in person_timeline

    contradictions = find_contradictions(entity_claims)
    # Real wiki may or may not have detectable contradictions
    # (depends on content) — just ensure no crash
    assert isinstance(contradictions, list)
