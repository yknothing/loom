#!/usr/bin/env python3
"""
worker.py — LLM API worker for deep ingest (V4)

Supports two modes:
  - Single-shot (backward compatible): one LLM call per article
  - Two-stage: Stage 1 (analysis) → Stage 2 (synthesis)

Calls Mimo V2.5 Pro, Kimi, or DeepSeek via HTTP.

Retry policy by error type:
  - 429 rate limit     → retry with exponential backoff + jitter
  - 5xx server error   → retry with backoff (server's problem)
  - Network glitch     → retry with backoff (transient)
  - Timeout            → ONE retry with 2x timeout, then skip
  - JSON/validation/4xx → NO retry, log and skip
"""

import json
import os
import random
import re
import time
import urllib.request
import urllib.error
from typing import Optional

from .prompts import (
    SYSTEM_PROMPT,
    ANALYSIS_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    build_article_prompt,
    build_analysis_prompt,
    build_synthesis_prompt,
)
from .validator import validate_result
from .long_form import (
    detect_long_form,
    generate_outline_prompt,
    segment_by_outline,
    cross_segment_synthesis_prompt,
)

OUTLINE_SYSTEM_PROMPT = (
    "You are a document structure analyst. Your task is to analyze article "
    "structure and produce a JSON outline of sections. Your output must be "
    "strictly JSON, no markdown fences, no extra text."
)


def _read_api_key(provider: str = "xiaomimimo") -> str:
    """Read API key from leader agent's auth-profiles."""
    import pathlib
    auth_path = pathlib.Path.home() / ".openclaw/agents/leader/agent/auth-profiles.json"
    with open(auth_path) as f:
        auth = json.load(f)
    return auth["profiles"][f"{provider}:default"]["key"]


# ⛔⛔⛔ MIMO CONSTRAINTS (violated 3 times, do NOT change without checking):
#   1. max_tokens MUST be ≤ 16384 (larger values cause SSL disconnect)
#   2. NEVER use ProxyHandler({}) or NO_PROXY to bypass system proxy
#      (Mimo SGP is only reachable via the system https_proxy)
# See MEMORY.md → "Mimo 硬性约束" for full context.

PROVIDERS = {
    "mimo": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "max_tokens": 16384,
        "api": "openai",
        "auth_header": "Bearer",
        "key_profile": "xiaomimimo",
    },
    "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "model": "kimi-for-coding",
        "max_tokens": 32768,
        "api": "anthropic",
        "auth_header": "x-api-key",
        "key_profile": "kimi",
        "supports_cache_control": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "max_tokens": 8192,
        "api": "openai",
        "auth_header": "Bearer",
        "key_profile": "deepseek",
    },
}


def _parse_frontmatter(text: str) -> dict:
    meta = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip("'\"")
    return meta


def _extract_body(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


def _classify_error(e: Exception) -> str:
    """
    Classify error for retry decision.
    Returns: "retry" | "retry_once" | "no_retry"
    """
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        if 400 <= code < 429:       # bad request, auth, not found
            return "no_retry"
        if code == 429:             # rate limit
            return "retry"
        if code >= 500:             # server error
            return "retry"

    if isinstance(e, TimeoutError):
        return "retry_once"         # timeout = maybe payload issue, try once more

    if isinstance(e, ConnectionError):
        return "retry"

    if isinstance(e, urllib.error.URLError):
        err_str = str(e.reason).lower() if hasattr(e, 'reason') else str(e).lower()
        transient = ['reset', 'eof', 'refused', 'unreachable', 'no route']
        permanent = ['certificate', 'name resolution', 'getaddrinfo']
        if any(kw in err_str for kw in permanent):
            return "no_retry"
        if any(kw in err_str for kw in transient):
            return "retry"
        return "retry_once"

    if isinstance(e, OSError):
        if hasattr(e, 'errno') and e.errno in (54, 60, 61, 64):
            return "retry"
        return "no_retry"

    return "no_retry"


def _single_llm_call(
    user_prompt: str,
    system_prompt: str,
    base_url: str,
    model: str,
    max_tokens: int,
    api_format: str,
    auth_header: str,
    api_key: str,
    max_retries: int = 3,
    timeout: int = 120,
    opener=None,
    use_cache: bool = False,
) -> dict:
    """
    Core LLM API call. Returns raw dict with: success, content, input_tokens,
    output_tokens, raw_response, latency_ms, error_log.
    On failure: success=False, error (str), plus the rest.
    """
    use_cache_control = False

    if api_format == "anthropic":
        url = f"{base_url}/v1/messages"
        if use_cache:
            system_block = [
                {"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}},
            ]
            use_cache_control = True
        else:
            system_block = system_prompt
        payload = {
            "model": model,
            "system": system_block,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
        }
    else:  # openai
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        }

    last_error = None
    current_timeout = timeout
    error_log = []

    _opener = opener

    # ⛔ Safety net: reject ProxyHandler({}) for Mimo URLs
    if _opener is not None and "xiaomimimo" in base_url:
        import urllib.request as _ur
        if isinstance(_opener, _ur.OpenerDirector):
            # Check if it's a no-proxy opener (ProxyHandler with empty dict)
            for h in _opener.handlers:
                if isinstance(h, _ur.ProxyHandler) and not h.proxies:
                    print(f"    ⛔ WARNING: ProxyHandler({{}}) detected for Mimo URL — this will cause Connection Reset!")
                    _opener = None  # Fall back to system proxy
                    break

    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, method="POST")
            req.add_header("Content-Type", "application/json")
            if api_format == "anthropic":
                req.add_header(auth_header, api_key)
                req.add_header("anthropic-version", "2023-06-01")
            else:
                req.add_header("Authorization", f"Bearer {api_key}")
            data = json.dumps(payload).encode("utf-8")

            _urlopen = _opener.open if _opener else urllib.request.urlopen
            with _urlopen(req, data, timeout=current_timeout) as resp:
                raw_resp = resp.read().decode("utf-8")
                result_json = json.loads(raw_resp)

            latency_ms = (time.time() - t0) * 1000

            if api_format == "anthropic":
                usage = result_json.get("usage", {})
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cache_creation = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cached_input_tokens", 0)
                content_blocks = result_json.get("content", [])
                content = "\n".join(
                    b["text"] for b in content_blocks if b.get("type") == "text"
                )
            else:
                usage = result_json.get("usage", {})
                inp = usage.get("prompt_tokens", 0)
                out = usage.get("completion_tokens", 0)
                cache_creation = 0
                cache_read = 0
                message = result_json.get("choices", [{}])[0].get("message", {})
                content = message.get("content", "")

            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?```\s*$", "", content)
            content = content.strip()

            return {
                "success": True,
                "content": content,
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cache_creation,
                "cached_input_tokens": cache_read,
                "raw_response": content,
                "latency_ms": latency_ms,
                "error_log": error_log,
            }

        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            last_error = f"HTTP {e.code}: {body_text[:100]}"
            elapsed_ms = (time.time() - t0) * 1000
            error_log.append({
                "attempt": attempt + 1, "phase": "http",
                "error_type": f"HTTP_{e.code}",
                "message": body_text[:200],
                "latency_ms": elapsed_ms,
            })

            # Fallback: if cache_control caused a 400, retry without it
            if (
                e.code == 400
                and use_cache_control
                and ("cache_control" in body_text.lower()
                     or "cache" in body_text.lower())
            ):
                payload["system"] = system_prompt  # plain string, no cache_control
                use_cache_control = False
                print("    ⚠️ cache_control not supported → retry without cache")
                continue

            if e.code == 429 and attempt < max_retries:
                wait = min(30 * (attempt + 1), 120) * (0.5 + random.random())
                print(f"    ⚠️ 429 rate limit → retry in {wait:.0f}s "
                      f"({attempt+2}/{max_retries+1})")
                time.sleep(wait)
                continue
            elif e.code >= 500 and attempt < max_retries:
                wait = min(10 * (attempt + 1), 60) * (0.5 + random.random())
                print(f"    ⚠️ HTTP {e.code} → retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            return {
                "success": False, "error": last_error[:200],
                "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
                "raw_response": body_text[:300],
                "latency_ms": elapsed_ms, "error_log": error_log,
            }

        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            error_class = _classify_error(e)
            last_error = f"{type(e).__name__}: {e}"
            elapsed_ms = (time.time() - t0) * 1000
            error_log.append({
                "attempt": attempt + 1, "phase": "network",
                "error_type": type(e).__name__,
                "message": str(e)[:200],
                "latency_ms": elapsed_ms,
                "classified": error_class,
            })
            if error_class == "no_retry":
                return {
                    "success": False, "error": last_error[:200],
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
                    "raw_response": "", "latency_ms": elapsed_ms,
                    "error_log": error_log,
                }
            if error_class == "retry_once":
                if isinstance(e, TimeoutError) and current_timeout == timeout:
                    current_timeout = timeout * 2
                    print(f"    ⚠️ Timeout ({timeout}s) → retry once with {current_timeout}s")
                    time.sleep(2)
                    continue
                else:
                    return {
                        "success": False,
                        "error": f"timeout (not retried): {last_error[:150]}",
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
                        "raw_response": "", "latency_ms": elapsed_ms,
                        "error_log": error_log,
                    }
            if attempt < max_retries:
                base_wait = 10 * (2 ** min(attempt, 5))
                wait = min(base_wait * (0.5 + random.random() * 0.5), 300)
                print(f"    ⚠️ Network → retry in {wait:.0f}s "
                      f"({attempt+2}/{max_retries+1}): {last_error[:80]}")
                time.sleep(wait)
                continue

        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            error_log.append({
                "attempt": attempt + 1, "phase": "unknown",
                "error_type": type(e).__name__,
                "message": str(e)[:200],
                "latency_ms": elapsed_ms,
            })
            return {
                "success": False, "error": f"unexpected: {e}"[:200],
                "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
                "raw_response": "", "latency_ms": elapsed_ms,
                "error_log": error_log,
            }

    return {
        "success": False, "error": f"retries_exhausted: {last_error}"[:200],
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
        "raw_response": "", "latency_ms": 0, "error_log": error_log,
    }


def _parse_json_content(content: str):
    """Parse JSON from LLM content string. Returns (parsed_dict, None) or (None, error_str)."""
    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except json.JSONDecodeError:
                return None, f"json_parse: content[:200]={content[:200]}"
    if parsed is None:
        return None, "json_parse: LLM returned non-JSON"
    return parsed, None


def _long_form_call(
    filepath: str,
    meta: dict,
    body: str,
    base_url: str,
    model: str,
    max_tokens: int,
    api_format: str,
    auth_header: str,
    api_key: str,
    max_retries: int = 3,
    timeout: int = 120,
    opener=None,
    use_cache: bool = False,
) -> dict:
    """
    Long-form multi-pass analysis.

    Flow:
      1. Generate outline via LLM → outline sections
      2. Segment body by outline → segments[]
      3. Stage 1 analysis for each segment → analyses[]
      4. Cross-segment synthesis → final result

    Falls back to single-shot on outline failure.
    Skips individual segment failures gracefully.
    """
    total_inp = 0
    total_out = 0
    total_latency = 0
    total_cache_creation = 0
    total_cache_read = 0
    error_log = []

    # ── Step 1: Outline generation ──
    outline_prompt = generate_outline_prompt(body)
    outline_result = _single_llm_call(
        user_prompt=outline_prompt,
        system_prompt=OUTLINE_SYSTEM_PROMPT,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        api_format=api_format,
        auth_header=auth_header,
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        opener=opener,
            use_cache=use_cache,
    )

    total_inp += outline_result["input_tokens"]
    total_out += outline_result["output_tokens"]
    total_latency += outline_result["latency_ms"]
    total_cache_creation += outline_result.get("cache_creation_input_tokens", 0)
    total_cache_read += outline_result.get("cached_input_tokens", 0)
    error_log.extend(outline_result["error_log"])

    if not outline_result["success"]:
        error_log.append({
            "attempt": 1, "phase": "long_form_outline",
            "error_type": "OutlineFailure",
            "message": f"Outline generation failed: {outline_result.get('error', 'unknown')}",
            "latency_ms": outline_result["latency_ms"],
        })
        # Fallback: single-shot with truncation
        user_prompt = build_article_prompt(meta, body)
        raw_result = _single_llm_call(
            user_prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )
        total_inp += raw_result["input_tokens"]
        total_out += raw_result["output_tokens"]
        total_latency += raw_result["latency_ms"]
        total_cache_creation += raw_result.get("cache_creation_input_tokens", 0)
        total_cache_read += raw_result.get("cached_input_tokens", 0)
        error_log.extend(raw_result["error_log"])

        if not raw_result["success"]:
            return {
                "success": False, "error": raw_result.get("error", "unknown"),
                "input_tokens": total_inp, "output_tokens": total_out,
                "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
                "raw_response": "", "latency_ms": total_latency,
                "error_log": error_log, "stage": "long_form_outline_fallback",
            }

        content = raw_result["content"]
        parsed, json_err = _parse_json_content(content)
        if json_err:
            return {
                "success": False, "error": json_err,
                "input_tokens": total_inp, "output_tokens": total_out,
                "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
                "raw_response": content[:500], "latency_ms": total_latency,
                "error_log": error_log, "stage": "long_form_outline_fallback",
            }

        is_valid, validation_msg = validate_result(parsed)
        if not is_valid:
            return {
                "success": False, "error": f"validation: {validation_msg}",
                "input_tokens": total_inp, "output_tokens": total_out,
                "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
                "raw_response": content[:500], "latency_ms": total_latency,
                "error_log": error_log, "stage": "long_form_outline_fallback",
            }

        parsed["_filepath"] = filepath
        parsed["_raw_response"] = content
        return {
            "success": True, "result": parsed,
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": content, "latency_ms": total_latency,
            "error_log": error_log, "stage": "single",
            "long_form_fallback": True,
        }

    # ── Parse outline ──
    outline_content = outline_result["content"]
    outline_parsed, outline_err = _parse_json_content(outline_content)
    if outline_err or not outline_parsed:
        error_log.append({
            "attempt": 1, "phase": "long_form_outline_parse",
            "error_type": "JSONDecodeError",
            "message": f"Outline JSON parse failed: {outline_err}",
            "latency_ms": outline_result["latency_ms"],
        })
        # Fallback to single-shot
        user_prompt = build_article_prompt(meta, body)
        raw_result = _single_llm_call(
            user_prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )
        total_inp += raw_result["input_tokens"]
        total_out += raw_result["output_tokens"]
        total_latency += raw_result["latency_ms"]
        total_cache_creation += raw_result.get("cache_creation_input_tokens", 0)
        total_cache_read += raw_result.get("cached_input_tokens", 0)
        error_log.extend(raw_result["error_log"])
        if raw_result["success"]:
            content = raw_result["content"]
            parsed, _ = _parse_json_content(content)
            if parsed:
                is_valid, _ = validate_result(parsed)
                if is_valid:
                    parsed["_filepath"] = filepath
                    parsed["_raw_response"] = content
                    return {
                        "success": True, "result": parsed,
                        "input_tokens": total_inp, "output_tokens": total_out,
                        "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
                        "raw_response": content, "latency_ms": total_latency,
                        "error_log": error_log, "stage": "single",
                        "long_form_fallback": True,
                    }
        return {
            "success": False, "error": "long_form outline parse failed, fallback also failed",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": "", "latency_ms": total_latency,
            "error_log": error_log, "stage": "long_form_failed",
        }

    sections = outline_parsed.get("sections", [])

    # ── Step 2: Segment by outline ──
    segments = segment_by_outline(body, sections)
    if not segments:
        error_log.append({
            "attempt": 1, "phase": "long_form_segment",
            "error_type": "SegmentationFailure",
            "message": "segment_by_outline returned empty",
        })
        # Fallback to single-shot
        user_prompt = build_article_prompt(meta, body)
        raw_result = _single_llm_call(
            user_prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )
        total_inp += raw_result["input_tokens"]
        total_out += raw_result["output_tokens"]
        total_latency += raw_result["latency_ms"]
        total_cache_creation += raw_result.get("cache_creation_input_tokens", 0)
        total_cache_read += raw_result.get("cached_input_tokens", 0)
        error_log.extend(raw_result["error_log"])
        if raw_result["success"]:
            content = raw_result["content"]
            parsed, _ = _parse_json_content(content)
            if parsed:
                is_valid, _ = validate_result(parsed)
                if is_valid:
                    parsed["_filepath"] = filepath
                    parsed["_raw_response"] = content
                    return {
                        "success": True, "result": parsed,
                        "input_tokens": total_inp, "output_tokens": total_out,
                        "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
                        "raw_response": content, "latency_ms": total_latency,
                        "error_log": error_log, "stage": "single",
                        "long_form_fallback": True,
                    }
        return {
            "success": False, "error": "long_form segmentation failed",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": "", "latency_ms": total_latency,
            "error_log": error_log, "stage": "long_form_failed",
        }

    segment_count = len(segments)

    # ── Step 3: Stage 1 analysis for each segment ──
    analyses = []
    for seg_idx, seg_text in segments:
        seg_prompt = build_analysis_prompt(meta, seg_text)
        seg_result = _single_llm_call(
            user_prompt=seg_prompt,
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )

        total_inp += seg_result["input_tokens"]
        total_out += seg_result["output_tokens"]
        total_latency += seg_result["latency_ms"]
        total_cache_creation += seg_result.get("cache_creation_input_tokens", 0)
        total_cache_read += seg_result.get("cached_input_tokens", 0)
        error_log.extend(seg_result["error_log"])

        if not seg_result["success"]:
            error_log.append({
                "attempt": 1, "phase": "long_form_segment_analysis",
                "error_type": "SegmentAnalysisFailure",
                "message": f"Segment {seg_idx} analysis failed: {seg_result.get('error', 'unknown')}",
                "segment": seg_idx,
            })
            continue  # Skip failed segment

        seg_parsed, seg_err = _parse_json_content(seg_result["content"])
        if seg_err:
            error_log.append({
                "attempt": 1, "phase": "long_form_segment_parse",
                "error_type": "JSONDecodeError",
                "message": f"Segment {seg_idx} JSON parse failed: {seg_err}",
                "segment": seg_idx,
            })
            continue

        seg_parsed["segment"] = seg_idx
        analyses.append(seg_parsed)

    # ── Check if we got any analyses ──
    if not analyses:
        error_log.append({
            "attempt": 1, "phase": "long_form_no_analyses",
            "error_type": "AllSegmentsFailed",
            "message": f"All {segment_count} segment analyses failed",
        })
        return {
            "success": False,
            "error": f"long_form: all {segment_count} segment analyses failed",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": "", "latency_ms": total_latency,
            "error_log": error_log, "stage": "long_form_failed",
        }

    # ── Step 4: Cross-segment synthesis ──
    synthesis_prompt = cross_segment_synthesis_prompt(analyses)
    synthesis_result = _single_llm_call(
        user_prompt=synthesis_prompt,
        system_prompt=SYSTEM_PROMPT,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        api_format=api_format,
        auth_header=auth_header,
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        opener=opener,
            use_cache=use_cache,
    )

    total_inp += synthesis_result["input_tokens"]
    total_out += synthesis_result["output_tokens"]
    total_latency += synthesis_result["latency_ms"]
    total_cache_creation += synthesis_result.get("cache_creation_input_tokens", 0)
    total_cache_read += synthesis_result.get("cached_input_tokens", 0)
    error_log.extend(synthesis_result["error_log"])

    if not synthesis_result["success"]:
        error_log.append({
            "attempt": 1, "phase": "long_form_synthesis",
            "error_type": "SynthesisFailure",
            "message": f"Cross-segment synthesis failed: {synthesis_result.get('error', 'unknown')}",
        })
        # Return individual analyses as separate results — best effort
        # Build a merged result from the first analysis as a fallback
        return {
            "success": False,
            "error": f"long_form synthesis failed, {len(analyses)}/{segment_count} segments analyzed",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": json.dumps(analyses, ensure_ascii=False),
            "latency_ms": total_latency,
            "error_log": error_log,
            "stage": "long_form_synthesis_failed",
        }

    # ── Parse and validate synthesis ──
    synth_content = synthesis_result["content"]
    synth_parsed, synth_err = _parse_json_content(synth_content)
    if synth_err:
        error_log.append({
            "attempt": 1, "phase": "long_form_synthesis_parse",
            "error_type": "JSONDecodeError",
            "message": f"Synthesis JSON parse failed: {synth_err}",
        })
        return {
            "success": False,
            "error": f"long_form synthesis parse failed",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": synth_content[:500],
            "latency_ms": total_latency, "error_log": error_log,
            "stage": "long_form_failed",
        }

    is_valid, validation_msg = validate_result(synth_parsed)
    if not is_valid:
        error_log.append({
            "attempt": 1, "phase": "long_form_validation",
            "error_type": "ValidationError",
            "message": validation_msg,
            "latency_ms": total_latency,
            "tokens": f"{total_inp}+{total_out}",
        })
        return {
            "success": False, "error": f"long_form validation: {validation_msg}",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": synth_content[:500],
            "latency_ms": total_latency, "error_log": error_log,
            "stage": "long_form_failed",
        }

    # ── Success ──
    synth_parsed["_filepath"] = filepath
    synth_parsed["_raw_response"] = synth_content
    synth_parsed["stage"] = "long_form"
    synth_parsed["segment_count"] = segment_count
    synth_parsed["segments_json"] = json.dumps(
        analyses, ensure_ascii=False
    )

    return {
        "success": True, "result": synth_parsed,
        "input_tokens": total_inp, "output_tokens": total_out,
        "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
        "raw_response": synth_content, "latency_ms": total_latency,
        "error_log": error_log, "stage": "long_form",
        "segment_count": segment_count,
        "segments_json": json.dumps(analyses, ensure_ascii=False),
    }


def call_llm(
    filepath: str,
    base_url: str = "",
    model: str = "",
    max_retries: int = 3,
    timeout: int = 120,
    api_key: Optional[str] = None,
    provider: str = "mimo",
    two_stage: bool = False,
) -> dict:
    """
    Process a single raw article through LLM.

    When two_stage=False (default): single-shot call (backward compatible).
    When two_stage=True: two-stage compile + reflect.
      Stage 1: ANALYSIS_PROMPT → deep analysis JSON
      Stage 2: SYNTHESIS_PROMPT + Stage 1 JSON → enhanced structured output

    Returns dict: success, result, error, input_tokens, output_tokens,
                  raw_response, latency_ms, error_log, stage.
    """
    # --- Read and parse file ---
    try:
        with open(filepath, encoding="utf-8") as f:
            raw_text = f.read()
    except Exception as e:
        return {
            "success": False,
            "error": f"read error: {e}",
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cached_input_tokens": 0,
            "raw_response": "", "latency_ms": 0,
            "error_log": [{"attempt": 0, "phase": "read", "error_type": "IOError",
                           "message": str(e)}],
        }

    meta = _parse_frontmatter(raw_text)
    body = _extract_body(raw_text)

    # --- Resolve provider config ---
    prov = PROVIDERS.get(provider, PROVIDERS["kimi"])
    if api_key is None:
        key_profile = prov.get("key_profile", provider)
        api_key = _read_api_key(key_profile)

    effective_base_url = base_url or prov["base_url"]
    effective_model = model or prov["model"]
    max_out_tokens = prov["max_tokens"]
    api_format = prov["api"]
    auth_header = prov["auth_header"]

    # All providers go through system proxy (https_proxy env var)
    opener = None

    # Cache control: only for providers that support it
    use_cache = prov.get("supports_cache_control", api_format == "anthropic")

    # ============================================================
    # Long-form detection (applies to ALL modes)
    # ============================================================
    if detect_long_form(body):
        return _long_form_call(
            filepath=filepath,
            meta=meta,
            body=body,
            base_url=effective_base_url,
            model=effective_model,
            max_tokens=max_out_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )

    # ============================================================
    # Single-shot mode (backward compatible)
    # ============================================================
    if not two_stage:
        user_prompt = build_article_prompt(meta, body)
        raw_result = _single_llm_call(
            user_prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            base_url=effective_base_url,
            model=effective_model,
            max_tokens=max_out_tokens,
            api_format=api_format,
            auth_header=auth_header,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            opener=opener,
            use_cache=use_cache,
        )

        if not raw_result["success"]:
            raw_result["result"] = None
            return raw_result

        content = raw_result["content"]
        inp = raw_result["input_tokens"]
        out = raw_result["output_tokens"]
        latency_ms = raw_result["latency_ms"]
        error_log = raw_result["error_log"]
        cache_creation = raw_result.get("cache_creation_input_tokens", 0)
        cache_read = raw_result.get("cached_input_tokens", 0)

        parsed, json_err = _parse_json_content(content)
        if json_err:
            error_log.append({"attempt": 1, "phase": "json_parse",
                              "error_type": "JSONDecodeError",
                              "message": json_err,
                              "latency_ms": latency_ms, "tokens": f"{inp}+{out}"})
            return {
                "success": False, "error": json_err,
                "input_tokens": inp, "output_tokens": out,
                "cache_creation_input_tokens": cache_creation,
                "cached_input_tokens": cache_read,
                "raw_response": content[:500],
                "latency_ms": latency_ms, "error_log": error_log,
            }

        is_valid, validation_msg = validate_result(parsed)
        if not is_valid:
            error_log.append({"attempt": 1, "phase": "validation",
                              "error_type": "ValidationError",
                              "message": validation_msg,
                              "latency_ms": latency_ms, "tokens": f"{inp}+{out}"})
            return {
                "success": False, "error": f"validation: {validation_msg}",
                "input_tokens": inp, "output_tokens": out,
                "cache_creation_input_tokens": cache_creation,
                "cached_input_tokens": cache_read,
                "raw_response": content[:500],
                "latency_ms": latency_ms, "error_log": error_log,
            }

        parsed["_filepath"] = filepath
        parsed["_raw_response"] = content
        return {
            "success": True, "result": parsed,
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": cache_creation,
            "cached_input_tokens": cache_read,
            "raw_response": content, "latency_ms": latency_ms,
            "error_log": error_log, "stage": "single",
        }

    # ============================================================
    # Two-stage mode
    # ============================================================
    total_inp = 0
    total_out = 0
    total_latency = 0
    total_cache_creation = 0
    total_cache_read = 0
    error_log = []

    # --- Stage 1: Analysis ---
    s1_prompt = build_analysis_prompt(meta, body)
    s1_result = _single_llm_call(
        user_prompt=s1_prompt,
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        base_url=effective_base_url,
        model=effective_model,
        max_tokens=max_out_tokens,
        api_format=api_format,
        auth_header=auth_header,
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        opener=opener,
            use_cache=use_cache,
    )

    if not s1_result["success"]:
        s1_result["result"] = None
        s1_result["stage"] = "stage1_failed"
        return s1_result

    total_inp += s1_result["input_tokens"]
    total_out += s1_result["output_tokens"]
    total_latency += s1_result["latency_ms"]
    total_cache_creation += s1_result.get("cache_creation_input_tokens", 0)
    total_cache_read += s1_result.get("cached_input_tokens", 0)
    error_log.extend(s1_result["error_log"])

    s1_content = s1_result["content"]
    stage1_parsed, s1_json_err = _parse_json_content(s1_content)
    if s1_json_err:
        error_log.append({"attempt": 1, "phase": "stage1_json_parse",
                          "error_type": "JSONDecodeError",
                          "message": s1_json_err,
                          "latency_ms": s1_result["latency_ms"],
                          "tokens": f"{s1_result['input_tokens']}+{s1_result['output_tokens']}"})
        return {
            "success": False, "error": f"stage1_{s1_json_err}",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": s1_content[:500],
            "latency_ms": total_latency, "error_log": error_log,
            "stage": "stage1_failed",
        }

    # --- Stage 2: Synthesis ---
    s2_prompt = build_synthesis_prompt(meta, body, stage1_parsed)
    s2_result = _single_llm_call(
        user_prompt=s2_prompt,
        system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        base_url=effective_base_url,
        model=effective_model,
        max_tokens=max_out_tokens,
        api_format=api_format,
        auth_header=auth_header,
        api_key=api_key,
        max_retries=max_retries,
        timeout=timeout,
        opener=opener,
            use_cache=use_cache,
    )

    if not s2_result["success"]:
        s2_result["input_tokens"] = total_inp + s2_result.get("input_tokens", 0)
        s2_result["output_tokens"] = total_out + s2_result.get("output_tokens", 0)
        s2_result["latency_ms"] = total_latency + s2_result.get("latency_ms", 0)
        s2_result["error_log"] = error_log + s2_result.get("error_log", [])
        s2_result["stage"] = "stage2_failed"
        return s2_result

    total_inp += s2_result["input_tokens"]
    total_out += s2_result["output_tokens"]
    total_latency += s2_result["latency_ms"]
    total_cache_creation += s2_result.get("cache_creation_input_tokens", 0)
    total_cache_read += s2_result.get("cached_input_tokens", 0)
    error_log.extend(s2_result["error_log"])

    s2_content = s2_result["content"]
    stage2_parsed, s2_json_err = _parse_json_content(s2_content)
    if s2_json_err:
        error_log.append({"attempt": 1, "phase": "stage2_json_parse",
                          "error_type": "JSONDecodeError",
                          "message": s2_json_err,
                          "latency_ms": s2_result["latency_ms"],
                          "tokens": f"{s2_result['input_tokens']}+{s2_result['output_tokens']}"})
        return {
            "success": False, "error": f"stage2_{s2_json_err}",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": s2_content[:500],
            "latency_ms": total_latency, "error_log": error_log,
            "stage": "stage2_failed",
        }

    is_valid, validation_msg = validate_result(stage2_parsed)
    if not is_valid:
        error_log.append({"attempt": 1, "phase": "stage2_validation",
                          "error_type": "ValidationError",
                          "message": validation_msg,
                          "latency_ms": total_latency,
                          "tokens": f"{total_inp}+{total_out}"})
        return {
            "success": False, "error": f"stage2_validation: {validation_msg}",
            "input_tokens": total_inp, "output_tokens": total_out,
            "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
            "raw_response": s2_content[:500],
            "latency_ms": total_latency, "error_log": error_log,
            "stage": "stage2_failed",
        }

    # --- Merge Stage 1 analysis into Stage 2 result ---
    stage2_parsed["_filepath"] = filepath
    stage2_parsed["_raw_response"] = s2_content
    stage2_parsed["_stage1_analysis"] = stage1_parsed
    stage2_parsed["stage"] = "two_stage"

    return {
        "success": True, "result": stage2_parsed,
        "input_tokens": total_inp, "output_tokens": total_out,
        "cache_creation_input_tokens": total_cache_creation, "cached_input_tokens": total_cache_read,
        "raw_response": s2_content, "latency_ms": total_latency,
        "error_log": error_log, "stage": "two_stage",
    }
