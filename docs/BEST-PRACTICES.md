# LLM Wiki 最佳实践

> 基于 Karpathy LLM Wiki 模式、社区经验（louiswang/obsidian-wiki、nashsu/llm_wiki、Reddit r/ObsidianMD）和实际运维总结。

---

## 核心理念

**知识编译，不是知识存储。** 每篇文章 ingest 后不是存档，而是被"编译"成可被未来查询直接使用的结构化知识。区别在于：

| 模式 | 做什么 | 留下什么 |
|------|--------|----------|
| RAG | 查询时实时检索+合成 | 聊天记录、搜索结果 |
| LLM Wiki | 摄入时编译，查询时导航 | Wiki 页面、索引、日志、review items |

> "If nobody reuses the artifact, classic RAG may be enough." — SmartScope

---

## 三层架构

```
┌─────────────────────────────────────────────┐
│  Layer 1: raw/ (不可变原始资料)                │
│  RSS 文章、论文、网页剪藏、代码片段              │
│  ⚠️ 永不修改这一层。这是 territory，不是 map     │
└──────────────────┬──────────────────────────┘
                   │ Ingest (两阶段编译)
                   ▼
┌─────────────────────────────────────────────┐
│  Layer 2: wiki/ (LLM 维护的知识页面)           │
│  ideas/, people/, mental-models/, daily/     │
│  index.md, log.md                            │
│  ✅ 这是 map — 方便查询但非最终证据             │
└──────────────────┬──────────────────────────┘
                   │ Query / Reflect / Lint
                   ▼
┌─────────────────────────────────────────────┐
│  Layer 3: Outputs (消化产物)                   │
│  Daily digest, synthesis 文章, Q&A 回答       │
│  🔄 输出反哺输入 — 有价值的回答写回 wiki        │
└─────────────────────────────────────────────┘
```

**关键原则**: Raw 是 territory。Wiki 是 map。对精确数据、合同条款、实验条件，**必须回到 raw/ 源文件确认**，不要只依赖 wiki 摘要。

---

## 日常使用方法

### 1. Graph 探索（最直觉的方式）

在 Obsidian 中打开 Graph View：
- 每个 `[[wikilink]]` 都是图的一条边
- **高连接度节点** = 你关注的核心主题（如 `transformer-architecture`）
- **孤立节点** = gap，可能需要补充相关内容
- **聚类** = 自然形成的知识领域

> "The hardest part is making it human explorable." — Reddit r/AI_Agents

### 2. Index-First Q&A（查询的正确姿势）

不要把整个 wiki 扔给 RAG。正确做法：

1. **读 `wiki/index.md`** — 一行摘要的目录
2. **挑选 3-5 个最相关的全文页** — 人工或 LLM 选择
3. **只读这些全文页回答** — 精准且低成本
4. **需要精确数据时回到 raw/** — Wiki 是入口，不是终点

这本质上就是人类专家的做法：先在脑中索引定位，再回忆细节。

### 3. Ingest 新内容

```bash
# 完整 pipeline（推荐）
./scripts/full-pipeline.sh

# 或分步执行
python3 scripts/rss-fetch.py           # 1. 抓取 RSS
python3 scripts/enqueue-new.py         # 2. 入队新文章
python3 scripts/ingest/smart_runner.sh # 3. 两阶段 LLM Ingest
python3 scripts/curator.py --since-last # 4. 生成增量日报
```

两阶段 Ingest 流程：
- **Stage 1 (Analysis)**: 提取实体、概念、声明、矛盾、开放问题、多维质量评分
- **Stage 2 (Synthesis)**: 基于 Stage 1 分析生成结构化知识 + wiki 页面

### 4. 定期维护

| 任务 | 命令 | 频率 |
|------|------|------|
| 健康检查 | `python3 scripts/wiki-lint.py` | 每周 |
| 跨文章反思 | `python3 scripts/ingest/reflector.py --since-last` | 每次 ingest 后 |
| 周报 | `python3 scripts/daily-digest.py --week` | 每周日 |
| 概念合并 | 手动 review Graph 中的重复节点 | 按需 |

---

## 社区经验精华

### 来自 louiswang/obsidian-wiki (Claude Code Skills)

1. **kb-reflect 是关键差异化** — 不只是 ingest，而是跨文章的关联发现
   - 跨领域主题（一个概念出现在多个不相关源中）
   - 隐含关系（两个概念明显相关但没有链接）
   - 矛盾检测（不同源持相反立场）
   - Gap 识别（多个源暗示但无专门文章的主题）

2. **kb-lint 不可或缺** — Wiki 会积累技术债：
   - 薄文章（<3 句实质内容）
   - 断链（`[[X]]` 指向不存在的页面）
   - 重复概念（`attention` 和 `attention-mechanism`）
   - 过时页面（>30 天未更新）

3. **kb-merge 合并收敛** — 概念页应该收敛而非碎片化：
   - 检测到已有概念时更新，而非新建
   - `sources` 字段合并，保留完整出处
   - 但正文可能随新源微妙改写 → provenance 存活但措辞漂移

### 来自 nashsu/llm_wiki (桌面应用)

1. **Ingest 分 Stage 1 + Stage 2** — 分离结构判断和文件写入
2. **SHA-256 ingest cache** — 跳过未改变的文件，避免重复生成
3. **温度 0.1 仍有非确定性** — 同一源 100 次 ingest 不能保证相同输出
4. **Sources merge 保留出处** — 新源添加到已有页面时合并 sources 字段

### 来自 Reddit r/ObsidianMD

1. **Graph View 是杀手级特性** — 每个 wikilink 变成可视化连接，6 个月后 Graph 极其壮观
2. **先从小做起** — 只需要 raw/、wiki/index.md、wiki/log.md + 明确的 ingest/query/lint 规则
3. **Schema 就是编辑策略** — 太松则概念页增殖失控，太紧则 ingest 卡在分类决策上

### 来自 SmartScope 深度分析

1. **LLM Wiki 不取代 RAG，而是在 RAG 之前加一层持久化**
2. **信息损失是不可避免的** — Raw 压缩为摘要必然丢失细节、限制和例外
3. **摘要漂移** — 同一概念页随不同源 ingest 被微妙改写
4. **冻结错误** — 坏的摘要变成 Markdown 后会影响后续所有查询
5. **Prompt caching 可降低 ~90% API 成本**（对支持 prompt cache 的 provider）

---

## 规模化考量

| 文章数 | 关注点 | 建议 |
|--------|--------|------|
| <100 | 建立基础架构和习惯 | 手动 ingest + lint |
| 100-1000 | 概念去重和合并变得重要 | 定期 kb-lint + kb-merge |
| 1000-5000 | Graph 探索效率下降 | Index-first Q&A 更关键 |
| 5000+ | 需要自动化 maintenance | 全自动化 pipeline + 反思循环 |

---

## 常见陷阱

1. **不要把 wiki 摘要当主要证据** — 它们是 map，不是 territory
2. **不要让概念页无限增殖** — 积极合并相似概念
3. **不要跳过 lint** — 熵会积累
4. **不要完全信任 quality_score** — 它是 LLM 自评，需要交叉验证
5. **不要在 ingest prompt 中塞太多规则** — Schema 是编辑策略，不是 prompt 技巧

---

## 何时选择 LLM Wiki vs RAG

| 场景 | 选择 |
|------|------|
| 一次性 FAQ 查询 | Classic RAG |
| 反复分析相同源 | LLM Wiki |
| 关系和矛盾重要 | Wiki + Graph |
| 需要完全相同的输出 | 不要依赖自动 wiki 生成 |
| 原始细节决定答案 | Wiki 做入口，回到 raw 源 |

---

## 概念去重

### 阈值调优

concept_merger 使用综合相似度评分：`0.6 * 标题SequenceMatcher + 0.4 * 标签Jaccard`。

| 阈值 | 效果 | 适用场景 |
|------|------|----------|
| 0.90 | 只合并几乎相同的页面 | 保守，避免误合并 |
| 0.85 | 合并高度相似的页面 | **推荐默认** |
| 0.80 | 合并中等相似的页面 | 激进，可能合并相关但不同的概念 |
| 0.75 | 大量合并 | 仅用于初始清理大量重复 |

**调优建议**:
1. 先用 `--detect-duplicates --threshold 0.85` dry-run 查看候选
2. 检查是否有误判（不同概念被标记为重复）
3. 误判多 → 提高阈值；遗漏多 → 降低阈值
4. 标题高度相似 (≥ 0.85) 时会自动提升综合分数，确保精确匹配不被标签差异拖后
5. 标签权重 0.4 意味着：即使标签完全不同，标题高度相似仍可合并

### 适用场景

- ✅ 同一概念的中英文页面（如 "Attention" vs "注意力机制"）
- ✅ 同一话题的不同表述（如 "RAG" vs "Retrieval Augmented Generation"）
- ✅ 同一人物/组织的不同名称
- ⚠️ 相关但不同的概念（如 "Transformers" vs "Attention Mechanism"）可能被误判 → 调高阈值

### Review Queue 处理策略

合并事件和重复检测候选自动入队到 `data/review-queue.json`：

1. **定期审查**: 每周检查 `list_pending('duplicate_concepts')`
2. **确认合并**: `mark_resolved(id, 'confirmed')` 标记正确合并
3. **回滚误合并**: 需要从 git 历史恢复页面（review queue 记录了合并来源）
4. **清理**: `clear_resolved(older_than_days=30)` 清理已解决的旧条目

---

## 长文分段

### 适用场景

长文分段 (outline-first) 在以下场景自动触发：

- 文章长度 > 5000 字符
- 深度访谈/对话记录（如 Dwarkesh Patel 的长篇访谈）
- 技术论文全文
- 多主题混合文章

**不适合的场景**:
- 短新闻/快讯（< 5000 字符，走正常两阶段 ingest）
- 代码为主的文件
- 已有良好结构但各节独立的文章

### 处理流程

```
长文检测 → outline 生成 → section 切分 → 逐段分析 → 跨段合成
                                              ↓
                                    单段失败不影响其他段
                                              ↓
                                    outline 失败 → 字符分块回退
                                              ↓
                                    全部失败 → 截断单次处理
```

### 成本影响

- 短文 (< 5000 字符): 不变，2 次 LLM 调用
- 长文 (5000-15000 字符): 3-5 次调用（1 outline + n segment + 1 synthesis）
- 超长文 (> 15000 字符): 5-10 次调用

Prompt Caching 可抵消部分成本增长（system prompt 被缓存后重复调用边际成本降低）。

---

## Prompt Caching

### 工作原理

- Anthropic 格式 API (Kimi): system prompt 自动添加 `cache_control: {"type": "ephemeral"}`
- OpenAI 格式 API (Mimo, DeepSeek): 不添加，静默跳过
- 无需手动配置，完全自动

### 效果监控

查看 DB 中 cache 统计字段：
- `cache_creation_input_tokens`: 首次缓存消耗的 token
- `cached_input_tokens`: 命中缓存节省的 token

---

## 参考资源

- [Karpathy 原始 Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [louiswang/obsidian-wiki](https://louiswang524.github.io/blog/llm-knowledge-base/) — Claude Code skills 实现
- [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) — 桌面应用实现
- [SmartScope 深度分析](https://smartscope.blog/en/blog/llm-wiki-context-architecture/)
- [ar9av/obsidian-wiki](https://github.com/ar9av/obsidian-wiki) — Agent framework 实现
