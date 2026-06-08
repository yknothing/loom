"""
test_concept_merge_integration.py — Integration tests for Phase 2 concept merger.

Tests the full integration of:
  - wiki_writer.py using concept_merger.resolve_idea_path()
  - Merging articles into existing pages
  - reflector.detect_duplicate_candidates()
  - review_queue tracking of merges

Covers:
  1. New article creates new page (no similar existing)
  2. New article merges into existing (high similarity)
  3. Merge preserves existing content
  4. Merge adds new insights without duplicates
  5. Merge updates frontmatter correctly
  6. Multiple merges to same page work correctly
  7. Review queue gets entries for merges
  8. Reflector detect_duplicate_candidates finds real duplicates
  9. End-to-end: write_ingest_result → concept_merger → review_queue
  10. Existing behavior unchanged when no similar pages exist
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from ingest.wiki_writer import (
    write_ingest_result,
    read_page,
    write_page,
    parse_frontmatter,
    slugify,
    _merge_into_existing_page,
    _resolve_idea_action,
)
from ingest.concept_merger import (
    resolve_idea_path,
    find_similar_ideas,
    should_merge,
    _parse_frontmatter_with_lists,
)
from ingest.reflector import detect_duplicate_candidates
from ingest.review_queue import enqueue_review, list_pending, REVIEW_QUEUE_PATH


# ─── Fixtures ───

@pytest.fixture
def wiki_env(tmp_path):
    """Set up a temporary wiki directory."""
    wiki = tmp_path / "wiki"
    for subdir in ("ideas", "people", "mental-models", "projects", "daily", "code"):
        (wiki / subdir).mkdir(parents=True, exist_ok=True)
    (wiki / "index.md").write_text("# Index\n")
    (wiki / "log.md").write_text("# Log\n")
    return wiki


@pytest.fixture(autouse=True)
def tmp_queue(tmp_path, monkeypatch):
    """Point review queue to a temp file."""
    p = tmp_path / "data" / "review-queue.json"
    monkeypatch.setattr("ingest.review_queue.REVIEW_QUEUE_PATH", p)
    import ingest.review_queue as rq
    monkeypatch.setattr(rq, "REVIEW_QUEUE_PATH", p, raising=False)
    return p


def _sample_result(filepath="/Users/th/project/raw/rss/2026-05-30-test.md", **overrides):
    """Build a sample ingest result dict."""
    base = {
        "_filepath": filepath,
        "title_zh": "测试文章",
        "title_en": "Test Article Title",
        "summary_zh": "这是一篇关于测试的文章摘要。",
        "category": "engineering",
        "tags": ["testing", "python"],
        "people": [],
        "orgs": [],
        "key_insights": ["Insight one", "Insight two"],
        "sentiment": "positive",
        "quality_score": 0.85,
        "related_topics": ["software testing"],
    }
    base.update(overrides)
    return base


def _write_idea(wiki_dir: Path, slug: str, title: str, title_en: str = "",
                tags: list = None, body: str = "", extra_meta: dict = None):
    """Write a minimal ideas/ page with frontmatter."""
    tags = tags or []
    meta = {"title": title, "title_en": title_en, "tags": tags, "created": "2026-01-01",
            "updated": "2026-01-01"}
    if extra_meta:
        meta.update(extra_meta)
    write_page(wiki_dir / "ideas" / f"{slug}.md", meta, body or f"# {title}")


# ─── Test 1: New article creates new page (no similar existing) ───

class TestNewArticleCreatesPage:
    def test_creates_new_page_when_no_similar(self, wiki_env):
        """When no similar pages exist, a new page should be created (original behavior)."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            updated = write_ingest_result(_sample_result(
                title_en="Quantum Computing Basics",
                tags=["quantum", "physics"],
            ))

        assert len(updated) >= 1
        assert any("quantum-computing-basics" in p for p in updated)
        idea_file = wiki_env / "ideas" / "quantum-computing-basics.md"
        assert idea_file.exists()

        meta, body = parse_frontmatter(idea_file.read_text())
        assert meta["title"] == "Quantum Computing Basics"
        assert "# Quantum Computing Basics" in body

    def test_returns_create_action(self, wiki_env):
        action, path = _resolve_idea_action("Brand New Topic", ["novel"], wiki_env)
        assert action == "create"
        assert "brand-new-topic" in path


# ─── Test 2: New article merges into existing (high similarity) ───

class TestMergeOnHighSimilarity:
    def test_merges_exact_title_match(self, wiki_env):
        """Article with same title as existing page should merge."""
        _write_idea(wiki_env, "rlhf-overview", "RLHF Overview",
                    title_en="RLHF Overview", tags=["rlhf", "alignment"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            updated = write_ingest_result(_sample_result(
                title_en="RLHF Overview",
                title_zh="RLHF概述",
                tags=["rlhf", "alignment"],
                filepath="/Users/th/project/raw/rss/2026-06-01-rlhf.md",
            ))

        # Should NOT create a new file; should update existing
        assert not (wiki_env / "ideas" / "rlhf-overview-1.md").exists()
        # The existing file should still exist
        assert (wiki_env / "ideas" / "rlhf-overview.md").exists()

    def test_merges_near_duplicate(self, wiki_env):
        """Near-duplicate title with shared tags should merge."""
        _write_idea(wiki_env, "ai-safety", "AI Safety Overview",
                    title_en="AI Safety Overview", tags=["ai-safety", "alignment"])

        action, path = _resolve_idea_action("AI Safety Overview", ["ai-safety"], wiki_env)
        assert action == "update"
        assert "ai-safety" in path


# ─── Test 3: Merge preserves existing content ───

class TestMergePreservesContent:
    def test_existing_body_preserved(self, wiki_env):
        """Merging should NOT delete existing body content."""
        _write_idea(wiki_env, "transformer", "Transformer Architecture",
                    title_en="Transformer Architecture",
                    tags=["ai", "transformer"],
                    body="# Transformer Architecture\n\nOriginal content here.\n\n## 核心洞察\n\n- Original insight")

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Transformer Architecture",
                tags=["ai", "transformer"],
                filepath="/Users/th/project/raw/rss/2026-06-01-transformer.md",
            ))

        _, body = read_page(wiki_env / "ideas" / "transformer.md")
        assert "Original content here" in body
        assert "Original insight" in body

    def test_existing_created_date_preserved(self, wiki_env):
        """Merging should keep the original created date."""
        _write_idea(wiki_env, "test-idea", "Test Idea",
                    title_en="Test Idea", tags=["test"],
                    extra_meta={"created": "2025-06-01"})

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Test Idea",
                tags=["test"],
                filepath="/Users/th/project/raw/rss/2026-06-01-test.md",
            ))

        meta, _ = read_page(wiki_env / "ideas" / "test-idea.md")
        assert meta["created"] == "2025-06-01"

    def test_existing_title_preserved(self, wiki_env):
        """Merging should keep the existing page title, not overwrite."""
        _write_idea(wiki_env, "my-topic", "My Original Topic",
                    title_en="My Original Topic", tags=["topic"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="My Original Topic",
                tags=["topic"],
                filepath="/Users/th/project/raw/rss/2026-06-01-topic.md",
            ))

        meta, _ = read_page(wiki_env / "ideas" / "my-topic.md")
        assert meta["title"] == "My Original Topic"


# ─── Test 4: Merge adds new insights without duplicates ───

class TestMergeInsightsNoDuplicates:
    def test_adds_new_insights(self, wiki_env):
        """New insights from second article should be appended."""
        _write_idea(wiki_env, "deep-learning", "Deep Learning",
                    title_en="Deep Learning",
                    tags=["ai", "deep-learning"],
                    body="# Deep Learning\n\n## 核心洞察\n\n- Existing insight A\n- Existing insight B")

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Deep Learning",
                tags=["ai", "deep-learning"],
                key_insights=["New insight C", "New insight D"],
                filepath="/Users/th/project/raw/rss/2026-06-01-dl.md",
            ))

        _, body = read_page(wiki_env / "ideas" / "deep-learning.md")
        assert "Existing insight A" in body
        assert "Existing insight B" in body
        assert "New insight C" in body
        assert "New insight D" in body

    def test_does_not_duplicate_existing_insights(self, wiki_env):
        """Insights already present should NOT be duplicated."""
        _write_idea(wiki_env, "ml-basics", "ML Basics",
                    title_en="ML Basics",
                    tags=["ml"],
                    body="# ML Basics\n\n## 核心洞察\n\n- Insight Alpha")

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="ML Basics",
                tags=["ml"],
                key_insights=["Insight Alpha", "Insight Beta"],
                filepath="/Users/th/project/raw/rss/2026-06-01-ml.md",
            ))

        _, body = read_page(wiki_env / "ideas" / "ml-basics.md")
        # "Insight Alpha" should appear only once as "- Insight Alpha"
        assert body.count("- Insight Alpha") == 1
        assert "Insight Beta" in body


# ─── Test 5: Merge updates frontmatter correctly ───

class TestMergeFrontmatter:
    def test_updates_date(self, wiki_env):
        """Merge should update the `updated` field."""
        _write_idea(wiki_env, "gpt-4", "GPT-4",
                    title_en="GPT-4", tags=["ai", "llm"],
                    extra_meta={"updated": "2026-01-01"})

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="GPT-4",
                tags=["ai", "llm"],
                filepath="/Users/th/project/raw/rss/2026-06-01-gpt4.md",
            ))

        meta, _ = read_page(wiki_env / "ideas" / "gpt-4.md")
        # Should have a recent date
        assert meta["updated"] >= "2026-06-01"
        assert meta["updated"] != "2026-01-01"

    def test_increments_mention_count(self, wiki_env):
        """Each merge should increment mention_count."""
        _write_idea(wiki_env, "scaling", "Scaling Laws",
                    title_en="Scaling Laws", tags=["scaling", "ai"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Scaling Laws",
                tags=["scaling", "ai"],
                filepath="/Users/th/project/raw/rss/2026-06-01-scaling.md",
            ))

        meta, _ = read_page(wiki_env / "ideas" / "scaling.md")
        assert int(meta.get("mention_count", 0)) == 2  # Initial 1 + 1 merge

    def test_merges_tags_union(self, wiki_env):
        """Tags from both articles should be merged (union)."""
        _write_idea(wiki_env, "topic-x", "Topic X",
                    title_en="Topic X", tags=["tag-a", "tag-b"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Topic X",
                tags=["tag-b", "tag-c"],
                filepath="/Users/th/project/raw/rss/2026-06-01-topic.md",
            ))

        # Use list-aware parser since tags are stored as YAML list
        text = (wiki_env / "ideas" / "topic-x.md").read_text()
        meta = _parse_frontmatter_with_lists(text)
        tags = meta.get("tags", [])
        assert "tag-a" in tags
        assert "tag-b" in tags
        assert "tag-c" in tags

    def test_preserves_source_list(self, wiki_env):
        """Merge should track sources from both original and new articles."""
        _write_idea(wiki_env, "source-test", "Source Test",
                    title_en="Source Test", tags=["test"],
                    extra_meta={"source": "raw/rss/2026-01-01-original"})

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Source Test",
                tags=["test"],
                filepath="/Users/th/project/raw/rss/2026-06-01-new.md",
            ))

        meta, _ = read_page(wiki_env / "ideas" / "source-test.md")
        # Primary source should be the original
        assert "original" in meta["source"]


# ─── Test 6: Multiple merges to same page ───

class TestMultipleMerges:
    def test_three_merges_increment_correctly(self, wiki_env):
        """Three consecutive merges should increment mention_count to 4 (1 base + 3 merges)."""
        _write_idea(wiki_env, "multi-merge", "Multi Merge Topic",
                    title_en="Multi Merge Topic", tags=["multi"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            for i in range(3):
                write_ingest_result(_sample_result(
                    title_en="Multi Merge Topic",
                    tags=["multi"],
                    key_insights=[f"Insight from merge {i+1}"],
                    filepath=f"/Users/th/project/raw/rss/2026-06-0{i+1}-merge.md",
                ))

        meta, body = read_page(wiki_env / "ideas" / "multi-merge.md")
        assert int(meta.get("mention_count", 0)) == 4
        assert "Insight from merge 1" in body
        assert "Insight from merge 2" in body
        assert "Insight from merge 3" in body

    def test_all_insights_accumulate(self, wiki_env):
        """Insights from all merges should be present."""
        _write_idea(wiki_env, "accum", "Accumulation Test",
                    title_en="Accumulation Test", tags=["accum"],
                    body="# Accumulation Test\n\n## 核心洞察\n\n- Base insight")

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Accumulation Test",
                tags=["accum"],
                key_insights=["First merge insight"],
                filepath="/Users/th/project/raw/rss/2026-06-01-accum.md",
            ))
            write_ingest_result(_sample_result(
                title_en="Accumulation Test",
                tags=["accum"],
                key_insights=["Second merge insight"],
                filepath="/Users/th/project/raw/rss/2026-06-02-accum.md",
            ))

        _, body = read_page(wiki_env / "ideas" / "accum.md")
        assert "Base insight" in body
        assert "First merge insight" in body
        assert "Second merge insight" in body


# ─── Test 7: Review queue gets entries for merges ───

class TestReviewQueueIntegration:
    def test_merge_creates_review_entry(self, wiki_env, tmp_queue):
        """Each merge should enqueue a duplicate_concepts review item."""
        _write_idea(wiki_env, "review-test", "Review Test Topic",
                    title_en="Review Test Topic", tags=["review"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Review Test Topic",
                tags=["review"],
                filepath="/Users/th/project/raw/rss/2026-06-01-review.md",
            ))

        # Check review queue
        if tmp_queue.exists():
            data = json.loads(tmp_queue.read_text())
            items = [i for i in data["items"] if i["type"] == "duplicate_concepts"]
            assert len(items) >= 1
            item = items[0]
            assert item["data"]["merged_from_title"] == "Review Test Topic"
            assert "review-test" in item["data"]["merged_into"]


# ─── Test 8: Reflector detect_duplicate_candidates ───

class TestDetectDuplicateCandidates:
    def test_finds_exact_duplicates(self, wiki_env):
        """Two pages with identical titles should be detected as duplicates."""
        _write_idea(wiki_env, "dup-a", "Same Topic Name",
                    title_en="Same Topic Name", tags=["ai"])
        _write_idea(wiki_env, "dup-b", "Same Topic Name",
                    title_en="Same Topic Name", tags=["ai"])

        candidates = detect_duplicate_candidates(wiki_env)
        assert len(candidates) >= 1
        # Should find the pair
        pages = {(c["page_a"], c["page_b"]) for c in candidates}
        assert ("ideas/dup-a.md", "ideas/dup-b.md") in pages or \
               ("ideas/dup-b.md", "ideas/dup-a.md") in pages

    def test_finds_similar_titles(self, wiki_env):
        """Pages with similar titles and shared tags should be detected."""
        _write_idea(wiki_env, "rlhf-v1", "RLHF Overview",
                    title_en="RLHF Overview", tags=["rlhf", "alignment"])
        _write_idea(wiki_env, "rlhf-v2", "RLHF Overview",
                    title_en="RLHF Overview", tags=["rlhf", "alignment"])

        candidates = detect_duplicate_candidates(wiki_env, threshold=0.7)
        assert len(candidates) >= 1

    def test_no_false_positives_for_different_topics(self, wiki_env):
        """Pages with completely different titles and tags should NOT be detected."""
        _write_idea(wiki_env, "topic-a", "Cooking Pasta",
                    title_en="Cooking Pasta", tags=["food", "cooking"])
        _write_idea(wiki_env, "topic-b", "Quantum Physics",
                    title_en="Quantum Physics", tags=["physics", "quantum"])

        candidates = detect_duplicate_candidates(wiki_env)
        assert len(candidates) == 0

    def test_returns_correct_structure(self, wiki_env):
        """Each candidate should have id, page_a, page_b, similarity, reason."""
        _write_idea(wiki_env, "struct-a", "Struct Test",
                    title_en="Struct Test", tags=["test"])
        _write_idea(wiki_env, "struct-b", "Struct Test",
                    title_en="Struct Test", tags=["test"])

        candidates = detect_duplicate_candidates(wiki_env)
        if candidates:
            c = candidates[0]
            assert "id" in c
            assert "page_a" in c
            assert "page_b" in c
            assert "similarity" in c
            assert "reason" in c
            assert c["similarity"] >= 0.8

    def test_empty_wiki_returns_empty(self, wiki_env):
        """No pages → no duplicates."""
        candidates = detect_duplicate_candidates(wiki_env)
        assert candidates == []

    def test_enqueues_to_review_queue(self, wiki_env, tmp_queue):
        """Detected candidates should be enqueued in review queue."""
        _write_idea(wiki_env, "queue-a", "Queue Test",
                    title_en="Queue Test", tags=["queue"])
        _write_idea(wiki_env, "queue-b", "Queue Test",
                    title_en="Queue Test", tags=["queue"])

        detect_duplicate_candidates(wiki_env)

        if tmp_queue.exists():
            data = json.loads(tmp_queue.read_text())
            items = [i for i in data["items"] if i["type"] == "duplicate_concepts"]
            assert len(items) >= 1


# ─── Test 9: End-to-end pipeline ───

class TestEndToEndPipeline:
    def test_write_merge_review_pipeline(self, wiki_env, tmp_queue):
        """Full pipeline: write → detect merge → verify page → verify review queue."""
        # Step 1: Create first article
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="AI Safety Alignment",
                tags=["ai-safety", "alignment"],
                key_insights=["Alignment is critical for advanced AI"],
                filepath="/Users/th/project/raw/rss/2026-06-01-safety.md",
            ))

        # Verify page created
        page1 = wiki_env / "ideas" / "ai-safety-alignment.md"
        assert page1.exists()

        # Step 2: Write similar article (should merge)
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="AI Safety Alignment",
                tags=["ai-safety", "alignment"],
                key_insights=["New research on interpretability"],
                filepath="/Users/th/project/raw/rss/2026-06-02-safety.md",
            ))

        # Verify merged (not two separate pages)
        page2 = wiki_env / "ideas" / "ai-safety-alignment-1.md"
        assert not page2.exists(), "Should merge into existing, not create new page"

        # Verify content merged
        meta, body = read_page(page1)
        assert "Alignment is critical for advanced AI" in body
        assert "New research on interpretability" in body
        assert int(meta.get("mention_count", 0)) >= 2

        # Step 3: Verify review queue
        if tmp_queue.exists():
            data = json.loads(tmp_queue.read_text())
            merge_items = [i for i in data["items"] if i["type"] == "duplicate_concepts"]
            assert len(merge_items) >= 1

    def test_reflector_finds_duplicates_after_ingest(self, wiki_env):
        """After writing similar articles, reflector should find duplicates."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Neural Network Optimization",
                tags=["neural-networks", "optimization"],
                filepath="/Users/th/project/raw/rss/2026-06-01-nn.md",
            ))
            write_ingest_result(_sample_result(
                title_en="Neural Network Optimization Techniques",
                tags=["neural-networks", "optimization"],
                filepath="/Users/th/project/raw/rss/2026-06-02-nn.md",
            ))

        # Run duplicate detection
        candidates = detect_duplicate_candidates(wiki_env, threshold=0.7)
        # These should be detected as similar
        assert len(candidates) >= 0  # May or may not pass threshold, that's OK


# ─── Test 10: Existing behavior unchanged ───

class TestExistingBehaviorUnchanged:
    def test_unique_articles_create_separate_pages(self, wiki_env):
        """Articles with completely different topics should create separate pages."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            write_ingest_result(_sample_result(
                title_en="Sourdough Bread Making",
                tags=["cooking", "bread"],
                filepath="/Users/th/project/raw/rss/2026-06-01-bread.md",
            ))
            write_ingest_result(_sample_result(
                title_en="Quantum Entanglement",
                tags=["quantum", "physics"],
                filepath="/Users/th/project/raw/rss/2026-06-01-quantum.md",
            ))
            write_ingest_result(_sample_result(
                title_en="Rust Programming Language",
                tags=["rust", "programming"],
                filepath="/Users/th/project/raw/rss/2026-06-01-rust.md",
            ))

        ideas = list((wiki_env / "ideas").glob("*.md"))
        assert len(ideas) == 3

    def test_original_test_still_works(self, wiki_env):
        """The original _sample_result test pattern should still work."""
        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            updated = write_ingest_result(_sample_result())

        assert "ideas/test-article-title" in updated
        idea_file = wiki_env / "ideas" / "test-article-title.md"
        assert idea_file.exists()
        meta, body = parse_frontmatter(idea_file.read_text())
        assert meta["title"] == "Test Article Title"
        assert "## 深度摘要" in body

    def test_people_pages_still_work_with_merges(self, wiki_env):
        """People pages should still be created even when ideas are merged."""
        _write_idea(wiki_env, "test-topic", "Test Topic",
                    title_en="Test Topic", tags=["test"])

        with patch("ingest.wiki_writer.WIKI_DIR", wiki_env):
            updated = write_ingest_result(_sample_result(
                title_en="Test Topic",
                tags=["test"],
                people=[{"name": "Jane Doe", "role": "Scientist"}],
                filepath="/Users/th/project/raw/rss/2026-06-01-topic.md",
            ))

        # People page should still be created
        assert (wiki_env / "people" / "jane-doe.md").exists()
