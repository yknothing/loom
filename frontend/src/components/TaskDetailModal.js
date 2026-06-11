import React, { useEffect, useState } from "react";
import { X } from "lucide-react";
import api from "../lib/api";
import { Badge, Spinner, StatusBadge } from "./ui";

export default function TaskDetailModal({ taskId, onClose }) {
  const [detail, setDetail] = useState(null);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    if (taskId == null) return;
    setDetail(null);
    api.get(`/tasks/${taskId}`).then((r) => setDetail(r.data)).catch(() => onClose());
  }, [taskId, onClose]);

  if (taskId == null) return null;

  const t = detail?.task;
  const r = detail?.result;

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6"
      onClick={onClose}
      data-testid="task-detail-modal"
    >
      <div
        className="bg-white rounded-md w-full max-w-3xl max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-line sticky top-0 bg-white">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm text-muted">#{taskId}</span>
            {t && <StatusBadge status={t.status} />}
            {t?.stage && <Badge>{t.stage}</Badge>}
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink" data-testid="close-task-detail">
            <X className="w-5 h-5" />
          </button>
        </div>

        {!detail ? (
          <div className="flex justify-center py-20">
            <Spinner className="w-7 h-7" />
          </div>
        ) : (
          <div className="px-6 py-5 space-y-5">
            <div>
              <div className="label-xs mb-1">原始文件</div>
              <div className="font-mono text-sm">{t.filepath}</div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <div className="label-xs mb-1">模型</div>
                <div className="font-mono text-xs">{t.llm_model || "—"}</div>
              </div>
              <div>
                <div className="label-xs mb-1">Token</div>
                <div className="font-mono text-xs">
                  {t.input_tokens + t.output_tokens > 0 ? `${t.input_tokens} + ${t.output_tokens}` : "—"}
                </div>
              </div>
              <div>
                <div className="label-xs mb-1">重试次数</div>
                <div className="font-mono text-xs">{t.retry_count}</div>
              </div>
              <div>
                <div className="label-xs mb-1">完成时间</div>
                <div className="font-mono text-xs">{t.completed_at || "—"}</div>
              </div>
            </div>

            {t.error_message && (
              <div>
                <div className="label-xs mb-1 text-red-600">错误信息</div>
                <pre className="text-xs bg-red-50 border border-red-200 rounded-md p-3 whitespace-pre-wrap text-red-700">
                  {t.error_message}
                </pre>
              </div>
            )}

            {r && (
              <>
                <div className="border-t border-line pt-5">
                  <div className="text-lg font-bold tracking-tight">{r.title_zh || r.title_en}</div>
                  {r.title_en && r.title_zh && <div className="text-sm text-muted">{r.title_en}</div>}
                  <div className="flex gap-1.5 mt-2 flex-wrap">
                    {r.category && <Badge className="!bg-primary/10 !text-primary !border-primary/20">{r.category}</Badge>}
                    {(r.tags || []).map((tag) => (
                      <Badge key={tag}>{tag}</Badge>
                    ))}
                    {r.quality_score != null && <Badge>Q {Number(r.quality_score).toFixed(1)}</Badge>}
                    {r.sentiment && <Badge>{r.sentiment}</Badge>}
                  </div>
                </div>
                {r.summary_zh && (
                  <div>
                    <div className="label-xs mb-1">深度摘要</div>
                    <p className="text-sm leading-relaxed text-zinc-700">{r.summary_zh}</p>
                  </div>
                )}
                {(r.key_insights || []).length > 0 && (
                  <div>
                    <div className="label-xs mb-1">核心洞察</div>
                    <ul className="text-sm space-y-1.5 list-disc pl-5 text-zinc-700">
                      {r.key_insights.map((ins, i) => (
                        <li key={i}>{ins}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {(r.people || []).length > 0 && (
                  <div>
                    <div className="label-xs mb-1">相关人物</div>
                    <div className="flex gap-1.5 flex-wrap">
                      {r.people.map((p, i) => (
                        <Badge key={i}>
                          {p.name}
                          {p.role ? ` · ${p.role}` : ""}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <button
                    onClick={() => setShowRaw(!showRaw)}
                    className="text-xs text-primary hover:underline"
                    data-testid="toggle-raw-response"
                  >
                    {showRaw ? "隐藏" : "查看"} LLM 原始输出
                  </button>
                  {showRaw && (
                    <pre className="mt-2 text-xs bg-surface rounded-md p-3 overflow-x-auto font-mono text-zinc-600 max-h-80">
                      {r.raw_llm_response}
                    </pre>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
