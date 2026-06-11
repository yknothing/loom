"""Wiki routes — tree / page / search / knowledge graph / lint."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import loom_bridge as bridge
from auth import get_current_user

router = APIRouter(prefix="/wiki", tags=["wiki"])


class PageEditBody(BaseModel):
    path: str = Field(min_length=1)
    body: str = Field(min_length=1)


@router.put("/page")
async def edit_page(payload: PageEditBody, user: dict = Depends(get_current_user)):
    ok = await asyncio.to_thread(
        bridge.update_wiki_page, payload.path, payload.body, user["email"])
    if not ok:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"ok": True}


@router.get("/tree")
async def tree(user: dict = Depends(get_current_user)):
    return await asyncio.to_thread(bridge.wiki_tree)


@router.get("/page")
async def page(path: str, user: dict = Depends(get_current_user)):
    result = await asyncio.to_thread(bridge.read_wiki_page, path)
    if result is None:
        raise HTTPException(status_code=404, detail="页面不存在")
    return result


@router.get("/search")
async def search(q: str, scope: str = "wiki",
                 user: dict = Depends(get_current_user)):
    if not q or len(q.strip()) < 1:
        return []
    if scope not in ("wiki", "raw", "all"):
        scope = "wiki"
    return await asyncio.to_thread(bridge.search_pages, q.strip(), scope)


@router.get("/graph")
async def graph(user: dict = Depends(get_current_user)):
    return await asyncio.to_thread(bridge.build_graph)


@router.get("/lint")
async def lint(user: dict = Depends(get_current_user)):
    return await asyncio.to_thread(bridge.lint_report)
