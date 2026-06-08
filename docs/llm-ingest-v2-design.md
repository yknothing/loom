# LLM Deep Ingest V2 — 设计文档

## 目标

用 LLM 对 1042 篇 raw RSS 文章进行深度理解：
- 提取高质量摘要（中文）
- 准确实体识别（人物、组织、概念）
- 自动分类和打标签
- 构建人物知识图谱
- 生成跨文章的主题连接

## 架构概览

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Task Queue  │────▶│   Worker     │────▶│  Quality     │────▶│  Wiki Writer │
│  (SQLite)   │     │  (LLM Call)  │     │  Check       │     │  (File Ops)  │
└─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       ▲                   │                    │                     │
       │              ┌────▼────┐          ┌────▼────┐          ┌────▼────┐
       │              │  Retry  │          │  Reject │          │  Commit │
       │              │  Queue  │          │  Log    │          │  + Index│
       │              └─────────┘          └─────────┘          └─────────┘
       │
  ┌────┴─────┐
  │ Progress │
  │ Tracker  │
  └──────────┘
```

## 核心组件

### 1. Task Queue (`task-queue.db` — SQLite)

```sql
CREATE TABLE ingest_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT UNIQUE NOT NULL,          -- raw/rss/2026-06-02-xxx.md
    status TEXT DEFAULT 'pending',          -- pending|running|done|failed|rejected
    priority INTEGER DEFAULT 0,             -- higher = first
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    llm_model TEXT,                         -- which model processed this
    input_tokens INTEGER,
    output_tokens INTEGER,
    result_hash TEXT                        -- hash of LLM output for dedup
);

CREATE TABLE ingest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES ingest_tasks(id),
    raw_filepath TEXT NOT NULL,
    title_zh TEXT,                          -- 中文标题
    title_en TEXT,                          -- 英文原标题
    summary_zh TEXT,                        -- 中文摘要 (3-5句)
    category TEXT,                          -- LLM分类: ai|engineering|business|science|culture|opinion
    tags TEXT,                              -- JSON array of tags
    people TEXT,                            -- JSON array of {name, role, org}
    orgs TEXT,                              -- JSON array of organization names
    key_insights TEXT,                      -- JSON array of key insights
    sentiment TEXT,                         -- positive|neutral|negative|critical
    quality_score REAL,                     -- 0-1 LLM自评内容质量
    related_topics TEXT,                    -- JSON array of related topic suggestions
    raw_llm_response TEXT,                  -- 完整LLM响应，用于debug
    created_at TEXT DEFAULT (datetime('now'))
);

-- 断点续传：查询未完成任务
CREATE INDEX idx_status ON ingest_tasks(status);
CREATE INDEX idx_filepath ON ingest_tasks(filepath);
```

### 2. Worker — LLM 调用

每次处理一篇文章：
- 读取 raw 文件
- 构造 prompt（含 frontmatter 元数据）
- 调用 LLM API
- 解析结构化 JSON 输出
- 存入 results 表

**Prompt 模板：**

```
你是一个专业的科技内容分析专家。请分析以下文章，输出严格 JSON 格式的结果。

## 文章元数据
- 来源: {source}
- URL: {url}  
- 日期: {date}
- 类别: {category}

## 文章正文
{content，截断到 8000 字符}

## 输出要求（严格 JSON）
{
  "title_zh": "中文标题翻译",
  "title_en": "英文原标题",
  "summary_zh": "3-5句话的中文深度摘要，抓住核心论点和洞察",
  "category": "ai|engineering|business|science|culture|opinion|other",
  "tags": ["tag1", "tag2", ...],  // 5-10个标签
  "people": [
    {"name": "真实人名", "role": "角色/头衔", "org": "组织"}
  ],
  "orgs": ["组织名1", ...],
  "key_insights": [
    "核心洞察1",
    "核心洞察2"
  ],
  "sentiment": "positive|neutral|negative|critical",
  "quality_score": 0.8,  // 0-1, 评估文章的信息密度和价值
  "related_topics": ["相关主题1", ...]
}

注意：
- people 只提取真实人物，不要把网站名、文章标题、短语误认为人名
- summary 要有深度，不是简单复述
- tags 要具体，不要泛泛的 "technology"
- quality_score 要客观，水文给低分
```

### 3. Rate Limiter / 并发控制

```python
class RateLimiter:
    """Token bucket rate limiter"""
    def __init__(self, rpm=10, tpm=50000):
        self.rpm = rpm          # requests per minute
        self.tpm = tpm          # tokens per minute
        self.request_times = []
        self.token_usage = []
    
    async def acquire(self, estimated_tokens=2000):
        """Wait until a request slot is available"""
        now = time.time()
        # Clean old records
        self.request_times = [t for t in self.request_times if now - t < 60]
        self.token_usage = [(t, tok) for t, tok in self.token_usage if now - t < 60]
        
        # Check RPM
        if len(self.request_times) >= self.rpm:
            wait = 60 - (now - self.request_times[0]) + 1
            await asyncio.sleep(wait)
        
        # Check TPM
        total_tokens = sum(tok for _, tok in self.token_usage)
        if total_tokens + estimated_tokens > self.tpm:
            wait = 60 - (now - self.token_usage[0][0]) + 1
            await asyncio.sleep(wait)
        
        self.request_times.append(time.time())
```

### 4. Quality Check

```python
def validate_result(result: dict, raw_content: str) -> tuple[bool, str]:
    """Validate LLM output quality"""
    errors = []
    
    # 必填字段
    required = ['title_zh', 'summary_zh', 'category', 'tags', 'key_insights']
    for field in required:
        if not result.get(field):
            errors.append(f"missing required field: {field}")
    
    # 摘要长度 (至少50字)
    if len(result.get('summary_zh', '')) < 50:
        errors.append("summary_zh too short")
    
    # people 数量合理 (0-10)
    people = result.get('people', [])
    if len(people) > 10:
        errors.append(f"too many people: {len(people)}")
    
    # tags 数量合理 (3-15)
    tags = result.get('tags', [])
    if len(tags) < 3 or len(tags) > 15:
        errors.append(f"unusual tag count: {len(tags)}")
    
    # quality_score 在范围内
    score = result.get('quality_score', 0)
    if not (0 <= score <= 1):
        errors.append(f"invalid quality_score: {score}")
    
    return len(errors) == 0, '; '.join(errors)
```

### 5. Wiki Writer — 结果写入

从 results 表读取，更新 wiki 页面：
- `ideas/{slug}.md` — 用 LLM 结果替换之前的关键词提取内容
- `people/{slug}.md` — 用准确的实体信息更新
- `wiki/log.md` — 更新处理日志
- `wiki/index.md` — 重建索引

### 6. Progress Tracker

```python
class ProgressTracker:
    """实时进度追踪"""
    def __init__(self, db_path):
        self.db_path = db_path
    
    def summary(self) -> dict:
        return {
            "total": count("all"),
            "pending": count("pending"),
            "running": count("running"),
            "done": count("done"),
            "failed": count("failed"),
            "rejected": count("rejected"),
            "tokens_used": sum(input_tokens) + sum(output_tokens),
            "elapsed_minutes": ...,
            "eta_minutes": ...
        }
    
    def report(self) -> str:
        """生成可读的进度报告"""
        s = self.summary()
        return f"""
📊 Ingest Progress
  ✅ Done: {s['done']}/{s['total']} ({s['done']/s['total']*100:.1f}%)
  ⏳ Pending: {s['pending']}
  🔄 Running: {s['running']}
  ❌ Failed: {s['failed']}
  🚫 Rejected: {s['rejected']}
  💰 Tokens: {s['tokens_used']:,}
  ⏱️  ETA: {s['eta_minutes']:.0f} min
        """
```

## 运行模式

### Batch Mode (初次全量处理)
```bash
python3 scripts/llm-ingest-v2.py \
    --init-queue \           # 初始化队列，扫描所有未处理文件
    --model deepseek/deepseek-chat \
    --concurrency 3 \        # 并发数
    --rpm 10 \               # 每分钟请求数限制
    --batch-size 50 \        # 每50篇保存一次进度
    --progress-interval 60   # 每60秒输出进度
```

### Resume Mode (断点续传)
```bash
python3 scripts/llm-ingest-v2.py \
    --resume \               # 从上次中断处继续
    --model deepseek/deepseek-chat
```

### Incremental Mode (日常增量)
```bash
python3 scripts/llm-ingest-v2.py \
    --incremental \          # 只处理新增文件
    --model deepseek/deepseek-chat
```

## 容错设计

1. **每个 task 独立** — 一个失败不影响其他
2. **自动重试** — 最多3次，指数退避 (30s, 120s, 300s)
3. **结果校验** — JSON schema 验证 + 逻辑检查
4. **进度持久化** — SQLite 存储，任何时刻可中断恢复
5. **超时控制** — 单篇文章 LLM 调用超时 60s
6. **日志完整** — 每个 task 的错误信息都记录

## 预估资源

- 1042 篇文章
- 平均每篇: input ~1500 tokens, output ~800 tokens
- 总计: ~1.5M input + ~0.8M output = ~2.3M tokens
- DeepSeek-Chat 价格: ~$0.15/M input, ~$0.60/M output ≈ **$0.70 总计**
- 并发3 + RPM 10 → 预计 ~2-3小时完成

## 文件结构

```
scripts/
├── llm-ingest-v2.py        # 主入口
├── ingest/
│   ├── __init__.py
│   ├── queue.py            # SQLite task queue
│   ├── worker.py           # LLM 调用 worker
│   ├── rate_limiter.py     # 速率限制
│   ├── validator.py        # 结果校验
│   ├── wiki_writer.py      # Wiki 页面写入
│   ├── progress.py         # 进度追踪
│   └── prompts.py          # Prompt 模板
└── ...
data/
├── task-queue.db           # SQLite 任务队列
└── ingest-report.json      # 最终报告
```
