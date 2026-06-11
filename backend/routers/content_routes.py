"""Content routes — submit articles, review queue, RSS sources, settings, providers."""
import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import loom_bridge as bridge
from auth import get_current_user
from db import db
from routers.pipeline_routes import FETCH_STATE, STATE, _push_event, _runner

router = APIRouter(tags=["content"])


# ────────────── Submit ──────────────

class SubmitBody(BaseModel):
    type: str = Field(pattern="^(url|text)$")
    url: str = ""
    title: str = ""
    content: str = ""
    category: str = ""
    auto_process: bool = True


@router.post("/submit")
async def submit(body: SubmitBody, user: dict = Depends(get_current_user)):
    try:
        if body.type == "url":
            if not body.url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="请输入有效的 URL（http/https）")
            result = await asyncio.to_thread(bridge.submit_url, body.url, body.category)
        else:
            if not body.title.strip():
                raise HTTPException(status_code=400, detail="请输入标题")
            result = await asyncio.to_thread(
                bridge.submit_text, body.title, body.content, body.category)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"抓取 URL 失败: {str(e)[:150]}")

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "提交失败"))

    # Optionally process immediately (submitted tasks have priority 100)
    if body.auto_process and not STATE["running"]:
        settings = await db.settings.find_one({"_id": "pipeline"}) or {}
        STATE.update({
            "running": True, "processed": 0, "succeeded": 0, "failed": 0,
            "max_tasks": 1, "provider": settings.get("provider"),
            "model": settings.get("model", ""),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "current": None,
        })
        _push_event({"type": "info", "message": f"即时处理提交: {result['title'][:50]}"})
        asyncio.create_task(_runner(
            1, settings.get("provider"), settings.get("model", ""),
            settings.get("two_stage", True), 0))
        result["processing"] = True
    return result


# ────────────── Review queue ──────────────

class ResolveBody(BaseModel):
    resolution: str = Field(min_length=1, max_length=500)


@router.get("/review")
async def review(status: str = "pending", type: str = None,
                 user: dict = Depends(get_current_user)):
    items, stats = await asyncio.gather(
        asyncio.to_thread(bridge.review_list, status, type),
        asyncio.to_thread(bridge.review_stats),
    )
    return {"items": items, "stats": stats}


@router.post("/review/{item_id}/resolve")
async def resolve(item_id: str, body: ResolveBody,
                  user: dict = Depends(get_current_user)):
    try:
        await asyncio.to_thread(bridge.review_resolve, item_id, body.resolution)
    except ValueError:
        raise HTTPException(status_code=404, detail="审查项不存在")
    return {"ok": True}


# ────────────── RSS sources ──────────────

class FeedItem(BaseModel):
    name: str
    url: str
    category: str = "general"
    priority: str = "medium"


class FeedsBody(BaseModel):
    feeds: list[FeedItem]


@router.get("/sources")
async def sources(user: dict = Depends(get_current_user)):
    feeds = await asyncio.to_thread(bridge.get_feeds)
    return {"feeds": feeds, "fetch": FETCH_STATE}


@router.put("/sources")
async def save_sources(body: FeedsBody, user: dict = Depends(get_current_user)):
    await asyncio.to_thread(bridge.save_feeds, [f.model_dump() for f in body.feeds])
    return {"ok": True}


async def _fetch_job():
    try:
        result = await asyncio.to_thread(bridge.run_rss_fetch)
        FETCH_STATE["result"] = result
    except Exception as e:  # noqa: BLE001
        FETCH_STATE["result"] = {"exit_code": -1, "output": str(e)[:500]}
    finally:
        FETCH_STATE["running"] = False


@router.post("/sources/fetch")
async def fetch_sources(user: dict = Depends(get_current_user)):
    if FETCH_STATE["running"]:
        raise HTTPException(status_code=409, detail="RSS 抓取正在进行中")
    FETCH_STATE.update({
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
    })
    asyncio.create_task(_fetch_job())
    return {"ok": True}


# ────────────── Settings & providers ──────────────

class SettingsBody(BaseModel):
    provider: str | None = None
    model: str = ""
    two_stage: bool = True


@router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    doc = await db.settings.find_one({"_id": "pipeline"}) or {}
    providers = await asyncio.to_thread(bridge.providers_info)
    return {
        "provider": doc.get("provider") or providers["default"],
        "model": doc.get("model", ""),
        "two_stage": doc.get("two_stage", True),
        **providers,
    }


@router.put("/settings")
async def put_settings(body: SettingsBody, user: dict = Depends(get_current_user)):
    await db.settings.update_one(
        {"_id": "pipeline"},
        {"$set": {"provider": body.provider, "model": body.model,
                  "two_stage": body.two_stage,
                  "updated_by": user["email"],
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)
    return {"ok": True}
