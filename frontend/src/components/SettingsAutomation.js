import React, { useEffect, useState } from "react";
import { CalendarClock, Mail, Eye, Send, Save } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Badge, Button, Card, Input, Select, Spinner } from "./ui";

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export default function SettingsAutomation() {
  const [cfg, setCfg] = useState(null);
  const [preview, setPreview] = useState(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    api.get("/schedule").then((r) => setCfg(r.data)).catch((e) => toast.error(errText(e)));
  }, []);

  if (!cfg)
    return (
      <Card className="p-6 flex justify-center">
        <Spinner />
      </Card>
    );

  const save = async () => {
    try {
      const { data } = await api.put("/schedule", {
        rss_enabled: cfg.rss_enabled,
        rss_hour: Number(cfg.rss_hour),
        auto_pipeline: cfg.auto_pipeline,
        pipeline_max: Number(cfg.pipeline_max),
        digest_enabled: cfg.digest_enabled,
        digest_weekday: Number(cfg.digest_weekday),
        digest_hour: Number(cfg.digest_hour),
        digest_recipients: (cfg._recipients_text ?? (cfg.digest_recipients || []).join(", "))
          .split(/[,，;\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setCfg({ ...cfg, jobs: data.jobs });
      toast.success("自动化设置已保存");
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const showPreview = async () => {
    try {
      const { data } = await api.get("/digest/preview");
      setPreview(data.html);
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const sendNow = async () => {
    setSending(true);
    try {
      const { data } = await api.post("/digest/send", {});
      toast.success(`简报已发送给 ${data.recipients.length} 位成员`);
    } catch (e) {
      toast.error(errText(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        {/* 定时调度 */}
        <Card className="p-6">
          <div className="label-xs mb-4 flex items-center gap-1.5">
            <CalendarClock className="w-3.5 h-3.5" /> 定时调度（UTC 时间）
          </div>
          <div className="space-y-4">
            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                data-testid="schedule-rss-enabled"
                type="checkbox"
                checked={cfg.rss_enabled}
                onChange={(e) => setCfg({ ...cfg, rss_enabled: e.target.checked })}
                className="w-4 h-4 accent-[#002FA7]"
              />
              每日自动抓取 RSS
            </label>
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted">抓取时间：每天</span>
              <Input
                data-testid="schedule-rss-hour"
                type="number"
                min="0"
                max="23"
                value={cfg.rss_hour}
                onChange={(e) => setCfg({ ...cfg, rss_hour: e.target.value })}
                className="!w-20"
              />
              <span className="text-muted">点（UTC）</span>
            </div>
            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                data-testid="schedule-auto-pipeline"
                type="checkbox"
                checked={cfg.auto_pipeline}
                onChange={(e) => setCfg({ ...cfg, auto_pipeline: e.target.checked })}
                className="w-4 h-4 accent-[#002FA7]"
              />
              抓取后自动运行 Pipeline（最多
              <Input
                type="number"
                min="1"
                max="200"
                value={cfg.pipeline_max}
                onChange={(e) => setCfg({ ...cfg, pipeline_max: e.target.value })}
                className="!w-20 mx-1"
              />
              个任务）
            </label>
            <div className="text-xs text-muted space-y-1 pt-2 border-t border-line">
              <div>下次 RSS 任务: {cfg.jobs?.rss_job ? cfg.jobs.rss_job.slice(0, 16).replace("T", " ") + " UTC" : "未启用"}</div>
              {cfg.last_rss_run && <div>上次运行: {cfg.last_rss_run.slice(0, 16).replace("T", " ")} UTC</div>}
            </div>
          </div>
        </Card>

        {/* 每周简报 */}
        <Card className="p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="label-xs flex items-center gap-1.5">
              <Mail className="w-3.5 h-3.5" /> 每周知识简报
            </div>
            {cfg.email_configured ? (
              <Badge className="!bg-green-50 !text-green-700 !border-green-200">邮件服务已配置</Badge>
            ) : (
              <Badge className="!bg-amber-50 !text-amber-700 !border-amber-200">需配置 RESEND_API_KEY</Badge>
            )}
          </div>
          <div className="space-y-4">
            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                data-testid="digest-enabled"
                type="checkbox"
                checked={cfg.digest_enabled}
                onChange={(e) => setCfg({ ...cfg, digest_enabled: e.target.checked })}
                className="w-4 h-4 accent-[#002FA7]"
              />
              每周自动推送简报邮件
            </label>
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted">发送时间：每</span>
              <Select
                data-testid="digest-weekday"
                value={cfg.digest_weekday}
                onChange={(e) => setCfg({ ...cfg, digest_weekday: e.target.value })}
                className="!w-28"
              >
                {WEEKDAYS.map((d, i) => (
                  <option key={i} value={i}>
                    {d}
                  </option>
                ))}
              </Select>
              <Input
                data-testid="digest-hour"
                type="number"
                min="0"
                max="23"
                value={cfg.digest_hour}
                onChange={(e) => setCfg({ ...cfg, digest_hour: e.target.value })}
                className="!w-20"
              />
              <span className="text-muted">点（UTC）</span>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1.5">收件人（逗号分隔，留空 = 全体注册成员）</label>
              <Input
                data-testid="digest-recipients"
                value={cfg._recipients_text ?? (cfg.digest_recipients || []).join(", ")}
                onChange={(e) => setCfg({ ...cfg, _recipients_text: e.target.value })}
                placeholder="a@team.dev, b@team.dev"
              />
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={showPreview} data-testid="digest-preview-btn">
                <Eye className="w-4 h-4" /> 预览简报
              </Button>
              <Button onClick={sendNow} disabled={sending} data-testid="digest-send-btn">
                <Send className="w-4 h-4" /> {sending ? "发送中…" : "立即发送"}
              </Button>
            </div>
            {cfg.last_digest_sent && (
              <div className="text-xs text-muted pt-2 border-t border-line">
                上次发送: {cfg.last_digest_sent.slice(0, 16).replace("T", " ")} UTC
                {cfg.jobs?.digest_job && <> · 下次: {cfg.jobs.digest_job.slice(0, 16).replace("T", " ")} UTC</>}
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Button onClick={save} data-testid="save-automation-btn">
          <Save className="w-4 h-4" /> 保存自动化设置
        </Button>
      </div>

      {/* 简报预览弹窗 */}
      {preview && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8"
          onClick={() => setPreview(null)}
          data-testid="digest-preview-modal"
        >
          <div className="bg-white rounded-md w-full max-w-2xl h-[80vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-line">
              <span className="text-sm font-semibold">简报预览</span>
              <button onClick={() => setPreview(null)} className="text-sm text-muted hover:text-ink" data-testid="close-preview-btn">
                关闭 ✕
              </button>
            </div>
            <iframe title="digest-preview" srcDoc={preview} className="w-full h-full border-0" />
          </div>
        </div>
      )}
    </>
  );
}
