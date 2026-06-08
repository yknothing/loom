# Cognitive Flywheel — 项目状态与经验记录

> 最后更新: 2026-06-08
> 本文件跟踪项目真实状态、已知问题、经验教训。

## 当前状态

### 数据
| 指标 | 值 |
|------|---|
| Raw 文章总数 | 1093 |
| 已 Ingest | 1093 (done=1093, pending=0, failed=0) |
| Ingest Stage 分布 | single: 1088, two_stage: 5, long_form: 0 |
| RSS 源 | 38 feeds |
| 代码行数 | ~12,800 行 (scripts/ + tests/) |
| 测试 | 346 passed |

### 流程状态
| 功能 | 状态 | 说明 |
|------|------|------|
| RSS 抓取 | ✅ 稳定 | rss-fetch.py，30+ 源，URL hash 去重 |
| Single-shot Ingest | ✅ 稳定 | 老算法，1088 篇全部用此模式完成 |
| Two-stage Ingest | ✅ 已验证 | 默认模式，5 篇端到端验证通过 |
| Long-form Ingest | ❌ 不可用 | Mimo SGP 对多请求长连接不稳定，outline 反复 SSL disconnect |
| Prompt Caching | ✅ 已修复 | 仅对 supports_cache_control=True 的 provider 启用 |
| Concept Merger | ✅ 稳定 | SequenceMatcher 去重，阈值 0.8 |
| Wiki Writer | ✅ 稳定 | 创建/合并 ideas + people 页面 |
| Review Queue | ✅ 稳定 | JSON 文件存储，线程安全 |
| Wiki Lint | ✅ 稳定 | 断裂链接、孤立页面、frontmatter 检查 |
| Daily Digest | ✅ 稳定 | 周摘要生成 |
| Curator 精选 | ✅ 稳定 | quality_score + 日期 + 去重 |

### Provider 状态
| Provider | API 格式 | 可用性 | 注意事项 |
|----------|----------|--------|----------|
| Mimo (mimo-v2.5-pro) | OpenAI | 间歇不稳定 | ⛔ 必须走系统代理，max_tokens ≤ 16384 |
| Kimi (kimi-for-coding) | Anthropic | 有订阅 | ⛔ 不支持 cache_control |
| DeepSeek | OpenAI | 后付费 | 仅逃生通道 |

## 已知 Bug 与问题

### BUG-001: Long-form 在 Mimo 上不可用
- **症状**: `_long_form_call()` 的 outline 请求反复 `RemoteDisconnected`
- **根因**: Mimo SGP 对多请求长连接不稳定（单次请求 OK，但 outline→segments→synthesis 的连续请求容易断）
- **临时方案**: LONG_FORM_THRESHOLD 提高至 50000，大部分文章走 two-stage
- **影响**: 110 篇 >50K 的超长文章无法用 long-form 处理
- **状态**: OPEN

### BUG-002: 1088 篇旧文章使用 single-shot 而非 two-stage
- **症状**: Stage 分布 single=1088, two_stage=5
- **根因**: 之前 ingest 时 max_tokens=131072 导致 long-form 全部 fallback 到 single，且 `llm-ingest-v2.py` 未传 `two_stage=True`
- **影响**: 旧文章的分析质量低于 two-stage
- **状态**: OPEN — 待决定是否全量重跑

## 经验记录

### LESSON-001: Mimo 硬性约束（犯过 3 次）
1. **必须走系统代理**（https_proxy env）— 绝对不能用 ProxyHandler({}) 或 NO_PROXY=* 绕过
2. **max_tokens 上限 16384** — 超过会被 SSL disconnect
3. **遇到 Mimo 连接问题，先检查代理和 max_tokens，不要归咎于"集群不稳定"**
4. **证据**: 2026-06-08 直接对比测试：ProxyHandler({}) → Connection Reset，走代理 → 2.5s 成功

### LESSON-002: 新功能必须有端到端验收
- **事件**: long-form 和 two-stage 代码写完后，跑了 1091 篇文章全没生效
- **原因**: 
  1. 没有检查 stage 分布，只看 done 数量
  2. long-form 静默 fallback 到 single 没有任何告警
  3. two-stage 调用入口没接上
- **规则**: 跑完后必须检查 stage 分布作为验收标准

### LESSON-003: Memory 记录只写结论不写依据 → 错误结论无法被推翻
- **事件**: 6月4日排查 Mimo 连接问题，结论写的是"集群间歇性宕机"，实际是代理配置错误
- **后果**: 错误的 memory 导致后续 2 次犯同样错误
- **规则**: 
  1. 排查结论必须附带证据和推理过程
  2. 发现旧记录有误时必须回去修正原文
  3. 对 memory 保持审慎，写入前自问证据链

### LESSON-004: 不要归咎于外部服务不稳定
- **事件**: Mimo 连接问题连续 3 次归因于"SGP 集群不稳定"，但每次根因都是代码（ProxyHandler、max_tokens）
- **规则**: 先排除本地代码问题（代理配置、参数值），再考虑服务端

## 架构决策记录 (ADR)

### ADR-007: Two-stage 为默认 Ingest 模式
- **日期**: 2026-06-08
- **背景**: Single-shot 质量有限，long-form 在 Mimo 上不稳定
- **决策**: 默认使用 two-stage（compile+reflect），`--single` 回退
- **效果**: 
  - Token 消耗: ~3500+5500 (vs single ~3000+2200)
  - 耗时: ~80-100s (vs single ~35-40s)
  - 输出质量: 洞察更深入，摘要更详细
- **状态**: ✅ 已验证

### ADR-008: Long-form 阈值提高至 50K
- **日期**: 2026-06-08
- **背景**: Mimo SGP 对多请求长连接不稳定，5K-50K 文章走 long-form 反复失败
- **决策**: LONG_FORM_THRESHOLD 从 5000 提高至 50000
- **效果**: 只有 110 篇 >50K 文章会触发 long-form（目前这些文章走 two-stage single-shot）
- **风险**: 超长文章可能丢失后半部分洞见
- **状态**: 临时方案，需要稳定的 provider 才能降低阈值

### ADR-009: Provider 级别的 cache_control 控制
- **日期**: 2026-06-08
- **背景**: Kimi 使用 Anthropic API 格式但不支持 cache_control，发送后导致 API 挂死
- **决策**: 只有 `supports_cache_control: True` 的 provider 才发缓存控制头
- **状态**: ✅ 已修复

## 待办事项

### 短期
- [ ] 决定 1088 篇旧文章是否全量重跑 two-stage
- [ ] 为 long-form 找到稳定方案（换 provider 或优化请求策略）
- [ ] RSS 抓取 cron 自动入队新文章

### 中期
- [ ] Curator 精选流程自动化
- [ ] Reflector 跨文章综合分析
- [ ] Wiki 质量评分体系

### 长期
- [ ] Web UI
- [ ] 多用户支持
- [ ] 搜索集成 (qmd)
