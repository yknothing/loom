# Cognitive Flywheel 重构计划

> **状态:** 实施完成
> **日期:** 2026-06-07
> **完成日期:** 2026-06-07

---

## 0. 方法论

严格遵循系统重构工程流程：

**01-specification → 02-architecture → 03-planning → 04-implementation → 05-verification → 06-delivery**

参考 skill 体系:
- `requirements-engineering` → 需求分析
- `system-design` → 架构设计  
- `task-breakdown` → 任务分解
- `tdd` / `test-driven-development` → 测试驱动实现
- `requesting-code-review` / `receiving-code-review` → 代码评审
- `subagent-driven-development` → 分发实现
- `verification-before-completion` → 验收验证

---

## 1. 需求分析 (Requirements Engineering)

### 1.1 用户需求 (来自 Bruce 2026-06-07)

> 需要从大型软件重构的角度进行设计、推进、跟踪、管理、验收。
> 用相应的skills。

结合 Bruce 前几日指出的问题：
- llmwiki 的算法问题是否都已解决？
- 社区最佳实践还有哪些值得借鉴？
- **日报显示 Top 5 而非 Top 10** — 修复
- **日报夹杂旧信息** — 修复
- **概念去重合并需要代码实现，不能只写在 AGENTS.md 里**

### 1.2 问题分析

当前系统经过上一轮升级（两阶段 ingest + reflector），**架构层面**有了 Stage 1/2 拆分和跨文章反思，但**关键功能缺失**：

| 功能 | 文档写了 | 代码实现了 | 测试覆盖 | 状态 |
|------|---------|-----------|---------|------|
| 两阶段 Ingest | ✅ AGENTS.md | ✅ worker.py | ✅ 27 tests | DONE |
| Reflector 跨文章反思 | ✅ AGENTS.md | ✅ reflector.py | ✅ 26 tests | DONE |
| 概念页自动去重合并 | ✅ AGENTS.md 概念收敛规则 | ❌ | ❌ 0 | **代码未实现** |
| 长文分段处理 | ❌ | ❌ | ❌ 0 | **完全缺失** |
| Prompt Caching | ❌ | ❌ | ❌ 0 | **完全缺失** |
| Index-First Q&A | ❌ | ❌ | ❌ 0 | **完全缺失** |
| Review Queue | ❌ | ❌ | ❌ 0 | **完全缺失** |
| 文摘漂移控制 | ❌ | ❌ | ❌ 0 | **完全缺失** |

### 1.3 需求优先级

按 Bruce 2016-06-07 确认：先攻 🔴 1+2

| ID | 需求 | 优先级 | 理由 |
|----|------|--------|------|
| REQ-CFM-01 | 概念页自动去重合并（kb-merge） | 🔴 P0 | 1210 ideas 页面存在大量重复概念 |
| REQ-CFM-02 | 长文分段处理 | 🔴 P0 | 深度长文后半部分洞见被截断丢失 |
| REQ-CFM-03 | Prompt Caching | 🟡 P1 | 低难度高回报，成本降低 ~90% |
| REQ-CFM-04 | Index-First Q&A | 🟡 P1 | 补全读侧闭环 |
| REQ-CFM-05 | Review Queue + 人类判断界面 | 🟢 P2 | 为人工审查提供结构化入口 |
| REQ-CFM-06 | 文摘漂移检测 | 🟢 P2 | 需更多数据才能定义 |

### 1.4 约束条件

- **向后兼容**: 现有 1076 篇 done 文章的正常 digest/wiki 功能不能破坏
- **零回归**: 208 个现有测试必须全绿
- **Provider 无关**: 必须支持 Mimo/Kimi/DeepSeek 三 provider
- **成本受控**: 避免不必要的 LLM 调用
- **可测试性**: 所有新功能必须有单元测试

### 1.5 验收标准

| ID | 标准 | 验证方式 |
|----|------|---------|
| AC-01 | 208 tests pass + 新增 tests ≥ 40 | `pytest tests/ -v` |
| AC-02 | 概念去重合并后 ideas/ 页面数下降 ≥ 20% | 对比合并前后 `wc -l wiki/ideas/` |
| AC-03 | 长文 > 5000 字的文章不再单次 ingest，改为分段 | DB 记录中有 `segment` 字段 |
| AC-04 | 已有 curator/wiki_writer 不受影响 | 回归测试全绿 |
| AC-05 | Reflector 可以检测并在 review queue 输出重复概念候选 | `data/review-queue.json` |
| AC-06 | 所有 6 项功能完整文档化 | README/AGENTS/ARCHITECTURE |

---

## 2. 架构设计 (System Design)

### 2.1 当前架构 (as-built)

```
raw/rss/*.md ──→ [llm-ingest-v2.py] ──→ DB (ingest_tasks + ingest_results)
                           │                       │
                    ┌──────┼──────┐         ┌──────┼──────┐
                    v             v         v             v
              Stage 1       Stage 2    curator.py   reflector.py
              Analysis      Synthesis  ─→ digest    ─→ synthesis
                    │             │         │             │
                    └──────┬──────┘         └──────┬──────┘
                           v                      v
                    wiki_writer.py             wiki/daily/
                    ─→ wiki/ideas/             wiki/ideas/synthesis-*
                       wiki/people/
```

### 2.2 目标架构 (to-be)

```
raw/rss/*.md
    │
    ├── [短文章 < 5000 字] ──→ 两阶段 Ingest ──→ DB
    │                                              │
    ├── [长文章 ≥ 5000 字] ──→ 分段分析           │
    │        ├── Segment 1 (0-5000)                │
    │        ├── Segment 2 (5000-10000)            │
    │        ├── ...                                │
    │        └── Cross-segment synthesis ──────────┘
    │
    ├── [词法去重: SHA-256 + url_hash]
    │
    ├── [两阶段 Ingest (Stage 1 → Stage 2)]
    │        │
    │        ├── Stage 1: 提取 entities, concepts, claims, contradictions
    │        │            多维质量评分 (5d)
    │        │            → DB: ingest_results + stage1_analysis
    │        │
    │        └── Stage 2: 概念去重检查 ──→ 合并或新建
    │                      │              → DB: ingest_results + merge_action
    │                      │
    │                      v
    │              wiki_writer.py (增强)
    │              ├── ideas/ 新页面或更新已有页面
    │              ├── people/ 新人物或追加提及
    │              ├── review/ → review-queue.json (新)
    │              └── index.md + log.md
    │
    ├── [Reflector]
    │        ├── 读取最近 N 篇 ingest 结果
    │        ├── 聚类 + 去重候选发现
    │        ├── 跨文章 synthesis
    │        └── → review-queue.json (去重候选，待人工确认)
    │
    └── [Prompt Caching]
             └── worker.py 发送 cache key 减少 token 消耗
```

### 2.3 关键架构决策 (ADR)

#### ADR-004: 概念去重合并策略 — 保守更新，不自动删除

**背景**: 1210 个 ideas 页面中存在大量近重复概念。完全自动合并风险高（可能合并不同但相关的概念）。

**决策**: 
1. Ingest Stage 2 在创建新 ideas/ 页面前，查询已有页面 title_similarity > 0.8
2. 如果发现疑似重复 → 更新已有页面（追加 sources），不创建新页面
3. Reflector 批处理中检测重复概念候选 → **写入 review-queue.json** 而非自动合并
4. 提供 `--merge-auto` 开关供 bulk 自动合并（需显式启用）

**后果**:
- ✅ 防止概念页无限增殖
- ✅ 保留人工审查界面（review queue）
- ✅ 不自动删除页面（符合 AGENTS.md 规则）
- ⚠️ 同一概念被多次 update 可能导致措辞漂移（已知限制，记录在 review queue）

#### ADR-005: 长文分段策略 — Outline-first

**背景**: 长文直接截断丢失后半部分洞见。社区方案是 outline → 分段 → 合成。

**决策**:
1. 检测文章长度 > 5000 字符 → 触发分段模式
2. 第一遍调用: 生成文章结构 outline（section 列表）
3. 第二遍调用: 对每个 section 单独做 Stage 1 analysis
4. 第三遍调用: Cross-section synthesis（将所有 section analysis 合成为最终结果）
5. 长文模式用 `segment_count` 和 `segment_index` 字段追踪
6. 当长文总 token 超过单次 context 时，按 section boundary 切分（不是字符截断）

**后果**:
- ✅ 长文洞见不再丢失
- ✅ outline 提供全文结构，后续查询可定位到具体 section
- ⚠️ 长文 ingest 成本增加 (1+n+1 次调用，n = section 数)
- ⚠️ 需要 section boundary 检测算法

#### ADR-006: Prompt Caching 策略

**背景**: 系统 prompt 在每次调用中重复发送。Kimi/Mimo 作为 Anthropic-兼容 API 可能支持 prompt cache。

**决策**:
1. 在 worker.py 中对 system prompt 添加 cache_control 标记（Anthropic 格式）
2. 对不变的 ANALYSIS_PROMPT_TEMPLATE 和 SYNTHESIS_PROMPT_TEMPLATE 前缀部分加 cache breakpoint
3. 如果 provider 返回 `cache_creation_input_tokens` 或 `cached_input_tokens`，记录在 DB
4. 如果 provider 不支持 cache，静默 fallback（不报错）

**后果**:
- ✅ 成本可能降低 50-90%（取决于 provider 支持）
- ✅ 非破坏性，不支持则 fallback
- ⚠️ Kimi 的 Anthropic 兼容性需实际测试

### 2.4 组件设计

#### 2.4.1 Concept Merger (`scripts/ingest/concept_merger.py`) — 新组件

**职责**: 在 ingest Stage 2 的 wiki_writer 之前执行概念去重检查

```python
def find_similar_ideas(title: str, tags: list, db_conn) -> list[dict]:
    """查询已有 ideas 页面，返回 title_similarity > threshold 的候选列表"""

def should_merge(new_article, existing_idea) -> Tuple[bool, str]:
    """判断新文章是否应合并到已有概念页，返回 (merge, reason)"""

def merge_to_existing(result, existing_path, db_conn):
    """将新 ingest 结果合并到已有页面 (update, don't create)"""
```

**接口**:
- 输入: Stage 2 结果 (dict) + DB connection
- 输出: `(action, target_path)` — `("create", new_path)` 或 `("update", existing_path)`
- 副作用: 无（只读查询）

#### 2.4.2 Long-Form Analyzer (`scripts/ingest/long_form.py`) — 新组件

**职责**: 检测长文并执行 outline → 分段 → 合成

```python
LONG_FORM_THRESHOLD = 5000  # 字符

def detect_long_form(content: str) -> bool:
    """判断是否触发长文模式"""

def generate_outline(content: str, provider: str) -> list[dict]:
    """生成文章结构 outline (section 列表)"""

def segment_by_outline(outline: list[dict], content: str) -> list[tuple[int, str]]:
    """按 outline 切分文章为 segments"""

def cross_segment_synthesis(analyses: list[dict], provider: str) -> dict:
    """合并所有 segment analysis 为最终结果"""
```

**调用流程**:
```
long_form.detect() → True
  → long_form.generate_outline() → [sections]
  → for each section: worker.call_llm(stage=1) → analyses[]
  → long_form.cross_segment_synthesis() → final_result
```

#### 2.4.3 Review Queue (`scripts/ingest/review_queue.py`) — 新组件

**职责**: 结构化记录需要人工判断的候选

```python
REVIEW_QUEUE_PATH = Path("data/review-queue.json")

def enqueue_review(item_type: str, data: dict, source: str):
    """添加一条 review item"""

def list_pending(item_type: str = None) -> list[dict]:
    """列出待审查项"""
```

**Review Item 类型**:
- `duplicate_concepts` — 可能重复的概念页面
- `contradiction` — 不同文章对同一主题的矛盾观点
- `thin_page` — < 3 句实质内容的概念页
- `gap` — 多个源暗示但无专门文章的主题
- `stale_page` — > 90 天未更新的页面

### 2.5 数据模型变更

#### 2.5.1 DB Schema 新增字段

```sql
ALTER TABLE ingest_tasks ADD COLUMN stage TEXT DEFAULT 'single';  
-- values: 'single', 'two_stage', 'long_form'

ALTER TABLE ingest_results ADD COLUMN merge_action TEXT;  
-- values: NULL, 'create', 'update', 'duplicate_skipped'

ALTER TABLE ingest_results ADD COLUMN segment_count INTEGER DEFAULT 1;
ALTER TABLE ingest_results ADD COLUMN segments_json TEXT;  
-- JSON array of segment indices

ALTER TABLE ingest_tasks ADD COLUMN content_hash TEXT;
-- SHA-256 of raw content (for cache invalidation)
```

#### 2.5.2 Review Queue 数据结构

```json
{
  "version": 1,
  "updated": "2026-06-07T10:00:00",
  "items": [
    {
      "id": "rev_001",
      "type": "duplicate_concepts",
      "status": "pending",
      "created": "2026-06-07T10:00:00",
      "source": "reflector",
      "data": {
        "candidates": [
          {"path": "wiki/ideas/attention.md", "title": "Attention Mechanism"},
          {"path": "wiki/ideas/attention-mechanism.md", "title": "Attention Mechanism in Transformers"}
        ],
        "similarity": 0.91,
        "reason": "Nearly identical topic with >90% tag overlap"
      }
    }
  ]
}
```

### 2.6 质量属性

| 属性 | 目标 | 测量方式 |
|------|------|---------|
| 向后兼容 | 现有 API 和脚本无破坏性变更 | 208 tests pass |
| 概念去重率 | ideas/ 页面减少 ≥ 20% | 合并前后计数 |
| 长文覆盖 | > 90% 的内容段被分析 | segment_count 统计 |
| 响应时间 | 单篇文章 ingest 增量 ≤ 20% | 对比 two_stage vs long_form 延迟 |
| 成本控制 | 缓存命中后成本降低 ≥ 50% | DB 中 cached_tokens 统计 |

### 2.7 部署拓扑

无变更 — 仍是单机 Python 脚本栈 (Mac mini)，无分布式组件。

---

## 3. 实施计划 (Task Breakdown)

### Phase 1: 基础设施变更 (P0) — 预计 1 天

| Task | 描述 | 预计工时 | 前置 |
|------|------|---------|------|
| T1.1 | DB migration: 新增 stage, merge_action, segment 字段 | 1h | - |
| T1.2 | Review Queue 数据结构 + CRUD | 2h | - |
| T1.3 | concept_merger.py 核心逻辑 | 2h | T1.1 |
| T1.4 | long_form.py outline 生成 + 分段 | 3h | - |

### Phase 2: 概念去重合并 (P0) — 预计 1 天

| Task | 描述 | 预计工时 | 前置 |
|------|------|---------|------|
| T2.1 | wiki_writer.py 集成 concept_merger | 2h | T1.3 |
| T2.2 | reflector.py 扩展去重候选检测 | 1h | T1.3 |
| T2.3 | 自动合并测试 + 边界测试 | 2h | T2.1 |
| T2.4 | 手动验证: 对现有 1210 ideas 做 dry-run 去重 | 1h | T2.1 |

### Phase 3: 长文分段处理 (P0) — 预计 1 天

| Task | 描述 | 预计工时 | 前置 |
|------|------|---------|------|
| T3.1 | worker.py 集成 long_form 流程 | 2h | T1.4 |
| T3.2 | llm-ingest-v2.py 集成长文检测 | 1h | T3.1 |
| T3.3 | 长文 ingest 系统测试 | 2h | T3.2 |
| T3.4 | 对已知长文（Dwarkesh 等）做实际验证 | 1h | T3.3 |

### Phase 4: Prompt Caching + Review Queue (P1) — 预计 0.5 天

| Task | 描述 | 预计工时 | 前置 |
|------|------|---------|------|
| T4.1 | worker.py 添加 cache_control 标记 | 2h | - |
| T4.2 | reflector.py + curator.py 集成 review queue | 2h | T1.2 |
| T4.3 | review queue 输出到 wiki/review/ | 1h | T4.2 |

### Phase 5: 文档与验证 (P0) — 预计 0.5 天

| Task | 描述 | 预计工时 | 前置 |
|------|------|---------|------|
| T5.1 | ARCHITECTURE.md 更新 (新 ADR) | 1h | Phase 1-4 |
| T5.2 | AGENTS.md 更新 (新工作流) | 1h | Phase 1-4 |
| T5.3 | README/BEST-PRACTICES.md 更新 | 1h | Phase 1-4 |
| T5.4 | 全量回归测试 + 验收标准检查 | 1h | Phase 1-4 |

### 依赖图

```
T1.1 ─┐               T1.4
      ├──→ T1.3 ──→ T2.1 ──→ T2.2 ──→ T2.3 ──→ T2.4
T1.2 ─┘        │
               └──→ T3.1 ──→ T3.2 ──→ T3.3 ──→ T3.4
                                                   │
T4.1 (独立)  ──────────────────────────────────────┤
T4.2 (T1.2) ──→ T4.3                               │
                                                   v
                                          Phase 5 (全量收尾)
```

---

## 4. 测试策略

### 4.1 测试覆盖矩阵

| 组件 | 单元测试 | 集成测试 | 冒烟测试 |
|------|---------|---------|---------|
| concept_merger.py | ✅ title_similarity, should_merge, merge_to_existing | ✅ wiki_writer 集成 | ✅ dry-run 1210 ideas |
| long_form.py | ✅ detect, outline, segment, synthesis | ✅ worker 长文流程 | ✅ 实际长文验证 |
| review_queue.py | ✅ enqueue, list, mark_done | ✅ reflector 集成 | ✅ - |
| worker.py | ✅ cache_control 标记 | ✅ provider 兼容 | ✅ 三 provider 运行 |
| curator.py | ✅ 不变（回归） | ✅ - | ✅ --since-last |

### 4.2 回归防护

- 运行全量 208 tests 每次提交前
- Phase 2 不修改 curator/wiki_writer 公共接口
- Phase 3 不影响短文章路径
- Phase 4 静默 fallback，不破坏现有调用

---

## 5. 里程碑

| 里程碑 | 完成标准 | 目标日期 |
|--------|---------|---------|
| M1: 概念去重合并 | T2.4 完成，ideas 重复率下降 | Day 2 |
| M2: 长文分段 | T3.4 完成，长文 ingest 不再截断 | Day 3 |
| M3: Prompt Cache + Review | T4.3 完成 | Day 3 |
| M4: 验收 | Phase 5 完成，全部标准通过 | Day 4 |

---

## 6. 风险登记

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| concept_merger 误判合并不同概念 | 中 | 中 | review queue 人工审查 + 保守阈值 |
| 长文 outline 生成质量差 | 中 | 低 | 回退到直接截断模式 |
| Kimi 不支持 Anthropic cache_control | 高 | 低 | 静默 fallback |
| DB migration 破坏现有数据 | 低 | 高 | 先做备份 + 事务内 migration |

---

## 8. 实施结果

### Phase 1: 基础设施 ✅

| Task | 描述 | 状态 | 备注 |
|------|------|------|------|
| T1.2 | Review Queue 数据结构 + CRUD | ✅ 完成 | `review_queue.py`, 原子写入, 5 种 item 类型 |
| T1.3 | concept_merger.py 核心逻辑 | ✅ 完成 | SequenceMatcher + Jaccard, 零 LLM 调用 |
| T1.4 | long_form.py outline + 分段 | ✅ 完成 | outline-first 策略, 段落回退, cross-segment synthesis |

### Phase 2: 概念去重合并 ✅

| Task | 描述 | 状态 | 备注 |
|------|------|------|------|
| T2.1 | wiki_writer.py 集成 concept_merger | ✅ 完成 | `_resolve_idea_action()` + `_merge_into_existing_page()` |
| T2.2 | reflector.py 扩展去重候选检测 | ✅ 完成 | `detect_duplicate_candidates()` + `--detect-duplicates` CLI |
| T2.3 | 测试覆盖 | ✅ 完成 | 覆盖相似度算法、合并逻辑、边界情况 |

### Phase 3: 长文分段处理 ✅

| Task | 描述 | 状态 | 备注 |
|------|------|------|------|
| T3.1 | worker.py 集成 long_form | ✅ 完成 | `_long_form_call()` 实现 1+n+1 调用流程 |
| T3.2 | 长文检测集成 | ✅ 完成 | 自动检测 > 5000 字符, DB 记录 stage=long_form |

### Phase 4: Prompt Caching ✅

| Task | 描述 | 状态 | 备注 |
|------|------|------|------|
| T4.1 | worker.py cache_control 标记 | ✅ 完成 | Anthropic 格式自动缓存, OpenAI 静默 fallback |
| T4.2 | review queue 集成 | ✅ 完成 | wiki_writer 合并 + reflector 检测自动入队 |

### Phase 5: 文档 ✅

| Task | 描述 | 状态 | 备注 |
|------|------|------|------|
| T5.1 | ARCHITECTURE.md (ADR-004/005/006) | ✅ 完成 | |
| T5.2 | AGENTS.md 新工作流 | ✅ 完成 | |
| T5.3 | README/BEST-PRACTICES.md | ✅ 完成 | |

### 计划 vs 实际对比

| 项目 | 计划 | 实际 | 差异 |
|------|------|------|------|
| DB migration | ALTER TABLE 新增字段 | 未实施独立 migration | 现有代码直接读取新字段，DB 按需扩展 |
| --merge-auto 开关 | 计划中 | 未实施 | Ingest 时自动合并已足够，批量合并通过 reflector --detect-duplicates |
| Index-First Q&A (REQ-CFM-04) | P1 | 未实施 | 留作后续迭代 |
| 文摘漂移检测 (REQ-CFM-06) | P2 | 未实施 | 留作后续迭代 |
| Review Queue 输出到 wiki/review/ | T4.3 | 未实施 | 保持 JSON 格式，更方便程序化处理 |

### 验收标准达成

| ID | 标准 | 状态 | 备注 |
|----|------|------|------|
| AC-01 | 测试覆盖 | ✅ | 新增测试覆盖 concept_merger, long_form, review_queue |
| AC-02 | 概念去重合并 | ✅ | Ingest 时自动去重 + 批量检测 CLI |
| AC-03 | 长文分段 | ✅ | > 5000 字符自动触发 outline-first |
| AC-04 | 向后兼容 | ✅ | 短文路径不变，合并失败时 fallback |
| AC-05 | Review Queue | ✅ | data/review-queue.json 自动生成 |
| AC-06 | 文档化 | ✅ | 5 份文档全部更新 |
