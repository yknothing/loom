import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link as LinkIcon, FileText, Send } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Button, Card, Input, PageHeader, Select, Textarea } from "../components/ui";

const CATEGORIES = ["ai", "engineering", "business", "science", "culture", "opinion", "security", "hardware", "other"];

export default function Submit() {
  const [mode, setMode] = useState("url");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("");
  const [autoProcess, setAutoProcess] = useState(true);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/submit", {
        type: mode,
        url,
        title,
        content,
        category,
        auto_process: autoProcess,
      });
      toast.success(
        data.processing
          ? `已提交并开始即时编译: ${data.title?.slice(0, 40)}`
          : `已加入队列: ${data.title?.slice(0, 40)}`
      );
      setUrl("");
      setTitle("");
      setContent("");
      navigate("/");
    } catch (err) {
      toast.error(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="submit-page">
      <PageHeader title="提交内容" sub="提交 URL 或文本，立即进入知识编译 Pipeline" />
      <Card className="max-w-2xl p-8">
        <div className="flex gap-2 mb-6">
          <button
            data-testid="submit-mode-url"
            onClick={() => setMode("url")}
            className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md border transition-colors ${
              mode === "url" ? "bg-primary text-white border-primary" : "border-line text-zinc-600 hover:border-zinc-400"
            }`}
          >
            <LinkIcon className="w-4 h-4" /> 网页 URL
          </button>
          <button
            data-testid="submit-mode-text"
            onClick={() => setMode("text")}
            className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md border transition-colors ${
              mode === "text" ? "bg-primary text-white border-primary" : "border-line text-zinc-600 hover:border-zinc-400"
            }`}
          >
            <FileText className="w-4 h-4" /> 粘贴文本
          </button>
        </div>

        <form onSubmit={submit} className="space-y-5">
          {mode === "url" ? (
            <div>
              <label className="label-xs block mb-1.5">文章 URL</label>
              <Input
                data-testid="submit-url-input"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/article"
                required
              />
              <p className="text-xs text-muted mt-1.5">系统会自动抓取网页正文并保存为不可变原始资料</p>
            </div>
          ) : (
            <>
              <div>
                <label className="label-xs block mb-1.5">标题</label>
                <Input
                  data-testid="submit-title-input"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="文章标题"
                  required
                />
              </div>
              <div>
                <label className="label-xs block mb-1.5">正文内容</label>
                <Textarea
                  data-testid="submit-content-input"
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  rows={10}
                  placeholder="粘贴文章正文（至少 50 字符）…"
                  required
                />
              </div>
            </>
          )}
          <div>
            <label className="label-xs block mb-1.5">分类（可选）</label>
            <Select data-testid="submit-category-select" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">自动判断</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          </div>
          <label className="flex items-center gap-2.5 text-sm cursor-pointer">
            <input
              data-testid="auto-process-checkbox"
              type="checkbox"
              checked={autoProcess}
              onChange={(e) => setAutoProcess(e.target.checked)}
              className="w-4 h-4 accent-[#002FA7]"
            />
            提交后立即编译（调用 LLM 即时生成知识页面）
          </label>
          <Button type="submit" disabled={busy} data-testid="submit-article-btn">
            <Send className="w-4 h-4" /> {busy ? "提交中…" : "提交"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
