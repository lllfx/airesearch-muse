"""看板服务层：API、MCP 工具、runner 都通过它改状态，保证每次迁移都合法、都有事件、都推给前端。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .models import TRANSITIONS, ActionReview, FormField, InputRequest, LibraryItem, MemoryItem, PlanStep, Receipt, Schedule, Status, Task
from .store import Store

log = logging.getLogger("muse.board")


class IllegalTransition(Exception):
    pass


class EventBus:
    """进程内广播。SSE 订阅者各拿一个 queue；慢消费者丢消息而不是阻塞生产者。"""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, data: dict) -> None:
        msg = {"kind": kind, "data": data}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


class Board:
    def __init__(self, store: Store, bus: EventBus):
        self.store = store
        self.bus = bus

    # ---------- 读 ----------

    def task(self, task_id: str) -> Task:
        t = self.store.get_task(task_id)
        if not t:
            raise KeyError(task_id)
        return t

    # ---------- 状态迁移 ----------

    def transition(self, task: Task, to: Status, *, note: str = "", **fields) -> Task:
        if to not in TRANSITIONS[task.status]:
            raise IllegalTransition(f"{task.id}: {task.status.value} -> {to.value}")
        frm = task.status
        task.status = to
        for k, v in fields.items():
            setattr(task, k, v)
        now = datetime.now(timezone.utc)
        if to == Status.running and task.started_at is None:
            task.started_at = now
        if to.terminal:
            task.finished_at = now
        self.store.save_task(task)
        self._event(task.id, "board/status", {"from": frm.value, "to": to.value, "note": note})
        self.bus.publish("task", task.model_dump(mode="json"))
        return task

    def _event(self, task_id: str, kind: str, payload: dict) -> None:
        ev = self.store.append_event(task_id, kind, payload)
        self.bus.publish("event", ev.model_dump(mode="json"))

    def record_session_event(self, task_id: str, event: dict) -> None:
        """把 dsh 的会话事件挑有用的存进时间线。全量日志 dsh 自己有，这里只留给人看的部分。"""
        etype = event.get("type", "")
        data = event.get("data") or {}
        if etype == "assistant/message":
            text = _text_of(data.get("message", {}))
            if text:
                self._event(task_id, "session/assistant", {"text": text})
                t = self.store.get_task(task_id)
                if t:
                    t.summary = text[:2000]
                    self.store.save_task(t)
        elif etype == "tool/call":
            self._event(task_id, "session/tool_call", {"call_id": data.get("callId"), "name": data.get("name"),
                                                       "arguments": _short(data.get("arguments"), 2000)})
        elif etype == "tool/result":
            msg = data.get("message") or data
            self._event(task_id, "session/tool_result", {"call_id": msg.get("callId") or data.get("callId"), "name": data.get("name"),
                                                         "text": _short(_text_of(msg), 2000), "error": bool(data.get("error") or msg.get("isError"))})
        elif etype == "user/message":
            # 由看板发出的消息已经记过 board/user_message；这里只收 dsh 自己注入的上下文，略过
            pass
        elif etype == "turn/end":
            self._event(task_id, "session/turn_end", {"reason": data.get("reason")})
        elif etype in ("compaction/summary", "llm/retry"):
            self._event(task_id, f"session/{etype.replace('/', '_')}", {})

    # ---------- agent 侧动作（MCP 工具调用进来） ----------

    def set_plan(self, task_id: str, steps: list[str], done: int = 0, current: int | None = None) -> Task:
        t = self.task(task_id)
        plan = []
        for i, s in enumerate(steps):
            st = "done" if i < done else ("doing" if (current is not None and i == current) or (current is None and i == done) else "pending")
            plan.append(PlanStep(title=s, status=st))
        t.plan = plan
        self.store.save_task(t)
        self._event(task_id, "board/plan", {"steps": [p.model_dump() for p in plan]})
        self.bus.publish("task", t.model_dump(mode="json"))
        return t

    def request_input(self, task_id: str, question: str, options: list[str] | None, fields: list[FormField] | None = None) -> InputRequest:
        t = self.task(task_id)
        req = self.store.create_input_request(task_id, question, options, fields)
        self._event(task_id, "board/input_requested", req.model_dump(mode="json"))
        if t.status == Status.running:
            self.transition(t, Status.waiting_input, note=req.id)
        self.bus.publish("input_request", req.model_dump(mode="json"))
        return req

    def request_approval(self, task_id: str, action: str, payload: str, reason: str) -> ActionReview:
        t = self.task(task_id)
        rev = self.store.create_action_review(task_id, action, payload, reason)
        self._event(task_id, "board/approval_requested", rev.model_dump(mode="json"))
        if t.status == Status.running:
            self.transition(t, Status.waiting_approval, note=rev.id)
        self.bus.publish("action_review", rev.model_dump(mode="json"))
        return rev

    def add_receipt(self, task_id: str, kind: str, summary: str, path: str, meta: str) -> Receipt:
        self.task(task_id)
        r = self.store.create_receipt(task_id, kind, summary, path, meta)
        self._event(task_id, "board/receipt", r.model_dump(mode="json"))
        self.bus.publish("receipt", r.model_dump(mode="json"))
        return r

    def card(self, task_id: str, card: dict) -> None:
        """富媒体卡片（table / chart / file / markdown）。只进时间线，由前端渲染。"""
        self.task(task_id)
        self._event(task_id, "board/card", card)

    def spawn_subtask(self, parent_id: str, title: str, prompt: str, skill: str = "") -> Task:
        parent = self.task(parent_id)
        child = self.store.create_task(title=title, prompt=prompt, skill=skill, priority=parent.priority,
                                       requires_review=False, max_retries=parent.max_retries, parent_id=parent.id, status=Status.queued)
        self._event(parent.id, "board/subtask", {"id": child.id, "title": child.title, "status": child.status.value})
        self._event(child.id, "board/status", {"from": "backlog", "to": "queued", "note": f"spawned by {parent.id}"})
        self.bus.publish("task", child.model_dump(mode="json"))
        return child

    # ---------- 跨任务：记忆 / 资料库 / 定时 ----------

    def memory_set(self, key: str, value: str, task_id: str = "") -> MemoryItem:
        it = self.store.memory_set(key, value, task_id)
        if task_id:
            self._event(task_id, "board/memory", {"key": key, "value": _short(value, 300)})
        self.bus.publish("memory", it.model_dump(mode="json"))
        return it

    def library_add(self, task_id: str, title: str, summary: str, path: str = "", tags: list[str] | None = None) -> LibraryItem:
        it = self.store.library_add(task_id, title, summary, path, tags)
        self._event(task_id, "board/library", it.model_dump(mode="json"))
        self.bus.publish("library", it.model_dump(mode="json"))
        return it

    def schedule_create(self, title: str, prompt: str, skill: str, every: str, created_by_task: str = "") -> Schedule:
        nxt = next_run(every, datetime.now(timezone.utc))
        sch = self.store.schedule_create(title, prompt, skill, every, nxt, created_by_task)
        if created_by_task:
            self._event(created_by_task, "board/schedule", {"id": sch.id, "title": title, "every": every})
        self.bus.publish("schedule", sch.model_dump(mode="json"))
        return sch

    # ---------- 人侧动作（REST 进来） ----------

    def user_message(self, task_id: str, text: str) -> None:
        self._event(task_id, "board/user_message", {"text": text})

    def answer(self, req_id: str, answer: str) -> tuple[Task, InputRequest]:
        req = self.store.answer_input_request(req_id, answer)
        t = self.task(req.task_id)
        self._event(t.id, "board/input_answered", {"id": req.id, "answer": answer})
        self.bus.publish("input_request", req.model_dump(mode="json"))
        return t, req

    def decide(self, rev_id: str, decision: str, comment: str) -> tuple[Task, ActionReview]:
        rev = self.store.decide_action_review(rev_id, decision, comment)
        t = self.task(rev.task_id)
        self._event(t.id, "board/approval_decided", {"id": rev.id, "decision": decision, "comment": comment})
        self.bus.publish("action_review", rev.model_dump(mode="json"))
        return t, rev


def _text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _short(v, n: int = 600) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"… (+{len(s) - n} chars)"


_EVERY_RE = re.compile(r"^(\d+)\s*([mh])$")


def next_run(every: str, after: datetime) -> datetime:
    """支持四种写法：'30m' / '2h' / 'daily 09:30' / 'weekdays 09:30'（时间按本机时区）。"""
    every = every.strip().lower()
    m = _EVERY_RE.match(every)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            raise ValueError("interval must be positive")
        return after + timedelta(minutes=n if unit == "m" else n * 60)
    parts = every.split()
    if len(parts) == 2 and parts[0] in ("daily", "weekdays"):
        hh, mm = (int(x) for x in parts[1].split(":"))
        local = after.astimezone()
        cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        while cand <= local or (parts[0] == "weekdays" and cand.weekday() >= 5):
            cand += timedelta(days=1)
        return cand.astimezone(timezone.utc)
    raise ValueError("every must be like '30m', '2h', 'daily 09:30' or 'weekdays 09:30'")
