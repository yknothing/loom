import React, { useCallback, useEffect, useState } from "react";
import { KeyRound, Rss, Save, DownloadCloud, Trash2, Plus } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Badge, Button, Card, Input, PageHeader, Select, Spinner } from "../components/ui";
import SettingsAutomation from "../components/SettingsAutomation";

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [sources, setSources] = useState(null);
  const [fetching, setFetching] = useState(false);

  const load = useCallback(async () => {
    const [s, src] = await Promise.all([api.get("/settings"), api.get("/sources")]);
    setSettings(s.data);
    setSources(src.data);
    setFetching(src.data.fetch?.running || false);
  }, []);

  useEffect(() => {
    load().catch((e) => toast.error(errText(e)));
  }, [load]);

  // RSS 抓取进行中时轮询状态
  useEffect(() => {
    if (!fetching) return;
    const t = setInterval(async () => {
      const { data } = await api.get("/sources");
      setSources(data);
      if (!data.fetch?.running) {
        setFetching(false);
        const r = data.fetch?.result;
        if (r) toast.success(`RSS 抓取完成，新增入队 ${r.enqueued?.added ?? 0} 篇`);
      }
    }, 3000);
    return () => clearInterval(t);
  }, [fetching]);

  if (!settings || !sources)
    return (
      <div className="flex justify-center py-32">
        <Spinner className="w-8 h-8" />
      </div>
    );

  const saveSettings = async () => {
    try {
      await api.put("/settings", {
        provider: settings.provider,
        model: settings.model,
        two_stage: settings.two_stage,
      });
      toast.success("Pipeline 设置已保存");
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const saveFeeds = async () => {
    try {
      await api.put("/sources", { feeds: sources.feeds });
      toast.success("RSS 源已保存");
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const fetchNow = async () => {
    try {
      await api.post("/sources/fetch");
      setFetching(true);
      toast.info("RSS 抓取已开始（后台运行）");
    } catch (e) {
      toast.error(errText(e));
    }
  };

  const updateFeed = (i, key, val) => {
    const feeds = [...sources.feeds];
    feeds[i] = { ...feeds[i], [key]: val };
    setSources({ ...sources, feeds });
  };

  return (
    <div data-testid="settings-page">
      <PageHeader title="设置" sub="LLM Provider、Pipeline 行为与 RSS 数据源" />

      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        {/* Provider */}
        <Card className="p-6">
          <div className="label-xs mb-4 flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" /> LLM PROVIDERS
          </div>
          <table className="w-full text-sm mb-4">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="label-xs py-2">名称</th>
                <th className="label-xs py-2">模型</th>
                <th className="label-xs py-2">协议</th>
                <th className="label-xs py-2">密钥</th>
              </tr>
            </thead>
            <tbody data-testid="providers-table">
              {settings.providers.map((p) => (
                <tr key={p.name} className="border-b border-line last:border-0">
                  <td className="py-2.5 font-mono text-xs">
                    {p.name}
                    {p.is_default && <Badge className="ml-2 !bg-primary/10 !text-primary !border-primary/20">默认</Badge>}
                  </td>
                  <td className="py-2.5 font-mono text-xs text-muted">{p.model}</td>
                  <td className="py-2.5 text-xs text-muted">{p.api}</td>
                  <td className="py-2.5">
                    {p.has_key ? (
                      <Badge className="!bg-green-50 !text-green-700 !border-green-200">已配置</Badge>
                    ) : (
                      <Badge className="!bg-red-50 !text-red-600 !border-red-200">未配置</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted">
            emergent 为通用 Key（OpenAI / Claude / Gemini），其余 Provider 通过环境变量
            <code className="font-mono mx-1">LOOM_API_KEY_&lt;NAME&gt;</code>
            或 config/loom.yml 配置密钥。
          </p>
        </Card>

        {/* Pipeline 设置 */}
        <Card className="p-6">
          <div className="label-xs mb-4">PIPELINE 设置</div>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-muted block mb-1.5">激活 Provider</label>
              <Select
                data-testid="settings-provider-select"
                value={settings.provider || ""}
                onChange={(e) => setSettings({ ...settings, provider: e.target.value })}
              >
                {settings.providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name} {p.has_key ? "" : "（密钥未配置）"}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1.5">模型覆盖（留空使用 Provider 默认）</label>
              <Input
                data-testid="settings-model-input"
                value={settings.model}
                onChange={(e) => setSettings({ ...settings, model: e.target.value })}
                placeholder="如 openai/gpt-5.4 · anthropic/claude-sonnet-4-6 · gemini/gemini-3-flash-preview"
              />
            </div>
            <label className="flex items-center gap-2.5 text-sm cursor-pointer">
              <input
                data-testid="settings-two-stage-checkbox"
                type="checkbox"
                checked={settings.two_stage}
                onChange={(e) => setSettings({ ...settings, two_stage: e.target.checked })}
                className="w-4 h-4 accent-[#002FA7]"
              />
              两阶段编译（分析 → 综合，质量更高，成本约 2 倍）
            </label>
            <Button onClick={saveSettings} data-testid="save-settings-btn">
              <Save className="w-4 h-4" /> 保存设置
            </Button>
          </div>
        </Card>
      </div>

      {/* RSS 源 */}
      <Card className="p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="label-xs flex items-center gap-1.5">
            <Rss className="w-3.5 h-3.5" /> RSS 数据源（{sources.feeds.length}）
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() =>
                setSources({
                  ...sources,
                  feeds: [...sources.feeds, { name: "", url: "", category: "general", priority: "medium" }],
                })
              }
              data-testid="add-feed-btn"
            >
              <Plus className="w-4 h-4" /> 添加
            </Button>
            <Button variant="outline" onClick={saveFeeds} data-testid="save-feeds-btn">
              <Save className="w-4 h-4" /> 保存
            </Button>
            <Button onClick={fetchNow} disabled={fetching} data-testid="fetch-rss-btn">
              <DownloadCloud className="w-4 h-4" /> {fetching ? "抓取中…" : "立即抓取"}
            </Button>
          </div>
        </div>
        <div className="space-y-2" data-testid="feeds-list">
          {sources.feeds.map((f, i) => (
            <div key={i} className="grid grid-cols-12 gap-2 items-center">
              <Input className="col-span-3" value={f.name} placeholder="名称" onChange={(e) => updateFeed(i, "name", e.target.value)} />
              <Input className="col-span-6" value={f.url} placeholder="https://…/feed.xml" onChange={(e) => updateFeed(i, "url", e.target.value)} />
              <Input className="col-span-2" value={f.category} placeholder="分类" onChange={(e) => updateFeed(i, "category", e.target.value)} />
              <button
                className="col-span-1 text-muted hover:text-red-600 flex justify-center"
                onClick={() => setSources({ ...sources, feeds: sources.feeds.filter((_, j) => j !== i) })}
                data-testid={`delete-feed-${i}`}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
        {sources.fetch?.result && (
          <pre className="mt-4 text-xs bg-surface rounded-md p-3 overflow-x-auto font-mono text-zinc-600 max-h-48">
            {sources.fetch.result.output}
          </pre>
        )}
      </Card>

      <SettingsAutomation />
    </div>
  );
}
