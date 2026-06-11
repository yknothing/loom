import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search as SearchIcon } from "lucide-react";
import api, { errText } from "../lib/api";
import { toast } from "sonner";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Select, Spinner } from "../components/ui";

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("wiki");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const search = async (e) => {
    e && e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    try {
      const { data } = await api.get("/wiki/search", { params: { q: q.trim(), scope } });
      setResults(data);
    } catch (err) {
      toast.error(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="search-page">
      <PageHeader title="全文搜索" sub="跨知识库与原始资料的关键词检索" />
      <form onSubmit={search} className="flex gap-3 mb-8 max-w-2xl">
        <Input
          data-testid="search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索概念、人物、标签、正文…"
          autoFocus
        />
        <Select value={scope} onChange={(e) => setScope(e.target.value)} className="!w-36" data-testid="search-scope-select">
          <option value="wiki">知识库</option>
          <option value="raw">原始资料</option>
          <option value="all">全部</option>
        </Select>
        <Button type="submit" disabled={busy} data-testid="search-submit-btn">
          {busy ? <Spinner className="w-4 h-4 !text-white" /> : <SearchIcon className="w-4 h-4" />}
          搜索
        </Button>
      </form>

      {results && (
        <div className="space-y-3 max-w-3xl" data-testid="search-results">
          <div className="text-sm text-muted">{results.length} 条结果</div>
          {results.length === 0 && <EmptyState title="未找到匹配结果" sub="换个关键词试试，或扩大搜索范围" />}
          {results.map((r, i) => (
            <Card
              key={i}
              hover
              className="p-5 cursor-pointer"
              data-testid={`search-result-${i}`}
              onClick={() => r.source === "wiki" && navigate(`/wiki?page=${encodeURIComponent(r.path)}`)}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base font-semibold">{r.title}</span>
                <Badge className={r.source === "wiki" ? "!bg-primary/10 !text-primary !border-primary/20" : ""}>
                  {r.source === "wiki" ? "知识库" : "原始资料"}
                </Badge>
                <span className="text-xs text-muted ml-auto font-mono">{r.path}</span>
              </div>
              {r.snippet && <p className="text-sm text-zinc-600 line-clamp-2">…{r.snippet}…</p>}
              <div className="flex gap-1.5 mt-2 flex-wrap">
                {r.tags.map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
