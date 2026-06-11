import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  CheckCircle2, Clock3, XCircle, Coins, BookOpen, FileText,
  Play, Square, ShieldAlert,
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from "recharts";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import {
  Button, Card, Input, PageHeader, Spinner, StatCard, StatusBadge, Badge,
} from "../components/ui";

const CAT_COLORS = ["#002FA7", "#16A34A", "#D97706", "#DC2626", "#0891B2", "#7C3AED", "#71717A", "#0D9488", "#BE185D"];

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [maxTasks, setMaxTasks] = useState(10);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/dashboard");
      setData(data);
    } catch (e) {
      /* silent */
    }
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(load, 4000);
    return () => clearInterval(timer.current);
  }, [load]);

  if (!data)
    return (
      <div className="flex justify-center py-32">
        <Spinner className="w-8 h-8" />
      </div>
    );

  const { queue, wiki, recent_results, daily_activity, categories, review_pending, pipeline } = data;

  const runPipeline = async () => {
    try {
      await api.post("/pipeline/run", { max_tasks: Number(maxTasks) || 10 });
      toast.success("Pipeline 已启动");
      load();
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const stopPipeline = async () => {
    try {
      await api.post("/pipeline/stop");
      toast.info("已请求停止");
    } catch (e) {
      toast.error(errText(e));
    }
  };

  return (
    <div data-testid="dashboard-page">
      <PageHeader title="总览" sub="Pipeline 运行状态与知识库概况" />

      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
        <StatCard testId="stat-done" label="已完成" value={queue.done} icon={CheckCircle2} accent />
        <StatCard testId="stat-pending" label="待处理" value={queue.pending} icon={Clock3} />
        <StatCard testId="stat-failed" label="失败" value={queue.failed} icon={XCircle} />
        <StatCard
          testId="stat-tokens"
          label="Token 消耗"
          value={`${((queue.input_tokens + queue.output_tokens) / 1000).toFixed(1)}k`}
          sub={`≈ $${queue.cost_usd}`}
          icon={Coins}
        />
        <StatCard testId="stat-wiki" label="Wiki 页面" value={wiki.wiki_pages} icon={BookOpen} />
        <StatCard testId="stat-raw" label="原始资料" value={wiki.raw_files} icon={FileText} />
      </div>

      {review_pending > 0 && (
        <Link to="/review" data-testid="review-alert-link">
          <Card className="p-4 mb-8 flex items-center gap-3 border-amber-300 bg-amber-50 hover:border-amber-400">
            <ShieldAlert className="w-5 h-5 text-amber-600" />
            <span className="text-sm font-medium text-amber-800">
              有 {review_pending} 个待审查项（概念合并 / 矛盾检测），点击查看
            </span>
          </Card>
        </Link>
      )}

      <div className="grid lg:grid-cols-3 gap-6 mb-8">
        {/* Pipeline 控制 */}
        <Card className="p-6 lg:col-span-1">
          <div className="label-xs mb-4">PIPELINE 控制</div>
          {pipeline.running ? (
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Spinner className="w-4 h-4" />
                <span className="text-sm font-medium">
                  运行中 · {pipeline.processed}/{pipeline.max_tasks}
                </span>
              </div>
              <div className="w-full bg-surface rounded h-2 mb-4">
                <div
                  className="bg-primary h-2 rounded transition-all"
                  style={{ width: `${(pipeline.processed / Math.max(pipeline.max_tasks, 1)) * 100}%` }}
                />
              </div>
              <div className="text-xs text-muted mb-4">
                成功 {pipeline.succeeded} · 失败 {pipeline.failed}
              </div>
              <Button variant="danger" onClick={stopPipeline} data-testid="pipeline-stop-btn">
                <Square className="w-4 h-4" /> 停止
              </Button>
            </div>
          ) : (
            <div>
              <label className="text-xs text-muted block mb-1.5">本次最多处理任务数</label>
              <div className="flex gap-2">
                <Input
                  data-testid="pipeline-max-input"
                  type="number"
                  min="1"
                  max="200"
                  value={maxTasks}
                  onChange={(e) => setMaxTasks(e.target.value)}
                  className="w-24"
                />
                <Button onClick={runPipeline} disabled={queue.pending === 0} data-testid="pipeline-run-btn">
                  <Play className="w-4 h-4" /> 运行
                </Button>
              </div>
              {queue.pending === 0 && (
                <div className="text-xs text-muted mt-2">队列为空 — 可在「提交内容」或「设置」中抓取 RSS</div>
              )}
            </div>
          )}
          {/* 事件流 */}
          <div className="mt-6 space-y-2 max-h-56 overflow-y-auto" data-testid="pipeline-events">
            {(pipeline.events || []).slice(0, 10).map((ev, i) => (
              <div key={i} className="text-xs flex gap-2 items-start">
                <span
                  className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${
                    ev.type === "success" ? "bg-green-500" : ev.type === "error" ? "bg-red-500" : "bg-zinc-400"
                  }`}
                />
                <div className="min-w-0">
                  <div className="truncate font-medium">{ev.message}</div>
                  {ev.detail && <div className="text-muted truncate">{ev.detail}</div>}
                </div>
              </div>
            ))}
            {(!pipeline.events || pipeline.events.length === 0) && (
              <div className="text-xs text-muted">暂无运行记录</div>
            )}
          </div>
        </Card>

        {/* 14日活动 */}
        <Card className="p-6 lg:col-span-2">
          <div className="label-xs mb-4">近 14 日处理活动</div>
          {daily_activity.length === 0 ? (
            <div className="text-sm text-muted py-12 text-center">暂无数据 — 运行 Pipeline 后这里会显示趋势</div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={daily_activity}>
                <XAxis dataKey="d" tick={{ fontSize: 11 }} stroke="#A1A1AA" />
                <YAxis tick={{ fontSize: 11 }} stroke="#A1A1AA" allowDecimals={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid #E4E4E7" }} />
                <Area type="monotone" dataKey="done" name="完成" stroke="#002FA7" fill="#002FA7" fillOpacity={0.12} strokeWidth={2} />
                <Area type="monotone" dataKey="failed" name="失败" stroke="#DC2626" fill="#DC2626" fillOpacity={0.08} strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* 分类分布 */}
        <Card className="p-6">
          <div className="label-xs mb-4">知识分类分布</div>
          {categories.length === 0 ? (
            <div className="text-sm text-muted py-12 text-center">暂无数据</div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={categories} layout="vertical" margin={{ left: 10 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="category" tick={{ fontSize: 12 }} width={86} stroke="#A1A1AA" />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid #E4E4E7" }} />
                <Bar dataKey="count" name="数量" radius={[0, 4, 4, 0]}>
                  {categories.map((_, i) => (
                    <Cell key={i} fill={CAT_COLORS[i % CAT_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* 最近编译结果 */}
        <Card className="p-6 lg:col-span-2">
          <div className="flex items-center justify-between mb-4">
            <div className="label-xs">最近编译结果</div>
            <Link to="/tasks" className="text-xs text-primary hover:underline" data-testid="view-all-tasks-link">
              查看全部 →
            </Link>
          </div>
          {recent_results.length === 0 ? (
            <div className="text-sm text-muted py-12 text-center">还没有编译结果 — 提交文章或运行 Pipeline</div>
          ) : (
            <div className="divide-y divide-line" data-testid="recent-results-list">
              {recent_results.map((r) => (
                <div key={r.id} className="py-3 flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium truncate">{r.title_zh || r.title_en || r.filename}</div>
                    <div className="text-xs text-muted truncate mt-0.5">{r.summary_zh}</div>
                    <div className="flex gap-1.5 mt-1.5 flex-wrap">
                      {r.category && <Badge>{r.category}</Badge>}
                      {(r.tags || []).slice(0, 4).map((t) => (
                        <Badge key={t}>{t}</Badge>
                      ))}
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-xs font-mono text-muted">
                      {r.quality_score != null ? `Q ${Number(r.quality_score).toFixed(1)}` : ""}
                    </div>
                    <div className="text-xs text-muted mt-1">{(r.created_at || "").slice(0, 10)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
