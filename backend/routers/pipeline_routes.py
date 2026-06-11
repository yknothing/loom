"""Dashboard / tasks / pipeline-runner routes."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import loom_bridge as bridge
from auth import get_current_user
from db import db

router = APIRouter(tags=["pipeline"])

# ── In-process pipeline runner state ──
STATE = {
    "running": False, "processed": 0, "succeeded": 0, "failed": 0,
    "max_tasks": 0, "provider": None, "model": "", "current": None,
    "started_at": None, "finished_at": None, "events": [],
}

FETCH_STATE = {"running": False, "started_at": None, "result": None}


def _push_event(ev: dict):
    ev["time"] = datetime.now(timezone.utc).isoformat()
    STATE["events"].insert(0, ev)
    del STATE["events"][30:]


async def _runner(max_tasks: int, provider: str, model: str,
                  two_stage: bool, delay: float):
    try:
        await asyncio.to_thread(bridge.reset_stuck)
        while STATE["running"] and STATE["processed"] < max_tasks:
            res = await asyncio.to_thread(
                bridge.process_one, provider, model, two_stage)
            if res is None:
                _push_event({"type": "info", "message": "队列已空"})
                break
            STATE["processed"] += 1
            if res["ok"]:
                STATE["succeeded"] += 1
                _push_event({"type": "success", "message": res["title"],
                             "detail": f"更新页面: {', '.join(res['pages'][:4])}",
                             "tokens": res.get("tokens", 0)})
            else:
                STATE["failed"] += 1
                _push_event({"type": "error", "message": res["filename"],
                             "detail": res.get("error", "")})
            STATE["current"] = None
            if STATE["running"] and STATE["processed"] < max_tasks and delay > 0:
                await asyncio.sleep(delay)
        await asyncio.to_thread(bridge.rebuild_wiki_index)
    except Exception as e:  # noqa: BLE001
        _push_event({"type": "error", "message": "Pipeline 运行异常", "detail": str(e)[:200]})
    finally:
        STATE["running"] = False
        STATE["finished_at"] = datetime.now(timezone.utc).isoformat()


class RunBody(BaseModel):
    max_tasks: int = Field(default=10, ge=1, le=200)
    provider: str | None = None
    model: str = ""
    two_stage: bool | None = None
    delay: float = Field(default=1.0, ge=0, le=30)


async def _pipeline_settings() -> dict:
    doc = await db.settings.find_one({"_id": "pipeline"}) or {}
    return {
        "provider": doc.get("provider"),
        "model": doc.get("model", ""),
        "two_stage": doc.get("two_stage", True),
    }


@router.get("/dashboard")
async def dashboard(user: dict = Depends(get_current_user)):
    stats, overview, recent, activity, categories, review = await asyncio.gather(
        asyncio.to_thread(bridge.queue_stats),
        asyncio.to_thread(bridge.wiki_overview),
        asyncio.to_thread(bridge.recent_results, 8),
        asyncio.to_thread(bridge.daily_activity, 14),
        asyncio.to_thread(bridge.category_distribution),
        asyncio.to_thread(bridge.review_stats),
    )
    review_pending = sum(v.get("pending", 0) for v in review.values())
    return {
        "queue": stats,
        "wiki": overview,
        "recent_results": recent,
        "daily_activity": activity,
        "categories": categories,
        "review_pending": review_pending,
        "pipeline": {k: v for k, v in STATE.items()},
    }


@router.get("/tasks")
async def tasks(status: str = None, search: str = None,
                limit: int = 50, offset: int = 0,
                user: dict = Depends(get_current_user)):
    return await asyncio.to_thread(bridge.list_tasks, status, search,
                                   min(limit, 200), offset)


@router.post("/tasks/{task_id}/retry")
async def retry(task_id: int, user: dict = Depends(get_current_user)):
    ok = await asyncio.to_thread(bridge.retry_task, task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="该任务无法重试（仅 failed/rejected 可重试）")
    return {"ok": True}


@router.post("/tasks/retry-failed")
async def retry_failed(user: dict = Depends(get_current_user)):
    n = await asyncio.to_thread(bridge.retry_all_failed)
    return {"ok": True, "count": n}


@router.post("/tasks/enqueue-raw")
async def enqueue_raw(user: dict = Depends(get_current_user)):
    return await asyncio.to_thread(bridge.enqueue_all_raw)


@router.post("/pipeline/run")
async def pipeline_run(body: RunBody, user: dict = Depends(get_current_user)):
    if STATE["running"]:
        raise HTTPException(status_code=409, detail="Pipeline 正在运行中")
    settings = await _pipeline_settings()
    provider = body.provider or settings["provider"]
    model = body.model or settings["model"]
    two_stage = settings["two_stage"] if body.two_stage is None else body.two_stage
    STATE.update({
        "running": True, "processed": 0, "succeeded": 0, "failed": 0,
        "max_tasks": body.max_tasks, "provider": provider, "model": model,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "current": None,
    })
    _push_event({"type": "info",
                 "message": f"Pipeline 启动 (最多 {body.max_tasks} 个任务)"})
    asyncio.create_task(_runner(body.max_tasks, provider, model, two_stage, body.delay))
    return {"ok": True}


@router.post("/pipeline/stop")
async def pipeline_stop(user: dict = Depends(get_current_user)):
    STATE["running"] = False
    _push_event({"type": "info", "message": "已请求停止 Pipeline"})
    return {"ok": True}


@router.get("/pipeline/status")
async def pipeline_status(user: dict = Depends(get_current_user)):
    stats = await asyncio.to_thread(bridge.queue_stats)
    return {"state": STATE, "queue": stats, "rss_fetch": FETCH_STATE}
