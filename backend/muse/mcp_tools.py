"""暴露给 agent 的四个看板工具。dsh 通过 mcp-client（streamable-http）接进来，工具名为 mcp__muse__<name>。

agent 从任务提示里拿到 task_id，每次调用都带上。挂起的机制就在这里：
request_input / request_approval 立刻返回一段"已记录，请结束本轮"的文本，agent 结束 turn，
dsh 报 session.status=idle，runner 看到任务已经是 waiting_* 就不动它；人回复后 runner 再 prompt 一次。
"""
from __future__ import annotations

import json
import logging

from mcp.server.mcpserver import MCPServer

from .board import Board

log = logging.getLogger("muse.mcp")

INSTRUCTIONS = """\
These tools keep the Muse task board in sync with your work. Your task id is given in the task prompt.

Workflow: muse_plan (once, before acting; update it as steps complete) → do the work →
muse_receipt for every deliverable (report, data file, action taken).

When you need a human decision, information you cannot obtain, or permission for an action with
external side effects (sending, publishing, trading), call muse_request_input or
muse_request_approval, then END YOUR TURN IMMEDIATELY with a one-line status. Do not wait, poll,
or guess. You will be resumed with the answer as a new message.
"""


class BoardTools:
    """纯 Python 实现，MockEngine 直接调它；FastMCP 只是在外面包一层 schema。"""

    def __init__(self, board: Board):
        self.board = board

    async def muse_plan(self, task_id: str, steps: list[str], done: int = 0, current: int | None = None) -> str:
        t = self.board.set_plan(task_id, steps, done, current)
        return f"Plan recorded for {t.id}: {len(steps)} steps, {done} done."

    async def muse_request_input(self, task_id: str, question: str, options: list[str] | None = None) -> str:
        req = self.board.request_input(task_id, question, options)
        return (f"Input request {req.id} recorded; the task is now waiting for a human. "
                "END YOUR TURN NOW with a one-line status. You will be resumed with the answer.")

    async def muse_request_approval(self, task_id: str, action: str, payload: str, reason: str = "") -> str:
        try:
            json.loads(payload)
        except json.JSONDecodeError:
            return "Error: payload must be a JSON string describing the action parameters."
        rev = self.board.request_approval(task_id, action, payload, reason)
        return (f"Approval request {rev.id} recorded for action '{action}'; the task is now waiting for a human. "
                "Do NOT perform the action. END YOUR TURN NOW. You will be resumed with the decision.")

    async def muse_receipt(self, task_id: str, kind: str, summary: str, path: str = "", meta: str = "") -> str:
        r = self.board.add_receipt(task_id, kind, summary, path, meta)
        return f"Receipt {r.id} recorded ({kind}{': ' + path if path else ''})."


def build_mcp(board: Board) -> tuple[MCPServer, BoardTools]:
    tools = BoardTools(board)
    mcp = MCPServer("muse", instructions=INSTRUCTIONS)

    @mcp.tool(description="Publish or update the task's step plan. steps: ordered titles; done: how many are complete; current: index of the step in progress.")
    async def muse_plan(task_id: str, steps: list[str], done: int = 0, current: int | None = None) -> str:
        return await tools.muse_plan(task_id, steps, done, current)

    @mcp.tool(description="Ask the human a question you cannot answer yourself (missing data, ambiguous goal, which source to use). Then end your turn.")
    async def muse_request_input(task_id: str, question: str, options: list[str] | None = None) -> str:
        return await tools.muse_request_input(task_id, question, options)

    @mcp.tool(description="Request approval before any action with external side effects. payload is a JSON string of the exact parameters. Then end your turn without performing the action.")
    async def muse_request_approval(task_id: str, action: str, payload: str, reason: str = "") -> str:
        return await tools.muse_request_approval(task_id, action, payload, reason)

    @mcp.tool(description="Record a deliverable: kind is report|data|file|action; path is workspace-relative; meta is a JSON string (sources, windows, checksums).")
    async def muse_receipt(task_id: str, kind: str, summary: str, path: str = "", meta: str = "") -> str:
        return await tools.muse_receipt(task_id, kind, summary, path, meta)

    return mcp, tools
