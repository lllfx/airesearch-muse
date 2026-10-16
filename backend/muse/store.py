"""SQLite 存储。同步 sqlite3 + 一把锁；FastAPI 的同步 handler 跑在线程池里，足够。"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import ActionReview, Event, InputRequest, PlanStep, Receipt, Status, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, skill TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 1, requires_review INTEGER NOT NULL DEFAULT 1,
  max_retries INTEGER NOT NULL DEFAULT 1, retries INTEGER NOT NULL DEFAULT 0,
  session_id TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '', plan TEXT NOT NULL DEFAULT '[]',
  error TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS input_requests (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  question TEXT NOT NULL, options TEXT NOT NULL DEFAULT '[]', answer TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, answered_at TEXT
);
CREATE TABLE IF NOT EXISTS action_reviews (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  action TEXT NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, path TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id, id);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        self._db.close()

    # ---------- tasks ----------

    @staticmethod
    def _task(r: sqlite3.Row) -> Task:
        return Task(
            id=r["id"], title=r["title"], prompt=r["prompt"], skill=r["skill"], status=Status(r["status"]),
            priority=r["priority"], requires_review=bool(r["requires_review"]), max_retries=r["max_retries"],
            retries=r["retries"], session_id=r["session_id"], workspace=r["workspace"],
            plan=[PlanStep(**p) for p in json.loads(r["plan"] or "[]")], error=r["error"], summary=r["summary"],
            created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
            started_at=_dt(r["started_at"]), finished_at=_dt(r["finished_at"]),
        )

    def create_task(self, **fields) -> Task:
        t = Task(id=new_id("task"), created_at=now(), updated_at=now(), **fields)
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id,title,prompt,skill,status,priority,requires_review,max_retries,retries,session_id,workspace,plan,error,summary,created_at,updated_at,started_at,finished_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t.id, t.title, t.prompt, t.skill, t.status.value, t.priority, int(t.requires_review), t.max_retries, t.retries,
                 t.session_id, t.workspace, json.dumps([p.model_dump() for p in t.plan]), t.error, t.summary,
                 t.created_at.isoformat(), t.updated_at.isoformat(), None, None),
            )
        return t

    def save_task(self, t: Task) -> Task:
        t.updated_at = now()
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET title=?,prompt=?,skill=?,status=?,priority=?,requires_review=?,max_retries=?,retries=?,session_id=?,workspace=?,plan=?,error=?,summary=?,updated_at=?,started_at=?,finished_at=? WHERE id=?",
                (t.title, t.prompt, t.skill, t.status.value, t.priority, int(t.requires_review), t.max_retries, t.retries,
                 t.session_id, t.workspace, json.dumps([p.model_dump() for p in t.plan]), t.error, t.summary,
                 t.updated_at.isoformat(), t.started_at.isoformat() if t.started_at else None,
                 t.finished_at.isoformat() if t.finished_at else None, t.id),
            )
            if cur.rowcount == 0:
                raise KeyError(t.id)
        return t

    def get_task(self, task_id: str) -> Task | None:
        r = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(r) if r else None

    def list_tasks(self, *statuses: Status) -> list[Task]:
        if statuses:
            q = f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY priority DESC, created_at"
            rows = self._db.execute(q, [s.value for s in statuses]).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM tasks ORDER BY priority DESC, created_at DESC").fetchall()
        return [self._task(r) for r in rows]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    # ---------- input requests ----------

    @staticmethod
    def _inq(r: sqlite3.Row) -> InputRequest:
        return InputRequest(id=r["id"], task_id=r["task_id"], question=r["question"], options=json.loads(r["options"]),
                            answer=r["answer"], created_at=_dt(r["created_at"]), answered_at=_dt(r["answered_at"]))

    def create_input_request(self, task_id: str, question: str, options: list[str] | None = None) -> InputRequest:
        r = InputRequest(id=new_id("inq"), task_id=task_id, question=question, options=options or [], created_at=now())
        with self._lock:
            self._db.execute("INSERT INTO input_requests (id,task_id,question,options,answer,created_at) VALUES (?,?,?,?,?,?)",
                             (r.id, r.task_id, r.question, json.dumps(r.options), "", r.created_at.isoformat()))
        return r

    def answer_input_request(self, req_id: str, answer: str) -> InputRequest:
        with self._lock:
            cur = self._db.execute("UPDATE input_requests SET answer=?, answered_at=? WHERE id=? AND answered_at IS NULL",
                                   (answer, now().isoformat(), req_id))
            if cur.rowcount == 0:
                raise KeyError(req_id)
        return self._inq(self._db.execute("SELECT * FROM input_requests WHERE id=?", (req_id,)).fetchone())

    def list_input_requests(self, task_id: str) -> list[InputRequest]:
        return [self._inq(r) for r in self._db.execute("SELECT * FROM input_requests WHERE task_id=? ORDER BY created_at", (task_id,))]

    def pending_input_request(self, task_id: str) -> InputRequest | None:
        r = self._db.execute("SELECT * FROM input_requests WHERE task_id=? AND answered_at IS NULL ORDER BY created_at LIMIT 1", (task_id,)).fetchone()
        return self._inq(r) if r else None

    # ---------- action reviews ----------

    @staticmethod
    def _rev(r: sqlite3.Row) -> ActionReview:
        return ActionReview(id=r["id"], task_id=r["task_id"], action=r["action"], payload=r["payload"], reason=r["reason"],
                            decision=r["decision"], comment=r["comment"], created_at=_dt(r["created_at"]), decided_at=_dt(r["decided_at"]))

    def create_action_review(self, task_id: str, action: str, payload: str, reason: str = "") -> ActionReview:
        r = ActionReview(id=new_id("rev"), task_id=task_id, action=action, payload=payload, reason=reason, created_at=now())
        with self._lock:
            self._db.execute("INSERT INTO action_reviews (id,task_id,action,payload,reason,created_at) VALUES (?,?,?,?,?,?)",
                             (r.id, r.task_id, r.action, r.payload, r.reason, r.created_at.isoformat()))
        return r

    def decide_action_review(self, rev_id: str, decision: str, comment: str = "") -> ActionReview:
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        with self._lock:
            cur = self._db.execute("UPDATE action_reviews SET decision=?, comment=?, decided_at=? WHERE id=? AND decided_at IS NULL",
                                   (decision, comment, now().isoformat(), rev_id))
            if cur.rowcount == 0:
                raise KeyError(rev_id)
        return self._rev(self._db.execute("SELECT * FROM action_reviews WHERE id=?", (rev_id,)).fetchone())

    def list_action_reviews(self, task_id: str) -> list[ActionReview]:
        return [self._rev(r) for r in self._db.execute("SELECT * FROM action_reviews WHERE task_id=? ORDER BY created_at", (task_id,))]

    def pending_action_review(self, task_id: str) -> ActionReview | None:
        r = self._db.execute("SELECT * FROM action_reviews WHERE task_id=? AND decided_at IS NULL ORDER BY created_at LIMIT 1", (task_id,)).fetchone()
        return self._rev(r) if r else None

    # ---------- receipts ----------

    def create_receipt(self, task_id: str, kind: str, summary: str, path: str = "", meta: str = "") -> Receipt:
        r = Receipt(id=new_id("rcpt"), task_id=task_id, kind=kind, path=path, summary=summary, meta=meta, created_at=now())
        with self._lock:
            self._db.execute("INSERT INTO receipts (id,task_id,kind,path,summary,meta,created_at) VALUES (?,?,?,?,?,?,?)",
                             (r.id, r.task_id, r.kind, r.path, r.summary, r.meta, r.created_at.isoformat()))
        return r

    def list_receipts(self, task_id: str) -> list[Receipt]:
        return [Receipt(id=r["id"], task_id=r["task_id"], kind=r["kind"], path=r["path"], summary=r["summary"], meta=r["meta"], created_at=_dt(r["created_at"]))
                for r in self._db.execute("SELECT * FROM receipts WHERE task_id=? ORDER BY created_at", (task_id,))]

    # ---------- events ----------

    def append_event(self, task_id: str, kind: str, payload: dict) -> Event:
        ts = now()
        with self._lock:
            cur = self._db.execute("INSERT INTO events (task_id,kind,payload,created_at) VALUES (?,?,?,?)",
                                   (task_id, kind, json.dumps(payload, ensure_ascii=False, default=str), ts.isoformat()))
            eid = cur.lastrowid
        return Event(id=eid, task_id=task_id, kind=kind, payload=payload, created_at=ts)

    def list_events(self, task_id: str, after_id: int = 0, limit: int = 500) -> list[Event]:
        rows = self._db.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?", (task_id, after_id, min(limit, 2000)))
        return [Event(id=r["id"], task_id=r["task_id"], kind=r["kind"], payload=json.loads(r["payload"]), created_at=_dt(r["created_at"])) for r in rows]
