"""Agent 引擎接口 + 两个实现：

- DshEngine：启动 `dsh --profile sdk --patch dsh/muse.patch.yml`，走 stdio 上的换行分隔 JSON-RPC。
  协议见 deepseek-harness/packages/sdk/protocol/src/types.ts：
    请求  initialize / session/prompt / session/resume / session/wait / shutdown
    通知  session.event（完整会话日志事件）/ session.status（idle|running）/ subagent.*
- MockEngine：不调模型，按脚本模拟一条任务的生命周期，用于离线演示和测试。

两者对 runner 暴露同一组方法：start / prompt / resume / stop，以及两个回调 on_event / on_status。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .config import Settings

log = logging.getLogger("muse.engine")

EventCB = Callable[[str, dict], Awaitable[None]]      # (session_id, event)
StatusCB = Callable[[str, str], Awaitable[None]]      # (session_id, "idle"|"running")


class Engine(Protocol):
    on_event: EventCB | None
    on_status: StatusCB | None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def prompt(self, session_id: str, text: str) -> None: ...
    async def resume(self, session_id: str) -> bool: ...


class EngineError(RuntimeError):
    pass


# ---------------------------------------------------------------- dsh ----

class DshEngine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.on_event: EventCB | None = None
        self.on_status: StatusCB | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        env = os.environ.copy()
        env["DSH_HOME"] = str(self.s.dsh_home)
        env["MUSE_MCP_URL"] = self.s.mcp_url
        env["MUSE_TOKEN"] = self.s.token
        env["MUSE_SKILL_DIR"] = str(self.s.skill_dir)
        self.s.dsh_home.mkdir(parents=True, exist_ok=True)
        self.s.workspace.mkdir(parents=True, exist_ok=True)
        args = [*self.s.dsh_bin, "--profile", "sdk", "--patch", str(self.s.dsh_patch)]
        log.info("starting dsh: %s", " ".join(args))
        self._proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.s.workspace), env=env,
        )
        self._reader = asyncio.create_task(self._read_loop(), name="dsh-stdout")
        self._stderr = asyncio.create_task(self._stderr_loop(), name="dsh-stderr")
        info = await self.request("initialize", {
            "cwd": str(self.s.workspace), "provider": self.s.provider, "model": self.s.model,
        }, timeout=120)
        log.info("dsh ready: %s", info.get("serverInfo"))

    async def stop(self) -> None:
        if not self._proc:
            return
        try:
            await self.request("shutdown", None, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("shutdown request failed: %s", e)
        if self._proc.stdin:
            self._proc.stdin.close()
        try:
            await asyncio.wait_for(self._proc.wait(), 10)
        except asyncio.TimeoutError:
            self._proc.kill()
        for t in (self._reader, self._stderr):
            if t:
                t.cancel()
        self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def prompt(self, session_id: str, text: str) -> None:
        await self.request("session/prompt", {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": text}]})

    async def resume(self, session_id: str) -> bool:
        try:
            await self.request("session/resume", {"sessionId": session_id})
            return True
        except EngineError as e:
            log.warning("resume %s failed: %s", session_id, e)
            return False

    async def request(self, method: str, params: Any, timeout: float = 60) -> dict:
        if not self.alive:
            raise EngineError("dsh process is not running")
        rid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        async with self._write_lock:
            assert self._proc and self._proc.stdin
            self._proc.stdin.write((json.dumps(msg) + "\n").encode())
            await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("dsh non-json stdout: %s", line[:200])
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                fut = self._pending.get(msg["id"])
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(EngineError(json.dumps(msg["error"], ensure_ascii=False)))
                    else:
                        fut.set_result(msg.get("result") or {})
            elif "method" in msg:
                await self._dispatch(msg["method"], msg.get("params") or {})
        log.warning("dsh stdout closed (exit=%s)", self._proc.returncode if self._proc else None)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(EngineError("dsh exited"))

    async def _dispatch(self, method: str, params: dict) -> None:
        try:
            if method == "session.event" and self.on_event:
                await self.on_event(params["sessionId"], params["event"])
            elif method == "session.status" and self.on_status:
                await self.on_status(params["sessionId"], params["status"])
            else:
                log.debug("notification %s", method)
        except Exception:  # noqa: BLE001
            log.exception("notification handler failed for %s", method)

    async def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            log.info("dsh: %s", line.decode(errors="replace").rstrip())


# --------------------------------------------------------------- mock ----

class MockEngine:
    """脚本化引擎。每个 prompt 触发一段模拟剧本，通过 MCP 工具函数直接操作看板，和真 agent 走同一条路。

    剧本（按 session 内第几次 prompt 决定）：
      第 1 次：发布计划 → 调 request_input 提问 → 结束本轮（任务挂起）
      第 2 次：收到回答 → 调 request_approval → 结束本轮（再次挂起）
      第 3 次：收到审批结果 → 写回执 → 给出总结 → 结束（进入 review/done）
    tools 由 runner 注入，签名同 mcp_tools 里的实现。
    """

    def __init__(self, tools: Any, delay: float = 0.05):
        self.tools = tools
        self.delay = delay
        self.on_event: EventCB | None = None
        self.on_status: StatusCB | None = None
        self._turns: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = {}  # dsh 对同一会话的 turn 是串行的，这里也串行
        self._queued: dict[str, int] = {}          # 还没跑完的 prompt 数；dsh 只在 inbox 排空后才报 idle

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    async def resume(self, session_id: str) -> bool:
        return True

    async def prompt(self, session_id: str, text: str) -> None:
        self._queued[session_id] = self._queued.get(session_id, 0) + 1

        async def serialized() -> None:
            async with self._locks.setdefault(session_id, asyncio.Lock()):
                try:
                    await self._run(session_id, text)
                finally:
                    self._queued[session_id] -= 1
                    if self._queued[session_id] == 0 and self.on_status:
                        await self.on_status(session_id, "idle")
        t = asyncio.create_task(serialized())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _emit(self, sid: str, etype: str, data: dict) -> None:
        if self.on_event:
            await self.on_event(sid, {"type": etype, "data": data})

    async def _say(self, sid: str, text: str) -> None:
        await self._emit(sid, "assistant/message", {"message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    async def _call(self, sid: str, name: str, **args: Any) -> str:
        await self._emit(sid, "tool/call", {"name": f"mcp__muse__{name}", "arguments": json.dumps(args, ensure_ascii=False)})
        result = await getattr(self.tools, name)(**args)
        await self._emit(sid, "tool/result", {"name": f"mcp__muse__{name}", "content": [{"type": "text", "text": result}]})
        return result

    async def _run(self, sid: str, text: str) -> None:
        lower = text.lower()
        is_notice = "subtask task_" in lower and "finished with status" in lower   # 子任务结束通知
        is_chat = "message from the human" in lower or is_notice
        is_child = "subtask of parent" in lower
        if is_chat:
            n = self._turns.get(sid, 0)          # 插话不消耗剧本进度
        else:
            n = self._turns[sid] = self._turns.get(sid, 0) + 1
        if self.on_status:
            await self.on_status(sid, "running")
        await self._emit(sid, "user/message", {"message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        await asyncio.sleep(self.delay)
        try:
            if is_notice:
                await self._say(sid, "收到子任务结论，已并入分析。")
            elif is_chat and n < 3:
                await self._say(sid, "收到，我会在后续步骤里一并处理。")
            elif is_child:
                # 子任务剧本：一轮做完
                await self._call(sid, "muse_plan", task_id=sid, steps=["取数", "结论"], done=2)
                await self._call(sid, "muse_receipt", task_id=sid, kind="data", path=f"data/{sid}.json", summary="子任务取数完成")
                await self._say(sid, "子任务结论：样本内动量因子 IC 0.04，显著。")
            elif is_chat:
                # 插话 / 重开：回应一句，顺手展示一张表
                await self._say(sid, "收到你的消息，我补一张对比表。")
                await self._call(sid, "muse_show_table", task_id=sid, title="追加对比", columns=["指标", "600519", "行业均值"],
                                 rows=[["PE(TTM)", 26.5, 31.2], ["ROE", "6.03%", "9.8%"]], note="数据来源：demo-offline")
            elif n == 1:
                await self._say(sid, "收到任务。先发布计划，再拉数据。")
                await self._call(sid, "muse_plan", task_id=sid, steps=["拉取行情与财务", "计算因子", "撰写报告", "提交回执"])
                await asyncio.sleep(self.delay)
                await self._emit(sid, "tool/call", {"callId": "c1", "name": "bash", "arguments": '{"command":"python scripts/fetch_fin.py 600519"}'})
                await self._emit(sid, "tool/result", {"message": {"callId": "c1", "role": "tool", "content": [{"type": "text", "text": "wrote data/600519/financials.json (demo-offline, 6 quarters)"}]}})
                await self._call(sid, "muse_show_chart", task_id=sid, title="近 6 季营收与净利（亿元）", chart_type="bar",
                                 x=["25Q1", "25Q2", "25Q3", "25Q4", "26Q1", "26Q2"],
                                 series=[{"name": "营收", "values": [98, 104, 110, 121, 118, 129]}, {"name": "净利", "values": [21, 24, 19, 26, 14, 12]}],
                                 note="数据来源：demo-offline")
                await self._call(sid, "muse_memory_set", task_id=sid, key="demo.last_code", value="600519")
                await self._call(sid, "muse_request_input", task_id=sid, question="净利连续两季下滑，报告口径按哪种？",
                                 options=["保守", "中性"],
                                 fields=[{"name": "horizon", "label": "研究周期", "type": "choice", "options": ["3个月", "6个月", "12个月"]},
                                         {"name": "focus", "label": "重点关注", "type": "text", "required": False}])
                await self._say(sid, "已记录问题，等待回答后继续。")
            elif n == 2:
                await self._say(sid, "收到回答。继续计算因子，并派一个子任务做因子显著性检验。")
                await self._call(sid, "muse_plan", task_id=sid, steps=["拉取行情与财务", "计算因子", "撰写报告", "提交回执"], done=2)
                await self._call(sid, "muse_show_table", task_id=sid, title="因子表", columns=["因子", "数值", "窗口"],
                                 rows=[["revenue_yoy", "23.74%", "25Q2→26Q2"], ["net_profit_yoy", "-48.26%", "25Q2→26Q2"], ["pe_ttm", "26.5x", "TTM"]],
                                 note="数据来源：demo-offline")
                await self._call(sid, "muse_spawn_subtask", task_id=sid, title="动量因子显著性检验", prompt="subtask of parent: 检验动量因子 IC")
                await self._call(sid, "muse_library_add", task_id=sid, title="600519 因子快照", summary="营收同比 +23.7%，净利同比 -48.3%，PE 26.5x", tags=["600519", "因子"])
                await self._call(sid, "muse_request_approval", task_id=sid, action="send_report", payload='{"to":"pm@fund.example","subject":"600519 研究报告"}', reason="报告写完后需要外发给基金经理")
                await self._say(sid, "已申请外发审批，等待结果。")
            else:
                approved = "approved" in text.lower() or "批准" in text
                await self._say(sid, "审批结果已收到。" + ("将在验收后外发。" if approved else "不外发，仅保存本地。"))
                await self._call(sid, "muse_attach_file", task_id=sid, path="reports/600519.md", title="600519 研究报告", kind="report")
                await self._call(sid, "muse_show_markdown", task_id=sid, title="结论摘要", markdown="**中性**。营收同比 +23.7%，净利同比 -48.3%。\n\n- 盈利质量下滑\n- 估值合理")
                await self._call(sid, "muse_receipt", task_id=sid, kind="report", path="reports/600519.md", summary="中性。营收同比 +23.7%，净利同比 -48.3%，盈利质量下滑但估值合理。", meta='{"source":"demo-offline","window":"2025Q2→2026Q2"}')
                await self._call(sid, "muse_plan", task_id=sid, steps=["拉取行情与财务", "计算因子", "撰写报告", "提交回执"], done=4)
                await self._say(sid, "任务完成：报告位于 reports/600519.md，结论中性。")
        finally:
            pass  # idle 由 prompt() 的串行包装在队列排空后统一发出
