import type { LibraryItem, MemoryItem, Schedule, SkillInfo, Task, TaskDetail, TaskEvent } from './types'

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.status === 204 ? (undefined as T) : res.json()
}
const post = (body?: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  listTasks: () => fetch('/api/tasks').then(j<Task[]>),
  getTask: (id: string) => fetch(`/api/tasks/${id}`).then(j<TaskDetail>),
  events: (id: string, after = 0) => fetch(`/api/tasks/${id}/events?after=${after}&limit=2000`).then(j<TaskEvent[]>),
  children: (id: string) => fetch(`/api/tasks/${id}/children`).then(j<Task[]>),
  createTask: (b: { title: string; prompt: string; skill?: string; priority?: number; requires_review?: boolean; dispatch?: boolean }) =>
    fetch('/api/tasks', post(b)).then(j<Task>),
  say: (id: string, text: string) => fetch(`/api/tasks/${id}/say`, post({ text })).then(j<Task>),
  dispatch: (id: string) => fetch(`/api/tasks/${id}/dispatch`, post()).then(j<Task>),
  cancel: (id: string) => fetch(`/api/tasks/${id}/cancel`, post()).then(j<Task>),
  accept: (id: string) => fetch(`/api/tasks/${id}/accept`, post()).then(j<Task>),
  remove: (id: string) => fetch(`/api/tasks/${id}`, { method: 'DELETE' }).then(j<void>),
  answer: (reqId: string, answer: string) => fetch(`/api/input-requests/${reqId}/answer`, post({ answer })).then(j<Task>),
  decide: (revId: string, decision: 'approved' | 'rejected', comment: string) =>
    fetch(`/api/action-reviews/${revId}/decide`, post({ decision, comment })).then(j<Task>),
  fileUrl: (taskId: string, path: string) => `/api/tasks/${taskId}/files/${path}`,
  memory: () => fetch('/api/memory').then(j<MemoryItem[]>),
  memoryPut: (key: string, value: string) => fetch(`/api/memory/${encodeURIComponent(key)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }).then(j<MemoryItem>),
  memoryDelete: (key: string) => fetch(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }).then(j<void>),
  library: (q = '') => fetch(`/api/library?q=${encodeURIComponent(q)}`).then(j<LibraryItem[]>),
  schedules: () => fetch('/api/schedules').then(j<Schedule[]>),
  scheduleCreate: (b: { title: string; prompt: string; skill?: string; every: string }) => fetch('/api/schedules', post(b)).then(j<Schedule>),
  scheduleToggle: (id: string) => fetch(`/api/schedules/${id}/toggle`, post()).then(j<Schedule>),
  scheduleDelete: (id: string) => fetch(`/api/schedules/${id}`, { method: 'DELETE' }).then(j<void>),
  skills: () => fetch('/api/skills').then(j<SkillInfo[]>),
  health: () => fetch('/api/health').then(j<{ engine: string; alive: boolean }>),
}

/** 订阅后端 SSE。返回取消函数。 */
export function subscribe(handlers: Record<string, (data: any) => void>): () => void {
  const es = new EventSource('/api/stream')
  for (const [k, fn] of Object.entries(handlers)) es.addEventListener(k, (e) => fn(JSON.parse((e as MessageEvent).data)))
  return () => es.close()
}
