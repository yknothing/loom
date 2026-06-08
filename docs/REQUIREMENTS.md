# Cognitive Flywheel — 需求文档

> 最后更新: 2026-05-31

## 项目概述

Cognitive Flywheel 是一个自我生长的个人知识管理系统，灵感来自 [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)（34K stars）。

核心理念：**知识应该像飞轮一样自旋加速**。每次新信息进入，不仅创建新页面，还更新已有页面、强化交叉引用、标记矛盾。认知网络持续生长。

系统自动从 30+ AI/科技 RSS 源抓取文章，提取关键词和实体，维护一个 Obsidian 兼容的结构化 Wiki。

## 核心需求

### REQ-1: RSS Feed 配置

**描述**: 维护一个 YAML 格式的 RSS 源配置文件，包含 30+ AI/科技高质量博客。

**验收标准**:
- 配置文件位于 `config/rss-feeds.yml`
- 每个源包含 `name`, `url`, `category`, `priority` 字段
- 源来自 Karpathy 推荐的 HN Top 100 博客列表
- 分类包括: ai, engineering, security, science, startups, tech
- 优先级: high / medium / low

### REQ-2: RSS 抓取与去重

**描述**: 从配置的 RSS 源抓取文章，保存为 Markdown，自动去重。

**验收标准**:
- 脚本: `scripts/rss-fetch.py`
- 支持 `--dry-run` 预览模式
- 支持 `--feed <name>` 按名称筛选源
- 去重基于 URL SHA256 hash（前 12 位），写入 frontmatter `url_hash` 字段
- 文章保存到 `raw/rss/` 目录，格式 `YYYY-MM-DD-slug.md`
- 每篇文章包含 YAML frontmatter: `source`, `url`, `url_hash`, `date`, `fetched`, `category`, `priority`
- HTML 自动转纯文本
- 输出统计: 新增/已存在/错误数

### REQ-3: LLM Ingest Pipeline

**描述**: 使用 LLM API 处理 `raw/` 中的原始文章，提取洞见，创建/更新 Wiki 页面。

**验收标准**:
- 主脚本: `scripts/llm-ingest-v2.py`
- Worker: `scripts/ingest/worker.py`
- 支持 `--resume`（从队列恢复）、`--init`（初始化队列）、`--incremental`（仅新文件）
- 支持 `--provider`（kimi/mimo/deepseek）、`--max`（限制数量）、`--delay`（请求间隔）
- **默认 two-stage 模式**（compile+reflect），`--single` 回退到单发
- 支持 prompt caching（仅对 supports_cache_control=True 的 provider）
- 任务队列: SQLite (`data/task-queue.db`)，支持 resume、stuck task 重置
- 错误分类重试策略: 429/5xx→重试, 4xx/JSON→不重试, 超时→最多1次
- 输出: `wiki/ideas/`, `wiki/people/`, `wiki/index.md`, `wiki/log.md`
- **三种模式**:
  1. **Single-shot**: 单次 LLM 调用，快速但分析浅
  2. **Two-stage** (默认): Stage 1 analysis + Stage 2 synthesis，输出更丰富
  3. **Long-form** (>50K chars): Outline→分段分析→综合合成（⚠️ Mimo 上不稳定）

### REQ-4: Wiki Lint 健康检查

**描述**: 定期扫描 Wiki 页面，检测各类问题并报告。

**验收标准**:
- 脚本: `scripts/wiki-lint.py`
- 检查项:
  - 🔴 **断裂链接**: `[[wikilink]]` 指向不存在的页面
  - 🟡 **孤立页面**: 无入站链接的页面
  - 🟡 **不完整 frontmatter**: 缺少必需字段
  - 🟡 **过期页面**: 超过 30 天未更新
- 支持 `--fix` 自动修复安全的 frontmatter 问题
- 退出码: 断裂链接存在时返回 1
- 输出健康报告: 🔴 / 🟡 / 🟢 统计
- 各子目录有不同的必需 frontmatter 字段集

### REQ-5: 每日/每周 Digest 生成

**描述**: 基于 `log.md` 中的活动记录，生成周期性摘要页面。

**验收标准**:
- 脚本: `scripts/daily-digest.py`
- 支持 `--week YYYY-WXX` 指定周
- 自动生成当前周的摘要
- 输出到 `wiki/daily/YYYY-WXX.md`
- 包含: 统计（处理条目数、更新页面数）、本周精华、新发现、矛盾/张力（V2）、下周关注
- 自动追加操作日志到 `log.md`

### REQ-6: 每日自动化 Pipeline

**描述**: 一键运行完整的日常知识更新流程。

**验收标准**:
- 脚本: `scripts/daily-pipeline.sh`
- 5 步流程:
  1. RSS 抓取
  2. 新文章 ingest
  3. Digest 生成
  4. 周一执行 Wiki lint
  5. Git commit & push
- 日志保存到 `logs/pipeline-YYYY-MM-DD.log`
- 每步独立容错（一步失败不阻断后续）
- Commit 消息格式: `daily: YYYY-MM-DD knowledge update`

### REQ-7: Cron 定时任务

**描述**: 通过 cron 每日自动执行 pipeline。

**验收标准**:
- 脚本: `scripts/setup-cron.sh`
- 默认每天 06:00 执行
- 自动检测并替换已有 cron 条目
- 日志输出到 `logs/cron.log`

### REQ-8: Seed 数据（初始 Wiki 页面）

**描述**: 创建 5 个初始 Wiki 页面，作为知识网络的种子。

**验收标准**:
- `wiki/ideas/software-30.md` — Software 3.0 概念
- `wiki/ideas/vibe-coding.md` — Vibe Coding 概念
- `wiki/ideas/verifiability.md` — 可验证性
- `wiki/ideas/compounding-knowledge.md` — 复利知识
- `wiki/people/andrej-karpathy.md` — Karpathy 人物档案
- `wiki/projects/cognitive-flywheel.md` — 项目文档
- `wiki/code/llm-wiki-pattern.md` — LLM Wiki 模式说明
- 所有页面符合 AGENTS.md 定义的模板格式

### REQ-FUTURE: qmd 搜索集成

**描述**: 将 Wiki 与 `qmd` 搜索工具集成，支持快速查询。

**状态**: V1 不实现，预留接口。

## 非功能性需求

| 需求 | 描述 |
|------|------|
| Python 版本 | Python 3.10+ |
| 外部依赖 | 仅 `feedparser` 和 `pyyaml` |
| LLM API | V1 不使用外部 LLM API，采用纯 Python 关键词频率提取 |
| Wiki 格式 | Markdown + YAML frontmatter，Obsidian 兼容的 `[[wikilink]]` |
| 编码 | 所有文件 UTF-8 |
| 命名 | 文件名/目录名均使用 `kebab-case` |
| 双语 | 中文摘要优先，英文原文保留 |
| 审计 | Append-only `log.md` 记录所有操作 |

## V1 范围外

- ❌ 真实 LLM API 调用（GPT/Claude）
- ❌ Web UI
- ❌ 多用户支持
- ❌ 数据库（纯文件系统）
- ❌ 月度 Digest
- ❌ 矛盾自动检测
