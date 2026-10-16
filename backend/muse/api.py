"""REST + SSE。前端只和这一层说话。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .board import Board, IllegalTransition
from .models import AnswerBody, DecisionBody, Status, TaskCreate, TaskDetail, TaskUpdate
from .runner import Runner

log = logging.getLogger("muse.api")
router = APIRouter(prefix="/api")


def board(req: Request) -> Board:
    return req.app.state.board


def runner(req: Request) -> Runner:
    return req.app.state.runner


def _404(e: KeyError):
    raise HTTPException(404, f"not found: {e}")


# ---------- tasks ----------

@router.get("/tasks")
def list_tasks(b: Board = Depends(board)):
    return b.store.list_tasks()


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, b: Board = Depends(board), r: Runner = Depends(runner)):
    t = b.store.create_task(title=body.title, prompt=body.prompt, skill=body.skill, priority=body.priority,
                            requires_review=body.requires_review, max_retries=body.max_retries)
    b.bus.publish("task", t.model_dump(mode="json"))
    if body.dispatch:
        b.transition(t, Status.queued, note="created")
        r.wake()
    return t


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str, b: Board = Depends(board)):
    try:
        t = b.task(task_id)
    except KeyError as e:
        _404(e)
    return TaskDetail(task=t, input_requests=b.store.list_input_requests(task_id),
                      action_reviews=b.store.list_action_reviews(task_id), receipts=b.store.list_receipts(task_id))


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, b: Board = Depends(board)):
    try:
        t = b.task(task_id)
    except KeyError as e:
        _404(e)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    b.store.save_task(t)
    b.bus.publish("task", t.model_dump(mode="json"))
    return t


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, b: Board = Depends(board)):
    b.store.delete_task(task_id)
    b.bus.publish("task_deleted", {"id": task_id})


@router.get("/tasks/{task_id}/events")
def task_events(task_id: str, after: int = 0, limit: int = 500, b: Board = Depends(board)):
    return b.store.list_events(task_id, after, limit)


# ---------- 人侧动作 ----------

@router.post("/tasks/{task_id}/dispatch")
def dispatch(task_id: str, b: Board = Depends(board), r: Runner = Depends(runner)):
    """backlog / interrupted / failed / review(打回) → queued"""
    try:
        t = b.transition(b.task(task_id), Status.queued, note="dispatched by human")
    except KeyError as e:
        _404(e)
    except IllegalTransition as e:
        raise HTTPException(409, str(e))
    r.wake()
    return t


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, b: Board = Depends(board)):
    try:
        return b.transition(b.task(task_id), Status.cancelled, note="cancelled by human")
    except KeyError as e:
        _404(e)
    except IllegalTransition as e:
        raise HTTPException(409, str(e))


@router.post("/tasks/{task_id}/accept")
def accept(task_id: str, b: Board = Depends(board)):
    """review → done"""
    try:
        return b.transition(b.task(task_id), Status.done, note="accepted by human")
    except KeyError as e:
        _404(e)
    except IllegalTransition as e:
        raise HTTPException(409, str(e))


@router.post("/input-requests/{req_id}/answer")
async def answer(req_id: str, body: AnswerBody, r: Runner = Depends(runner)):
    try:
        return await r.answer(req_id, body.answer)
    except KeyError as e:
        _404(e)


@router.post("/action-reviews/{rev_id}/decide")
async def decide(rev_id: str, body: DecisionBody, r: Runner = Depends(runner)):
    try:
        return await r.decide(rev_id, body.decision, body.comment)
    except KeyError as e:
        _404(e)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- 产物 ----------

@router.get("/tasks/{task_id}/files/{path:path}")
def task_file(task_id: str, path: str, b: Board = Depends(board)):
    try:
        t = b.task(task_id)
    except KeyError as e:
        _404(e)
    root = Path(t.workspace or ".").resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(403, "outside workspace")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)


# ---------- SSE ----------

@router.get("/stream")
async def stream(req: Request, b: Board = Depends(board)):
    q = b.bus.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await req.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                    yield f"event: {msg['kind']}\ndata: {json.dumps(msg['data'], ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            b.bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/health")
def health(req: Request):
    eng = req.app.state.runner.engine
    return {"ok": True, "engine": type(eng).__name__, "alive": getattr(eng, "alive", True)}
