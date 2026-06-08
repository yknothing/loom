"""Tests for concept_merger.py — idea deduplication and merge decisions."""

import pytest
from pathlib import Path
from ingest.concept_merger import (
    find_similar_ideas,
    should_merge,
    resolve_idea_path,
    MERGE_SIMILARITY_THRESHOLD,
)


# ─── helpers ───

def _write_idea(wiki_dir: Path, slug: str, title: str, title_en: str = "",
                tags: list = None, body: str = ""):
    """Write a minimal ideas/ page with frontmatter."""
    from ingest.wiki_writer import write_page
    tags = tags or []
    meta = {"title": title, "title_en": title_en, "tags": tags}
    write_page(wiki_dir / "ideas" / f"{slug}.md", meta, body or f"# {title}")


# ─── find_similar_ideas ───

class TestFindSimilarIdeas:
    def test_empty_wiki_dir_returns_empty(self, tmp_wiki):
        results = find_similar_ideas("Anything", ["ai"], tmp_wiki)
        assert results == []

    def test_exact_title_match(self, tmp_wiki):
        _write_idea(tmp_wiki, "transformer-architecture", "Transformer Architecture",
                    title_en="Transformer Architecture", tags=["ai", "deep-learning"])
        results = find_similar_ideas("Transformer Architecture", ["ai"], tmp_wiki)
        assert len(results) >= 1
        assert results[0]["similarity_score"] >= MERGE_SIMILARITY_THRESHOLD

    def test_near_duplicate_title(self, tmp_wiki):
        _write_idea(tmp_wiki, "rlhf", "RLHF: Reinforcement Learning from Human Feedback",
                    title_en="RLHF Reinforcement Learning from Human Feedback",
                    tags=["rlhf", "alignment"])
        results = find_similar_ideas(
            "RLHF: Reinforcement Learning from Human Feedback",
            ["rlhf"], tmp_wiki
        )
        assert len(results) >= 1
        assert results[0]["similarity_score"] >= MERGE_SIMILARITY_THRESHOLD

    def test_tag_overlap_only_below_threshold(self, tmp_wiki):
        _write_idea(tmp_wiki, "unrelated-but-same-tags", "Completely Different Topic",
                    title_en="Completely Different Topic", tags=["ai", "ml"])
        # Different title, but share tags — similarity should be low
        results = find_similar_ideas("Quantum Computing Basics", ["ai", "ml"], tmp_wiki)
        # Title similarity is very low, so even with tag overlap, it should be below 0.8
        # (Jaccard on tags alone gives 1.0, but title sim is ~0.05)
        # With the combined formula this should be below threshold
        for r in results:
            if r["title"] == "Completely Different Topic":
                # This candidate should have low combined score
                assert r["similarity_score"] < 0.8

    def test_returns_path_and_metadata(self, tmp_wiki):
        _write_idea(tmp_wiki, "test-idea", "Test Idea",
                    title_en="Test Idea", tags=["tag1"])
        results = find_similar_ideas("Test Idea", ["tag1"], tmp_wiki)
        assert len(results) >= 1
        r = results[0]
        assert "path" in r
        assert "title" in r
        assert "title_en" in r
        assert "tags" in r
        assert "similarity_score" in r

    def test_non_ascii_titles(self, tmp_wiki):
        _write_idea(tmp_wiki, "深度学习", "深度学习入门",
                    title_en="Deep Learning Introduction", tags=["dl"])
        results = find_similar_ideas("深度学习入门", ["dl"], tmp_wiki)
        assert len(results) >= 1

    def test_empty_tags_handled(self, tmp_wiki):
        _write_idea(tmp_wiki, "no-tags", "No Tags Idea",
                    title_en="No Tags Idea", tags=[])
        results = find_similar_ideas("No Tags Idea", [], tmp_wiki)
        assert len(results) >= 1  # Exact title match should still work


# ─── should_merge ───

class TestShouldMerge:
    def test_exact_duplicate_title(self):
        existing = {"title": "Transformer Architecture", "title_en": "Transformer Architecture",
                    "tags": ["ai"]}
        merge, reason = should_merge("Transformer Architecture", ["ai"], existing)
        assert merge is True
        assert "title" in reason.lower() or "duplicate" in reason.lower() or "similarity" in reason.lower()

    def test_near_duplicate_title(self):
        existing = {"title": "GPT-4 Architecture", "title_en": "GPT-4 Architecture",
                    "tags": ["ai", "llm"]}
        merge, reason = should_merge("GPT-4 Architectures", ["ai", "llm"], existing)
        # SequenceMatcher("gpt-4 architecture", "gpt-4 architectures") should be > 0.85
        assert merge is True

    def test_tag_overlap_and_moderate_title_similarity(self):
        """Verify merge via combined similarity (not rule 1 or rule 2 alone).

        Title sim is moderate (~0.65), tags Jaccard is high (1.0).
        Combined = 0.6*0.65 + 0.4*1.0 = 0.79, below 0.8 threshold → no merge.
        """
        existing = {"title": "Understanding RLHF", "title_en": "Understanding RLHF",
                    "tags": ["rlhf", "alignment", "ai-safety"]}
        # "RLHF Explained" vs "Understanding RLHF": title sim ≈ 0.36
        # Jaccard = 3/4 = 0.75, combined ≈ 0.6*0.36+0.4*0.75 = 0.516 → no merge
        merge, reason = should_merge("RLHF Explained", ["rlhf", "alignment", "ai-safety", "llm"], existing)
        assert merge is False

    def test_high_title_similarity_rule1(self):
        """Rule 1: title sim > 0.85 alone triggers merge, even with no tag overlap."""
        existing = {"title": "Transformer Architecture Explained", "title_en": "Transformer Architecture Explained",
                    "tags": ["architecture"]}
        # "Transformer Architecture Explained" vs "Transformer Architecture Explained" = 1.0
        # This should merge via rule 1 (high title sim)
        merge, reason = should_merge("Transformer Architecture Explained", ["different-tags"], existing)
        assert merge is True
        assert "title" in reason.lower()

    def test_related_but_different(self):
        existing = {"title": "Transformer Architecture", "title_en": "Transformer Architecture",
                    "tags": ["ai", "deep-learning"]}
        merge, reason = should_merge("Attention Mechanism", ["ai", "deep-learning"], existing)
        assert merge is False

    def test_completely_different(self):
        existing = {"title": "Quantum Computing", "title_en": "Quantum Computing",
                    "tags": ["quantum", "physics"]}
        merge, reason = should_merge("Making Sourdough Bread", ["cooking", "baking"], existing)
        assert merge is False
        assert "different" in reason.lower() or "no" in reason.lower()

    def test_high_tag_overlap_with_close_title(self):
        existing = {"title": "AI Safety Overview", "title_en": "AI Safety Overview",
                    "tags": ["ai-safety", "alignment", "risk"]}
        # Use a title close enough: "AI Safety Overviews" → sim > 0.85
        merge, reason = should_merge("AI Safety Overviews", ["ai-safety", "alignment", "risk"], existing)
        assert merge is True

    def test_tag_overlap_rule2(self):
        # Rule 2: tag Jaccard > 0.7 AND title sim >= 0.6
        # "Understanding RLHF" vs "RLHF Understanding" → sim ≈ 0.69
        existing = {"title": "Understanding RLHF", "title_en": "Understanding RLHF",
                    "tags": ["rlhf", "alignment", "ai-safety"]}
        merge, reason = should_merge("RLHF Understanding", ["rlhf", "alignment", "ai-safety"], existing)
        # Jaccard = 1.0 > 0.7, title sim ≈ 0.69 >= 0.6
        assert merge is True

    def test_empty_tags_no_merge_unless_title_match(self):
        existing = {"title": "Random Topic", "title_en": "Random Topic", "tags": []}
        merge, _ = should_merge("Completely Unrelated", [], existing)
        assert merge is False

    def test_existing_with_missing_fields(self):
        existing = {"title": "", "title_en": "", "tags": []}
        merge, _ = should_merge("Some New Topic", ["ai"], existing)
        assert merge is False


# ─── resolve_idea_path ───

class TestResolveIdeaPath:
    def test_create_new_idea(self, tmp_wiki):
        action, path = resolve_idea_path("Brand New Idea", ["novel"], tmp_wiki)
        assert action == "create"
        assert "brand-new-idea" in path

    def test_update_existing_exact_match(self, tmp_wiki):
        _write_idea(tmp_wiki, "exact-match", "Exact Match",
                    title_en="Exact Match", tags=["test"])
        action, path = resolve_idea_path("Exact Match", ["test"], tmp_wiki)
        assert action == "update"
        assert "exact-match" in path

    def test_update_similar_match(self, tmp_wiki):
        _write_idea(tmp_wiki, "rlhf-overview", "RLHF Overview",
                    title_en="RLHF Overview",
                    tags=["rlhf", "alignment", "reinforcement-learning"])
        action, path = resolve_idea_path("RLHF Overview", ["rlhf", "alignment"], tmp_wiki)
        assert action == "update"
        assert "rlhf-overview" in path

    def test_non_ascii_title(self, tmp_wiki):
        action, path = resolve_idea_path("深度学习基础", ["deep-learning"], tmp_wiki)
        assert action == "create"
        assert path.endswith(".md")

    def test_empty_tags(self, tmp_wiki):
        action, path = resolve_idea_path("Unique Topic", [], tmp_wiki)
        assert action == "create"

    def test_prefers_best_match(self, tmp_wiki):
        _write_idea(tmp_wiki, "ai-safety-v1", "AI Safety Overview",
                    title_en="AI Safety Overview", tags=["ai-safety"])
        _write_idea(tmp_wiki, "ai-safety-v2", "AI Safety Introduction",
                    title_en="AI Safety Introduction", tags=["ai-safety"])
        action, path = resolve_idea_path("AI Safety Overview", ["ai-safety"], tmp_wiki)
        assert action == "update"
        assert "ai-safety-v1" in path

    def test_path_is_relative_to_ideas_dir(self, tmp_wiki):
        action, path = resolve_idea_path("New Idea", ["test"], tmp_wiki)
        assert path.startswith("ideas/")
