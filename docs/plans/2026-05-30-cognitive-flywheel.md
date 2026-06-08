# Cognitive Flywheel 实现计划

> 日期: 2026-05-30
> 状态: ✅ 全部完成

## 背景

基于 Karpathy LLM Wiki 理念，构建一个自我生长的个人知识管理系统。V1 采用纯 Python 实现，不依赖外部 LLM API。

## 任务清单

### Task 1: 项目初始化与 Schema 定义
**状态**: ✅ 完成

初始化 Git 仓库，创建 `AGENTS.md` 定义 Wiki 结构规范。
- 目录结构（raw/, wiki/, config/, scripts/）
- 页面模板（ideas, people, projects, mental-models, daily, code）
- 操作流程（Ingest / Query / Lint）
- 命名规范（kebab-case, YYYY-MM-DD 日期格式）

### Task 2: RSS Feed 配置
**状态**: ✅ 完成

创建 `config/rss-feeds.yml`，配置 38 个高质量 RSS 源。
- 基于 Karpathy 推荐的 HN Top 100 博客
- 分类: ai, engineering, security, science, startups, tech
- 优先级: high / medium / low
- 输出文件: `config/rss-feeds.yml`

### Task 3: RSS 抓取脚本
**状态**: ✅ 完成

实现 `scripts/rss-fetch.py`，从配置源抓取文章并去重。
- feedparser 解析 RSS/Atom
- SHA256 hash 去重机制
- HTML → 纯文本转换
- 支持 `--dry-run` 和 `--feed` 参数
- 输出文件: `scripts/rss-fetch.py`

### Task 4: LLM Ingest Pipeline
**状态**: ✅ 完成

实现 `scripts/llm-ingest.py`，处理原始文章并更新 Wiki。
- 纯 Python 关键词频率提取
- 人物名检测（正则匹配）
- 自动创建/更新 ideas/ 和 people/ 页面
- index.md 自动重建
- log.md append-only 日志
- 输出文件: `scripts/llm-ingest.py`

### Task 5: Wiki Lint 健康检查
**状态**: ✅ 完成

实现 `scripts/wiki-lint.py`，检测 Wiki 健康问题。
- 断裂链接检测
- 孤立页面检测
- Frontmatter 完整性检查
- 过期页面检测（>30 天）
- `--fix` 自动修复模式
- 输出文件: `scripts/wiki-lint.py`

### Task 6: 每日 Digest 生成
**状态**: ✅ 完成

实现 `scripts/daily-digest.py`，生成周摘要。
- 解析 log.md 中的活动记录
- 统计: 处理条目数、更新页面数、新概念/人物
- 输出到 `wiki/daily/YYYY-WXX.md`
- 输出文件: `scripts/daily-digest.py`

### Task 7: 自动化 Pipeline 与 Cron
**状态**: ✅ 完成

创建 `daily-pipeline.sh` 串联全部流程，`setup-cron.sh` 配置定时任务。
- 5 步 pipeline: RSS → Ingest → Digest → Lint(周一) → Git push
- 每步独立容错
- Cron 每天 06:00 执行
- 输出文件: `scripts/daily-pipeline.sh`, `scripts/setup-cron.sh`

### Task 8: Seed 数据
**状态**: ✅ 完成

创建 8 个初始 Wiki 页面作为知识网络种子。
- 4 个 ideas/ 页面（software-30, vibe-coding, verifiability, compounding-knowledge）
- 1 个 people/ 页面（andrej-karpathy）
- 1 个 projects/ 页面（cognitive-flywheel）
- 1 个 code/ 页面（llm-wiki-pattern）
- 1 个 daily/ 页面（2026-W22）
- 加 wiki/index.md 和 wiki/log.md

## 文件清单

共 19 个项目文件:

```
cognitive-flywheel/
├── AGENTS.md                          # Schema 定义
├── README.md                          # 项目说明
├── .gitignore                         # Git 忽略规则
├── config/
│   └── rss-feeds.yml                  # 38 个 RSS 源配置
├── scripts/
│   ├── requirements.txt               # Python 依赖 (feedparser, pyyaml)
│   ├── rss-fetch.py                   # RSS 抓取脚本
│   ├── llm-ingest.py                  # Ingest pipeline
│   ├── wiki-lint.py                   # Wiki 健康检查
│   ├── daily-digest.py               # 周摘要生成
│   ├── daily-pipeline.sh             # 每日自动化 Pipeline
│   └── setup-cron.sh                  # Cron 定时任务配置
├── wiki/
│   ├── index.md                       # 全局知识索引
│   ├── log.md                         # 操作日志
│   ├── ideas/
│   │   ├── software-30.md             # Software 3.0
│   │   ├── vibe-coding.md             # Vibe Coding
│   │   ├── verifiability.md           # 可验证性
│   │   └── compounding-knowledge.md   # 复利知识
│   ├── people/
│   │   └── andrej-karpathy.md         # Andrej Karpathy
│   ├── projects/
│   │   └── cognitive-flywheel.md      # 项目文档
│   ├── code/
│   │   └── llm-wiki-pattern.md        # LLM Wiki 模式
│   └── daily/
│       └── 2026-W22.md                # W22 周摘要
└── docs/
    ├── REQUIREMENTS.md                # 需求文档
    ├── ARCHITECTURE.md                # 架构文档
    └── plans/
        └── 2026-05-30-cognitive-flywheel.md  # 本计划
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.10+, Bash |
| 外部依赖 | feedparser ≥ 6.0, pyyaml ≥ 6.0 |
| 存储 | 纯文件系统（Markdown + YAML frontmatter） |
| Wiki 格式 | Obsidian 兼容 `[[wikilink]]` |
| 版本控制 | Git |
| 定时任务 | cron |
| LLM (V1) | 无（纯 Python 关键词提取） |
