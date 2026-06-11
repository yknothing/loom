"""Automation routes — schedule config & weekly digest (preview / send)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

import loom_bridge as bridge
import os
import scheduler as sched
from auth import get_current_user
from digest import render_digest_html

router = APIRouter(tags=["automation"])


class ScheduleBody(BaseModel):
    rss_enabled: bool = False
    rss_hour: int = Field(default=6, ge=0, le=23)
    auto_pipeline: bool = True
    pipeline_max: int = Field(default=20, ge=1, le=200)
    digest_enabled: bool = False
    digest_weekday: int = Field(default=0, ge=0, le=6)
    digest_hour: int = Field(default=9, ge=0, le=23)
    digest_recipients: list[EmailStr] = []


@router.get("/schedule")
async def get_schedule(user: dict = Depends(get_current_user)):
    cfg = await sched.get_schedule()
    cfg["jobs"] = sched.job_status()
    cfg["email_configured"] = bool(os.environ.get("RESEND_API_KEY"))
    return cfg


@router.put("/schedule")
async def put_schedule(body: ScheduleBody, user: dict = Depends(get_current_user)):
    cfg = body.model_dump()
    await sched.save_schedule(cfg)
    sched.apply_schedule(cfg)
    return {"ok": True, "jobs": sched.job_status()}


@router.get("/digest/preview")
async def digest_preview(user: dict = Depends(get_current_user)):
    data = await asyncio.to_thread(bridge.digest_data, 7)
    html = render_digest_html(data, os.environ.get("FRONTEND_URL", ""))
    return {"html": html, "data": data}


class SendBody(BaseModel):
    recipients: list[EmailStr] = []


@router.post("/digest/send")
async def digest_send(body: SendBody, user: dict = Depends(get_current_user)):
    try:
        result = await sched.run_digest_send(body.recipients or None)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 — surface provider errors cleanly
        raise HTTPException(status_code=502, detail=f"邮件发送失败: {str(e)[:200]}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "发送失败"))
    return result
