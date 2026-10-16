"""任务 runner：把看板状态机和引擎接起来。

职责：
- 派发：queued → running，给引擎发第一条 prompt（任务描述 + task_id + skill 提示）
- 恢复：人回答/审批后，把答案作为新 prompt 发回同一个 session
- 收尾：引擎报 idle 时，根据任务当前状态决定 → review / done / 保持 waiting
- 自愈：后端重启时，把上次还在 running 的任务标成 interrupted，可一键恢复
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import json

from .board import Board, IllegalTransition, next_run
from .config import Settings
from .engine import Engine, EngineError
from .models import Status, Task

log = logging.getLogger("muse.runner")


class Runner:
    def __init__(self, board: Board, engine: Engine, settings: Settings):
        self.board = board
        self.engine = engine
        self.s = settings
        self.engine.on_event = self._on_event
        self.engine.on_status = self._on_status
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._sched_task: asyncio.Task | None = None
        # 任务还没启动时收到的聊天消息，启动后紧跟初始 prompt 发出
        self._pending_say: dict[str, list[str]] = {}

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._recover_after_restart()
        await self.engine.start()
        self._loop_task = asyncio.create_task(self._dispatch_loop(), name="runner-dispatch")
        self._sched_task = asyncio.create_task(self._schedule_loop(), name="runner-schedule")
        self._wake.set()

    async def stop(self) -> None:
        for t in (self._loop_task, self._sched_task):
            if t:
                t.cancel()
        await self.engine.stop()

    def _recover_after_restart(self) -> None:
        """上次进程没走完的任务：running 的标 interrupted；waiting 的保持（它们本来就在等人）。"""
        for t in self.board.store.list_tasks(Status.running):
            try:
                self.board.transition(t, Status.interrupted, note="backend restarted")
            except IllegalTransition:
                pass

    def wake(self) -> None:
        self._wake.set()

    # ---------- 派发 ----------

    async def _dispatch_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._dispatch_once()
            except Exception:  # noqa: BLE001
                log.exception("dispatch failed")
            await asyncio.sleep(0.2)
            if self.board.store.list_tasks(Status.queued):
                self._wake.set()

    async def _dispatch_once(self) -> None:
        active = len(self.board.store.list_tasks(Status.running))
        for t in self.board.store.list_tasks(Status.queued):
            if active >= self.s.max_concurrent_tasks:
                return
            await self._start_task(t)
            active += 1

    async def _start_task(self, t: Task) -> None:
        resumed = False
        if not t.session_id:
            t.session_id = t.id
        elif t.retries > 0 or t.started_at is not None:
            resumed = await self.engine.resume(t.session_id)   # 断点续跑：dsh 会话日志还在
        t.workspace = str(self.s.workspace)
        self.board.transition(t, Status.running, note="resumed" if resumed else "started")
        text = self._continue_prompt(t) if resumed else self._initial_prompt(t)
        try:
            await self.engine.prompt(t.session_id, text)
            for extra in self._pending_say.pop(t.id, []):
                await self.engine.prompt(t.session_id, f"Task id: {t.id}. Message from the human: {extra}")
        except EngineError as e:
            self.board.transition(t, Status.failed, note="prompt failed", error=str(e))

    def _initial_prompt(self, t: Task) -> str:
        parts = [f"Task id: {t.id}. Pass this as task_id to every muse_* tool call."]
        if t.skill:
            parts.append(f"Load the `{t.skill}` skill first with the skill tool and follow it.")
        parts.append("Publish a plan with muse_plan before doing anything else.")
        parts.append("")
        parts.append(t.prompt)
        return "\n".join(parts)

    def _continue_prompt(self, t: Task) -> str:
        return (f"Task id: {t.id}. The previous run was interrupted. Review what you already did "
                "(check receipts and files in the workspace), update the plan, and continue from where you left off.")

    # ---------- 人回复后的恢复 ----------

    async def answer(self, req_id: str, answer: str) -> Task:
        t, req = self.board.answer(req_id, answer)
        if t.status == Status.waiting_input and not self.board.store.pending_input_request(t.id):
            self.board.transition(t, Status.running, note=f"answered {req.id}")
            await self.engine.prompt(t.session_id, f"Task id: {t.id}. Answer to your question \"{req.question}\": {answer}\nContinue the task.")
        return self.board.task(t.id)

    async def decide(self, rev_id: str, decision: str, comment: str) -> Task:
        t, rev = self.board.decide(rev_id, decision, comment)
        if t.status == Status.waiting_approval and not self.board.store.pending_action_review(t.id):
            self.board.transition(t, Status.running, note=f"{decision} {rev.id}")
            verdict = "APPROVED — you may now perform it" if decision == "approved" else "REJECTED — do not perform it"
            extra = f" Reviewer comment: {comment}" if comment else ""
            await self.engine.prompt(t.session_id, f"Task id: {t.id}. Decision on action '{rev.action}' ({rev.id}): {verdict}.{extra}\nContinue the task.")
        return self.board.task(t.id)

    async def say(self, task_id: str, text: str) -> Task:
        """聊天框里的消息。按任务当前状态决定怎么送达：

        running           → 直接 prompt，dsh 在当前轮结束后作为下一轮处理（插话）
        waiting_input     → 当作对未决问题的回答
        waiting_approval  → 记录并 prompt，决定做出后 agent 会看到
        review/done/failed/interrupted → 重开：转 running 再 prompt
        backlog/queued    → 先存着，任务启动后紧跟初始 prompt 发出
        """
        t = self.board.task(task_id)
        self.board.user_message(t.id, text)
        if t.status == Status.waiting_input:
            req = self.board.store.pending_input_request(t.id)
            if req:
                return await self.answer(req.id, text)
        if t.status in (Status.backlog, Status.queued):
            self._pending_say.setdefault(t.id, []).append(text)
            if t.status == Status.backlog:
                self.board.transition(t, Status.queued, note="message from human")
                self.wake()
            return self.board.task(t.id)
        if t.status in (Status.review, Status.done, Status.failed, Status.interrupted):
            if t.status == Status.interrupted and t.session_id:
                await self.engine.resume(t.session_id)
            self.board.transition(t, Status.running, note="reopened by message")
        elif t.status == Status.cancelled:
            raise IllegalTransition("cancelled task cannot be messaged; create a new task")
        await self.engine.prompt(t.session_id or t.id, f"Task id: {t.id}. Message from the human: {text}")
        return self.board.task(t.id)

    async def _notify_parent(self, child: Task) -> None:
        """子任务结束：把结论和回执作为消息送给父任务。父任务空闲就重开，在跑就插话。"""
        if not child.parent_id:
            return
        parent = self.board.store.get_task(child.parent_id)
        if not parent or parent.status in (Status.cancelled,):
            return
        receipts = self.board.store.list_receipts(child.id)
        lines = [f"Subtask {child.id} '{child.title}' finished with status {child.status.value}."]
        if child.summary:
            lines.append(f"Conclusion: {child.summary[:1500]}")
        for r in receipts:
            lines.append(f"- receipt [{r.kind}] {r.summary}" + (f" ({r.path})" if r.path else ""))
        if child.error:
            lines.append(f"Error: {child.error}")
        text = "\n".join(lines)
        self.board.store.append_event(parent.id, "board/subtask", {"id": child.id, "title": child.title, "status": child.status.value, "summary": child.summary[:300]})
        self.board.bus.publish("task", parent.model_dump(mode="json"))
        try:
            if parent.status in (Status.review, Status.done, Status.failed, Status.interrupted):
                self.board.transition(parent, Status.running, note=f"subtask {child.id} finished")
            if parent.status in (Status.running, Status.waiting_approval, Status.waiting_input):
                await self.engine.prompt(parent.session_id or parent.id, f"Task id: {parent.id}. {text}")
            else:
                self._pending_say.setdefault(parent.id, []).append(text)
        except (EngineError, IllegalTransition) as e:
            log.warning("notify parent %s failed: %s", parent.id, e)

    async def _schedule_loop(self) -> None:
        while True:
            try:
                now = datetime.now(timezone.utc)
                for sch in self.board.store.schedule_due(now):
                    t = self.board.store.create_task(title=f"{sch.title} · {now.astimezone():%m-%d %H:%M}", prompt=sch.prompt, skill=sch.skill,
                                                     requires_review=True, schedule_id=sch.id, status=Status.queued)
                    sch.last_task_id = t.id
                    sch.next_run_at = next_run(sch.every, now)
                    self.board.store.schedule_update(sch)
                    self.board.store.append_event(t.id, "board/status", {"from": "backlog", "to": "queued", "note": f"schedule {sch.id}"})
                    self.board.bus.publish("task", t.model_dump(mode="json"))
                    self.board.bus.publish("schedule", sch.model_dump(mode="json"))
                    self.wake()
            except Exception:  # noqa: BLE001
                log.exception("schedule loop failed")
            await asyncio.sleep(30)

    # ---------- 引擎回调 ----------

    async def _on_event(self, session_id: str, event: dict) -> None:
        t = self.board.store.get_task(session_id)
        if not t:
            return  # 子代理的 session 或非看板会话
        self.board.record_session_event(t.id, event)
        if event.get("type") == "turn/end":
            reason = (event.get("data") or {}).get("reason") or {}
            if isinstance(reason, dict) and reason.get("kind") == "error" and t.status == Status.running:
                self._fail_or_retry(self.board.task(t.id), str(reason.get("error", {}).get("message", "turn error")))

    async def _on_status(self, session_id: str, status: str) -> None:
        if status != "idle":
            return
        t = self.board.store.get_task(session_id)
        if not t or t.status != Status.running:
            return  # waiting_* / failed / cancelled 都保持原样
        # agent 自己停了：有待审就是等人，否则视为做完
        if self.board.store.pending_input_request(t.id):
            self.board.transition(t, Status.waiting_input)
        elif self.board.store.pending_action_review(t.id):
            self.board.transition(t, Status.waiting_approval)
        elif t.requires_review:
            self.board.transition(t, Status.review, note="agent finished")
            await self._notify_parent(self.board.task(t.id))
        else:
            self.board.transition(t, Status.done, note="agent finished")
            await self._notify_parent(self.board.task(t.id))

    def _fail_or_retry(self, t: Task, err: str) -> None:
        if t.retries < t.max_retries:
            t.retries += 1
            self.board.transition(t, Status.failed, note="will retry", error=err)
            self.board.transition(self.board.task(t.id), Status.queued, note=f"retry {t.retries}/{t.max_retries}")
            self.wake()
        else:
            self.board.transition(t, Status.failed, error=err)
            asyncio.create_task(self._notify_parent(self.board.task(t.id)))
