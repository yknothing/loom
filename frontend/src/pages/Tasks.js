import React, { useCallback, useEffect, useState } from "react";
import { RotateCcw, FolderSync } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Button, Card, EmptyState, Input, PageHeader, StatusBadge } from "../components/ui";

const FILTERS = [
  { key: "", label: "全部" },
  { key: "pending", label: "待处理" },
  { key: "running", label: "处理中" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败" },
  { key: "rejected", label: "已拒绝" },
];
const PAGE_SIZE = 25;

export default function Tasks() {
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/tasks", {
        params: { status: status || undefined, search: search || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE },
      });
      setData(data);
    } catch (e) {
      toast.error(errText(e));
    } finally {
      setLoading(false);
    }
  }, [status, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = async (id) => {
    try {
      await api.post(`/tasks/${id}/retry`);
      toast.success("已重新加入队列");
      load();
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const retryAll = async () => {
    try {
      const { data } = await api.post("/tasks/retry-failed");
      toast.success(`已重置 ${data.count} 个失败任务`);
      load();
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const enqueueRaw = async () => {
    try {
      const { data } = await api.post("/tasks/enqueue-raw");
      toast.success(`扫描完成：新增 ${data.added}，已存在 ${data.skipped}`);
      load();
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const totalPages = Math.ceil(data.total / PAGE_SIZE);

  return (
    <div data-testid="tasks-page">
      <PageHeader title="任务队列" sub={`共 ${data.total} 个任务`}>
        <Button variant="outline" onClick={enqueueRaw} data-testid="enqueue-raw-btn">
          <FolderSync className="w-4 h-4" /> 扫描原始资料入队
        </Button>
        <Button variant="outline" onClick={retryAll} data-testid="retry-all-btn">
          <RotateCcw className="w-4 h-4" /> 重试全部失败
        </Button>
      </PageHeader>

      <div className="flex flex-wrap items-center gap-2 mb-5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            data-testid={`filter-${f.key || "all"}`}
            onClick={() => {
              setStatus(f.key);
              setPage(0);
            }}
            className={`px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ${
              status === f.key
                ? "bg-primary text-white border-primary"
                : "bg-white text-zinc-600 border-line hover:border-zinc-400"
            }`}
          >
            {f.label}
          </button>
        ))}
        <div className="ml-auto w-64">
          <Input
            data-testid="task-search-input"
            placeholder="按文件名搜索…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
          />
        </div>
      </div>

      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="label-xs px-4 py-3">ID</th>
              <th className="label-xs px-4 py-3">文件</th>
              <th className="label-xs px-4 py-3">状态</th>
              <th className="label-xs px-4 py-3">模式</th>
              <th className="label-xs px-4 py-3">Token</th>
              <th className="label-xs px-4 py-3">重试</th>
              <th className="label-xs px-4 py-3">完成时间</th>
              <th className="label-xs px-4 py-3"></th>
            </tr>
          </thead>
          <tbody data-testid="tasks-table-body">
            {data.items.map((t) => (
              <tr key={t.id} className="border-b border-line last:border-0 hover:bg-surface/60">
                <td className="px-4 py-2.5 font-mono text-xs text-muted">{t.id}</td>
                <td className="px-4 py-2.5 max-w-[320px]">
                  <div className="truncate font-medium">{t.filename}</div>
                  {t.error_message && (
                    <div className="text-xs text-red-600 truncate mt-0.5" title={t.error_message}>
                      {t.error_message}
                    </div>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={t.status} />
                </td>
                <td className="px-4 py-2.5 text-xs text-muted">{t.stage || "—"}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted">
                  {t.input_tokens + t.output_tokens > 0 ? `${t.input_tokens}+${t.output_tokens}` : "—"}
                </td>
                <td className="px-4 py-2.5 text-xs text-muted">{t.retry_count}</td>
                <td className="px-4 py-2.5 text-xs text-muted">{t.completed_at || "—"}</td>
                <td className="px-4 py-2.5">
                  {(t.status === "failed" || t.status === "rejected") && (
                    <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => retry(t.id)} data-testid={`retry-task-${t.id}`}>
                      <RotateCcw className="w-3.5 h-3.5" /> 重试
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && data.items.length === 0 && <EmptyState title="没有任务" sub="提交内容或抓取 RSS 后任务会出现在这里" />}
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 mt-4">
          <Button variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)} data-testid="prev-page-btn">
            上一页
          </Button>
          <span className="text-sm text-muted">
            {page + 1} / {totalPages}
          </span>
          <Button variant="outline" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)} data-testid="next-page-btn">
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}
