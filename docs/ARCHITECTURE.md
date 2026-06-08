# Cognitive Flywheel — 架构文档

> 最后更新: 2026-06-07

## 三层架构

```
┌─────────────────────────────────────────────────┐
│  AGENTS.md — Schema 层                          │
│  定义 Wiki 结构规范、页面模板、操作流程           │
│  LLM 读取此文件了解如何维护 wiki/                │
└──────────────────────┬──────────────────────────┘
                       │ governs
                       ▼
┌─────────────────────────────────────────────────┐
│  wiki/ — 知识层 (LLM 维护)                      │
│  结构化的知识页面，持续更新                       │
│  LLM 拥有完全读写权限                            │
└──────────────────────┬──────────────────────────┘
                       │ reads from (never writes)
                       ▼
┌─────────────────────────────────────────────────┐
│  raw/ — 原始资料层 (不可变)                      │
│  RSS 文章、论文、网页剪藏等原始数据               │
│  写入后永不修改                                   │
└─────────────────────────────────────────────────┘
```

**核心约束**: 数据单向流动 `raw/ → wiki/`。LLM 只读 `raw/`，全权负责 `wiki/`。`raw/` 一旦写入不可修改。

## 数据流

```
 RSS 源 (30+)          手动添加
     │                     │
     ▼                     ▼
┌──────────┐        ┌──────────┐
│rss-fetch │───────▶│  raw/    │  不可变原始资料
│  .py     │        │  rss/    │  YAML frontmatter + 正文
└──────────┘        │  papers/ │
                    │  web/    │
                    │  code/   │
                    │  journal/│
                    └────┬─────┘
                         │
                    读取 raw 文件
                    提取关键词/实体
                         │
                         ▼
                   ┌───────────┐
                   │llm-ingest │
                   │   .py     │
                   └─────┬─────┘
                         │
              创建/更新 Wiki 页面
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ ideas/   │   │ people/  │   │ daily/   │
   │ 概念页面 │   │ 人物档案 │   │ 周摘要   │
   └──────────┘   └──────────┘   └──────────┘
         │               │               │
         └───────────────┼───────────────┘
                         │
                    更新索引/日志
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        ┌──────────┐         ┌──────────┐
        │index.md  │         │ log.md   │
        │ 全局目录 │         │ 操作日志 │
        └──────────┘         │(append)  │
                             └──────────┘

    ┌──────────────┐     ┌──────────────┐
    │ wiki-lint.py │     │daily-digest  │
    │ 健康检查     │     │   .py        │
    │ 断裂链接     │     │ 生成周摘要   │
    │ 孤立页面     │     └──────┬───────┘
    │ frontmatter  │            │
    └──────────────┘            ▼
                          wiki/daily/

    ┌──────────────────────────────────┐
    │     daily-pipeline.sh           │
    │  每日自动执行以上全部步骤         │
    │  RSS → Ingest → Digest → Lint   │
    │  → Git commit & push            │
    └──────────────────────────────────┘
           │
     cron (06:00 daily)
     setup-cron.sh
```

## 目录结构

```
cognitive-flywheel/
├── AGENTS.md              # Schema 层: Wiki 维护规则、页面模板、命名规范
├── README.md              # 项目说明
├── config/
│   └── rss-feeds.yml      # RSS 源配置 (38 feeds)
├── raw/                   # 不可变原始资料
│   ├── rss/               # RSS 抓取的文章 (YYYY-MM-DD-slug.md)
│   ├── papers/            # 论文、长文
│   ├── web/               # 网页剪藏
│   ├── code/              # 代码片段
│   ├── journal/           # 个人反思/对话摘要
│   └── assets/            # 图片等附件
├── wiki/                  # LLM 维护的知识页面
│   ├── index.md           # 全局目录 (自动维护)
│   ├── log.md             # 操作日志 (append-only)
│   ├── ideas/             # 概念/主题页面
│   ├── people/            # 思考者档案
│   ├── projects/          # 项目文档
│   ├── mental-models/     # 思维模型
│   ├── daily/             # 每周摘要
│   └── code/              # 技术文档
├── scripts/               # 自动化脚本
│   ├── rss-fetch.py       # RSS 抓取
│   ├── llm-ingest.py      # Ingest pipeline
│   ├── wiki-lint.py       # 健康检查
│   ├── daily-digest.py    # Digest 生成
│   ├── daily-pipeline.sh  # 每日完整流程
│   ├── setup-cron.sh      # Cron 配置
│   └── requirements.txt   # Python 依赖
├── logs/                  # Pipeline 日志 (gitignored)
└── docs/                  # 项目文档
```

## 组件说明

### scripts/rss-fetch.py — RSS 抓取器

| 属性 | 值 |
|------|---|
| 输入 | `config/rss-feeds.yml` |
| 输出 | `raw/rss/YYYY-MM-DD-slug.md` |
| 依赖 | feedparser, pyyaml |
| 功能 | 从 38 个 RSS/Atom 源抓取文章，去重保存 |

**去重机制**: 对文章 URL 计算 SHA256 哈希，取前 12 位存入 frontmatter `url_hash`。抓取前扫描 `raw/rss/` 已有文件的 `url_hash`，跳过已存在的文章。

**关键参数**: `--dry-run`（预览）, `--feed <name>`（筛选源）

### scripts/llm-ingest.py — Ingest Pipeline

| 属性 | 值 |
|------|---|
| 输入 | `raw/` 下的文章文件 |
| 输出 | `wiki/ideas/`, `wiki/people/`, `wiki/index.md`, `wiki/log.md` |
| 依赖 | 仅标准库 |
| 功能 | 提取关键词/实体，创建/更新 Wiki 页面 |

**提取算法 (V1)**:
- **关键词**: 分词 → 去停用词 → 词频排序 → top 15
- **人物名**: 正则匹配大写首字母词对（"First Last"），过滤常见误判
- **URL**: 正则提取 `https?://` 链接

**处理流程**:
1. 读取 raw 文件，提取标题/关键词/人物/URL
2. 创建/更新 `ideas/<slug>.md`（已有则追加补充段落）
3. 为每个检测到的人物创建/更新 `people/<slug>.md`
4. 追加操作记录到 `log.md`
5. 重建 `index.md`

**关键参数**: `--file`（单文件）, `--all-unprocessed`（批量）, `--dry-run`, `--llm`（预留）

### scripts/wiki-lint.py — Wiki 健康检查

| 属性 | 值 |
|------|---|
| 输入 | `wiki/` 全部页面 |
| 输出 | 终端报告（可选自动修复） |
| 依赖 | 仅标准库 |
| 功能 | 检测断裂链接、孤立页面、不完整 frontmatter、过期页面 |

**各目录必需 frontmatter**:
- `ideas/`: title, created, updated
- `people/`: name, updated
- `projects/`: name, status, created, updated
- `daily/`: period, type, generated
- `code/`: title, created, updated

### scripts/daily-digest.py — 周摘要生成

| 属性 | 值 |
|------|---|
| 输入 | `wiki/log.md` |
| 输出 | `wiki/daily/YYYY-WXX.md` |
| 依赖 | 仅标准库 |
| 功能 | 解析 log.md 中指定周的活动，生成统计摘要 |

### scripts/daily-pipeline.sh — 每日 Pipeline

5 步串联: RSS 抓取 → Ingest → Digest → 周一 Lint → Git commit & push。每步独立容错。

### scripts/setup-cron.sh — Cron 配置

注册 `daily-pipeline.sh` 到 crontab，每天 06:00 执行。

## 新增组件

### scripts/ingest/concept_merger.py — 概念去重合并

| 属性 | 值 |
|------|---|
| 输入 | 新文章 title + tags，已有 wiki/ideas/ 页面 |
| 输出 | (action, path) — create 或 update |
| 依赖 | 仅标准库 (difflib.SequenceMatcher) |
| 阈值 | MERGE_SIMILARITY_THRESHOLD = 0.8 |

**去重算法**:
1. 计算 title_similarity（SequenceMatcher，比较 title 和 title_en）
2. 计算 tag_jaccard（标签集合 Jaccard 相似度）
3. 综合评分: `0.6 * title_sim + 0.4 * tag_jaccard`，标题高度相似时(≥0.85)提升至标题分数
4. 综合评分 ≥ 0.8 → 候选合并
5. 合并决策: 标题相似度 > 0.85 直接合并；标签 Jaccard > 0.7 + 标题 ≥ 0.6 合并

**集成点**: wiki_writer.write_ingest_result() 调用 resolve_idea_path() 决定创建新页还是更新已有页。

### scripts/ingest/long_form.py — 长文分段处理

| 属性 | 值 |
|------|---|
| 输入 | raw 文章内容 |
| 输出 | 分段文本 + 综合分析 prompt |
| 阈值 | LONG_FORM_THRESHOLD = 50000 字符 |

**处理流程 (Outline-first)**:
1. 检测文章长度 > 50000 字符 → 触发长文模式
2. 第一次 LLM 调用: 生成文章结构 outline（section 列表，含 start_marker/end_marker）
3. 按 outline 的 marker 定位 section boundary，切分为 segments
4. 如果 outline 解析失败或覆盖率 < 50%，回退到段落级字符数分块
5. 每个 segment 单独做 Stage 1 analysis
6. 最后一次 LLM 调用: cross_segment_synthesis 合成最终结果

**集成点**: worker.py 的 _long_form_call() 在检测到长文时调用。

### scripts/ingest/review_queue.py — Review Queue

| 属性 | 值 |
|------|---|
| 存储 | `data/review-queue.json` |
| 线程安全 | write-to-temp + rename 原子写入 |
| Item 类型 | duplicate_concepts, contradiction, thin_page, gap, stale_page |

**CRUD 操作**:
- `enqueue_review(type, data, source)` — 添加审查项
- `list_pending(type=None)` — 列出待审查项
- `mark_resolved(id, resolution)` — 标记已解决
- `get_stats()` — 按类型/状态统计
- `clear_resolved(older_than_days=30)` — 清理已解决的旧条目

**写入来源**: wiki_writer 合并时、reflector 检测重复时自动写入。

## 架构图（更新版）

```
raw/rss/*.md ──→ [llm-ingest-v2.py / worker.py] ──→ DB (ingest_tasks + ingest_results)
                           │
                    ┌──────┼──────────────────────┐
                    v      v                      v
              [短文]     [长文 ≥ 5000]       [去重检查]
              两阶段      │                    concept_merger
              Ingest     v                      │
                    outline 生成                 │
                    → 分段处理                   │
                    → segment analyses           │
                    → cross-segment              │
                      synthesis                  │
                       │                        │
                       └────────┬───────────────┘
                                v
                         wiki_writer.py
                         ├── create 或 update (由 merger 决定)
                         ├── 合并时写入 review-queue.json
                         └── wiki/ideas/, people/, index.md, log.md

    [Reflector]
        ├── 聚类 + 跨文章 synthesis
        ├── detect_duplicate_candidates → review-queue.json
        └── wiki/ideas/synthesis-*.md

    [Prompt Caching]
        └── Anthropic 格式: cache_control: ephemeral
            OpenAI 格式: 静默 fallback
            记录 cache_creation/cached tokens 到 DB
```

## 关键技术决策

### ADR-001: V1 纯 Python，不使用外部 LLM API

**背景**: Karpathy 的 LLM Wiki 理念依赖 LLM 进行内容提取和知识合成。但在 V1 阶段，我们希望零成本、零配置、离线运行。

**决策**: V1 使用纯 Python 关键词频率提取替代 LLM API。`llm-ingest.py` 的 `--llm` 参数预留未来扩展。

**后果**:
- ✅ 零外部 API 依赖，可离线运行
- ✅ 快速，无网络延迟
- ⚠️ 提取质量有限（无法理解语义，仅词频统计）
- ⚠️ 人物检测依赖简单正则，误报/漏报较多

### ADR-002: Markdown + YAML Frontmatter (Obsidian 兼容)

**背景**: Wiki 页面需要结构化元数据，同时保持人类可读性。

**决策**: 使用 Markdown 文件 + YAML frontmatter，支持 `[[wikilink]]` 格式。

**后果**:
- ✅ Obsidian 用户可直接打开 `wiki/` 目录
- ✅ 纯文本，Git 友好，可 diff
- ✅ 无需数据库
- ⚠️ frontmatter 解析是手写的简单解析器（非完整 YAML parser）

### ADR-003: Append-only log.md 审计机制

**背景**: 需要追踪所有 Wiki 操作，保证可审计性。

**决策**: `wiki/log.md` 采用 append-only 模式，每条记录包含日期、操作类型、来源文件、更新页面。

**后果**:
- ✅ 完整操作历史，可回溯
- ✅ `llm-ingest.py` 通过解析 log.md 判断哪些文件已处理（去重）
- ⚠️ log.md 会持续增长，需要定期归档（未来）

### ADR-004: 概念去重合并策略 — 保守更新，不自动删除

**背景**: ideas/ 页面中存在大量近重复概念。完全自动合并风险高。

**决策**:
1. Ingest Stage 2（wiki_writer）在创建新 ideas/ 页面前，调用 concept_merger 查询相似度
2. 综合评分 ≥ 0.8 → 更新已有页面（追加 sources、insights），不创建新页面
3. 合并时将事件写入 review-queue.json 供追踪
4. Reflector 的 `--detect-duplicates` 模式可批量扫描所有 ideas/ 页面，发现遗漏的重复候选
5. 不自动删除页面（符合 AGENTS.md 规则）

**实际实现**:
- 相似度算法: `0.6 * SequenceMatcher(title) + 0.4 * Jaccard(tags)`
- 标题高度相似 (≥ 0.85) 直接合并；标签重叠 > 0.7 + 标题 ≥ 0.6 合并
- 合并时保留已有内容，追加新 insights（去重），更新 mention_count

**后果**:
- ✅ 防止概念页无限增殖
- ✅ 不自动删除，保留人工审查界面
- ✅ 零 LLM 调用开销（纯 difflib + set 运算）
- ⚠️ 合并阈值需根据实际效果调优（默认 0.8）

### ADR-005: 长文分段策略 — Outline-first

**背景**: 长文直接截断丢失后半部分洞见。社区方案是 outline → 分段 → 合成。

**决策**:
1. 检测文章长度 > 50000 字符 → 触发长文模式
2. 第一次 LLM 调用: 生成文章结构 outline（JSON sections 列表，含 start_marker/end_marker）
3. 按 outline 的 marker 定位 section boundary 切分（不是字符截断）
4. 每个 segment 单独做 Stage 1 analysis
5. 最后调用 cross_segment_synthesis 合成最终结果
6. Outline 解析失败或覆盖率 < 50% 时回退到段落级字符数分块

**实际实现**:
- `_long_form_call()` 在 worker.py 中集成长文流程
- outline 失败自动回退到 single-shot 截断模式
- 单个 segment 失败不阻塞其他 segment
- DB 记录 `stage = 'long_form'` 和 segment 信息

**后果**:
- ✅ 长文洞见不再丢失
- ✅ 按 section boundary 切分保留语义完整性
- ✅ 优雅降级（outline 失败 → 字符分块 → 截断）
- ⚠️ 长文 ingest 成本增加 (1 + n + 1 次 LLM 调用)

### ADR-006: Prompt Caching 策略

**背景**: 系统 prompt 在每次调用中重复发送，造成 token 浪费。

**决策**:
1. 对 Anthropic 格式的 API 请求，将 system prompt 包装为 `["type": "text", "cache_control": {"type": "ephemeral"}}]`
2. 对 OpenAI 格式不添加 cache_control，静默 fallback
3. 如果 provider 返回 `cache_creation_input_tokens` 或 `cached_input_tokens`，记录在结果中

**实际实现**:
- worker.py 的 `_single_llm_call()` 根据 `api_format` 判断是否添加 cache_control
- Kimi (anthropic 格式) 自动享受 cache
- Mimo/DeepSeek (openai 格式) 不受影响
- cache 统计字段: `cache_creation_input_tokens`, `cached_input_tokens`

**后果**:
- ✅ 非破坏性，不支持则静默 fallback
- ✅ Anthropic 格式 provider 成本显著降低
- ⚠️ 实际 cache 命中率取决于 provider 实现

## 数据模型

### Raw 文件 Frontmatter

```yaml
---
source: Simon Willison        # RSS 源名称
url: https://...               # 原文 URL
url_hash: abc123def456         # URL SHA256[:12] (去重用)
date: 2026-05-30               # 文章发布日期
fetched: 2026-05-30            # 抓取日期
category: ai                   # 分类
priority: high                 # 优先级
---
```

### Wiki Ideas 页面 Frontmatter

```yaml
---
title: Software 3.0
created: 2026-05-30
updated: 2026-05-30
sources:
  - raw/rss/2026-05-30-example.md
related:
  - people/andrej-karpathy
tags:
  - software
  - llm
  - agents
---
```

### Wiki People 页面 Frontmatter

```yaml
---
name: Andrej Karpathy
role: AI Researcher
sources:
  - raw/rss/2026-05-30-example.md
updated: 2026-05-30
---
```

### Log 条目格式

```markdown
## [2026-05-30] ingest | 文章标题
来源: raw/rss/2026-05-30-slug.md
更新页面: ideas/example, people/john-doe
```
