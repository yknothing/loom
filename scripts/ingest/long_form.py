#!/usr/bin/env python3
"""
long_form.py — Detect and segment long articles for multi-pass LLM analysis.

Articles > 5000 chars are split into segments, each analyzed separately,
then synthesized into a single result. This module provides detection,
outline-based segmentation with fallback chunking, and synthesis prompt building.
"""

import json
import re
import textwrap
from typing import Tuple

LONG_FORM_THRESHOLD = 50000  # characters — only for truly massive articles; Mimo can't reliably handle multi-request long-form for shorter ones
_FALLBACK_MAX_SEGMENT = 5000
_FALLBACK_MIN_SEGMENT = 500


def detect_long_form(content: str) -> bool:
    """Return True if content length > LONG_FORM_THRESHOLD."""
    return len(content) > LONG_FORM_THRESHOLD


def generate_outline_prompt(content: str) -> str:
    """
    Build a prompt that asks the LLM to return a JSON outline of the article.
    Returns: {sections: [{title: "...", start_marker: "...", end_marker: "..."}]}
    """
    # Include a preview of the content (first 3000 chars) to give the LLM context
    preview = content[:3000]
    if len(content) > 3000:
        preview += "\n\n[... content continues ...]"

    return (
        "请分析以下文章,生成文章的结构大纲。输出严格 JSON 格式。\n\n"
        "## 文章预览\n"
        f"{preview}\n\n"
        "## 输出要求\n"
        "请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n"
        "{\n"
        '  "sections": [\n'
        "    {\n"
        '      "title": "章节标题",\n'
        '      "start_marker": "章节开始的文本标记(精确匹配文章中的文本)",\n'
        '      "end_marker": "下一章节开始的文本标记(最后一个section可省略)"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "## 规则\n"
        "1. 识别文章的自然分段(标题、小节等)。\n"
        "2. start_marker 必须是文章中实际存在的文本片段,用于定位分段边界。\n"
        "3. 每个分段应 >= 500 字符且 <= 5000 字符。如果某段太长,在段内找子分段点。\n"
        "4. 不要输出 JSON 之外的内容。不要 ```json 标记。\n"
    )


def _chunk_by_char_count(content: str, max_chars: int = _FALLBACK_MAX_SEGMENT,
                         min_chars: int = _FALLBACK_MIN_SEGMENT) -> list[Tuple[int, str]]:
    """Fallback: split content into chunks at paragraph boundaries."""
    if not content.strip():
        return []

    paragraphs = re.split(r'(\n\n+)', content)
    # Rebuild paragraphs with their separators: [(text, sep), ...]
    parts: list[Tuple[str, str]] = []
    i = 0
    while i < len(paragraphs):
        if i + 1 < len(paragraphs) and re.match(r'\n\n+', paragraphs[i + 1]):
            parts.append((paragraphs[i], paragraphs[i + 1]))
            i += 2
        else:
            if paragraphs[i].strip():  # skip empty trailing parts
                parts.append((paragraphs[i], ""))
            i += 1

    segments: list[str] = []
    current_parts: list[Tuple[str, str]] = []  # (text, following_sep)

    for text, sep in parts:
        candidate = text
        if current_parts:
            candidate = "".join(t + s for t, s in current_parts) + sep + text
        if len(candidate) > max_chars and current_parts:
            # Flush current segment preserving original separators
            segments.append("".join(t + s for t, s in current_parts))
            current_parts = [(text, sep)]
        else:
            current_parts.append((text, sep))

    if current_parts:
        combined = "".join(t + s for t, s in current_parts)
        # If the last chunk is too big, split it further
        if len(combined) > max_chars:
            # Split at sentence boundaries within the chunk
            sentences = re.split(r'((?<=[.!?。！？])\s+)', combined)
            # sentences alternates: [text, sep, text, sep, ...]
            sub_parts: list[Tuple[str, str]] = []
            si = 0
            while si < len(sentences):
                stxt = sentences[si]
                ssep = sentences[si + 1] if si + 1 < len(sentences) else ""
                if stxt or ssep:
                    sub_parts.append((stxt, ssep))
                si += 2

            sub_segments: list[str] = []
            cur: list[Tuple[str, str]] = []
            for stxt, ssep in sub_parts:
                candidate = stxt
                if cur:
                    candidate = "".join(t + s for t, s in cur) + stxt
                if len(candidate) > max_chars and cur:
                    sub_segments.append("".join(t + s for t, s in cur))
                    cur = [(stxt, ssep)]
                else:
                    cur.append((stxt, ssep))
            if cur:
                last = "".join(t + s for t, s in cur)
                # If it's still too long, just hard-split
                while len(last) > max_chars:
                    segments.append(last[:max_chars])
                    last = last[max_chars:]
                if last:
                    segments.append(last)
            segments.extend(sub_segments)
        else:
            segments.append(combined)

    # Merge tiny segments with previous, preserving separator
    merged = []
    for seg in segments:
        if merged and len(seg) < min_chars:
            merged[-1] = merged[-1] + "\n\n" + seg
        else:
            merged.append(seg)

    return [(i, text) for i, text in enumerate(merged)]


def segment_by_outline(content: str, outline: list[dict]) -> list[Tuple[int, str]]:
    """
    Split content into segments based on outline.
    Returns list of (segment_index, segment_text) tuples.

    Strategy:
    1. Try to find section boundaries by matching start_marker/end_marker
    2. If markers don't match, try section titles as headers
    3. Fallback: split by character count at paragraph boundaries
    """
    if not content.strip():
        return []

    if not outline:
        return _chunk_by_char_count(content)

    # Try marker-based segmentation
    segments = []
    for i, section in enumerate(outline):
        start_marker = section.get("start_marker", "")
        end_marker = section.get("end_marker", "")

        start_idx = 0
        if start_marker:
            idx = content.find(start_marker)
            if idx >= 0:
                start_idx = idx

        if i < len(outline) - 1:
            # Try end_marker first, then next section's start_marker
            end_idx = len(content)
            if end_marker:
                idx = content.find(end_marker)
                if idx >= 0:
                    end_idx = idx
            else:
                next_start = outline[i + 1].get("start_marker", "")
                if next_start:
                    idx = content.find(next_start)
                    if idx >= 0:
                        end_idx = idx
        else:
            end_idx = len(content)

        segment_text = content[start_idx:end_idx].strip()

        if segment_text:
            # If segment is too long, sub-chunk it
            if len(segment_text) > LONG_FORM_THRESHOLD:
                sub_segments = _chunk_by_char_count(segment_text)
                for sub_idx, sub_text in sub_segments:
                    segments.append((len(segments), sub_text))
            else:
                segments.append((len(segments), segment_text))

    # Validate: if we got no useful segments or total coverage < 50%, fall back
    total_covered = sum(len(text) for _, text in segments)
    if not segments or total_covered < len(content) * 0.5:
        return _chunk_by_char_count(content)

    return segments


def cross_segment_synthesis_prompt(analyses: list[dict]) -> str:
    """
    Build prompt to synthesize multiple segment analyses into one final result.
    Takes list of Stage 1 analysis dicts, returns prompt string.
    """

    if not analyses:
        return (
            "没有分段分析结果可供综合。请返回空的分析结果。\n\n"
            '输出: {"title_zh": "", "title_en": "", "summary_zh": "", '
            '"category": "other", "tags": [], "key_insights": []}'
        )

    if len(analyses) == 1:
        # Single segment — just ask to clean it up
        analysis_json = json.dumps(analyses[0], ensure_ascii=False, indent=2)
        return (
            "以下是一篇长文章的单段分析结果。请将其综合整理为标准的深度摘要格式。\n\n"
            f"## 分析结果\n{analysis_json}\n\n"
            "## 输出要求\n"
            "请输出标准的深度摘要 JSON 结构,包含: title_zh, title_en, summary_zh, "
            "category, tags, people, orgs, key_insights, sentiment, quality_score, "
            "related_topics, multi_quality_score, contradiction_flags, gap_indicators。\n"
            "不要输出 JSON 之外的内容。不要 ```json 标记。"
        )

    # Multi-segment synthesis
    analyses_text = ""
    for a in analyses:
        seg_idx = a.get("segment", "?")
        analyses_text += f"### 分段 {seg_idx}\n"
        analyses_text += f"```json\n{json.dumps(a, ensure_ascii=False, indent=2)}\n```\n\n"

    return (
        "以下是一篇长文章的多个分段分析结果。请将它们综合为一篇完整的深度摘要。\n\n"
        f"## 分段分析结果 (共 {len(analyses)} 段)\n\n"
        f"{analyses_text}\n"
        "## 综合要求\n"
        "1. 将各分段的摘要合并为一篇连贯的整体摘要(summary_zh)。\n"
        "2. 去重并合并标签(tags)、人物(people)、组织(orgs)。\n"
        "3. 综合所有分段的 key_insights,保留最有价值的洞察。\n"
        "4. 综合质量评分(multi_quality_score)取各分段的加权平均。\n"
        "5. 合并 contradiction_flags 和 gap_indicators。\n\n"
        "## 输出要求\n"
        "请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n"
        "{\n"
        '  "title_zh": "简洁的中文标题",\n'
        '  "title_en": "Original English Title",\n'
        '  "summary_zh": "3-5句话的深度中文摘要,综合所有分段",\n'
        '  "category": "ai|engineering|business|science|culture|opinion|security|hardware|other",\n'
        '  "tags": ["tag1", "tag2"],\n'
        '  "people": [{"name": "人名", "role": "角色", "org": "组织"}],\n'
        '  "orgs": ["组织名"],\n'
        '  "key_insights": ["洞察1", "洞察2"],\n'
        '  "sentiment": "positive|neutral|negative|critical",\n'
        '  "quality_score": 0.8,\n'
        '  "related_topics": ["主题1"],\n'
        '  "multi_quality_score": {\n'
        '    "information_density": 0.0,\n'
        '    "analytical_depth": 0.0,\n'
        '    "actionability": 0.0,\n'
        '    "uniqueness": 0.0,\n'
        '    "timeliness": 0.0,\n'
        '    "overall": 0.0\n'
        '  },\n'
        '  "contradiction_flags": [],\n'
        '  "gap_indicators": []\n'
        '}\n\n'
        "不要输出 JSON 之外的内容。不要 ```json 标记。"
    )
