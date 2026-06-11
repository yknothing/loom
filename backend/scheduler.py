"""Scheduler — APScheduler-backed automation for RSS ingest and weekly digest.

Schedule config lives in MongoDB (settings._id == "schedule") and is
re-applied whenever it changes via PUT /api/schedule.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import loom_bridge as bridge
from db import db
from digest import render_digest_html, send_email
from routers.pipeline_routes import STATE, _push_event, _runner

logger = logging.getLogger("loom.scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")

DEFAULT_SCHEDULE = {
    "rss_enabled": False,
    "rss_hour": 6,            # daily, UTC hour
    "auto_pipeline": True,    # run pipeline right after RSS fetch
    "pipeline_max": 20,
    "digest_enabled": False,
    "digest_weekday": 0,      # 0 = Monday
    "digest_hour": 9,         # UTC hour
    "digest_recipients": [],  # empty → all registered users
    "last_digest_sent": None,
    "last_rss_run": None,
}


async def get_schedule() -> dict:
    doc = await db.settings.find_one({"_id": "schedule"}) or {}
    cfg = dict(DEFAULT_SCHEDULE)
    cfg.update({k: v for k, v in doc.items() if k in DEFAULT_SCHEDULE})
    return cfg


async def save_schedule(cfg: dict):
    await db.settings.update_one(
        {"_id": "schedule"},
        {"$set": {**cfg, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)


async def digest_recipients(cfg: dict) -> list:
    if cfg.get("digest_recipients"):
        return cfg["digest_recipients"]
    return [u["email"] async for u in db.users.find({}, {"email": 1})]


async def run_digest_send(recipients: list = None) -> dict:
    """Build and send the weekly digest. Shared by scheduler and API."""
    cfg = await get_schedule()
    to_list = recipients or await digest_recipients(cfg)
    if not to_list:
        return {"ok": False, "error": "没有可用的收件人"}
    data = await asyncio.to_thread(bridge.digest_data, 7)
    html = render_digest_html(data, os.environ.get("FRONTEND_URL", ""))
    subject = f"Loom 每周知识简报 · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    result = await asyncio.to_thread(send_email, to_list, subject, html)
    await db.settings.update_one(
        {"_id": "schedule"},
        {"$set": {"last_digest_sent": datetime.now(timezone.utc).isoformat()}},
        upsert=True)
    return {"ok": True, "recipients": to_list, "email_id": (result or {}).get("id")}


async def _rss_job():
    logger.info("scheduled RSS fetch starting")
    try:
        result = await asyncio.to_thread(bridge.run_rss_fetch)
        await db.settings.update_one(
            {"_id": "schedule"},
            {"$set": {"last_rss_run": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
        cfg = await get_schedule()
        added = result.get("enqueued", {}).get("added", 0)
        _push_event({"type": "info",
                     "message": f"定时 RSS 抓取完成，新增入队 {added} 篇"})
        if cfg["auto_pipeline"] and added > 0 and not STATE["running"]:
            pipe = await db.settings.find_one({"_id": "pipeline"}) or {}
            max_tasks = int(cfg.get("pipeline_max", 20))
            STATE.update({
                "running": True, "processed": 0, "succeeded": 0, "failed": 0,
                "max_tasks": max_tasks, "provider": pipe.get("provider"),
                "model": pipe.get("model", ""),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None, "current": None,
            })
            _push_event({"type": "info", "message": f"定时 Pipeline 启动 (最多 {max_tasks} 个任务)"})
            asyncio.create_task(_runner(
                max_tasks, pipe.get("provider"), pipe.get("model", ""),
                pipe.get("two_stage", True), 1.0))
    except Exception as e:  # noqa: BLE001
        logger.error("scheduled RSS job failed: %s", e)
        _push_event({"type": "error", "message": "定时 RSS 抓取失败", "detail": str(e)[:200]})


async def _digest_job():
    logger.info("scheduled weekly digest starting")
    try:
        result = await run_digest_send()
        if result.get("ok"):
            _push_event({"type": "success",
                         "message": f"每周简报已发送给 {len(result['recipients'])} 位成员"})
    except Exception as e:  # noqa: BLE001
        logger.error("scheduled digest failed: %s", e)
        _push_event({"type": "error", "message": "每周简报发送失败", "detail": str(e)[:200]})


def apply_schedule(cfg: dict):
    """(Re)register jobs based on config. Idempotent."""
    for job_id in ("rss_job", "digest_job"):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    if cfg.get("rss_enabled"):
        scheduler.add_job(
            _rss_job, CronTrigger(hour=int(cfg["rss_hour"]), minute=0),
            id="rss_job", replace_existing=True)
    if cfg.get("digest_enabled"):
        scheduler.add_job(
            _digest_job,
            CronTrigger(day_of_week=int(cfg["digest_weekday"]),
                        hour=int(cfg["digest_hour"]), minute=0),
            id="digest_job", replace_existing=True)


def job_status() -> dict:
    out = {}
    for job_id in ("rss_job", "digest_job"):
        job = scheduler.get_job(job_id)
        out[job_id] = (job.next_run_time.isoformat()
                       if job and job.next_run_time else None)
    return out


async def init_scheduler():
    cfg = await get_schedule()
    apply_schedule(cfg)
    if not scheduler.running:
        scheduler.start()
