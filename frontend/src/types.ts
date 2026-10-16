export type Status =
  | 'backlog' | 'queued' | 'running' | 'waiting_input' | 'waiting_approval'
  | 'interrupted' | 'review' | 'done' | 'failed' | 'cancelled'

export interface PlanStep { title: string; status: 'pending' | 'doing' | 'done' | 'skipped' }

export interface Task {
  id: string; title: string; prompt: string; skill: string; status: Status; priority: number
  requires_review: boolean; max_retries: number; retries: number; session_id: string; workspace: string
  plan: PlanStep[]; error: string; summary: string
  created_at: string; updated_at: string; started_at: string | null; finished_at: string | null
}

export interface InputRequest {
  id: string; task_id: string; question: string; options: string[]; answer: string
  created_at: string; answered_at: string | null
}

export interface ActionReview {
  id: string; task_id: string; action: string; payload: string; reason: string
  decision: string; comment: string; created_at: string; decided_at: string | null
}

export interface Receipt {
  id: string; task_id: string; kind: string; path: string; summary: string; meta: string; created_at: string
}

export interface TaskEvent { id: number; task_id: string; kind: string; payload: Record<string, any>; created_at: string }

export interface TaskDetail { task: Task; input_requests: InputRequest[]; action_reviews: ActionReview[]; receipts: Receipt[] }

/** 看板分列：按"该谁动"而不是按时间阶段 */
export type Lane = 'you' | 'agent' | 'queue' | 'closed'

export const LANE_OF: Record<Status, Lane> = {
  waiting_input: 'you', waiting_approval: 'you', review: 'you', interrupted: 'you', failed: 'you',
  running: 'agent',
  queued: 'queue', backlog: 'queue',
  done: 'closed', cancelled: 'closed',
}

export const STATUS_LABEL: Record<Status, string> = {
  backlog: '待派发', queued: '排队中', running: '进行中', waiting_input: '等你回答', waiting_approval: '等你审批',
  interrupted: '已中断', review: '等你验收', done: '已完成', failed: '失败', cancelled: '已取消',
}
