import type { Task, TaskDetail, TaskEvent } from './types'

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.status === 204 ? (undefined as T) : res.json()
}
const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  listTasks: () => fetch('/api/tasks').then(j<Task[]>),
  getTask: (id: string) => fetch(`/api/tasks/${id}`).then(j<TaskDetail>),
  events: (id: string, after = 0) => fetch(`/api/tasks/${id}/events?after=${after}`).then(j<TaskEvent[]>),
  createTask: (b: { title: string; prompt: string; skill?: string; priority?: number; requires_review?: boolean; dispatch?: boolean }) =>
    fetch('/api/tasks', json(b)).then(j<Task>),
  dispatch: (id: string) => fetch(`/api/tasks/${id}/dispatch`, { method: 'POST' }).then(j<Task>),
  cancel: (id: string) => fetch(`/api/tasks/${id}/cancel`, { method: 'POST' }).then(j<Task>),
  accept: (id: string) => fetch(`/api/tasks/${id}/accept`, { method: 'POST' }).then(j<Task>),
  remove: (id: string) => fetch(`/api/tasks/${id}`, { method: 'DELETE' }).then(j<void>),
  answer: (reqId: string, answer: string) => fetch(`/api/input-requests/${reqId}/answer`, json({ answer })).then(j<Task>),
  decide: (revId: string, decision: 'approved' | 'rejected', comment: string) =>
    fetch(`/api/action-reviews/${revId}/decide`, json({ decision, comment })).then(j<Task>),
  fileUrl: (taskId: string, path: string) => `/api/tasks/${taskId}/files/${path}`,
}

/** 订阅后端 SSE。返回取消函数。 */
export function subscribe(handlers: Record<string, (data: any) => void>): () => void {
  const es = new EventSource('/api/stream')
  for (const [k, fn] of Object.entries(handlers)) es.addEventListener(k, (e) => fn(JSON.parse((e as MessageEvent).data)))
  return () => es.close()
}
