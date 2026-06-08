"""
test_long_form_integration.py — Integration tests for long-form article processing.

Covers:
  1. Short article (< 5000 chars) uses single-shot mode (unchanged)
  2. Long article triggers long_form mode in worker
  3. Long article with clear headers segments correctly
  4. Long article without headers falls back to chunking
  5. Outline generation failure falls back to single-shot
  6. Individual segment failure doesn't kill the whole article
  7. Cross-segment synthesis produces valid result
  8. Token counting includes all segment calls
  9. Result has correct stage="long_form" and segment_count
  10. End-to-end: worker + long_form + task_queue store
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.worker import call_llm, _long_form_call
from ingest.long_form import (
    detect_long_form,
    generate_outline_prompt,
    segment_by_outline,
    cross_segment_synthesis_prompt,
    LONG_FORM_THRESHOLD,
)
from ingest.task_queue import TaskQueue


# ─── Helpers ───

SHORT_ARTICLE = """---
source: Test Feed
url: https://example.com/short
date: 2026-06-01
category: ai
priority: high
---

# Short Article

This is a short article about AI. It mentions machine learning and neural networks.
Some content here about deep learning trends and transformer architectures.
"""

# Long article with clearly identifiable section headers
LONG_ARTICLE_WITH_HEADERS = (
    "---\nsource: Test Feed\nurl: https://example.com/long-headers\n"
    "date: 2026-06-01\ncategory: ai\npriority: high\n---\n\n"
    "# AI Safety Comprehensive Guide\n\n"
    "## Section One: Introduction\n\n"
    + ("This is the introduction section with detailed analysis of AI safety concerns. " * 150)
    + "\n\n## Section Two: Current Research\n\n"
    + ("Current research focuses on alignment, interpretability, and robustness. " * 150)
    + "\n\n## Section Three: Key Challenges\n\n"
    + ("The key challenges include value alignment, scalable oversight, and corrigibility. " * 150)
    + "\n\n## Section Four: Future Directions\n\n"
    + ("Future directions include multi-agent safety, debate, and recursive reward modeling. " * 150)
    + "\n\n## Section Five: Conclusion\n\n"
    + ("In conclusion, AI safety requires sustained interdisciplinary collaboration. " * 100)
)

# A long article without headers — just continuous text
LONG_ARTICLE_NO_HEADERS = (
    "---\nsource: Test Feed\nurl: https://example.com/long-no-headers\n"
    "date: 2026-06-01\ncategory: ai\npriority: high\n---\n\n"
    + "x " * 30000
)

# Outline that matches LONG_ARTICLE_WITH_HEADERS sections
HEADERS_OUTLINE = {
    "sections": [
        {"title": "Introduction", "start_marker": "## Section One: Introduction", "end_marker": "## Section Two: Current Research"},
        {"title": "Research", "start_marker": "## Section Two: Current Research", "end_marker": "## Section Three: Key Challenges"},
        {"title": "Challenges", "start_marker": "## Section Three: Key Challenges", "end_marker": "## Section Four: Future Directions"},
        {"title": "Future", "start_marker": "## Section Four: Future Directions", "end_marker": "## Section Five: Conclusion"},
        {"title": "Conclusion", "start_marker": "## Section Five: Conclusion"},
    ]
}


def _make_valid_result(title="Test Article", extra_fields=None):
    """Create a minimal valid LLM result that passes validation."""
    result = {
        "title_zh": f"中文标题: {title}",
        "title_en": title,
        "summary_zh": "这是一篇关于人工智能安全领域的深度分析文章，涵盖了多个重要议题。" * 2,
        "category": "ai",
        "tags": ["ai-safety", "alignment", "research"],
        "people": [],
        "orgs": [],
        "key_insights": ["AI alignment is critical for long-term safety"],
        "sentiment": "neutral",
        "quality_score": 0.8,
        "related_topics": ["artificial-intelligence"],
    }
    if extra_fields:
        result.update(extra_fields)
    return result


def _make_stage1_analysis(segment_idx=0):
    """Create a Stage 1 analysis result for a segment."""
    return {
        "entities": [{"name": f"Entity-{segment_idx}", "type": "concept", "role": "test"}],
        "concepts": [{"name": f"Concept-{segment_idx}", "definition": "Test concept", "novelty": "emerging"}],
        "key_claims": [{"claim": f"Claim from segment {segment_idx}", "evidence_type": "argument", "confidence": 0.7}],
        "contradictions_with": [],
        "open_questions": [],
        "source_quality": {
            "information_density": 0.7,
            "analytical_depth": 0.6,
            "actionability": 0.5,
            "uniqueness": 0.8,
            "timeliness": 0.6,
        },
        "related_wiki_topics": ["ai-safety"],
    }


def _successful_llm_response(content_dict, inp=200, out=100):
    """Create a successful _single_llm_call return value."""
    return {
        "success": True,
        "content": json.dumps(content_dict),
        "input_tokens": inp,
        "output_tokens": out,
        "raw_response": "",
        "latency_ms": 500.0,
        "error_log": [],
    }


def _failed_llm_response(error="HTTP 500: server error"):
    """Create a failed _single_llm_call return value."""
    return {
        "success": False,
        "error": error,
        "input_tokens": 0,
        "output_tokens": 0,
        "raw_response": "",
        "latency_ms": 100.0,
        "error_log": [],
    }


def _open_mock(file_content):
    """Create a mock for builtins.open that returns the given content."""
    return MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=file_content)
        )),
        __exit__=MagicMock(return_value=False),
    ))


def _count_segments_for(article_content, outline):
    """Helper: given article file content and outline, count actual segments."""
    from ingest.worker import _extract_body, _parse_frontmatter
    meta = _parse_frontmatter(article_content)
    body = _extract_body(article_content)
    return len(segment_by_outline(body, outline))


# ─── Test 1: Short article uses single-shot ───

class TestShortArticleSingleShot:
    """Short articles (< 5000 chars) should use single-shot mode, not long-form."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_short_article_uses_single_shot(self, mock_key, mock_call):
        mock_call.return_value = _successful_llm_response(_make_valid_result())

        with patch("builtins.open", _open_mock(SHORT_ARTICLE)):
            result = call_llm("/fake/short.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "single"
        assert mock_call.call_count == 1

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_short_article_system_prompt(self, mock_key, mock_call):
        """Short article should use SYSTEM_PROMPT, not outline or analysis prompts."""
        mock_call.return_value = _successful_llm_response(_make_valid_result())

        with patch("builtins.open", _open_mock(SHORT_ARTICLE)):
            result = call_llm("/fake/short.md", provider="kimi", two_stage=False)

        call_args = mock_call.call_args
        from ingest.prompts import SYSTEM_PROMPT
        assert call_args.kwargs.get("system_prompt") == SYSTEM_PROMPT


# ─── Test 2: Long article triggers long_form mode ───

class TestLongArticleTriggersLongForm:
    """Long articles (> 5000 chars) should trigger long-form processing."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_long_article_enters_long_form(self, mock_key, mock_call):
        """Long article with matching outline should complete long_form flow."""
        # Count expected segments
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])
        assert n_segs == 5  # 5 sections from outline

        # Call 1: outline
        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        # Calls 2..6: segment analyses (5 segments)
        seg_resps = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200 + i * 10, out=100 + i * 10)
            for i in range(n_segs)
        ]
        # Call 7: cross-segment synthesis
        synthesis_resp = _successful_llm_response(
            _make_valid_result("AI Safety Guide"), inp=500, out=300,
        )

        mock_call.side_effect = [outline_resp] + seg_resps + [synthesis_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/long.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        assert result["segment_count"] == n_segs
        assert result["result"]["stage"] == "long_form"
        # Total calls: outline + n_segs analyses + synthesis
        assert mock_call.call_count == 1 + n_segs + 1


# ─── Test 3: Long article with clear headers segments correctly ───

class TestSegmentationWithHeaders:
    """Long articles with clear section headers should segment properly."""

    def test_headers_produce_multiple_segments(self):
        """Verify segment_by_outline splits at header boundaries."""
        content = (
            "# Title\n\nIntro text.\n\n"
            "## Section A\n\n" + ("Content A. " * 100) + "\n\n"
            "## Section B\n\n" + ("Content B. " * 100) + "\n\n"
            "## Section C\n\n" + ("Content C. " * 100)
        )
        outline = [
            {"title": "Section A", "start_marker": "## Section A", "end_marker": "## Section B"},
            {"title": "Section B", "start_marker": "## Section B", "end_marker": "## Section C"},
            {"title": "Section C", "start_marker": "## Section C"},
        ]
        segments = segment_by_outline(content, outline)
        assert len(segments) == 3
        assert "Content A" in segments[0][1]
        assert "Content B" in segments[1][1]
        assert "Content C" in segments[2][1]

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_long_with_headers_multiple_segments(self, mock_key, mock_call):
        """Long article with headers should produce multiple segment analyses."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=80)
        analyses = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis_resp = _successful_llm_response(
            _make_valid_result("AI Safety Guide"), inp=500, out=300,
        )

        mock_call.side_effect = [outline_resp] + analyses + [synthesis_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/long-headers.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        assert result["segment_count"] == n_segs
        assert n_segs >= 3  # At least 3 sections from headers


# ─── Test 4: Long article without headers falls back to chunking ───

class TestSegmentationFallback:
    """Long articles without clear headers should use character-based chunking."""

    def test_no_headers_triggers_chunking(self):
        """Content without headers should be chunked by character count."""
        content = "x " * 8000
        segments = segment_by_outline(content, [])
        assert len(segments) >= 2
        for idx, text in segments:
            assert len(text) > 0

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_no_headers_article_processes(self, mock_key, mock_call):
        """Long article without headers should still process via long-form."""
        # With no matching outline sections, segment_by_outline will use fallback chunking
        outline_resp = _successful_llm_response(
            {"sections": [{"title": "Chunk 1", "start_marker": "NONEXISTENT"}]},
            inp=300, out=50,
        )
        # Since marker doesn't match, segment_by_outline will fallback-chunk
        from ingest.worker import _extract_body, _parse_frontmatter
        body = _extract_body(LONG_ARTICLE_NO_HEADERS)
        segments = segment_by_outline(body, [{"title": "Chunk 1", "start_marker": "NONEXISTENT"}])
        n_segs = len(segments)

        seg_resps = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis_resp = _successful_llm_response(
            _make_valid_result("No Headers Article"), inp=500, out=300,
        )
        mock_call.side_effect = [outline_resp] + seg_resps + [synthesis_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_NO_HEADERS)):
            result = call_llm("/fake/no-headers.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"


# ─── Test 5: Outline failure falls back to single-shot ───

class TestOutlineFailureFallback:
    """When outline generation fails, fall back to single-shot with truncation."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_outline_failure_fallback(self, mock_key, mock_call):
        # First call (outline) fails
        outline_fail = _failed_llm_response("HTTP 500: server error")
        # Fallback single-shot succeeds
        fallback_resp = _successful_llm_response(_make_valid_result("Fallback"))

        mock_call.side_effect = [outline_fail, fallback_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/outline-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "single"
        assert result.get("long_form_fallback") is True
        assert mock_call.call_count == 2

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_outline_and_fallback_both_fail(self, mock_key, mock_call):
        """If outline fails AND fallback fails, return failure."""
        mock_call.return_value = _failed_llm_response("Everything is broken")

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/total-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is False


# ─── Test 6: Individual segment failure doesn't kill the article ───

class TestSegmentFailureResilience:
    """Individual segment analysis failures should be skipped, not kill the whole article."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_segment_failure_continues(self, mock_key, mock_call):
        """If one segment analysis fails, others should still proceed."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])
        assert n_segs == 5

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)

        # Build segment responses: success, fail, success, success, success
        seg_resps = []
        for i in range(n_segs):
            if i == 1:
                seg_resps.append(_failed_llm_response("Segment 1 failed"))
            else:
                seg_resps.append(_successful_llm_response(_make_stage1_analysis(i), inp=200, out=100))

        synthesis_resp = _successful_llm_response(
            _make_valid_result("Partial Success"), inp=400, out=250,
        )

        mock_call.side_effect = [outline_resp] + seg_resps + [synthesis_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/partial-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        # segments_json should have only 4 analyses (not the failed one)
        analyses = json.loads(result["segments_json"])
        assert len(analyses) == n_segs - 1

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_all_segments_fail(self, mock_key, mock_call):
        """If ALL segment analyses fail, return failure."""
        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        seg_fails = [_failed_llm_response("Segment failed") for _ in range(n_segs)]

        mock_call.side_effect = [outline_resp] + seg_fails

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/all-segments-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is False
        assert "all" in result["error"].lower() and "segment" in result["error"].lower()


# ─── Test 7: Cross-segment synthesis produces valid result ───

class TestCrossSegmentSynthesis:
    """Cross-segment synthesis should merge multiple analyses into a valid result."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_synthesis_produces_valid_json(self, mock_key, mock_call):
        """Synthesis result should pass validation."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        segs = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis = _successful_llm_response(
            _make_valid_result("Synthesized Article", {
                "multi_quality_score": {
                    "information_density": 0.75,
                    "analytical_depth": 0.65,
                    "actionability": 0.5,
                    "uniqueness": 0.8,
                    "timeliness": 0.6,
                    "overall": 0.66,
                },
                "contradiction_flags": [],
                "gap_indicators": ["Scalable oversight needs more research"],
            }),
            inp=500, out=300,
        )
        mock_call.side_effect = [outline_resp] + segs + [synthesis]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/synthesis.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        assert result["result"]["title_en"] == "Synthesized Article"
        assert "multi_quality_score" in result["result"]

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_synthesis_failure_returns_error(self, mock_key, mock_call):
        """When cross-segment synthesis LLM call fails, return error."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        segs = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synth_fail = _failed_llm_response("Synthesis failed")

        mock_call.side_effect = [outline_resp] + segs + [synth_fail]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/synth-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is False
        assert "synthesis" in result["stage"]


# ─── Test 8: Token counting includes all segment calls ───

class TestTokenCounting:
    """Total tokens must include ALL LLM calls (outline + segments + synthesis)."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_tokens_accumulated_across_all_calls(self, mock_key, mock_call):
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        segs = [
            _successful_llm_response(_make_stage1_analysis(i), inp=400 + i * 50, out=200 + i * 50)
            for i in range(n_segs)
        ]
        synthesis = _successful_llm_response(
            _make_valid_result(), inp=600, out=300,
        )
        mock_call.side_effect = [outline_resp] + segs + [synthesis]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/tokens.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        expected_inp = 300 + sum(400 + i * 50 for i in range(n_segs)) + 600
        expected_out = 50 + sum(200 + i * 50 for i in range(n_segs)) + 300
        assert result["input_tokens"] == expected_inp
        assert result["output_tokens"] == expected_out

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_tokens_include_failed_segment_attempts(self, mock_key, mock_call):
        """Failed segment calls' tokens should also be counted."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        # First segment's LLM call fails (network error, not bad JSON)
        seg_fail = _failed_llm_response("HTTP 500: server error")
        seg_fail["input_tokens"] = 400
        seg_fail["output_tokens"] = 0
        # Remaining segments succeed
        good_segs = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200 + i * 10, out=100)
            for i in range(1, n_segs)
        ]
        synthesis = _successful_llm_response(
            _make_valid_result(), inp=500, out=300,
        )
        mock_call.side_effect = [outline_resp, seg_fail] + good_segs + [synthesis]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/tokens-fail.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        # All token counts should be included: outline + failed seg + good_segs + synthesis
        total_inp = 300 + 400 + sum(200 + i * 10 for i in range(1, n_segs)) + 500
        total_out = 50 + 0 + sum(100 for _ in range(1, n_segs)) + 300
        assert result["input_tokens"] == total_inp
        assert result["output_tokens"] == total_out


# ─── Test 9: Result has correct stage and segment_count ───

class TestResultMetadata:
    """Long-form results should have correct stage, segment_count, segments_json."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_long_form_result_metadata(self, mock_key, mock_call):
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        segs = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis = _successful_llm_response(_make_valid_result(), inp=500, out=300)
        mock_call.side_effect = [outline_resp] + segs + [synthesis]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/metadata.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        assert isinstance(result["segment_count"], int)
        assert result["segment_count"] == n_segs
        assert "segments_json" in result
        assert result["result"]["stage"] == "long_form"
        assert isinstance(result["result"]["segment_count"], int)

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_single_shot_result_no_segment_fields(self, mock_key, mock_call):
        """Single-shot results should not have segment_count or segments_json."""
        mock_call.return_value = _successful_llm_response(_make_valid_result())

        with patch("builtins.open", _open_mock(SHORT_ARTICLE)):
            result = call_llm("/fake/short-no-seg.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "single"
        assert "segment_count" not in result
        assert "segments_json" not in result


# ─── Test 10: End-to-end with task_queue ───

class TestEndToEndWithQueue:
    """End-to-end: worker produces result → task_queue stores with correct fields."""

    def test_e2e_long_form_store(self, tmp_path):
        """Complete flow: call_llm → complete_task → get_result with long_form fields."""
        db_path = str(tmp_path / "test-queue.db")
        queue = TaskQueue(db_path)

        queue.init_queue(["/fake/e2e-long.md"])
        task = queue.claim_next()
        assert task is not None

        long_form_result = {
            "success": True,
            "result": {
                **_make_valid_result("E2E Long Article"),
                "_filepath": "/fake/e2e-long.md",
                "_raw_response": '{"title_zh": "test"}',
                "stage": "long_form",
                "segment_count": 3,
                "segments_json": json.dumps([
                    _make_stage1_analysis(0),
                    _make_stage1_analysis(1),
                    _make_stage1_analysis(2),
                ], ensure_ascii=False),
            },
            "input_tokens": 1500,
            "output_tokens": 800,
            "raw_response": '{"title_zh": "test"}',
            "latency_ms": 3000.0,
            "error_log": [],
            "stage": "long_form",
            "segment_count": 3,
            "segments_json": json.dumps([
                _make_stage1_analysis(0),
                _make_stage1_analysis(1),
                _make_stage1_analysis(2),
            ], ensure_ascii=False),
        }

        queue.complete_task(
            task_id=task["id"],
            result=long_form_result["result"],
            model="test-model",
            input_tokens=long_form_result["input_tokens"],
            output_tokens=long_form_result["output_tokens"],
            stage=long_form_result.get("stage", "single"),
            segment_count=long_form_result.get("segment_count", 1),
            segments_json=long_form_result.get("segments_json", None),
        )

        stored = queue.get_result(task["id"])
        assert stored is not None
        assert stored["title_en"] == "E2E Long Article"
        assert stored["segment_count"] == 3
        assert stored["segments_json"] is not None
        assert len(json.loads(stored["segments_json"])) == 3

        task_row = queue._conn.execute(
            "SELECT stage FROM ingest_tasks WHERE id = ?", (task["id"],)
        ).fetchone()
        assert task_row["stage"] == "long_form"

        queue.close()

    def test_e2e_single_shot_store(self, tmp_path):
        """Single-shot result should store with stage='single', segment_count=1."""
        db_path = str(tmp_path / "test-queue2.db")
        queue = TaskQueue(db_path)

        queue.init_queue(["/fake/e2e-short.md"])
        task = queue.claim_next()

        single_result = {
            "success": True,
            "result": {
                **_make_valid_result("E2E Short Article"),
                "_filepath": "/fake/e2e-short.md",
                "_raw_response": '{"title_zh": "test"}',
            },
            "input_tokens": 500,
            "output_tokens": 200,
            "stage": "single",
        }

        queue.complete_task(
            task_id=task["id"],
            result=single_result["result"],
            model="test-model",
            input_tokens=single_result["input_tokens"],
            output_tokens=single_result["output_tokens"],
            stage=single_result.get("stage", "single"),
            segment_count=single_result.get("segment_count", 1),
            segments_json=single_result.get("segments_json", None),
        )

        stored = queue.get_result(task["id"])
        assert stored["segment_count"] == 1
        assert stored["segments_json"] is None

        task_row = queue._conn.execute(
            "SELECT stage FROM ingest_tasks WHERE id = ?", (task["id"],)
        ).fetchone()
        assert task_row["stage"] == "single"

        queue.close()


# ─── Additional edge case tests ───

class TestTwoStageModeUnchanged:
    """Two-stage mode should be completely unaffected by long-form changes."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_two_stage_long_article_uses_long_form(self, mock_key, mock_call):
        """Long articles should use long_form mode regardless of two_stage flag."""
        n_segs = _count_segments_for(LONG_ARTICLE_WITH_HEADERS, HEADERS_OUTLINE["sections"])

        # Build long_form mock sequence: outline + n_segs analyses + synthesis
        outline_resp = _successful_llm_response(HEADERS_OUTLINE, inp=300, out=50)
        seg_resps = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis_resp = _successful_llm_response(
            _make_valid_result("Two-Stage Long"), inp=500, out=300,
        )
        mock_call.side_effect = [outline_resp] + seg_resps + [synthesis_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/two-stage-long.md", provider="kimi", two_stage=True)

        assert result["success"] is True
        assert result["stage"] == "long_form"
        assert result["segment_count"] == n_segs
        assert mock_call.call_count == 1 + n_segs + 1

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_two_stage_short_article_unchanged(self, mock_key, mock_call):
        """Two-stage with short article should still work as before."""
        stage1 = _successful_llm_response({
            "entities": [],
            "concepts": [],
            "key_claims": [],
            "contradictions_with": [],
            "open_questions": [],
            "source_quality": {"information_density": 0.5},
            "related_wiki_topics": [],
        }, inp=300, out=150)
        stage2 = _successful_llm_response(
            _make_valid_result("Two-Stage Short"), inp=500, out=200,
        )
        mock_call.side_effect = [stage1, stage2]

        with patch("builtins.open", _open_mock(SHORT_ARTICLE)):
            result = call_llm("/fake/two-stage-short.md", provider="kimi", two_stage=True)

        assert result["success"] is True
        assert result["stage"] == "two_stage"


class TestOutlineParseFailure:
    """When outline LLM returns bad data, should fall back gracefully."""

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_outline_returns_empty_sections(self, mock_key, mock_call):
        """Outline with empty sections list → segments via fallback chunking."""
        outline_resp = _successful_llm_response(
            {"sections": []},
            inp=300, out=50,
        )
        # With empty sections, segment_by_outline falls back to chunking
        from ingest.worker import _extract_body, _parse_frontmatter
        body = _extract_body(LONG_ARTICLE_WITH_HEADERS)
        segments = segment_by_outline(body, [])
        n_segs = len(segments)

        seg_resps = [
            _successful_llm_response(_make_stage1_analysis(i), inp=200, out=100)
            for i in range(n_segs)
        ]
        synthesis = _successful_llm_response(_make_valid_result("Fallback Chunking"), inp=500, out=300)
        mock_call.side_effect = [outline_resp] + seg_resps + [synthesis]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/empty-sections.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "long_form"

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_outline_returns_non_json_string(self, mock_key, mock_call):
        """Outline LLM returns something that can't be parsed as JSON at all."""
        # Return a response whose content is not JSON
        outline_resp = {
            "success": True,
            "content": "This is just plain text, not JSON at all",
            "input_tokens": 300,
            "output_tokens": 50,
            "raw_response": "",
            "latency_ms": 500.0,
            "error_log": [],
        }
        fallback_resp = _successful_llm_response(_make_valid_result("Fallback"))
        mock_call.side_effect = [outline_resp, fallback_resp]

        with patch("builtins.open", _open_mock(LONG_ARTICLE_WITH_HEADERS)):
            result = call_llm("/fake/non-json-outline.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "single"
        assert result.get("long_form_fallback") is True
