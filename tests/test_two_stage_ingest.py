"""Tests for two-stage ingest: Stage 1 + Stage 2 prompts and flow."""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest.prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_SYSTEM_PROMPT,
    ANALYSIS_PROMPT_TEMPLATE,
    SYNTHESIS_PROMPT_TEMPLATE,
    build_article_prompt,
    build_analysis_prompt,
    build_synthesis_prompt,
    build_reflect_prompt,
)
from ingest.worker import (
    _parse_frontmatter,
    _extract_body,
    _parse_json_content,
    _classify_error,
)


# ────────────────────────────────────────────
# Stage 1: Analysis prompt tests
# ────────────────────────────────────────────

class TestBuildAnalysisPrompt:
    def test_returns_string(self):
        meta = {"source": "test", "url": "https://example.com", "date": "2026-06-01"}
        result = build_analysis_prompt(meta, "Some article content here.")
        assert isinstance(result, str)

    def test_contains_metadata(self):
        meta = {"source": "TechCrunch", "url": "https://tc.com/article", "date": "2026-06-01"}
        result = build_analysis_prompt(meta, "Content")
        assert "TechCrunch" in result
        assert "https://tc.com/article" in result
        assert "2026-06-01" in result

    def test_contains_analysis_fields(self):
        meta = {"source": "test"}
        result = build_analysis_prompt(meta, "Content")
        assert "entities" in result
        assert "concepts" in result
        assert "key_claims" in result
        assert "contradictions_with" in result
        assert "open_questions" in result
        assert "source_quality" in result
        assert "related_wiki_topics" in result

    def test_truncates_long_content(self):
        meta = {}
        long_content = "x" * 15000
        result = build_analysis_prompt(meta, long_content, max_chars=10000)
        assert "内容已截断" in result
        assert len(result) < 15000

    def test_short_content_not_truncated(self):
        meta = {}
        content = "Short content"
        result = build_analysis_prompt(meta, content, max_chars=10000)
        assert "内容已截断" not in result
        assert content in result

    def test_uses_analysis_system(self):
        assert "深度分析" in ANALYSIS_SYSTEM_PROMPT or "分析" in ANALYSIS_SYSTEM_PROMPT


# ────────────────────────────────────────────
# Stage 2: Synthesis prompt tests
# ────────────────────────────────────────────

class TestBuildSynthesisPrompt:
    def test_returns_string(self):
        meta = {"source": "test"}
        stage1 = {
            "entities": [],
            "concepts": [],
            "key_claims": [],
            "contradictions_with": [],
            "open_questions": [],
            "source_quality": {
                "information_density": 0.7,
                "analytical_depth": 0.8,
                "actionability": 0.5,
                "uniqueness": 0.6,
                "timeliness": 0.4,
            },
            "related_wiki_topics": [],
        }
        result = build_synthesis_prompt(meta, "Content", stage1)
        assert isinstance(result, str)

    def test_includes_stage1_json(self):
        meta = {}
        stage1 = {
            "entities": [{"name": "test", "type": "concept", "role": "test"}],
            "source_quality": {"information_density": 0.9},
        }
        result = build_synthesis_prompt(meta, "Content", stage1)
        assert "test" in result
        assert "information_density" in result

    def test_contains_output_fields(self):
        meta = {}
        stage1 = {"entities": [], "concepts": [], "key_claims": [],
                  "source_quality": {}}
        result = build_synthesis_prompt(meta, "Content", stage1)
        assert "title_zh" in result
        assert "title_en" in result
        assert "summary_zh" in result
        assert "multi_quality_score" in result
        assert "contradiction_flags" in result
        assert "gap_indicators" in result

    def test_backward_compatible_fields(self):
        """Stage 2 output must include all fields from the original format."""
        meta = {}
        stage1 = {"entities": [], "concepts": [], "source_quality": {}}
        result = build_synthesis_prompt(meta, "Content", stage1)
        # Original fields must all be present
        for field in ["title_zh", "title_en", "summary_zh", "category",
                       "tags", "people", "orgs", "key_insights",
                       "sentiment", "quality_score", "related_topics"]:
            assert field in result

    def test_includes_original_content(self):
        meta = {"source": "blog"}
        stage1 = {"entities": [], "source_quality": {}}
        content = "This is the original article content about AI."
        result = build_synthesis_prompt(meta, content, stage1)
        assert content in result


# ────────────────────────────────────────────
# Reflect prompt tests
# ────────────────────────────────────────────

class TestBuildReflectPrompt:
    def test_returns_string(self):
        articles = [
            {"title": "Test 1", "summary": "Summary 1", "tags": ["ai"]},
        ]
        result = build_reflect_prompt(articles)
        assert isinstance(result, str)

    def test_includes_article_count(self):
        articles = [
            {"title": f"Art {i}", "summary": f"Sum {i}", "tags": ["ai"]}
            for i in range(5)
        ]
        result = build_reflect_prompt(articles)
        assert "5" in result  # article_count=5

    def test_includes_article_titles(self):
        articles = [
            {"title": "Machine Learning Trends", "summary": "About ML", "tags": ["ml"]},
        ]
        result = build_reflect_prompt(articles)
        assert "Machine Learning Trends" in result

    def test_includes_insights(self):
        articles = [
            {"title": "Test", "key_insights": ["AI is growing fast"]},
        ]
        result = build_reflect_prompt(articles)
        assert "AI is growing fast" in result

    def test_empty_articles(self):
        result = build_reflect_prompt([])
        assert isinstance(result, str)
        assert "0" in result


# ────────────────────────────────────────────
# Worker helpers
# ────────────────────────────────────────────

class TestParseJsonContent:
    def test_valid_json(self):
        parsed, err = _parse_json_content('{"key": "value"}')
        assert parsed == {"key": "value"}
        assert err is None

    def test_invalid_json(self):
        parsed, err = _parse_json_content("not json at all")
        assert parsed is None
        assert err is not None

    def test_json_embedded_in_text(self):
        parsed, err = _parse_json_content('Here is the result:\n{"key": "val"}\nEnd.')
        assert parsed == {"key": "val"}
        assert err is None

    def test_json_with_markdown_fences_stripped_elsewhere(self):
        """After markdown fence stripping, content might still have edge cases."""
        parsed, err = _parse_json_content('{"title": "hello", "score": 0.8}')
        assert parsed["title"] == "hello"
        assert parsed["score"] == 0.8


class TestClassifyError:
    def test_4xx_no_retry(self):
        import urllib.error
        e = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        assert _classify_error(e) == "no_retry"

    def test_429_retry(self):
        import urllib.error
        e = urllib.error.HTTPError("url", 429, "Rate limit", {}, None)
        assert _classify_error(e) == "retry"

    def test_5xx_retry(self):
        import urllib.error
        e = urllib.error.HTTPError("url", 500, "Server error", {}, None)
        assert _classify_error(e) == "retry"

    def test_timeout_retry_once(self):
        assert _classify_error(TimeoutError("timed out")) == "retry_once"


# ────────────────────────────────────────────
# Two-stage flow integration (mocked LLM)
# ────────────────────────────────────────────

class TestTwoStageFlow:
    """Test two-stage call_llm with mocked _single_llm_call."""

    def _make_stage1_response(self):
        return {
            "success": True,
            "content": json.dumps({
                "entities": [{"name": "Sam Altman", "type": "person", "role": "CEO"}],
                "concepts": [{"name": "AGI", "definition": "Artificial General Intelligence", "novelty": "emerging"}],
                "key_claims": [{"claim": "AGI is near", "evidence_type": "argument", "confidence": 0.6}],
                "contradictions_with": [],
                "open_questions": ["When will AGI arrive?"],
                "source_quality": {
                    "information_density": 0.8,
                    "analytical_depth": 0.7,
                    "actionability": 0.3,
                    "uniqueness": 0.9,
                    "timeliness": 0.6,
                },
                "related_wiki_topics": ["agi", "openai"],
            }),
            "input_tokens": 500,
            "output_tokens": 300,
            "raw_response": "",
            "latency_ms": 1000,
            "error_log": [],
        }

    def _make_stage2_response(self):
        return {
            "success": True,
            "content": json.dumps({
                "title_zh": "AGI发展趋势分析",
                "title_en": "AGI Development Trends",
                "summary_zh": "这是一篇关于AGI发展趋势的深度分析文章。" * 10,
                "category": "ai",
                "tags": ["agi", "openai", "future"],
                "people": [{"name": "Sam Altman", "role": "CEO", "org": "OpenAI"}],
                "orgs": ["OpenAI"],
                "key_insights": ["AGI is approaching faster than expected"],
                "sentiment": "positive",
                "quality_score": 0.85,
                "related_topics": ["artificial-intelligence"],
                "multi_quality_score": {
                    "information_density": 0.8,
                    "analytical_depth": 0.7,
                    "actionability": 0.3,
                    "uniqueness": 0.9,
                    "timeliness": 0.6,
                    "overall": 0.66,
                },
                "contradiction_flags": [],
                "gap_indicators": ["AGI safety measures"],
            }),
            "input_tokens": 800,
            "output_tokens": 400,
            "raw_response": "",
            "latency_ms": 2000,
            "error_log": [],
        }

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_two_stage_success(self, mock_key, mock_call):
        from ingest.worker import call_llm

        mock_call.side_effect = [
            self._make_stage1_response(),
            self._make_stage2_response(),
        ]

        with patch("builtins.open",
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(
                           read=MagicMock(return_value="---\nsource: test\n---\n\nContent about AGI.")
                       )),
                       __exit__=MagicMock(return_value=False),
                   ))):
            result = call_llm("/fake/path.md", provider="kimi", two_stage=True)

        assert result["success"] is True
        assert result["stage"] == "two_stage"
        assert result["input_tokens"] == 1300  # 500 + 800
        assert result["output_tokens"] == 700  # 300 + 400
        assert result["result"]["stage"] == "two_stage"
        assert "_stage1_analysis" in result["result"]
        assert result["result"]["_stage1_analysis"]["entities"][0]["name"] == "Sam Altman"

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_stage1_failure(self, mock_key, mock_call):
        from ingest.worker import call_llm

        mock_call.return_value = {
            "success": False,
            "error": "HTTP 500: server error",
            "input_tokens": 0,
            "output_tokens": 0,
            "raw_response": "",
            "latency_ms": 500,
            "error_log": [],
        }

        with patch("builtins.open",
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(
                           read=MagicMock(return_value="---\nsource: test\n---\n\nContent.")
                       )),
                       __exit__=MagicMock(return_value=False),
                   ))):
            result = call_llm("/fake/path.md", provider="kimi", two_stage=True)

        assert result["success"] is False
        assert result["stage"] == "stage1_failed"
        assert mock_call.call_count == 1  # No stage 2 call

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_single_shot_backward_compat(self, mock_key, mock_call):
        from ingest.worker import call_llm

        mock_call.return_value = {
            "success": True,
            "content": json.dumps({
                "title_zh": "测试文章",
                "title_en": "Test Article",
                "summary_zh": "这是一篇测试文章的摘要，包含足够长度。" * 3,
                "category": "ai",
                "tags": ["test", "ai"],
                "people": [],
                "orgs": [],
                "key_insights": ["Test insight"],
                "sentiment": "neutral",
                "quality_score": 0.5,
                "related_topics": [],
            }),
            "input_tokens": 200,
            "output_tokens": 100,
            "raw_response": "",
            "latency_ms": 500,
            "error_log": [],
        }

        with patch("builtins.open",
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(
                           read=MagicMock(return_value="---\nsource: test\n---\n\nTest content.")
                       )),
                       __exit__=MagicMock(return_value=False),
                   ))):
            result = call_llm("/fake/path.md", provider="kimi", two_stage=False)

        assert result["success"] is True
        assert result["stage"] == "single"
        assert mock_call.call_count == 1

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_stage2_failure(self, mock_key, mock_call):
        from ingest.worker import call_llm

        mock_call.side_effect = [
            self._make_stage1_response(),
            {
                "success": False,
                "error": "HTTP 429: rate limited",
                "input_tokens": 0,
                "output_tokens": 0,
                "raw_response": "",
                "latency_ms": 100,
                "error_log": [],
            },
        ]

        with patch("builtins.open",
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(
                           read=MagicMock(return_value="---\nsource: test\n---\n\nContent.")
                       )),
                       __exit__=MagicMock(return_value=False),
                   ))):
            result = call_llm("/fake/path.md", provider="kimi", two_stage=True)

        assert result["success"] is False
        assert result["stage"] == "stage2_failed"
        assert result["input_tokens"] == 500  # Stage 1 tokens still counted


# ────────────────────────────────────────────
# Backward compatibility tests
# ────────────────────────────────────────────

class TestBackwardCompatibility:
    """Ensure old behavior is preserved when two_stage=False."""

    def test_build_article_prompt_unchanged(self):
        meta = {"source": "test", "url": "http://x.com", "date": "2026-06-01",
                "category": "ai", "priority": "high"}
        content = "Some article content about AI safety."
        prompt = build_article_prompt(meta, content)
        assert "请分析以下文章" in prompt
        assert "test" in prompt
        assert "AI safety" in prompt

    def test_system_prompt_unchanged(self):
        assert "科技内容分析专家" in SYSTEM_PROMPT

    def test_call_llm_signature_has_two_stage_default_false(self):
        import inspect
        from ingest.worker import call_llm
        sig = inspect.signature(call_llm)
        assert "two_stage" in sig.parameters
        assert sig.parameters["two_stage"].default is False
