import React, { useEffect, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { useNavigate } from "react-router-dom";
import { X } from "lucide-react";
import api from "../lib/api";
import { Badge, Button, Card, PageHeader, Spinner } from "../components/ui";

const GROUP_COLORS = {
  ideas: "#002FA7",
  people: "#16A34A",
  "mental-models": "#D97706",
  projects: "#DC2626",
  daily: "#71717A",
  code: "#0891B2",
};
const GROUP_LABELS = {
  ideas: "概念",
  people: "人物",
  "mental-models": "思维模型",
  projects: "项目",
  daily: "周摘要",
  code: "技术文档",
};

export default function GraphPage() {
  const [graph, setGraph] = useState(null);
  const [selected, setSelected] = useState(null);
  const [dims, setDims] = useState({ w: 800, h: 600 });
  const containerRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/wiki/graph").then((r) => setGraph(r.data)).catch(() => setGraph({ nodes: [], links: [] }));
  }, []);

  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        setDims({
          w: containerRef.current.clientWidth,
          h: Math.max(window.innerHeight - 320, 420),
        });
      }
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [graph]);

  return (
    <div data-testid="graph-page">
      <PageHeader title="知识图谱" sub={graph ? `${graph.nodes.length} 个节点 · ${graph.links.length} 条关联` : "加载中…"}>
        <div className="flex gap-3 flex-wrap">
          {Object.entries(GROUP_COLORS).map(([g, c]) => (
            <span key={g} className="flex items-center gap-1.5 text-xs text-muted">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: c }} />
              {GROUP_LABELS[g]}
            </span>
          ))}
        </div>
      </PageHeader>

      <div className="relative">
        <Card className="overflow-hidden" data-testid="graph-canvas">
          <div ref={containerRef}>
            {!graph ? (
              <div className="flex justify-center items-center" style={{ height: 480 }}>
                <Spinner className="w-8 h-8" />
              </div>
            ) : graph.nodes.length === 0 ? (
              <div className="flex flex-col justify-center items-center text-muted" style={{ height: 480 }}>
                <div className="text-lg font-semibold text-zinc-500">图谱为空</div>
                <div className="text-sm mt-1">运行 Pipeline 编译知识后，概念关联网络会出现在这里</div>
              </div>
            ) : (
              <ForceGraph2D
                width={dims.w}
                height={dims.h}
                graphData={graph}
                backgroundColor="#FFFFFF"
                nodeRelSize={4}
                nodeVal={(n) => n.val}
                nodeColor={(n) => GROUP_COLORS[n.group] || "#71717A"}
                linkColor={() => "rgba(9,9,11,0.12)"}
                linkWidth={1}
                cooldownTicks={120}
                onNodeClick={(n) => setSelected(n)}
                nodeCanvasObjectMode={() => "after"}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  if (globalScale < 1.2 && node.val < 3) return;
                  const label = node.label || node.id;
                  const fontSize = Math.max(11 / globalScale, 2.5);
                  ctx.font = `500 ${fontSize}px 'IBM Plex Sans', 'Noto Sans SC', sans-serif`;
                  ctx.textAlign = "center";
                  ctx.textBaseline = "top";
                  ctx.fillStyle = "rgba(9,9,11,0.75)";
                  ctx.fillText(label.slice(0, 24), node.x, node.y + 5);
                }}
              />
            )}
          </div>
        </Card>

        {/* 节点详情侧滑面板 */}
        {selected && (
          <Card
            className="absolute top-4 right-4 w-80 p-5 shadow-lg bg-white/95 backdrop-blur-xl"
            data-testid="graph-detail-panel"
          >
            <div className="flex items-start justify-between mb-3">
              <Badge style={{ background: `${GROUP_COLORS[selected.group]}15`, color: GROUP_COLORS[selected.group] }}>
                {GROUP_LABELS[selected.group] || selected.group}
              </Badge>
              <button onClick={() => setSelected(null)} className="text-muted hover:text-ink" data-testid="close-detail-panel">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="text-lg font-bold tracking-tight mb-1">{selected.label}</div>
            <div className="text-xs font-mono text-muted mb-3">{selected.id}</div>
            {selected.category && <div className="text-sm text-zinc-600 mb-1">分类: {selected.category}</div>}
            {selected.updated && <div className="text-sm text-zinc-600 mb-1">更新: {selected.updated}</div>}
            <div className="text-sm text-zinc-600 mb-4">关联度: {selected.val}</div>
            <Button
              className="w-full"
              onClick={() => navigate(`/wiki?page=${encodeURIComponent(selected.id)}`)}
              data-testid="open-wiki-from-graph-btn"
            >
              打开知识页面
            </Button>
          </Card>
        )}
      </div>
    </div>
  );
}
