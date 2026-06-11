import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, Link2, Pencil } from "lucide-react";
import { toast } from "sonner";
import api, { errText } from "../lib/api";
import { Badge, Button, Card, EmptyState, PageHeader, Spinner, Textarea } from "../components/ui";

const SECTION_LABELS = {
  ideas: "概念",
  people: "人物",
  "mental-models": "思维模型",
  projects: "项目",
  daily: "周摘要",
  code: "技术文档",
};

function preprocessWikilinks(body) {
  return body
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_, target, label) => `[${label}](wikilink:${target.trim()})`)
    .replace(/\[\[([^\]]+)\]\]/g, (_, target) => `[${target.trim()}](wikilink:${target.trim()})`);
}

function normalizeTarget(t) {
  let s = t.trim();
  if (s.endsWith(".md")) s = s.slice(0, -3);
  if (s.startsWith("wiki/")) s = s.slice(5);
  return s;
}

export default function Wiki() {
  const [params, setParams] = useSearchParams();
  const activePath = params.get("page") || "";
  const [tree, setTree] = useState(null);
  const [page, setPage] = useState(null);
  const [pageLoading, setPageLoading] = useState(false);
  const [open, setOpen] = useState({ ideas: true, people: true });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .get("/wiki/tree")
      .then((r) => {
        setTree(r.data);
        setOpen(Object.fromEntries(Object.keys(r.data).map((k) => [k, true])));
      })
      .catch(() => setTree({}));
  }, []);

  useEffect(() => {
    setEditing(false);
    if (!activePath) {
      setPage(null);
      return;
    }
    setPageLoading(true);
    api
      .get("/wiki/page", { params: { path: activePath } })
      .then((r) => setPage(r.data))
      .catch(() => setPage(null))
      .finally(() => setPageLoading(false));
  }, [activePath]);

  const openPage = (path) => setParams({ page: normalizeTarget(path) });

  const startEdit = () => {
    setDraft(page.body);
    setEditing(true);
  };

  const saveEdit = async () => {
    setSaving(true);
    try {
      await api.put("/wiki/page", { path: page.path, body: draft });
      toast.success("页面已保存（已记录到操作日志）");
      setEditing(false);
      const { data } = await api.get("/wiki/page", { params: { path: page.path } });
      setPage(data);
    } catch (e) {
      toast.error(errText(e));
    } finally {
      setSaving(false);
    }
  };

  const processedBody = useMemo(() => (page ? preprocessWikilinks(page.body) : ""), [page]);

  const totalPages = tree ? Object.values(tree).reduce((a, v) => a + v.length, 0) : 0;

  return (
    <div data-testid="wiki-page">
      <PageHeader title="知识库" sub={`${totalPages} 个页面 · LLM 自动维护的结构化知识`} />
      <div className="grid grid-cols-12 gap-6">
        {/* 目录树 */}
        <Card className="col-span-4 xl:col-span-3 p-4 max-h-[calc(100vh-220px)] overflow-y-auto" data-testid="wiki-tree">
          {!tree ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : (
            Object.entries(tree).map(([section, pages]) => (
              <div key={section} className="mb-2">
                <button
                  data-testid={`wiki-section-${section}`}
                  onClick={() => setOpen({ ...open, [section]: !open[section] })}
                  className="w-full flex items-center gap-1.5 px-2 py-1.5 text-sm font-semibold hover:bg-surface rounded"
                >
                  {open[section] ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  {SECTION_LABELS[section] || section}
                  <span className="ml-auto text-xs font-normal text-muted">{pages.length}</span>
                </button>
                {open[section] &&
                  pages.map((p) => (
                    <button
                      key={p.path}
                      data-testid={`wiki-page-link-${p.slug}`}
                      onClick={() => openPage(p.path)}
                      className={`w-full text-left px-3 py-1.5 ml-3 text-sm truncate rounded transition-colors block ${
                        activePath === p.path ? "bg-primary/10 text-primary font-medium" : "text-zinc-600 hover:bg-surface"
                      }`}
                      style={{ width: "calc(100% - 12px)" }}
                    >
                      {p.title}
                    </button>
                  ))}
              </div>
            ))
          )}
        </Card>

        {/* 页面内容 */}
        <Card className="col-span-8 xl:col-span-9 p-8 min-h-[400px]" data-testid="wiki-content">
          {pageLoading ? (
            <div className="flex justify-center py-20">
              <Spinner className="w-7 h-7" />
            </div>
          ) : !page ? (
            <EmptyState title="选择左侧页面开始阅读" sub="知识页面由 LLM Pipeline 自动创建和更新" />
          ) : (
            <div>
              <div className="flex flex-wrap gap-1.5 mb-4 items-center">
                {page.meta.category && <Badge className="!bg-primary/10 !text-primary !border-primary/20">{page.meta.category}</Badge>}
                {(Array.isArray(page.meta.tags) ? page.meta.tags : []).slice(0, 8).map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
                <span className="ml-auto flex items-center gap-3">
                  {page.meta.updated && <span className="text-xs text-muted">更新于 {page.meta.updated}</span>}
                  {!editing && (
                    <Button variant="outline" className="!px-2.5 !py-1 text-xs" onClick={startEdit} data-testid="edit-page-btn">
                      <Pencil className="w-3.5 h-3.5" /> 编辑
                    </Button>
                  )}
                </span>
              </div>
              {editing ? (
                <div data-testid="wiki-editor">
                  <Textarea
                    data-testid="wiki-edit-textarea"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={24}
                    className="font-mono !text-xs leading-relaxed"
                  />
                  <div className="flex gap-2 mt-3">
                    <Button onClick={saveEdit} disabled={saving} data-testid="save-page-btn">
                      {saving ? "保存中…" : "保存"}
                    </Button>
                    <Button variant="outline" onClick={() => setEditing(false)} data-testid="cancel-edit-btn">
                      取消
                    </Button>
                  </div>
                  <p className="text-xs text-muted mt-2">仅编辑正文，frontmatter 元数据会自动保留，`updated` 日期自动更新。</p>
                </div>
              ) : (
                <article className="prose prose-zinc prose-loom max-w-none prose-headings:tracking-tight">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) =>
                        href && href.startsWith("wikilink:") ? (
                          <button
                            onClick={() => openPage(href.slice("wikilink:".length))}
                            className="text-primary font-medium hover:underline inline"
                          >
                            {children}
                          </button>
                        ) : (
                          <a href={href} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        ),
                    }}
                  >
                    {processedBody}
                  </ReactMarkdown>
                </article>
              )}
              {page.backlinks.length > 0 && (
                <div className="mt-10 pt-6 border-t border-line">
                  <div className="label-xs mb-3 flex items-center gap-1.5">
                    <Link2 className="w-3.5 h-3.5" /> 反向链接
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {page.backlinks.map((b) => (
                      <button
                        key={b.path}
                        onClick={() => openPage(b.path)}
                        className="px-3 py-1.5 text-sm border border-line rounded-md hover:border-primary hover:text-primary transition-colors"
                        data-testid={`backlink-${b.path}`}
                      >
                        {b.title}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
