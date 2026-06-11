#!/usr/bin/env python3
"""
emergent_client.py — Synchronous wrapper around the emergentintegrations
universal-key LLM client (OpenAI / Anthropic / Gemini via one key).

Returns the exact same result dict shape as worker._single_llm_call so it can
be used as a drop-in transport for the ingest pipeline.

Model notation: "<llm_provider>/<model>", e.g. "openai/gpt-5.4",
"anthropic/claude-sonnet-4-6", "gemini/gemini-3-flash-preview".

Note: the universal-key client does not expose exact token usage for
non-streaming calls, so input/output token counts are estimated
(chars / 4) and flagged via "tokens_estimated": True.
"""

import asyncio
import re
import time
import uuid

_TRANSIENT_MARKERS = (
    "rate limit", "429", "timeout", "timed out", "connection",
    "overloaded", "503", "502", "500", "temporarily",
)


async def _acall(api_key: str, system_prompt: str, user_prompt: str,
                 llm_provider: str, llm_model: str,
                 max_tokens: int, timeout: int) -> str:
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    chat = (
        LlmChat(
            api_key=api_key,
            session_id=f"loom-{uuid.uuid4().hex[:12]}",
            system_message=system_prompt,
        )
        .with_model(llm_provider, llm_model)
        .with_params(max_tokens=max_tokens)
    )
    return await asyncio.wait_for(
        chat.send_message(UserMessage(text=user_prompt)), timeout=timeout
    )


def emergent_llm_call(
    user_prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    max_tokens: int = 8192,
    max_retries: int = 3,
    timeout: int = 180,
) -> dict:
    """Single LLM call through the universal key. Mirrors _single_llm_call's contract."""
    if "/" in model:
        llm_provider, llm_model = model.split("/", 1)
    else:
        llm_provider, llm_model = "openai", model

    error_log = []
    last_error = ""

    for attempt in range(max_retries + 1):
        t0 = time.time()
        try:
            content = asyncio.run(_acall(
                api_key, system_prompt, user_prompt,
                llm_provider, llm_model, max_tokens, timeout,
            ))
            latency_ms = (time.time() - t0) * 1000

            content = re.sub(r"^```(?:json)?\s*\n?", "", content or "")
            content = re.sub(r"\n?```\s*$", "", content)
            content = content.strip()

            est_in = max(1, (len(system_prompt) + len(user_prompt)) // 4)
            est_out = max(1, len(content) // 4)

            return {
                "success": True,
                "content": content,
                "input_tokens": est_in,
                "output_tokens": est_out,
                "cache_creation_input_tokens": 0,
                "cached_input_tokens": 0,
                "raw_response": content,
                "latency_ms": latency_ms,
                "error_log": error_log,
                "tokens_estimated": True,
            }

        except Exception as e:  # noqa: BLE001 — boundary with external SDK
            latency_ms = (time.time() - t0) * 1000
            last_error = f"{type(e).__name__}: {e}"
            err_lower = str(e).lower()
            error_log.append({
                "attempt": attempt + 1,
                "phase": "emergent",
                "error_type": type(e).__name__,
                "message": str(e)[:200],
                "latency_ms": latency_ms,
            })
            transient = any(m in err_lower for m in _TRANSIENT_MARKERS)
            if transient and attempt < max_retries:
                wait = min(10 * (attempt + 1), 60)
                print(f"    ⚠️ emergent transient error → retry in {wait}s "
                      f"({attempt + 2}/{max_retries + 1}): {last_error[:80]}")
                time.sleep(wait)
                continue
            break

    return {
        "success": False,
        "error": last_error[:200],
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cached_input_tokens": 0,
        "raw_response": "",
        "latency_ms": 0,
        "error_log": error_log,
    }
