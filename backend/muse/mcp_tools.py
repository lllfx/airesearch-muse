"""暴露给 agent 的看板工具。dsh 通过 mcp-client（streamable-http）接进来，工具名为 mcp__muse__<name>。

agent 从任务提示里拿到 task_id，每次调用都带上。挂起的机制就在这里：
request_input / request_approval 立刻返回一段"已记录，请结束本轮"的文本，agent 结束 turn，
dsh 报 session.status=idle，runner 看到任务已经是 waiting_* 就不动它；人回复后 runner 再 prompt 一次。

四组工具：
  看板     muse_plan / muse_request_input / muse_request_approval / muse_receipt
  富媒体   muse_show_table / muse_show_chart / muse_attach_file / muse_show_markdown
  协作     muse_spawn_subtask / muse_memory_set / muse_memory_get / muse_library_add / muse_library_search / muse_schedule
  问财     iwencai_market_query / iwencai_shareholder_query / iwencai_search
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

from .board import Board
from .iwencai import IwencaiClient
from .models import FormField

log = logging.getLogger("muse.mcp")

INSTRUCTIONS = """\
These tools keep the Muse task board in sync with your work. Your task id is given in the task prompt;
pass it as task_id to every muse_* call.

Workflow: muse_plan (once, before acting; update as steps complete) → do the work →
muse_receipt for every deliverable (report, data file, action taken).

Show results to the human as they are produced, not only at the end: muse_show_table for tabular
data, muse_show_chart for series, muse_attach_file for files you wrote, muse_show_markdown for a
short formatted note. These cards appear in the conversation immediately.

When you need a human decision, information you cannot obtain, or permission for an action with
external side effects (sending, publishing, trading), call muse_request_input (with `fields` for
a structured form) or muse_request_approval, then END YOUR TURN IMMEDIATELY with a one-line
status. Do not wait, poll, or guess. You will be resumed with the answer as a new message.

The human may also send you messages while you work; they arrive as new user messages.

For Chinese market data prefer the iwencai_* tools over shell scripts: they return structured JSON
and save the raw response under data/iwencai/ for citation. Always cite 数据来源：同花顺问财 with
the query and timestamp. Use muse_library_add to file reusable findings and muse_memory_set for
facts future tasks should know (e.g. preferred data source, the PM's watchlist).

Large independent sub-jobs (one per stock in a list, one per document) go to muse_spawn_subtask;
you receive each child's conclusion as a message when it finishes.
"""


class BoardTools:
    """纯 Python 实现，MockEngine 直接调它；MCPServer 只是在外面包一层 schema。"""

    def __init__(self, board: Board, iwencai: IwencaiClient | None = None, runner: Any = None):
        self.board = board
        self.iwencai = iwencai
        self.runner = runner  # 子任务派发要唤醒 runner；由 main 注入

    # ---- 看板 ----

    async def muse_plan(self, task_id: str, steps: list[str], done: int = 0, current: int | None = None) -> str:
        t = self.board.set_plan(task_id, steps, done, current)
        return f"Plan recorded for {t.id}: {len(steps)} steps, {done} done."

    async def muse_request_input(self, task_id: str, question: str, options: list[str] | None = None,
                                 fields: list[dict] | None = None) -> str:
        parsed = [FormField(**f) for f in (fields or [])]
        req = self.board.request_input(task_id, question, options, parsed)
        return (f"Input request {req.id} recorded; the task is now waiting for a human. "
                "END YOUR TURN NOW with a one-line status. You will be resumed with the answer"
                + (" as a JSON object keyed by field name." if parsed else "."))

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

    # ---- 富媒体卡片 ----

    async def muse_show_table(self, task_id: str, title: str, columns: list[str], rows: list[list[Any]], note: str = "") -> str:
        if len(rows) > 200:
            rows = rows[:200]
            note = (note + " " if note else "") + "(truncated to 200 rows)"
        self.board.card(task_id, {"type": "table", "title": title, "columns": columns, "rows": rows, "note": note})
        return f"Table '{title}' shown ({len(rows)} rows)."

    async def muse_show_chart(self, task_id: str, title: str, chart_type: str, x: list[Any],
                              series: list[dict], note: str = "", y_label: str = "") -> str:
        if chart_type not in ("line", "bar"):
            return "Error: chart_type must be 'line' or 'bar'."
        for s in series:
            if "name" not in s or "values" not in s or len(s["values"]) != len(x):
                return "Error: each series needs name and values with the same length as x."
        self.board.card(task_id, {"type": "chart", "title": title, "chart_type": chart_type, "x": x, "series": series, "note": note, "y_label": y_label})
        return f"Chart '{title}' shown ({len(series)} series, {len(x)} points)."

    async def muse_attach_file(self, task_id: str, path: str, title: str = "", kind: str = "file") -> str:
        r = self.board.add_receipt(task_id, kind, title or path, path, "")
        self.board.card(task_id, {"type": "file", "title": title or path, "path": path, "kind": kind, "receipt_id": r.id})
        return f"File attached: {path} (receipt {r.id})."

    async def muse_show_markdown(self, task_id: str, title: str, markdown: str) -> str:
        self.board.card(task_id, {"type": "markdown", "title": title, "markdown": markdown[:20000]})
        return f"Note '{title}' shown."

    # ---- 协作 ----

    async def muse_spawn_subtask(self, task_id: str, title: str, prompt: str, skill: str = "") -> str:
        child = self.board.spawn_subtask(task_id, title, prompt, skill)
        if self.runner is not None:
            self.runner.wake()
        return (f"Subtask {child.id} '{title}' queued. Continue with other work or end your turn; "
                "you will receive its conclusion as a message when it finishes.")

    async def muse_memory_set(self, task_id: str, key: str, value: str) -> str:
        self.board.memory_set(key, value, task_id)
        return f"Memory '{key}' saved."

    async def muse_memory_get(self, key: str = "") -> str:
        items = self.board.store.memory_list(key)
        if not items:
            return "No memory entries" + (f" with prefix '{key}'." if key else ".")
        return "\n".join(f"{i.key}: {i.value}" for i in items)

    async def muse_library_add(self, task_id: str, title: str, summary: str, path: str = "", tags: list[str] | None = None) -> str:
        it = self.board.library_add(task_id, title, summary, path, tags)
        return f"Library item {it.id} added: {title}."

    async def muse_library_search(self, query: str, limit: int = 10) -> str:
        items = self.board.store.library_search(query, limit)
        if not items:
            return "No matching library items."
        return "\n".join(f"- [{i.id}] {i.title} ({i.created_at.date()}, task {i.task_id}){' · ' + i.path if i.path else ''}\n  {i.summary[:300]}" for i in items)

    async def muse_schedule(self, task_id: str, title: str, prompt: str, every: str, skill: str = "") -> str:
        try:
            sch = self.board.schedule_create(title, prompt, skill, every, created_by_task=task_id)
        except ValueError as e:
            return f"Error: {e}"
        return f"Schedule {sch.id} created: '{title}' every {every}; next run {sch.next_run_at.astimezone():%Y-%m-%d %H:%M}."

    # ---- 问财 ----

    async def iwencai_market_query(self, task_id: str, query: str, page: int = 1, limit: int = 10) -> str:
        return await self._iwencai("market", task_id, query, page=page, limit=limit)

    async def iwencai_shareholder_query(self, task_id: str, query: str, page: int = 1, limit: int = 10) -> str:
        return await self._iwencai("shareholder", task_id, query, page=page, limit=limit)

    async def iwencai_search(self, task_id: str, channel: str, query: str, size: int = 10) -> str:
        if channel not in ("announcement", "news", "report"):
            return "Error: channel must be announcement, news or report."
        return await self._iwencai(channel, task_id, query, size=size)

    async def _iwencai(self, kind: str, task_id: str, query: str, **kw: Any) -> str:
        if self.iwencai is None:
            return "Error: iwencai is not configured (set IWENCAI_API_KEY)."
        try:
            res = await self.iwencai.call(kind, query, **kw)
        except Exception as e:  # noqa: BLE001
            return f"Error: iwencai {kind} failed: {e}"
        self.board.card(task_id, {"type": "data", "source": "同花顺问财", "kind": kind, "query": query,
                                  "count": res["count"], "path": res["path"], "preview": res["preview"]})
        return json.dumps({"source": "同花顺问财", "kind": kind, "query": query, "queried_at": res["queried_at"],
                           "count": res["count"], "raw_path": res["path"], "data": res["data"]}, ensure_ascii=False)


def build_mcp(board: Board, iwencai: IwencaiClient | None = None) -> tuple[MCPServer, BoardTools]:
    tools = BoardTools(board, iwencai)
    mcp = MCPServer("muse", instructions=INSTRUCTIONS)
    T = mcp.tool

    @T(description="Publish or update the task's step plan. steps: ordered titles; done: how many are complete; current: index of the step in progress.")
    async def muse_plan(task_id: str, steps: list[str], done: int = 0, current: int | None = None) -> str:
        return await tools.muse_plan(task_id, steps, done, current)

    @T(description="Ask the human a question you cannot answer yourself. options: quick choices. fields: a structured form, each {name,label,type(text|number|date|choice|multichoice),options?,required?}. Then end your turn.")
    async def muse_request_input(task_id: str, question: str, options: list[str] | None = None, fields: list[dict] | None = None) -> str:
        return await tools.muse_request_input(task_id, question, options, fields)

    @T(description="Request approval before any action with external side effects. payload is a JSON string of the exact parameters. Then end your turn without performing the action.")
    async def muse_request_approval(task_id: str, action: str, payload: str, reason: str = "") -> str:
        return await tools.muse_request_approval(task_id, action, payload, reason)

    @T(description="Record a deliverable: kind is report|data|file|action; path is workspace-relative; meta is a JSON string (sources, windows, checksums).")
    async def muse_receipt(task_id: str, kind: str, summary: str, path: str = "", meta: str = "") -> str:
        return await tools.muse_receipt(task_id, kind, summary, path, meta)

    @T(description="Show a table to the human in the conversation. columns: header names; rows: list of row arrays (max 200). Use for factor tables, holdings, comparisons.")
    async def muse_show_table(task_id: str, title: str, columns: list[str], rows: list[list[Any]], note: str = "") -> str:
        return await tools.muse_show_table(task_id, title, columns, rows, note)

    @T(description="Show a line or bar chart. x: category labels or dates; series: [{name, values}] with values aligned to x. Use for price history, factor trends, comparisons.")
    async def muse_show_chart(task_id: str, title: str, chart_type: str, x: list[Any], series: list[dict], note: str = "", y_label: str = "") -> str:
        return await tools.muse_show_chart(task_id, title, chart_type, x, series, note, y_label)

    @T(description="Attach a file you wrote in the workspace (report, xlsx, png, csv) so the human can open it. Also records a receipt.")
    async def muse_attach_file(task_id: str, path: str, title: str = "", kind: str = "file") -> str:
        return await tools.muse_attach_file(task_id, path, title, kind)

    @T(description="Show a short formatted markdown note (a finding, a caveat, an interim conclusion) as a card.")
    async def muse_show_markdown(task_id: str, title: str, markdown: str) -> str:
        return await tools.muse_show_markdown(task_id, title, markdown)

    @T(description="Delegate an independent sub-job to a child task with its own fresh context (e.g. one per stock). You get its conclusion as a message when it finishes. Max a few at a time.")
    async def muse_spawn_subtask(task_id: str, title: str, prompt: str, skill: str = "") -> str:
        return await tools.muse_spawn_subtask(task_id, title, prompt, skill)

    @T(description="Save a fact future tasks should know (preferred data source, watchlist, PM preferences). key is a short dotted path like 'pm.watchlist'.")
    async def muse_memory_set(task_id: str, key: str, value: str) -> str:
        return await tools.muse_memory_set(task_id, key, value)

    @T(description="Read saved facts. key is an optional prefix; empty returns everything.")
    async def muse_memory_get(key: str = "") -> str:
        return await tools.muse_memory_get(key)

    @T(description="File a reusable research finding into the shared library (title, summary, optional workspace path and tags).")
    async def muse_library_add(task_id: str, title: str, summary: str, path: str = "", tags: list[str] | None = None) -> str:
        return await tools.muse_library_add(task_id, title, summary, path, tags)

    @T(description="Search the shared research library by keywords. Check it before redoing research another task may have done.")
    async def muse_library_search(query: str, limit: int = 10) -> str:
        return await tools.muse_library_search(query, limit)

    @T(description="Create a recurring task. every: '30m', '2h', 'daily 09:30' or 'weekdays 09:30'. Each run creates a new task with the given prompt and skill.")
    async def muse_schedule(task_id: str, title: str, prompt: str, every: str, skill: str = "") -> str:
        return await tools.muse_schedule(task_id, title, prompt, every, skill)

    @T(description="同花顺问财行情查询：自然语言问句（如 '600519 近半年日线 收盘价 涨跌幅 成交量'）。返回结构化数据并把原始响应存到 data/iwencai/。默认 10 条，用 page/limit 翻页。")
    async def iwencai_market_query(task_id: str, query: str, page: int = 1, limit: int = 10) -> str:
        return await tools.iwencai_market_query(task_id, query, page, limit)

    @T(description="同花顺问财股东股本查询：实控人、前十大股东、股东户数、质押、增减持。自然语言问句。")
    async def iwencai_shareholder_query(task_id: str, query: str, page: int = 1, limit: int = 10) -> str:
        return await tools.iwencai_shareholder_query(task_id, query, page, limit)

    @T(description="同花顺问财搜索：channel 为 announcement（公告）/ news（财经新闻）/ report（研报）。返回标题、日期、来源、链接等。")
    async def iwencai_search(task_id: str, channel: str, query: str, size: int = 10) -> str:
        return await tools.iwencai_search(task_id, channel, query, size)

    return mcp, tools
