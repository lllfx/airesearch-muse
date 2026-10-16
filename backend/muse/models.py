"""任务板的领域模型。四个一等对象：任务、输入请求、动作审批、回执；外加任务时间线事件。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    backlog = "backlog"                    # 已创建，未派发
    queued = "queued"                      # 已派发，等 runner 领取
    running = "running"                    # agent 正在跑
    waiting_input = "waiting_input"        # agent 提了问题，等人回答
    waiting_approval = "waiting_approval"  # agent 要做外发动作，等人批准
    interrupted = "interrupted"            # 后端重启 / 进程异常，可恢复
    review = "review"                      # agent 做完，等人验收
    done = "done"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (Status.done, Status.failed, Status.cancelled)

    @property
    def waiting(self) -> bool:
        return self in (Status.waiting_input, Status.waiting_approval)


# 合法迁移表。runner 和 API 都只通过 transition() 改状态，保证看板上不会出现非法跳转。
TRANSITIONS: dict[Status, set[Status]] = {
    Status.backlog: {Status.queued, Status.cancelled},
    Status.queued: {Status.running, Status.cancelled, Status.backlog},
    Status.running: {Status.waiting_input, Status.waiting_approval, Status.review, Status.done, Status.failed, Status.interrupted, Status.cancelled},
    Status.waiting_input: {Status.running, Status.cancelled, Status.interrupted},
    Status.waiting_approval: {Status.running, Status.cancelled, Status.interrupted},
    Status.interrupted: {Status.queued, Status.cancelled, Status.failed},
    Status.review: {Status.done, Status.queued, Status.cancelled, Status.running},   # 打回重跑，或聊天中重开
    Status.failed: {Status.queued, Status.cancelled, Status.running},
    Status.done: {Status.running},                                   # 聊天继续 = 重开
    Status.cancelled: set(),
}


class PlanStep(BaseModel):
    title: str
    status: str = "pending"  # pending | doing | done | skipped


class Task(BaseModel):
    id: str
    title: str
    prompt: str
    skill: str = ""
    status: Status = Status.backlog
    priority: int = 1  # 0 低 1 中 2 高
    requires_review: bool = True
    max_retries: int = 1
    retries: int = 0
    session_id: str = ""  # dsh 会话 id；有它就能从断点恢复
    parent_id: str = ""   # 子任务指向父任务；父任务在子任务结束时收到通知
    schedule_id: str = ""  # 由定时计划创建的任务
    workspace: str = ""
    plan: list[PlanStep] = Field(default_factory=list)
    error: str = ""
    summary: str = ""  # agent 最后一段回复
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FormField(BaseModel):
    name: str
    label: str
    type: str = "text"  # text | number | date | choice | multichoice
    options: list[str] = Field(default_factory=list)
    required: bool = True


class InputRequest(BaseModel):
    id: str
    task_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    fields: list[FormField] = Field(default_factory=list)  # 非空则渲染成表单，answer 为 JSON
    answer: str = ""
    created_at: datetime
    answered_at: datetime | None = None


class ActionReview(BaseModel):
    id: str
    task_id: str
    action: str        # send_email / publish / trade ...
    payload: str       # JSON 文本，agent 准备好的参数
    reason: str = ""
    decision: str = ""  # approved | rejected
    comment: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class Receipt(BaseModel):
    id: str
    task_id: str
    kind: str  # report | data | file | action
    path: str = ""
    summary: str
    meta: str = ""  # JSON 文本：数据源、窗口、校验和
    created_at: datetime


class Event(BaseModel):
    id: int
    task_id: str
    kind: str      # board/* 来自看板，session/* 来自 dsh
    payload: dict
    created_at: datetime


class MemoryItem(BaseModel):
    key: str
    value: str
    task_id: str = ""
    updated_at: datetime


class LibraryItem(BaseModel):
    id: str
    task_id: str
    title: str
    summary: str
    path: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime


class Schedule(BaseModel):
    id: str
    title: str
    prompt: str
    skill: str = ""
    every: str          # "30m" | "2h" | "daily 09:30" | "weekdays 09:30"
    enabled: bool = True
    next_run_at: datetime
    last_task_id: str = ""
    created_by_task: str = ""
    created_at: datetime


# ---- API 请求体 ----

class SayBody(BaseModel):
    text: str

class TaskCreate(BaseModel):
    title: str
    prompt: str
    skill: str = ""
    priority: int = 1
    requires_review: bool = True
    max_retries: int = 1
    dispatch: bool = True  # 创建后立即派发


class TaskUpdate(BaseModel):
    title: str | None = None
    prompt: str | None = None
    priority: int | None = None
    requires_review: bool | None = None


class AnswerBody(BaseModel):
    answer: str


class DecisionBody(BaseModel):
    decision: str  # approved | rejected
    comment: str = ""


class TaskDetail(BaseModel):
    task: Task
    input_requests: list[InputRequest]
    action_reviews: list[ActionReview]
    receipts: list[Receipt]
