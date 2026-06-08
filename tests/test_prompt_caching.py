"""Tests for prompt caching in worker.py (_single_llm_call).

Verifies:
1. Anthropic format sends cache_control in system prompt
2. OpenAI format is unaffected (no cache_control)
3. Cache stats are extracted from Anthropic responses
4. Cache stats default to 0 on failures
5. Fallback: if cache_control causes a 400, retry without it
6. Cache stats propagate through call_llm (single-shot and two-stage)
"""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import urllib.error
import io

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest.worker import _single_llm_call, call_llm


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _mock_urlopen_success(response_json, api_format="anthropic"):
    """Return a context manager that mocks urlopen with a successful response."""
    raw = json.dumps(response_json).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_urlopen_http_error(status_code, body=""):
    """Raise HTTPError."""
    err = urllib.error.HTTPError(
        "http://test", status_code, "Error", {}, io.BytesIO(body.encode())
    )
    return err


# ────────────────────────────────────────────
# 1. Anthropic payload includes cache_control
# ────────────────────────────────────────────

class TestAnthropicCacheControl:
    """Verify Anthropic format sends cache_control in system blocks."""

    @patch("ingest.worker.urllib.request.urlopen")
    def test_system_is_array_with_cache_control(self, mock_urlopen):
        resp_json = {
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_creation_input_tokens": 80, "cached_input_tokens": 0},
            "content": [{"type": "text", "text": '{"key": "val"}'}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="You are a helpful assistant.",
            base_url="https://api.kimi.com/coding",
            model="kimi-for-coding",
            max_tokens=32768,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="test-key",
            max_retries=0,
            timeout=10,
            use_cache=True,
        )

        assert result["success"] is True
        # Verify the payload sent to the API
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # Request object
        # Read the data that was sent
        data = call_args[0][1]
        payload = json.loads(data)
        assert isinstance(payload["system"], list)
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert payload["system"][0]["text"] == "You are a helpful assistant."

    @patch("ingest.worker.urllib.request.urlopen")
    def test_cache_stats_extracted(self, mock_urlopen):
        resp_json = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 80,
                "cached_input_tokens": 20,
            },
            "content": [{"type": "text", "text": '{"result": true}'}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.kimi.com/coding",
            model="kimi",
            max_tokens=1000,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="key",
            max_retries=0,
        )

        assert result["success"] is True
        assert result["cache_creation_input_tokens"] == 80
        assert result["cached_input_tokens"] == 20

    @patch("ingest.worker.urllib.request.urlopen")
    def test_cache_stats_default_zero_when_missing(self, mock_urlopen):
        """Older APIs may not return cache fields."""
        resp_json = {
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "text", "text": '{"result": true}'}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.kimi.com/coding",
            model="kimi",
            max_tokens=1000,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="key",
            max_retries=0,
        )

        assert result["success"] is True
        assert result["cache_creation_input_tokens"] == 0
        assert result["cached_input_tokens"] == 0

    @patch("ingest.worker.urllib.request.urlopen")
    def test_system_is_plain_string_by_default(self, mock_urlopen):
        """Without use_cache=True, Anthropic format sends plain string system."""
        resp_json = {
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "text", "text": '{"key": "val"}'}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="You are a helpful assistant.",
            base_url="https://api.kimi.com/coding",
            model="kimi-for-coding",
            max_tokens=32768,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="test-key",
            max_retries=0,
            timeout=10,
            # use_cache not set → defaults to False
        )

        assert result["success"] is True
        data = mock_urlopen.call_args[0][1]
        payload = json.loads(data)
        # System should be a plain string, not an array with cache_control
        assert isinstance(payload["system"], str)
        assert payload["system"] == "You are a helpful assistant."


# ────────────────────────────────────────────
# 2. OpenAI format unaffected
# ────────────────────────────────────────────

class TestOpenAINoCacheControl:
    """Verify OpenAI format does NOT add cache_control."""

    @patch("ingest.worker.urllib.request.urlopen")
    def test_openai_no_cache_control_in_payload(self, mock_urlopen):
        resp_json = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [{"message": {"content": '{"result": true}'}}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="You are helpful.",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            max_tokens=8192,
            api_format="openai",
            auth_header="Bearer",
            api_key="key",
            max_retries=0,
        )

        assert result["success"] is True
        data = mock_urlopen.call_args[0][1]
        payload = json.loads(data)
        # OpenAI format: system is a plain string message
        messages = payload["messages"]
        system_msg = [m for m in messages if m["role"] == "system"][0]
        assert isinstance(system_msg["content"], str)
        assert "cache_control" not in str(payload)

    @patch("ingest.worker.urllib.request.urlopen")
    def test_openai_cache_stats_zero(self, mock_urlopen):
        resp_json = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "choices": [{"message": {"content": '{"result": true}'}}],
        }
        mock_urlopen.return_value = _mock_urlopen_success(resp_json)

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.deepseek.com/v1",
            model="deepseek",
            max_tokens=1000,
            api_format="openai",
            auth_header="Bearer",
            api_key="key",
            max_retries=0,
        )

        assert result["cache_creation_input_tokens"] == 0
        assert result["cached_input_tokens"] == 0


# ────────────────────────────────────────────
# 3. Cache stats zero on failure
# ────────────────────────────────────────────

class TestCacheStatsOnFailure:

    @patch("ingest.worker.urllib.request.urlopen")
    def test_http_error_returns_zero_cache(self, mock_urlopen):
        mock_urlopen.side_effect = _mock_urlopen_http_error(
            500, "Internal Server Error"
        )

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.kimi.com/coding",
            model="kimi",
            max_tokens=1000,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="key",
            max_retries=0,
        )

        assert result["success"] is False
        assert result["cache_creation_input_tokens"] == 0
        assert result["cached_input_tokens"] == 0

    @patch("ingest.worker.urllib.request.urlopen")
    def test_network_error_returns_zero_cache(self, mock_urlopen):
        mock_urlopen.side_effect = ConnectionError("Connection refused")

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.kimi.com/coding",
            model="kimi",
            max_tokens=1000,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="key",
            max_retries=0,
        )

        assert result["success"] is False
        assert result["cache_creation_input_tokens"] == 0
        assert result["cached_input_tokens"] == 0


# ────────────────────────────────────────────
# 4. Fallback when cache_control rejected
# ────────────────────────────────────────────

class TestCacheControlFallback:
    """If API returns 400 with 'cache' in body, retry without cache_control."""

    @patch("ingest.worker.urllib.request.urlopen")
    def test_fallback_on_cache_400(self, mock_urlopen):
        """First call 400 with 'cache_control' error, second call succeeds."""
        # First call: 400 error with cache in body
        err = _mock_urlopen_http_error(
            400, '{"error": "cache_control is not supported"}'
        )
        # Second call: success
        resp_json = {
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [{"type": "text", "text": '{"ok": true}'}],
        }
        mock_urlopen.side_effect = [
            err,
            _mock_urlopen_success(resp_json),
        ]

        result = _single_llm_call(
            user_prompt="test",
            system_prompt="sys",
            base_url="https://api.kimi.com/coding",
            model="kimi",
            max_tokens=1000,
            api_format="anthropic",
            auth_header="x-api-key",
            api_key="key",
            max_retries=1,
            use_cache=True,
        )

        assert result["success"] is True
        # Second call should have been made with plain string system
        assert mock_urlopen.call_count == 2
        # Verify second payload has no cache_control
        second_data = mock_urlopen.call_args_list[1][0][1]
        second_payload = json.loads(second_data)
        # After fallback, system should be plain string
        assert isinstance(second_payload["system"], str)


# ────────────────────────────────────────────
# 5. Cache stats propagate through call_llm
# ────────────────────────────────────────────

class TestCacheStatsPropagation:

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_single_shot_propagates_cache_stats(self, mock_key, mock_call):
        mock_call.return_value = {
            "success": True,
            "content": json.dumps({
                "title_zh": "测试",
                "title_en": "Test",
                "summary_zh": "摘要" * 20,
                "category": "ai",
                "tags": ["test", "ai"],
                "people": [],
                "orgs": [],
                "key_insights": ["insight"],
                "sentiment": "neutral",
                "quality_score": 0.5,
                "related_topics": ["ai"],
            }),
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 80,
            "cached_input_tokens": 0,
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
            result = call_llm("/fake/path.md", provider="kimi")

        assert result["success"] is True
        assert result["cache_creation_input_tokens"] == 80
        assert result["cached_input_tokens"] == 0

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_two_stage_accumulates_cache_stats(self, mock_key, mock_call):
        stage1 = {
            "success": True,
            "content": json.dumps({
                "entities": [],
                "concepts": [],
                "key_claims": [],
                "contradictions_with": [],
                "open_questions": [],
                "source_quality": {
                    "information_density": 0.5, "analytical_depth": 0.5,
                    "actionability": 0.5, "uniqueness": 0.5, "timeliness": 0.5,
                },
                "related_wiki_topics": [],
            }),
            "input_tokens": 500,
            "output_tokens": 300,
            "cache_creation_input_tokens": 400,
            "cached_input_tokens": 0,
            "raw_response": "",
            "latency_ms": 1000,
            "error_log": [],
        }
        stage2 = {
            "success": True,
            "content": json.dumps({
                "title_zh": "测试",
                "title_en": "Test",
                "summary_zh": "摘要" * 20,
                "category": "ai",
                "tags": ["test", "ai"],
                "people": [],
                "orgs": [],
                "key_insights": ["insight"],
                "sentiment": "neutral",
                "quality_score": 0.5,
                "related_topics": ["ai"],
                "multi_quality_score": {
                    "information_density": 0.5, "analytical_depth": 0.5,
                    "actionability": 0.5, "uniqueness": 0.5, "timeliness": 0.5,
                    "overall": 0.5,
                },
                "contradiction_flags": [],
                "gap_indicators": [],
            }),
            "input_tokens": 800,
            "output_tokens": 400,
            "cache_creation_input_tokens": 0,
            "cached_input_tokens": 600,
            "raw_response": "",
            "latency_ms": 2000,
            "error_log": [],
        }
        mock_call.side_effect = [stage1, stage2]

        with patch("builtins.open",
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(
                           read=MagicMock(return_value="---\nsource: test\n---\n\nContent.")
                       )),
                       __exit__=MagicMock(return_value=False),
                   ))):
            result = call_llm("/fake/path.md", provider="kimi", two_stage=True)

        assert result["success"] is True
        # Cache stats should be accumulated: 400 + 0 = 400, 0 + 600 = 600
        assert result["cache_creation_input_tokens"] == 400
        assert result["cached_input_tokens"] == 600
        assert result["input_tokens"] == 1300  # 500 + 800
        assert result["output_tokens"] == 700  # 300 + 400

    @patch("ingest.worker._single_llm_call")
    @patch("ingest.worker._read_api_key", return_value="test-key")
    def test_single_shot_mimo_no_cache_stats(self, mock_key, mock_call):
        """Mimo (OpenAI format) should have zero cache stats."""
        mock_call.return_value = {
            "success": True,
            "content": json.dumps({
                "title_zh": "测试",
                "title_en": "Test",
                "summary_zh": "摘要" * 20,
                "category": "ai",
                "tags": ["test", "ai"],
                "people": [],
                "orgs": [],
                "key_insights": ["insight"],
                "sentiment": "neutral",
                "quality_score": 0.5,
                "related_topics": ["ai"],
            }),
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cached_input_tokens": 0,
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
            result = call_llm("/fake/path.md", provider="mimo")

        assert result["success"] is True
        assert result["cache_creation_input_tokens"] == 0
        assert result["cached_input_tokens"] == 0
