#!/usr/bin/env python3
"""
validator.py — Quality validation for LLM ingest results
"""

import json
from typing import Tuple


VALID_CATEGORIES = {"ai", "engineering", "business", "science", "culture",
                    "opinion", "security", "hardware", "other"}
VALID_SENTIMENTS = {"positive", "neutral", "negative", "critical"}


def validate_result(result: dict) -> Tuple[bool, str]:
    """
    Validate an LLM result. Returns (is_valid, error_message).
    """
    errors = []

    # Required fields
    required = {
        "title_zh": str,
        "summary_zh": str,
        "category": str,
        "tags": list,
        "key_insights": list,
        "sentiment": str,
        "quality_score": (int, float),
    }

    for field, expected_type in required.items():
        val = result.get(field)
        if val is None:
            errors.append(f"missing required field: {field}")
        elif not isinstance(val, expected_type):
            errors.append(f"field '{field}' has wrong type: {type(val).__name__}, expected {expected_type}")

    # Summary quality
    summary = result.get("summary_zh", "")
    if isinstance(summary, str) and len(summary) < 40:
        errors.append(f"summary_zh too short ({len(summary)} chars, min 40)")

    # Category valid
    cat = result.get("category", "")
    if cat and cat not in VALID_CATEGORIES:
        errors.append(f"invalid category: {cat}")

    # Sentiment valid
    sent = result.get("sentiment", "")
    if sent and sent not in VALID_SENTIMENTS:
        errors.append(f"invalid sentiment: {sent}")

    # Tags count
    tags = result.get("tags", [])
    if isinstance(tags, list):
        if len(tags) < 2:
            errors.append(f"too few tags: {len(tags)} (min 2)")
        elif len(tags) > 20:
            errors.append(f"too many tags: {len(tags)} (max 20)")

    # Key insights count
    insights = result.get("key_insights", [])
    if isinstance(insights, list):
        if len(insights) < 1:
            errors.append("at least 1 key_insight required")
        elif len(insights) > 10:
            errors.append(f"too many key_insights: {len(insights)} (max 10)")

    # Quality score range
    score = result.get("quality_score", 0)
    if isinstance(score, (int, float)):
        if not (0 <= score <= 1):
            errors.append(f"quality_score out of range: {score}")
    else:
        errors.append(f"quality_score wrong type: {type(score)}")

    # People validation
    people = result.get("people", [])
    if isinstance(people, list):
        for i, p in enumerate(people):
            if isinstance(p, dict):
                if not p.get("name"):
                    errors.append(f"people[{i}] missing name")
            else:
                errors.append(f"people[{i}] should be a dict, got {type(p).__name__}")

    # Orgs should be strings
    orgs = result.get("orgs", [])
    if isinstance(orgs, list):
        for i, o in enumerate(orgs):
            if not isinstance(o, str):
                errors.append(f"orgs[{i}] should be string")

    return len(errors) == 0, "; ".join(errors) if errors else "OK"
