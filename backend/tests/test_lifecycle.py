"""用 MockEngine 走完整条任务生命周期：派发 → 挂起等输入 → 回答 → 挂起等审批 → 批准 → 回执 → review → 验收。"""
from __future__ import annotations

import asyncio
import contextlib
import os

import httpx
import pytest

os.environ["MUSE_ENGINE"] = "mock"





@contextlib.asynccontextmanager
async def running(tmp_path):
    """在同一个 task 里进出 lifespan，避开 anyio cancel scope 跨任务的限制。"""
    from muse.config import Settings
    from muse.main import create_app

    cfg = Settings(db_path=tmp_path / "t.sqlite3", workspace=tmp_path / "ws", engine="mock")
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def wait_status(c: httpx.AsyncClient, task_id: str, status: str, timeout: float = 5) -> dict:
    for _ in range(int(timeout / 0.05)):
        d = (await c.get(f"/api/tasks/{task_id}")).json()
        if d["task"]["status"] == status:
            return d
        await asyncio.sleep(0.05)
    raise AssertionError(f"task never reached {status}, last: {d['task']['status']}")


async def test_full_lifecycle(tmp_path):
  async with running(tmp_path) as client:
    r = await client.post("/api/tasks", json={"title": "研究 600519", "prompt": "研究贵州茅台", "skill": "stock-research"})
    assert r.status_code == 201
    tid = r.json()["id"]

    # 第一轮：agent 发布计划，然后提问 → waiting_input
    d = await wait_status(client, tid, "waiting_input")
    assert len(d["task"]["plan"]) == 4
    assert len(d["input_requests"]) == 1 and d["input_requests"][0]["answer"] == ""

    # 人回答 → running → agent 申请审批 → waiting_approval
    r = await client.post(f"/api/input-requests/{d['input_requests'][0]['id']}/answer", json={"answer": "换备用源"})
    assert r.status_code == 200
    d = await wait_status(client, tid, "waiting_approval")
    assert d["input_requests"][0]["answer"] == "换备用源"
    assert d["action_reviews"][0]["action"] == "send_report"

    # 人批准 → running → 回执 → review
    r = await client.post(f"/api/action-reviews/{d['action_reviews'][0]['id']}/decide", json={"decision": "approved", "comment": "ok"})
    assert r.status_code == 200
    d = await wait_status(client, tid, "review")
    assert any(r["kind"] == "report" and r["path"] == "reports/600519.md" for r in d["receipts"])
    assert all(s["status"] == "done" for s in d["task"]["plan"])
    evs = (await client.get(f"/api/tasks/{tid}/events")).json()
    assert any("中性" in e["payload"].get("text", "") for e in evs if e["kind"] == "session/assistant")

    # 验收 → done
    r = await client.post(f"/api/tasks/{tid}/accept")
    assert r.json()["status"] == "done"

    # 时间线里既有看板事件也有会话事件
    evs = (await client.get(f"/api/tasks/{tid}/events")).json()
    kinds = {e["kind"] for e in evs}
    assert {"board/status", "board/plan", "board/input_requested", "board/approval_requested", "board/receipt",
            "session/assistant", "session/tool_call"} <= kinds


async def test_illegal_transition_rejected(tmp_path):
  async with running(tmp_path) as client:
    r = await client.post("/api/tasks", json={"title": "x", "prompt": "y", "dispatch": False})
    tid = r.json()["id"]
    assert (await client.post(f"/api/tasks/{tid}/accept")).status_code == 409   # backlog → done 不合法
    assert (await client.post(f"/api/tasks/{tid}/cancel")).json()["status"] == "cancelled"


async def test_restart_marks_running_as_interrupted(tmp_path):
    from muse.config import Settings
    from muse.main import create_app
    from muse.models import Status

    cfg = Settings(db_path=tmp_path / "t.sqlite3", workspace=tmp_path / "ws", engine="mock")
    app = create_app(cfg)
    t = app.state.board.store.create_task(title="t", prompt="p", status=Status.running, session_id="task_x")
    async with app.router.lifespan_context(app):
        assert app.state.board.task(t.id).status == Status.interrupted
