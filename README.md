# Loom — LLM 知识编译 Pipeline + Web 控制台

将原始资料转化为结构化知识库的自动化引擎，附带生产级 Web 控制台（FastAPI + React）。

## 设计原则

- **纯 Python 3.10+**，最小依赖
- **可复用**：不绑定特定数据源，可通过 config 适配任何知识库
- **容错**：错误分类重试，静默降级
- **测试覆盖**：346 个测试，支持 smoke test

## 组件

| 组件 | 说明 |
|------|------|
| `ingest/worker.py` | LLM Ingest 核心：single/two-stage/long-form 三种模式 |
| `ingest/task_queue.py` | SQLite 任务队列，支持 resume |
| `ingest/concept_merger.py` | 概念去重合并 |
| `ingest/wiki_writer.py` | 写入/更新 Wiki 页面 |
| `ingest/long_form.py` | 长文分段处理 |
| `ingest/review_queue.py` | 审查队列 |
| `ingest/reflector.py` | 跨文章综合分析 |
| `curator.py` | 精选内容生成 |
| `rss-fetch.py` | RSS 采集器 |
| `daily-digest.py` | 周摘要生成 |
| `wiki-lint.py` | Wiki 健康检查 |

## Provider 支持

- Mimo (mimo-v2.5-pro) — OpenAI 格式
- Kimi (kimi-for-coding) — Anthropic 格式
- DeepSeek — OpenAI 格式（逃生通道）

## 安装

```bash
cp scripts/ingest/ /path/to/your/project/
# 或
pip install git+https://github.com/bruceaiatgit/loom.git
```

## 测试

```bash
python -m pytest tests/ -q  # 346 passed
python -m pytest tests/ -q --run-smoke  # 含连通性测试
```

## 文档

- `docs/ARCHITECTURE.md` — 架构文档
- `docs/REQUIREMENTS.md` — 需求规格
- `docs/STATUS.md` — 项目状态与经验记录
