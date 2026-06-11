"""
loom_bridge.py — Service layer bridging the FastAPI console to the Loom
pipeline library (scripts/ingest). All functions here are synchronous and
must be invoked from async routes via asyncio.to_thread.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import yaml

LOOM_ROOT = Path(os.environ.get("LOOM_ROOT", "/app"))
SCRIPTS_DIR = LOOM_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest.task_queue import TaskQueue  # noqa: E402
from ingest.worker import call_llm  # noqa: E402
from ingest import review_queue as rq  # noqa: E402
from ingest.wiki_writer import (  # noqa: E402
    write_ingest_result, append_log, rebuild_index, slugify,
)
from ingest.concept_merger import _parse_frontmatter_with_lists  # noqa: E402
from ingest.config import db_path, raw_dir, wiki_dir, config_dir  # noqa: E402
from ingest.providers import (  # noqa: E402
    list_providers as _list_providers, default_provider, get_provider,
)

WIKI_SECTIONS = ["ideas", "people", "mental-models", "projects", "daily", "code"]
RAW_SECTIONS = ["rss", "papers", "web", "code", "journal"]

# Cost reference (USD per 1M tokens) — rough blended estimates for display only
COST_PER_M_INPUT = 0.435
COST_PER_M_OUTPUT = 0.87


def _q() -> TaskQueue:
    return TaskQueue(str(db_path()))


def ensure_dirs():
    for s in WIKI_SECTIONS:
        (wiki_dir() / s).mkdir(parents=True, exist_ok=True)
    for s in RAW_SECTIONS:
        (raw_dir() / s).mkdir(parents=True, exist_ok=True)
    (wiki_dir() / "log.md").touch(exist_ok=True)


# ────────────────────────────── Queue / tasks ──────────────────────────────

def queue_stats() -> dict:
    q = _q()
    try:
        s = q.stats()
        s["cost_usd"] = round(
            s["input_tokens"] / 1e6 * COST_PER_M_INPUT
            + s["output_tokens"] / 1e6 * COST_PER_M_OUTPUT, 4)
        return s
    finally:
        q.close()


def list_tasks(status: str = None, search: str = None,
               limit: int = 50, offset: int = 0) -> dict:
    q = _q()
    try:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if search:
            where.append("filepath LIKE ?")
            params.append(f"%{search}%")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = q._conn.execute(
            f"SELECT COUNT(*) c FROM ingest_tasks {clause}", params).fetchone()["c"]
        rows = q._conn.execute(
            f"""SELECT id, filepath, status, priority, retry_count, error_message,
                       llm_model, input_tokens, output_tokens, stage,
                       created_at, started_at, completed_at
                FROM ingest_tasks {clause}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset]).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["filename"] = Path(d["filepath"]).name
            items.append(d)
        return {"total": total, "items": items}
    finally:
        q.close()


def retry_task(task_id: int) -> bool:
    q = _q()
    try:
        cur = q._conn.execute(
            """UPDATE ingest_tasks
               SET status='pending', retry_count=0, error_message=NULL,
                   started_at=NULL, completed_at=NULL
               WHERE id = ? AND status IN ('failed', 'rejected')""",
            (task_id,))
        q._conn.commit()
        return cur.rowcount > 0
    finally:
        q.close()


def retry_all_failed() -> int:
    q = _q()
    try:
        cur = q._conn.execute(
            """UPDATE ingest_tasks
               SET status='pending', retry_count=0, error_message=NULL,
                   started_at=NULL, completed_at=NULL
               WHERE status = 'failed'""")
        q._conn.commit()
        return cur.rowcount
    finally:
        q.close()


def reset_stuck():
    q = _q()
    try:
        q.reset_stuck_tasks()
    finally:
        q.close()


def recent_results(limit: int = 10) -> list:
    q = _q()
    try:
        rows = q._conn.execute(
            """SELECT r.id, r.task_id, r.raw_filepath, r.title_zh, r.title_en,
                      r.summary_zh, r.category, r.tags, r.quality_score,
                      r.sentiment, r.created_at, r.merge_action,
                      t.input_tokens, t.output_tokens, t.llm_model, t.stage
               FROM ingest_results r
               LEFT JOIN ingest_tasks t ON t.id = r.task_id
               ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            d["filename"] = Path(d["raw_filepath"]).name
            out.append(d)
        return out
    finally:
        q.close()


def daily_activity(days: int = 14) -> list:
    q = _q()
    try:
        rows = q._conn.execute(
            """SELECT date(completed_at) d,
                      SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) done,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                      COALESCE(SUM(input_tokens + output_tokens), 0) tokens
               FROM ingest_tasks
               WHERE completed_at IS NOT NULL
                 AND date(completed_at) >= date('now', ?)
               GROUP BY date(completed_at) ORDER BY d""",
            (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        q.close()


def category_distribution() -> list:
    q = _q()
    try:
        rows = q._conn.execute(
            """SELECT COALESCE(category, 'other') category, COUNT(*) count
               FROM ingest_results GROUP BY category ORDER BY count DESC""").fetchall()
        return [dict(r) for r in rows]
    finally:
        q.close()


def enqueue_all_raw() -> dict:
    """Scan raw/ for .md files and enqueue any not yet in the queue."""
    files = []
    for sub in RAW_SECTIONS:
        d = raw_dir() / sub
        if d.exists():
            files += [str(p) for p in sorted(d.glob("*.md"))]
    q = _q()
    try:
        added, skipped = q.init_queue(files)
        return {"added": added, "skipped": skipped}
    finally:
        q.close()


# ────────────────────────────── Processing ──────────────────────────────

def process_one(provider: str = None, model: str = "",
                two_stage: bool = True, timeout: int = 180) -> dict:
    """Claim and process one pending task end-to-end. None if queue empty."""
    q = _q()
    try:
        task = q.claim_next()
        if not task:
            return None
        prov_name = provider or default_provider()
        result = call_llm(
            filepath=task["filepath"], model=model, provider=prov_name,
            two_stage=two_stage, timeout=timeout,
        )
        used_model = model or get_provider(prov_name).get("model", prov_name)
        if result["success"]:
            q.complete_task(
                task_id=task["id"], result=result["result"], model=used_model,
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                stage=result.get("stage", "single"),
                segment_count=result.get("segment_count", 1),
                segments_json=result.get("segments_json"),
            )
            updated = write_ingest_result(result["result"])
            append_log(
                source=task["filepath"],
                title=result["result"].get("title_en", ""),
                updated_pages=updated,
                tokens_in=result["input_tokens"],
                tokens_out=result["output_tokens"],
                model=used_model,
            )
            return {
                "ok": True, "task_id": task["id"],
                "title": result["result"].get("title_zh")
                or result["result"].get("title_en", ""),
                "filename": Path(task["filepath"]).name,
                "pages": updated,
                "tokens": result["input_tokens"] + result["output_tokens"],
            }
        q.fail_task(task["id"], result["error"])
        return {
            "ok": False, "task_id": task["id"],
            "filename": Path(task["filepath"]).name,
            "error": (result.get("error") or "")[:200],
        }
    finally:
        q.close()


def rebuild_wiki_index():
    rebuild_index()


# ────────────────────────────── Wiki ──────────────────────────────

def _read_page_file(p: Path):
    text = p.read_text(encoding="utf-8")
    meta = _parse_frontmatter_with_lists(text) if text.startswith("---") else {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
    return meta, body


def wiki_overview() -> dict:
    counts = {}
    for s in WIKI_SECTIONS:
        d = wiki_dir() / s
        counts[s] = len(list(d.glob("*.md"))) if d.exists() else 0
    raw_counts = {}
    for s in RAW_SECTIONS:
        d = raw_dir() / s
        raw_counts[s] = len(list(d.glob("*.md"))) if d.exists() else 0
    return {
        "wiki_pages": sum(counts.values()),
        "wiki_by_section": counts,
        "raw_files": sum(raw_counts.values()),
        "raw_by_section": raw_counts,
    }


def wiki_tree() -> dict:
    tree = {}
    for s in WIKI_SECTIONS:
        d = wiki_dir() / s
        pages = []
        if d.exists():
            for p in d.glob("*.md"):
                meta, _ = _read_page_file(p)
                pages.append({
                    "path": f"{s}/{p.stem}",
                    "slug": p.stem,
                    "title": meta.get("title") or meta.get("name") or p.stem,
                    "updated": str(meta.get("updated", "")),
                    "category": str(meta.get("category", "")),
                })
        pages.sort(key=lambda x: x["updated"], reverse=True)
        tree[s] = pages
    return tree


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _normalize_link(target: str) -> str:
    t = target.strip()
    if t.endswith(".md"):
        t = t[:-3]
    if t.startswith("wiki/"):
        t = t[5:]
    return t


def read_wiki_page(rel_path: str) -> dict:
    rel_path = rel_path.strip("/").replace("..", "")
    p = wiki_dir() / f"{rel_path}.md"
    if not p.exists():
        p = wiki_dir() / rel_path
        if not (p.exists() and p.suffix == ".md"):
            return None
        rel_path = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    meta, body = _read_page_file(p)
    # Backlinks: pages whose body links to this page
    backlinks = []
    for s in WIKI_SECTIONS:
        d = wiki_dir() / s
        if not d.exists():
            continue
        for other in d.glob("*.md"):
            other_rel = f"{s}/{other.stem}"
            if other_rel == rel_path:
                continue
            try:
                text = other.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in WIKILINK_RE.finditer(text):
                if _normalize_link(m.group(1)) == rel_path:
                    om, _ = _read_page_file(other)
                    backlinks.append({
                        "path": other_rel,
                        "title": om.get("title") or om.get("name") or other.stem,
                    })
                    break
    return {"path": rel_path, "meta": meta, "body": body, "backlinks": backlinks}


def search_pages(query: str, scope: str = "wiki", limit: int = 30) -> list:
    ql = query.lower()
    results = []

    def scan(base: Path, sections: list, source: str):
        for s in sections:
            d = base / s
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                try:
                    text = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                meta = _parse_frontmatter_with_lists(text) if text.startswith("---") else {}
                title = str(meta.get("title") or meta.get("name") or p.stem)
                tags = meta.get("tags", [])
                tags = tags if isinstance(tags, list) else [str(tags)]
                score = 0
                if ql in title.lower():
                    score += 10
                if any(ql in str(t).lower() for t in tags):
                    score += 5
                body_lower = text.lower()
                hits = body_lower.count(ql)
                score += min(hits, 5)
                if score == 0:
                    continue
                idx = body_lower.find(ql)
                snippet = ""
                if idx >= 0:
                    start = max(0, idx - 80)
                    snippet = text[start:idx + 120].replace("\n", " ").strip()
                results.append({
                    "source": source,
                    "path": f"{s}/{p.stem}",
                    "title": title,
                    "tags": [str(t) for t in tags][:6],
                    "snippet": snippet,
                    "score": score,
                    "updated": str(meta.get("updated") or meta.get("date") or ""),
                })

    if scope in ("wiki", "all"):
        scan(wiki_dir(), WIKI_SECTIONS, "wiki")
    if scope in ("raw", "all"):
        scan(raw_dir(), RAW_SECTIONS, "raw")
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def build_graph() -> dict:
    nodes = {}
    links = []
    for s in WIKI_SECTIONS:
        d = wiki_dir() / s
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            rel = f"{s}/{p.stem}"
            meta, body = _read_page_file(p)
            nodes[rel] = {
                "id": rel,
                "label": str(meta.get("title") or meta.get("name") or p.stem),
                "group": s,
                "category": str(meta.get("category", "")),
                "updated": str(meta.get("updated", "")),
                "val": 1,
                "_body": body,
                "_meta": meta,
            }
    for rel, node in nodes.items():
        for m in WIKILINK_RE.finditer(node["_body"]):
            target = _normalize_link(m.group(1))
            if target in nodes and target != rel:
                links.append({"source": rel, "target": target})
        related_people = node["_meta"].get("related_people", [])
        if isinstance(related_people, list):
            for rp in related_people:
                t = f"people/{slugify(str(rp))}"
                if t in nodes and t != rel:
                    links.append({"source": rel, "target": t})
    # dedupe links, weight nodes by degree
    seen = set()
    unique_links = []
    for l in links:
        key = (l["source"], l["target"])
        rkey = (l["target"], l["source"])
        if key in seen or rkey in seen:
            continue
        seen.add(key)
        unique_links.append(l)
        nodes[l["source"]]["val"] += 1
        nodes[l["target"]]["val"] += 1
    out_nodes = []
    for n in nodes.values():
        n.pop("_body", None)
        n.pop("_meta", None)
        out_nodes.append(n)
    return {"nodes": out_nodes, "links": unique_links}


def lint_report() -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "wiki-lint.py")],
            capture_output=True, text=True, timeout=120, cwd=str(LOOM_ROOT),
        )
        return {"exit_code": proc.returncode,
                "output": (proc.stdout + proc.stderr)[-8000:]}
    except Exception as e:
        return {"exit_code": -1, "output": str(e)}


# ────────────────────────────── Submission ──────────────────────────────

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _existing_url_hashes() -> set:
    hashes = set()
    for sub in RAW_SECTIONS:
        d = raw_dir() / sub
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            try:
                head = f.read_text(encoding="utf-8")[:600]
            except Exception:
                continue
            m = re.search(r"^url_hash:\s*(\S+)", head, re.M)
            if m:
                hashes.add(m.group(1))
    return hashes


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [l.strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def _save_raw(title: str, content: str, url: str, category: str,
              source: str) -> str:
    today = date.today().isoformat()
    slug = slugify(title)[:80] or "untitled"
    d = raw_dir() / "web"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{today}-{slug}.md"
    counter = 1
    while fp.exists():
        fp = d / f"{today}-{slug}-{counter}.md"
        counter += 1
    h = _url_hash(url or f"{title}-{datetime.now(timezone.utc).isoformat()}")
    fp.write_text(
        f"""---
source: {source}
url: {url or ''}
url_hash: {h}
date: {today}
fetched: {today}
category: {category or 'other'}
priority: high
---

# {title}

{content}
""", encoding="utf-8")
    return str(fp)


def submit_url(url: str, category: str = "") -> dict:
    h = _url_hash(url)
    if h in _existing_url_hashes():
        return {"ok": False, "error": "该 URL 已存在于知识库中（重复提交）"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LoomBot/2.0)"}
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=25)
    resp.raise_for_status()
    html = resp.text
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else url
    text = _strip_html(html)
    if len(text) < 200:
        return {"ok": False, "error": "无法从该 URL 提取足够的正文内容"}
    fp = _save_raw(title, text[:120000], url, category, "manual-url")
    return _enqueue_submitted(fp, title)


def submit_text(title: str, content: str, category: str = "") -> dict:
    if len(content.strip()) < 50:
        return {"ok": False, "error": "正文内容太短（至少 50 字符）"}
    fp = _save_raw(title.strip() or "Untitled", content.strip(), "", category,
                   "manual-text")
    return _enqueue_submitted(fp, title)


def _enqueue_submitted(filepath: str, title: str) -> dict:
    q = _q()
    try:
        q.init_queue([filepath], priority_fn=lambda fp: 100)
        row = q._conn.execute(
            "SELECT id FROM ingest_tasks WHERE filepath = ?", (filepath,)).fetchone()
        return {"ok": True, "filepath": filepath, "title": title,
                "task_id": row["id"] if row else None}
    finally:
        q.close()


# ────────────────────────────── RSS sources ──────────────────────────────

def get_feeds() -> list:
    p = config_dir() / "rss-feeds.yml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("feeds", [])


def save_feeds(feeds: list):
    p = config_dir() / "rss-feeds.yml"
    p.write_text(
        yaml.safe_dump({"feeds": feeds}, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def run_rss_fetch() -> dict:
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "rss-fetch.py"),
        "--config", str(config_dir() / "rss-feeds.yml"),
        "--raw-dir", str(raw_dir() / "rss"),
        "--timeout", "20",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                          cwd=str(LOOM_ROOT))
    enq = enqueue_all_raw()
    return {
        "exit_code": proc.returncode,
        "output": (proc.stdout + proc.stderr)[-6000:],
        "enqueued": enq,
    }


# ────────────────────────────── Review queue ──────────────────────────────

def review_list(status: str = "pending", item_type: str = None) -> list:
    path = rq.REVIEW_QUEUE_PATH
    if not path.exists():
        return []
    queue = json.loads(path.read_text(encoding="utf-8"))
    items = queue.get("items", [])
    if status:
        items = [i for i in items if i.get("status") == status]
    if item_type:
        items = [i for i in items if i.get("type") == item_type]
    items.sort(key=lambda i: i.get("created", ""), reverse=True)
    return items


def review_resolve(item_id: str, resolution: str):
    rq.mark_resolved(item_id, resolution)


def review_stats() -> dict:
    return rq.get_stats()


# ────────────────────────────── Digest ──────────────────────────────

def digest_data(days: int = 7) -> dict:
    """Aggregate the last N days of compiled knowledge for the weekly digest."""
    q = _q()
    try:
        rows = q._conn.execute(
            """SELECT title_zh, title_en, summary_zh, category,
                      quality_score, tags, created_at
               FROM ingest_results
               WHERE created_at >= datetime('now', ?)
               ORDER BY quality_score DESC, id DESC""",
            (f"-{days} days",)).fetchall()
        items = []
        cats = {}
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            cats[d.get("category") or "other"] = cats.get(d.get("category") or "other", 0) + 1
            items.append(d)
        tok = q._conn.execute(
            """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) t
               FROM ingest_tasks
               WHERE status = 'done' AND completed_at >= datetime('now', ?)""",
            (f"-{days} days",)).fetchone()["t"]
        review_pending = sum(v.get("pending", 0) for v in rq.get_stats().values())
        return {
            "items": items[:10],
            "total": len(items),
            "categories": cats,
            "tokens": tok,
            "review_pending": review_pending,
        }
    finally:
        q.close()


# ────────────────────────────── Task detail ──────────────────────────────

def get_task_detail(task_id: int) -> dict:
    q = _q()
    try:
        t = q._conn.execute(
            "SELECT * FROM ingest_tasks WHERE id = ?", (task_id,)).fetchone()
        if not t:
            return None
        task = dict(t)
        task["filename"] = Path(task["filepath"]).name
        r = q._conn.execute(
            """SELECT * FROM ingest_results WHERE task_id = ?
               ORDER BY id DESC LIMIT 1""", (task_id,)).fetchone()
        result = None
        if r:
            result = dict(r)
            for k in ("tags", "people", "orgs", "key_insights", "related_topics"):
                try:
                    result[k] = json.loads(result.get(k) or "[]")
                except Exception:
                    result[k] = []
            result["raw_llm_response"] = (result.get("raw_llm_response") or "")[:20000]
            result.pop("segments_json", None)
        return {"task": task, "result": result}
    finally:
        q.close()


# ────────────────────────────── Wiki editing ──────────────────────────────

def update_wiki_page(rel_path: str, body: str, editor: str = "") -> bool:
    """Overwrite a wiki page body, preserving frontmatter and bumping `updated`."""
    rel_path = rel_path.strip("/").replace("..", "")
    p = wiki_dir() / f"{rel_path}.md"
    if not p.exists():
        return False
    meta, _ = _read_page_file(p)
    meta["updated"] = date.today().isoformat()
    from ingest.wiki_writer import write_page
    write_page(p, meta, body.strip())
    # Append-only audit entry
    try:
        with open(wiki_dir() / "log.md", "a", encoding="utf-8") as f:
            f.write(f"\n## [{date.today().isoformat()}] manual-edit | {rel_path}\n"
                    f"编辑者: {editor}\n")
    except Exception:
        pass
    return True


# ────────────────────────────── Providers ──────────────────────────────

def providers_info() -> dict:
    return {"providers": _list_providers(), "default": default_provider()}
