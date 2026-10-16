"""对话化 + 富媒体卡片 + 子任务 + 记忆/资料库/定时。全部走 MockEngine。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os

import httpx

os.environ["MUSE_ENGINE"] = "mock"


@contextlib.asynccontextmanager
async def running(tmp_path):
    from muse.config import Settings
    from muse.main import create_app

    cfg = Settings(db_path=tmp_path / "t.sqlite3", workspace=tmp_path / "ws", engine="mock")
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def wait_status(c, task_id, status, timeout=5):
    for _ in range(int(timeout / 0.05)):
        d = (await c.get(f"/api/tasks/{task_id}")).json()
        if d["task"]["status"] == status:
            return d
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached {status}, last {d['task']['status']}")


async def kinds(c, tid):
    return [e["kind"] for e in (await c.get(f"/api/tasks/{tid}/events")).json()]


async def test_cards_form_subtask_and_chat(tmp_path):
  async with running(tmp_path) as c:
    tid = (await c.post("/api/tasks", json={"title": "研究 600519", "prompt": "研究贵州茅台", "skill": "stock-research"})).json()["id"]
    d = await wait_status(c, tid, "waiting_input")

    # 第一轮：图表卡片 + 带表单的问题 + 记忆
    evs = (await c.get(f"/api/tasks/{tid}/events")).json()
    cards = [e["payload"] for e in evs if e["kind"] == "board/card"]
    assert any(cd["type"] == "chart" and cd["chart_type"] == "bar" for cd in cards)
    req = d["input_requests"][0]
    assert [f["name"] for f in req["fields"]] == ["horizon", "focus"]
    assert (await c.get("/api/memory")).json()[0]["key"] == "demo.last_code"
    # tool_call / tool_result 带 call_id，前端靠它配对
    tc = [e for e in evs if e["kind"] == "session/tool_call" and e["payload"]["name"] == "bash"][0]
    tr = [e for e in evs if e["kind"] == "session/tool_result" and e["payload"].get("call_id") == tc["payload"]["call_id"]]
    assert tr, "tool result must pair with its call"

    # 表单回答：JSON
    await c.post(f"/api/input-requests/{req['id']}/answer", json={"answer": json.dumps({"horizon": "6个月", "focus": "ROE"}, ensure_ascii=False)})
    d = await wait_status(c, tid, "waiting_approval")

    # 第二轮：表格卡片、子任务、资料库
    assert any(cd["type"] == "table" for cd in [e["payload"] for e in (await c.get(f"/api/tasks/{tid}/events")).json() if e["kind"] == "board/card"])
    children = (await c.get(f"/api/tasks/{tid}/children")).json()
    assert len(children) == 1 and children[0]["parent_id"] == tid
    lib = (await c.get("/api/library", params={"q": "600519"})).json()
    assert lib and "600519" in lib[0]["title"]

    # 子任务自己跑完（requires_review=False → done），父任务收到通知事件
    cid = children[0]["id"]
    await wait_status(c, cid, "done")
    for _ in range(40):
        if any(e["kind"] == "board/subtask" and e["payload"].get("status") == "done" for e in (await c.get(f"/api/tasks/{tid}/events")).json()):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("parent never got subtask completion")

    # 审批 → 文件卡片 + markdown 卡片 → review
    rev = d["action_reviews"][0]
    await c.post(f"/api/action-reviews/{rev['id']}/decide", json={"decision": "approved", "comment": ""})
    d = await wait_status(c, tid, "review")
    types = {cd["type"] for cd in [e["payload"] for e in (await c.get(f"/api/tasks/{tid}/events")).json() if e["kind"] == "board/card"]}
    assert {"chart", "table", "file", "markdown"} <= types
    assert any(r["kind"] == "report" for r in d["receipts"])

    # 聊天重开：review → running → 回一张表 → review
    r = await c.post(f"/api/tasks/{tid}/say", json={"text": "再补一张和行业均值的对比"})
    assert r.status_code == 200 and r.json()["status"] == "running"
    d = await wait_status(c, tid, "review")
    ks = await kinds(c, tid)
    assert "board/user_message" in ks
    assert sum(1 for cd in [e["payload"] for e in (await c.get(f"/api/tasks/{tid}/events")).json() if e["kind"] == "board/card"] if cd["type"] == "table") == 2


async def test_say_while_waiting_input_answers_it(tmp_path):
  async with running(tmp_path) as c:
    tid = (await c.post("/api/tasks", json={"title": "t", "prompt": "p"})).json()["id"]
    await wait_status(c, tid, "waiting_input")
    r = await c.post(f"/api/tasks/{tid}/say", json={"text": "保守"})
    assert r.json()["status"] == "running"
    d = await wait_status(c, tid, "waiting_approval")
    assert d["input_requests"][0]["answer"] == "保守"


async def test_say_before_dispatch_is_delivered_after_start(tmp_path):
  async with running(tmp_path) as c:
    tid = (await c.post("/api/tasks", json={"title": "t", "prompt": "p", "dispatch": False})).json()["id"]
    r = await c.post(f"/api/tasks/{tid}/say", json={"text": "顺便看下股东变化"})
    assert r.json()["status"] == "queued"
    await wait_status(c, tid, "waiting_input")
    assert "board/user_message" in await kinds(c, tid)


async def test_schedule_creates_task_when_due(tmp_path):
  async with running(tmp_path) as c:
    r = await c.post("/api/schedules", json={"title": "盘后盯盘", "prompt": "看涨停板", "every": "30m"})
    assert r.status_code == 201
    sch = r.json()
    assert sch["enabled"] and sch["next_run_at"]
    # 把 next_run_at 拨到过去，等调度循环（测试里直接调内部方法，不等 30 秒）
    from muse.models import Schedule
    from datetime import datetime, timedelta, timezone
    store = c._transport.app.state.board.store  # type: ignore[attr-defined]
    s = [x for x in store.schedule_list() if x.id == sch["id"]][0]
    s.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.schedule_update(s)
    runner = c._transport.app.state.runner  # type: ignore[attr-defined]
    runner._sched_task.cancel()
    loop = asyncio.ensure_future(runner._schedule_loop())
    await asyncio.sleep(0.3)
    loop.cancel()
    tasks = (await c.get("/api/tasks")).json()
    assert any(t["schedule_id"] == sch["id"] for t in tasks)
    assert (await c.post(f"/api/schedules/{sch['id']}/toggle")).json()["enabled"] is False
    assert (await c.post("/api/schedules", json={"title": "x", "prompt": "y", "every": "sometimes"})).status_code == 400


def test_next_run_parser():
    from datetime import datetime, timezone
    from muse.board import next_run
    base = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert (next_run("30m", base) - base).total_seconds() == 1800
    assert (next_run("2h", base) - base).total_seconds() == 7200
    d = next_run("daily 09:30", base)
    assert d > base and d.astimezone().hour == 9 and d.astimezone().minute == 30
    w = next_run("weekdays 09:30", base)
    assert w.astimezone().weekday() < 5
