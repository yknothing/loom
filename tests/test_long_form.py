"""Tests for long_form.py — long article detection and segmentation."""

import pytest
from ingest.long_form import (
    detect_long_form,
    generate_outline_prompt,
    segment_by_outline,
    cross_segment_synthesis_prompt,
    LONG_FORM_THRESHOLD,
)


# ─── detect_long_form ───

class TestDetectLongForm:
    def test_short_content_is_not_long(self):
        assert detect_long_form("Short content") is False

    def test_exactly_at_threshold_is_not_long(self):
        content = "x" * LONG_FORM_THRESHOLD
        assert detect_long_form(content) is False

    def test_one_over_threshold_is_long(self):
        content = "x" * (LONG_FORM_THRESHOLD + 1)
        assert detect_long_form(content) is True

    def test_very_long_content(self):
        content = "x" * (LONG_FORM_THRESHOLD + 1)
        assert detect_long_form(content) is True

    def test_empty_content(self):
        assert detect_long_form("") is False

    def test_multibyte_chars_counted_correctly(self):
        # Each Chinese char is 1 in Python len() — count chars not bytes
        content = "中" * (LONG_FORM_THRESHOLD + 1)
        assert detect_long_form(content) is True


# ─── generate_outline_prompt ───

class TestGenerateOutlinePrompt:
    def test_returns_string(self):
        prompt = generate_outline_prompt("Some content here")
        assert isinstance(prompt, str)

    def test_mentions_json(self):
        prompt = generate_outline_prompt("Content")
        assert "JSON" in prompt or "json" in prompt

    def test_mentions_sections(self):
        prompt = generate_outline_prompt("Content")
        assert "section" in prompt.lower()

    def test_includes_content(self):
        content = "This is unique content about quantum computing"
        prompt = generate_outline_prompt(content)
        assert content in prompt

    def test_prompt_is_not_empty_for_empty_content(self):
        prompt = generate_outline_prompt("")
        assert len(prompt) > 0


# ─── segment_by_outline ───

class TestSegmentByOutline:
    def test_single_section(self):
        content = "# Intro\n\nSome intro text.\n\n# Body\n\nBody text here."
        outline = [
            {"title": "Intro", "start_marker": "# Intro", "end_marker": "# Body"},
            {"title": "Body", "start_marker": "# Body"},
        ]
        segments = segment_by_outline(content, outline)
        assert len(segments) >= 2
        # Each segment is (index, text)
        for idx, text in segments:
            assert isinstance(idx, int)
            assert isinstance(text, str)
            assert len(text) > 0

    def test_outline_with_markers(self):
        content = (
            "# Section A\n\nContent A paragraph.\n\n"
            "# Section B\n\nContent B paragraph.\n\n"
            "# Section C\n\nContent C paragraph."
        )
        outline = [
            {"title": "Section A", "start_marker": "# Section A", "end_marker": "# Section B"},
            {"title": "Section B", "start_marker": "# Section B", "end_marker": "# Section C"},
            {"title": "Section C", "start_marker": "# Section C"},
        ]
        segments = segment_by_outline(content, outline)
        assert len(segments) == 3
        assert "Section A" in segments[0][1] or "Content A" in segments[0][1]
        assert "Content B" in segments[1][1]

    def test_fallback_chunking_when_no_markers_match(self):
        # Outline has markers that don't exist in content
        content = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3.\n\n" * 500
        outline = [
            {"title": "Part 1", "start_marker": "NONEXISTENT_MARKER"},
        ]
        segments = segment_by_outline(content, outline)
        # Should fall back to chunking
        assert len(segments) >= 1
        for idx, text in segments:
            assert len(text) <= LONG_FORM_THRESHOLD

    def test_empty_content_returns_empty(self):
        segments = segment_by_outline("", [{"title": "Intro"}])
        assert segments == []

    def test_empty_outline_falls_back_to_chunking(self):
        content = "Some paragraph.\n\n" * 1000  # > 5000 chars
        segments = segment_by_outline(content, [])
        assert len(segments) >= 1
        for idx, text in segments:
            assert len(text) <= LONG_FORM_THRESHOLD
            assert len(text) >= 50  # Min reasonable size

    def test_segments_within_size_limits(self):
        content = "Word " * 20000  # ~100k chars
        outline = [{"title": "Section 1"}, {"title": "Section 2"}]
        segments = segment_by_outline(content, outline)
        for idx, text in segments:
            assert len(text) <= LONG_FORM_THRESHOLD

    def test_preserves_segment_indices(self):
        content = "# A\n\nText A.\n\n# B\n\nText B.\n\n# C\n\nText C."
        outline = [
            {"title": "A", "start_marker": "# A"},
            {"title": "B", "start_marker": "# B"},
            {"title": "C", "start_marker": "# C"},
        ]
        segments = segment_by_outline(content, outline)
        indices = [idx for idx, _ in segments]
        assert indices == list(range(len(segments)))

    def test_single_massive_section_fallback(self):
        # One section, very long, no headers — should chunk
        content = "A" * (LONG_FORM_THRESHOLD + 1)
        outline = [{"title": "Whole thing"}]
        segments = segment_by_outline(content, outline)
        assert len(segments) >= 2  # Should be split into multiple chunks


# ─── cross_segment_synthesis_prompt ───

class TestCrossSegmentSynthesisPrompt:
    def test_returns_string(self):
        analyses = [{"segment": 0, "summary": "Part 1 summary"}]
        prompt = cross_segment_synthesis_prompt(analyses)
        assert isinstance(prompt, str)

    def test_includes_all_analyses(self):
        analyses = [
            {"segment": 0, "summary": "First part"},
            {"segment": 1, "summary": "Second part"},
        ]
        prompt = cross_segment_synthesis_prompt(analyses)
        assert "First part" in prompt
        assert "Second part" in prompt

    def test_mentions_synthesis(self):
        prompt = cross_segment_synthesis_prompt([{"segment": 0, "summary": "x"}])
        assert "synthesis" in prompt.lower() or "综合" in prompt or "合并" in prompt

    def test_empty_analyses_list(self):
        prompt = cross_segment_synthesis_prompt([])
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_single_analysis(self):
        analyses = [{"segment": 0, "summary": "Only one segment", "tags": ["test"]}]
        prompt = cross_segment_synthesis_prompt(analyses)
        assert "Only one segment" in prompt

    def test_preserves_segment_indices(self):
        analyses = [
            {"segment": 0, "summary": "Part 0"},
            {"segment": 2, "summary": "Part 2"},
        ]
        prompt = cross_segment_synthesis_prompt(analyses)
        assert "0" in prompt
        assert "2" in prompt
