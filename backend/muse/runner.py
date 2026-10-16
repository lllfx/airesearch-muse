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

from .board import Board, IllegalTransition
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

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._recover_after_restart()
        await self.engine.start()
        self._loop_task = asyncio.create_task(self._dispatch_loop(), name="runner-dispatch")
        self._wake.set()

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
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
        else:
            self.board.transition(t, Status.done, note="agent finished")

    def _fail_or_retry(self, t: Task, err: str) -> None:
        if t.retries < t.max_retries:
            t.retries += 1
            self.board.transition(t, Status.failed, note="will retry", error=err)
            self.board.transition(self.board.task(t.id), Status.queued, note=f"retry {t.retries}/{t.max_retries}")
            self.wake()
        else:
            self.board.transition(t, Status.failed, error=err)
