import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Spinner } from "../components/ui";

const TYPE_LABELS = {
  duplicate_concepts: "概念合并",
  contradiction: "观点矛盾",
  thin_page: "内容单薄",
  gap: "知识缺口",
  stale_page: "页面过期",
};
const TYPE_COLORS = {
  duplicate_concepts: "!bg-blue-50 !text-blue-700 !border-blue-200",
  contradiction: "!bg-red-50 !text-red-700 !border-red-200",
  thin_page: "!bg-amber-50 !text-amber-700 !border-amber-200",
  gap: "!bg-purple-50 !text-purple-700 !border-purple-200",
  stale_page: "!bg-zinc-100 !text-zinc-600 !border-zinc-200",
};

export default function Review() {
  const [data, setData] = useState(null);
  const [resolving, setResolving] = useState(null);
  const [resolution, setResolution] = useState("");
  const [status, setStatus] = useState("pending");

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/review", { params: { status } });
      setData(data);
    } catch (e) {
      toast.error(errText(e));
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const resolve = async (id) => {
    if (!resolution.trim()) {
      toast.error("请填写处理说明");
      return;
    }
    try {
      await api.post(`/review/${id}/resolve`, { resolution: resolution.trim() });
      toast.success("已标记为已解决");
      setResolving(null);
      setResolution("");
      load();
    } catch (e) {
      toast.error(errText(e));
    }
  };

  if (!data)
    return (
      <div className="flex justify-center py-32">
        <Spinner className="w-8 h-8" />
      </div>
    );

  return (
    <div data-testid="review-page">
      <PageHeader title="审查队列" sub="Pipeline 自动发现的需人工确认的知识库事件">
        <div className="flex gap-2">
          {["pending", "resolved"].map((s) => (
            <button
              key={s}
              data-testid={`review-filter-${s}`}
              onClick={() => setStatus(s)}
              className={`px-3 py-1.5 text-sm font-medium rounded-md border transition-colors ${
                status === s ? "bg-primary text-white border-primary" : "border-line text-zinc-600 hover:border-zinc-400"
              }`}
            >
              {s === "pending" ? "待处理" : "已解决"}
            </button>
          ))}
        </div>
      </PageHeader>

      <div className="flex gap-3 mb-6 flex-wrap">
        {Object.entries(data.stats || {}).map(([type, counts]) => (
          <Card key={type} className="px-4 py-3 flex items-center gap-3">
            <Badge className={TYPE_COLORS[type]}>{TYPE_LABELS[type] || type}</Badge>
            <span className="text-sm font-mono">
              {counts.pending || 0} 待处理 / {counts.resolved || 0} 已解决
            </span>
          </Card>
        ))}
      </div>

      <div className="space-y-3 max-w-4xl" data-testid="review-items">
        {data.items.length === 0 && <EmptyState title="没有审查项" sub="Pipeline 合并概念或发现矛盾时会自动写入这里" />}
        {data.items.map((item) => (
          <Card key={item.id} className="p-5">
            <div className="flex items-center gap-2 mb-2">
              <Badge className={TYPE_COLORS[item.type]}>{TYPE_LABELS[item.type] || item.type}</Badge>
              <span className="text-xs font-mono text-muted">{item.id}</span>
              <span className="text-xs text-muted ml-auto">{(item.created || "").slice(0, 19).replace("T", " ")}</span>
            </div>
            <pre className="text-xs bg-surface rounded-md p-3 overflow-x-auto font-mono text-zinc-700 mb-3">
              {JSON.stringify(item.data, null, 2)}
            </pre>
            <div className="text-xs text-muted mb-3">来源: {item.source}</div>
            {item.status === "pending" ? (
              resolving === item.id ? (
                <div className="flex gap-2">
                  <Input
                    data-testid={`resolution-input-${item.id}`}
                    value={resolution}
                    onChange={(e) => setResolution(e.target.value)}
                    placeholder="处理说明，例如：合并正确 / 已手动拆分…"
                    autoFocus
                  />
                  <Button onClick={() => resolve(item.id)} data-testid={`confirm-resolve-${item.id}`}>
                    确认
                  </Button>
                  <Button variant="outline" onClick={() => setResolving(null)}>
                    取消
                  </Button>
                </div>
              ) : (
                <Button variant="outline" onClick={() => setResolving(item.id)} data-testid={`resolve-btn-${item.id}`}>
                  <CheckCircle2 className="w-4 h-4" /> 标记解决
                </Button>
              )
            ) : (
              <div className="text-sm text-green-700 flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" /> {item.resolution}
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
