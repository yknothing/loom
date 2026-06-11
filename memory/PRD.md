# Loom — 生产级 LLM 知识编译平台 PRD

## 原始问题陈述
> 发挥你的能力上限、资源上限，将这个demo级别的小项目完善、提升为卓越的生产级产品。除了上述完善、提升，也要增加一些最需要的功能，并且要具备极佳的可扩展性、可维护性、易用性等非功能属性。

用户选择：1) 先工程化加固(b)后 Web 控制台(a)；2) Provider 双支持(Emergent 通用 Key + 自有 Key)；3) 新功能全要(搜索/监控/图谱/在线提交)；4) 小团队多用户。

## 用户画像
- 小团队知识工作者：通过 Web 控制台浏览/搜索知识库、提交文章
- 管理员：管理 Pipeline、Provider、RSS 源、审查队列

## 架构
- **Loom 库** `/app/scripts/ingest`：原 CLI pipeline，新增 `providers.py`（Provider 注册表 + 三级密钥解析：env → loom.yml → legacy auth-profiles）与 `emergent_client.py`（通用 Key 同步客户端，OpenAI/Claude/Gemini，`<provider>/<model>` 记法）
- **后端** `/app/backend`：FastAPI。`loom_bridge.py` 服务层（asyncio.to_thread 调用同步 loom 库）；routers: auth(JWT httpOnly cookie + 暴力破解锁定 + admin 种子) / pipeline(仪表盘、任务、后台 runner) / wiki(树、页面、搜索、图谱、lint) / content(提交、审查、RSS 源、设置)
- **前端** `/app/frontend`：React + Tailwind，Swiss 高对比设计(Klein 蓝 #002FA7，IBM Plex Sans/Noto Sans SC)，全中文 UI。9 页面：登录/总览/任务队列/知识库/搜索/图谱/提交/审查/设置
- **数据**：用户/设置在 MongoDB(loom_production)；任务队列 SQLite /app/data/task-queue.db；wiki 为 /app/wiki Markdown 文件；config/loom.yml 中心化配置

## 已完成（2026-06-11）
- [x] Provider 抽象层 + Emergent 通用 Key 接入（默认 emergent / openai/gpt-5.1，gpt-5.4 代理不可用）
- [x] 预存在 bug 修复：llm-ingest.py 缺失 ROOT/RAW_DIR_BASE（4 个失败测试）、review_queue cwd 相对路径、测试隔离泄漏（conftest autouse fixture）、contradiction-detect CLAIM_SECTIONS 不含 pipeline 实际写出的"核心洞察"章节
- [x] JWT 多用户认证（注册/登录/登出/me/refresh、锁定、admin 种子）
- [x] Web 控制台全功能：Pipeline 实时监控(4s 轮询+事件流)、任务管理(筛选/搜索/重试/入队)、Wiki 浏览(wikilink 跳转+反向链接)、全文搜索(wiki/raw/all)、知识图谱(force-graph + 详情面板)、URL/文本提交(自动抓取正文+去重+优先编译)、审查队列、设置(Provider/模型/two-stage/RSS 源管理+抓取)
- [x] 测试：库 346 passed；测试代理后端 27/27、前端全流程通过
- [x] 种子数据：4 篇 raw 文章 + 7 个 wiki 页面（7 节点 6 边图谱）

## 已知阻塞
- **EMERGENT_LLM_KEY 预算耗尽**（max $0.001）：LLM 编译调用返回 Budget exceeded（已验证优雅降级：任务标记 failed、事件流显示错误）。用户充值后在任务队列点"重试全部失败"+ 运行 Pipeline 即可

## Backlog
- P0：用户充值后 E2E 验证 LLM 编译（4 个 pending/failed 种子任务）
- P1：Pipeline 定时调度（cron 替代 → 后端 APScheduler）；Wiki 页面在线编辑；任务详情页（查看 LLM 原始输出/分段）
- P2：Reflector/Curator/Digest 控制台入口；多 worker 时 Pipeline STATE 移入 DB；通知（编译完成/矛盾发现）；精确 token 计费（emergent 客户端目前为估算值）

## 凭据
见 /app/memory/test_credentials.md（admin@loom.dev / LoomAdmin2026!）
