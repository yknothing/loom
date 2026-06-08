#!/usr/bin/env python3
"""
prompts.py - LLM prompt templates for deep ingest

Supports two modes:
  1. Single-shot (backward compatible): SYSTEM_PROMPT + ARTICLE_PROMPT_TEMPLATE
  2. Two-stage: ANALYSIS_PROMPT (Stage 1) → SYNTHESIS_PROMPT (Stage 2)
"""

SYSTEM_PROMPT = "你是一个专业的科技内容分析专家。你擅长从技术文章、博客、新闻中提取关键洞察,准确识别人物和组织,并进行深度摘要。你的输出必须是严格的 JSON 格式,不要包含任何 JSON 之外的文字。"

ARTICLE_PROMPT_TEMPLATE = (
    "请分析以下文章,输出严格 JSON 格式的分析结果。\n\n"
    "## 文章元数据\n"
    "- 来源: {source}\n"
    "- URL: {url}\n"
    "- 日期: {date}\n"
    "- 原始分类: {category}\n"
    "- 优先级: {priority}\n\n"
    "## 文章正文\n{content}\n\n"
    "## 输出要求\n"
    '请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n'
    '{{\n'
    '  "title_zh": "简洁的中文标题",\n'
    '  "title_en": "Original English Title",\n'
    '  "summary_zh": "3-5句话的深度中文摘要。抓住核心论点、关键数据、独特洞察。不要简单复述,要提炼出文章的真正价值。",\n'
    '  "category": "从以下选择最匹配的一个: ai, engineering, business, science, culture, opinion, security, hardware, other",\n'
    '  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],\n'
    '  "people": [\n'
    '    {{"name": "真实人名", "role": "他们的角色/头衔", "org": "所属组织"}}\n'
    '  ],\n'
    '  "orgs": ["组织/公司名称"],\n'
    '  "key_insights": [\n'
    '    "核心洞察1: 具体且有信息量的洞察",\n'
    '    "核心洞察2: 另一个关键发现"\n'
    '  ],\n'
    '  "sentiment": "从以下选择: positive, neutral, negative, critical",\n'
    '  "quality_score": 0.8,\n'
    '  "related_topics": ["相关主题1", "相关主题2"]\n'
    '}}\n\n'
    "## 重要规则\n"
    "1. **people**: 只提取真实的、有名字的人。不要把网站名(如 Daring Fireball)、文章标题、短语、组织名误认为人名。如果不确定是不是真人,就不提取。\n"
    '2. **summary_zh**: 要有深度，抓住核心层面的洞察，不只是表面复述。\n'
    '3. **tags**: 要具体，比如 machine-learning 而不是 technology。5-8个标签。\n'
    "4. **key_insights**: 提炼出独立有价值的核心观点,即使不读原文也能理解。\n"
    "5. **quality_score**: 0-1 评分。0.3以下=水文/广告,0.5=一般信息,0.7=有价值的分析,0.9+=必读精品。\n"
    "6. **sentiment**: 作者对主题的态度。critical 表示批判性分析。\n"
    "7. 不要输出 JSON 之外的内容。不要 ```json 标记。"
)

# ────────────────────────────────────────────
# Stage 1: ANALYSIS_PROMPT — Deep article analysis
# ────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = (
    "你是一个专业的科技内容分析专家。你的任务是对文章进行深度分析,提取实体、概念、"
    "论点、与已有知识的矛盾、以及信息质量评估。你的输出必须是严格的 JSON 格式,"
    "不要包含任何 JSON 之外的文字。"
)

ANALYSIS_PROMPT_TEMPLATE = (
    "请对以下文章进行深度分析。输出严格 JSON 格式。\n\n"
    "## 文章元数据\n"
    "- 来源: {source}\n"
    "- URL: {url}\n"
    "- 日期: {date}\n"
    "- 原始分类: {category}\n"
    "- 优先级: {priority}\n\n"
    "## 文章正文\n{content}\n\n"
    "## 分析要求\n"
    "请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n"
    '{{\n'
    '  "entities": [\n'
    '    {{"name": "实体名称", "type": "person|org|concept|tech", "role": "该实体的角色或意义"}}\n'
    '  ],\n'
    '  "concepts": [\n'
    '    {{"name": "概念名称", "definition": "简要定义", "novelty": "new|established|emerging"}}\n'
    '  ],\n'
    '  "key_claims": [\n'
    '    {{"claim": "核心论断", "evidence_type": "data|anecdote|argument|speculation", "confidence": 0.8}}\n'
    '  ],\n'
    '  "contradictions_with": [\n'
    '    {{"topic": "矛盾主题", "existing_position": "已有观点", "new_position": "本文新观点"}}\n'
    '  ],\n'
    '  "open_questions": ["文章提出但未解答的问题"],\n'
    '  "source_quality": {{\n'
    '    "information_density": 0.0,\n'
    '    "analytical_depth": 0.0,\n'
    '    "actionability": 0.0,\n'
    '    "uniqueness": 0.0,\n'
    '    "timeliness": 0.0\n'
    '  }},\n'
    '  "related_wiki_topics": ["estimated-concept-slug-1", "estimated-concept-slug-2"]\n'
    '}}\n\n'
    "## 分析规则\n"
    "1. **entities**: 提取所有重要实体。person=真实人名, org=组织/公司, concept=抽象概念, tech=具体技术。\n"
    "2. **concepts**: 识别文章涉及的核心概念,评估其新颖性。new=首次提出, established=广泛认可, emerging=新兴趋势。\n"
    "3. **key_claims**: 提取可验证的核心论断。confidence=0-1,基于证据强度评估。\n"
    "4. **contradictions_with**: 如果文章的观点与你所知的常识或主流观点矛盾,请记录。如无矛盾留空数组。\n"
    "5. **open_questions**: 文章提出但未充分回答的问题,或读者可能进一步追问的问题。\n"
    "6. **source_quality**: 五维度评估,每项0-1分。\n"
    '   - information_density: 每段有多少新信息(非重复/填充)\n'
    "   - analytical_depth: 推理和论证的深度(非表面陈述)\n"
    "   - actionability: 读者能否基于此采取行动\n"
    "   - uniqueness: 这信息是否在其他地方也能找到\n"
    "   - timeliness: 内容的时效性(新闻>常青内容可能更高)\n"
    "7. **related_wiki_topics**: 估计可能相关的wiki主题slug(kebab-case)。\n"
    "8. 不要输出 JSON 之外的内容。不要 ```json 标记。"
)

# ────────────────────────────────────────────
# Stage 2: SYNTHESIS_PROMPT — Enhanced structured output
# ────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = SYSTEM_PROMPT  # Reuse same system prompt for backward compat

SYNTHESIS_PROMPT_TEMPLATE = (
    "基于以下深度分析和原文,生成增强版结构化摘要。\n\n"
    "## 文章元数据\n"
    "- 来源: {source}\n"
    "- URL: {url}\n"
    "- 日期: {date}\n"
    "- 原始分类: {category}\n"
    "- 优先级: {priority}\n\n"
    "## Stage 1 深度分析结果\n{stage1_json}\n\n"
    "## 文章正文\n{content}\n\n"
    "## 输出要求\n"
    "请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n"
    '{{\n'
    '  "title_zh": "简洁的中文标题",\n'
    '  "title_en": "Original English Title",\n'
    '  "summary_zh": "3-5句话的深度中文摘要。抓住核心论点、关键数据、独特洞察。不要简单复述,要提炼出文章的真正价值。参考Stage 1分析中的key_claims增强摘要深度。",\n'
    '  "category": "从以下选择最匹配的一个: ai, engineering, business, science, culture, opinion, security, hardware, other",\n'
    '  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],\n'
    '  "people": [\n'
    '    {{"name": "真实人名", "role": "他们的角色/头衔", "org": "所属组织"}}\n'
    '  ],\n'
    '  "orgs": ["组织/公司名称"],\n'
    '  "key_insights": [\n'
    '    "核心洞察1: 具体且有信息量的洞察",\n'
    '    "核心洞察2: 另一个关键发现"\n'
    '  ],\n'
    '  "sentiment": "从以下选择: positive, neutral, negative, critical",\n'
    '  "quality_score": 0.8,\n'
    '  "related_topics": ["相关主题1", "相关主题2"],\n'
    '  "multi_quality_score": {{\n'
    '    "information_density": 0.0,\n'
    '    "analytical_depth": 0.0,\n'
    '    "actionability": 0.0,\n'
    '    "uniqueness": 0.0,\n'
    '    "timeliness": 0.0,\n'
    '    "overall": 0.0\n'
    '  }},\n'
    '  "contradiction_flags": [\n'
    '    {{"topic": "矛盾主题", "description": "简要说明矛盾内容"}}\n'
    '  ],\n'
    '  "gap_indicators": ["文章提到但未深入探讨的主题"]\n'
    '}}\n\n'
    "## 重要规则\n"
    "1. **people**: 只提取真实的、有名字的人。不要把网站名、文章标题、短语、组织名误认为人名。利用Stage 1的entities中type=person的条目。\n"
    "2. **summary_zh**: 要有深度,抓住核心洞察。参考Stage 1的key_claims和concepts增强摘要。\n"
    "3. **tags**: 要具体。参考Stage 1的concepts和entities来补充标签。5-8个标签。\n"
    "4. **key_insights**: 结合Stage 1的key_claims,提炼出独立有价值的核心观点。\n"
    "5. **quality_score**: 基于Stage 1的source_quality各维度的加权综合评分。\n"
    "6. **multi_quality_score**: 直接使用Stage 1的source_quality评估,并计算overall=(density+depth+actionability+uniqueness+timeliness)/5。\n"
    "7. **contradiction_flags**: 基于Stage 1的contradictions_with生成。如果无矛盾则为空数组。\n"
    "8. **gap_indicators**: 基于Stage 1的open_questions和concepts中novelty=new/emerging的条目生成。\n"
    "9. 不要输出 JSON 之外的内容。不要 ```json 标记。"
)

# ────────────────────────────────────────────
# REFLECT_PROMPT — Post-batch reflection
# ────────────────────────────────────────────

REFLECT_SYSTEM_PROMPT = (
    "你是一个知识管理专家。你的任务是分析一组相关文章,发现跨文章的主题、矛盾、"
    "和综合创新机会。你的输出必须是严格的 JSON 格式,不要包含任何 JSON 之外的文字。"
)

REFLECT_PROMPT_TEMPLATE = (
    "分析以下一组相关文章的摘要和分析结果,找出跨文章的模式。\n\n"
    "## 文章集群 (共 {article_count} 篇)\n\n"
    "{articles_text}\n\n"
    "## 分析要求\n"
    "请输出以下 JSON 结构(不要包含 markdown 代码块标记):\n\n"
    '{{\n'
    '  "cross_cutting_themes": [\n'
    '    {{"theme": "主题名称", "description": "该主题如何贯穿多篇文章", "articles": ["article-title-1", "article-title-2"]}}\n'
    '  ],\n'
    '  "contradictions": [\n'
    '    {{"topic": "矛盾主题", "positions": [{{"article": "文章标题", "position": "观点"}}]}}\n'
    '  ],\n'
    '  "implicit_relationships": [\n'
    '    {{"concept_a": "概念A", "concept_b": "概念B", "connection": "隐含关联说明"}}\n'
    '  ],\n'
    '  "gaps": [\n'
    '    {{"topic": "缺失主题", "evidence": "多篇文章暗示但无专门讨论的证据", "suggested_title": "建议文章标题"}}\n'
    '  ],\n'
    '  "synthesis_opportunities": [\n'
    '    {{\n'
    '      "title": "综合文章标题",\n'
    '      "title_zh": "中文标题",\n'
    '      "synthesis_of": ["article-1", "article-2"],\n'
    '      "abstract": "综合摘要,说明为什么这些文章值得合并讨论",\n'
    '      "key_points": ["要点1", "要点2"]\n'
    '    }}\n'
    '  ]\n'
    '}}\n\n'
    "## 分析规则\n"
    "1. **cross_cutting_themes**: 至少出现2篇文章中的共同主题。描述其跨文章的表现形式。\n"
    "2. **contradictions**: 不同文章对同一主题持相反观点。列出各方立场。\n"
    "3. **implicit_relationships**: 不同文章中的概念之间存在的非显而易见的关联。\n"
    "4. **gaps**: 多篇文章都暗示但没有任何文章深入探讨的主题。提出建议标题。\n"
    "5. **synthesis_opportunities**: 强关联的文章组,值得创建综合概念文章。给出中英文标题和摘要。\n"
    "6. 如果某个类别没有发现,返回空数组。\n"
    "7. 不要输出 JSON 之外的内容。不要 ```json 标记。"
)


# ────────────────────────────────────────────
# Prompt builder functions
# ────────────────────────────────────────────

def build_article_prompt(meta: dict, content: str, max_chars: int = 10000) -> str:
    """Build the prompt for a single article (backward compatible single-shot mode)."""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... 内容已截断 ...]"

    return ARTICLE_PROMPT_TEMPLATE.format(
        source=meta.get("source", "unknown"),
        url=meta.get("url", ""),
        date=meta.get("date", ""),
        category=meta.get("category", ""),
        priority=meta.get("priority", ""),
        content=content,
    )


def build_analysis_prompt(meta: dict, content: str, max_chars: int = 10000) -> str:
    """Build Stage 1 analysis prompt for an article."""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... 内容已截断 ...]"

    return ANALYSIS_PROMPT_TEMPLATE.format(
        source=meta.get("source", "unknown"),
        url=meta.get("url", ""),
        date=meta.get("date", ""),
        category=meta.get("category", ""),
        priority=meta.get("priority", ""),
        content=content,
    )


def build_synthesis_prompt(meta: dict, content: str, stage1_result: dict,
                           max_chars: int = 10000) -> str:
    """Build Stage 2 synthesis prompt using Stage 1 analysis result."""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... 内容已截断 ...]"

    import json
    stage1_json = json.dumps(stage1_result, ensure_ascii=False, indent=2)

    return SYNTHESIS_PROMPT_TEMPLATE.format(
        source=meta.get("source", "unknown"),
        url=meta.get("url", ""),
        date=meta.get("date", ""),
        category=meta.get("category", ""),
        priority=meta.get("priority", ""),
        stage1_json=stage1_json,
        content=content,
    )


def build_reflect_prompt(articles: list[dict]) -> str:
    """Build reflection prompt for a cluster of articles.

    Each article dict should have: title, summary, tags, key_insights, analysis (optional)
    """
    articles_text = ""
    for i, art in enumerate(articles, 1):
        articles_text += f"### 文章 {i}: {art.get('title', 'Untitled')}\n"
        if art.get("summary"):
            articles_text += f"摘要: {art['summary']}\n"
        if art.get("tags"):
            articles_text += f"标签: {', '.join(art['tags'])}\n"
        if art.get("key_insights"):
            articles_text += "核心洞察:\n"
            for ins in art["key_insights"]:
                articles_text += f"  - {ins}\n"
        if art.get("analysis"):
            import json
            articles_text += f"Stage 1分析: {json.dumps(art['analysis'], ensure_ascii=False)}\n"
        articles_text += "\n"

    return REFLECT_PROMPT_TEMPLATE.format(
        article_count=len(articles),
        articles_text=articles_text,
    )
