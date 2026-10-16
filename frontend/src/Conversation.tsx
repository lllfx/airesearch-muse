import { useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribe } from './api'
import { ApprovalCard, ChartCard, DataCard, FileCard, MarkdownCard, PlanCard, QuestionCard, ReceiptCard, SubtaskCard, SystemLine, TableCard, ToolCard } from './cards'
import { Markdown } from './md'
import { STATUS_LABEL, type ActionReview, type InputRequest, type Task, type TaskDetail, type TaskEvent } from './types'

/** 对话节点：从时间线事件折叠出来。工具调用和结果按 call_id 配对成一个节点。 */
export type Node =
  | { kind: 'user'; id: string; text: string; at: string }
  | { kind: 'assistant'; id: string; text: string; at: string }
  | { kind: 'tool'; id: string; name: string; args: string; result?: string; error?: boolean; at: string }
  | { kind: 'card'; id: string; card: Record<string, any>; at: string }
  | { kind: 'plan'; id: string; steps: { title: string; status: string }[]; at: string }
  | { kind: 'question'; id: string; reqId: string; at: string }
  | { kind: 'approval'; id: string; revId: string; at: string }
  | { kind: 'receipt'; id: string; receipt: Record<string, any>; at: string }
  | { kind: 'subtask'; id: string; sub: Record<string, any>; at: string }
  | { kind: 'system'; id: string; text: string; at: string }

export function assemble(events: TaskEvent[]): Node[] {
  const out: Node[] = []
  const toolIdx = new Map<string, number>()
  for (const e of events) {
    const p = e.payload
    const id = String(e.id)
    const at = e.created_at
    switch (e.kind) {
      case 'board/user_message': out.push({ kind: 'user', id, text: p.text, at }); break
      case 'session/assistant': out.push({ kind: 'assistant', id, text: p.text, at }); break
      case 'session/tool_call':
        if (String(p.name ?? '').startsWith('mcp__muse__')) break   // 看板工具已经有自己的卡片
        toolIdx.set(p.call_id ?? id, out.length)
        out.push({ kind: 'tool', id, name: p.name, args: p.arguments ?? '', at }); break
      case 'session/tool_result': {
        if (String(p.name ?? '').startsWith('mcp__muse__')) break
        const i = toolIdx.get(p.call_id)
        if (i !== undefined && out[i].kind === 'tool') { (out[i] as any).result = p.text; (out[i] as any).error = p.error }
        else out.push({ kind: 'tool', id, name: p.name ?? 'tool', args: '', result: p.text, error: p.error, at })
        break
      }
      case 'board/card': out.push({ kind: 'card', id, card: p, at }); break
      case 'board/plan': {
        const last = out[out.length - 1]
        if (last && last.kind === 'plan') out[out.length - 1] = { kind: 'plan', id, steps: p.steps, at }   // 连续更新只留最新
        else out.push({ kind: 'plan', id, steps: p.steps, at })
        break
      }
      case 'board/input_requested': out.push({ kind: 'question', id, reqId: p.id, at }); break
      case 'board/approval_requested': out.push({ kind: 'approval', id, revId: p.id, at }); break
      case 'board/receipt': out.push({ kind: 'receipt', id, receipt: p, at }); break
      case 'board/subtask': {
        const i = out.findIndex((n) => n.kind === 'subtask' && n.sub.id === p.id)
        if (i >= 0) out[i] = { ...(out[i] as any), sub: { ...(out[i] as any).sub, ...p } }   // 同一子任务的后续状态只更新原卡片
        else out.push({ kind: 'subtask', id, sub: p, at })
        break
      }
      case 'board/status': {
        const text = `${STATUS_LABEL[p.from as keyof typeof STATUS_LABEL] ?? p.from} → ${STATUS_LABEL[p.to as keyof typeof STATUS_LABEL] ?? p.to}${p.note ? ` · ${p.note}` : ''}`
        out.push({ kind: 'system', id, text, at }); break
      }
      case 'session/turn_end': {
        const r = typeof p.reason === 'object' ? p.reason?.kind : p.reason
        if (r && r !== 'completed') out.push({ kind: 'system', id, text: `本轮结束：${r}`, at })
        break
      }
      case 'session/compaction_summary': out.push({ kind: 'system', id, text: '上下文已压缩', at }); break
      case 'session/llm_retry': out.push({ kind: 'system', id, text: '模型请求重试', at }); break
      default: break
    }
  }
  return out
}

export function Conversation({ task, rightOpen, onToggleRight }: { task: Task; rightOpen: boolean; onToggleRight: () => void }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [showSystem, setShowSystem] = useState(false)
  const lastId = useRef(0)
  const scroller = useRef<HTMLDivElement>(null)
  const stickBottom = useRef(true)

  const load = () => api.getTask(task.id).then(setDetail).catch(console.error)

  useEffect(() => {
    // 先订阅再拉历史，否则两者之间产生的事件会丢；用 id 去重合并
    const merge = (incoming: TaskEvent[]) => setEvents((prev) => {
      const seen = new Set(prev.map((e) => e.id))
      const add = incoming.filter((e) => !seen.has(e.id))
      if (add.length === 0) return prev
      const next = [...prev, ...add].sort((a, b) => a.id - b.id)
      lastId.current = next[next.length - 1].id
      return next
    })
    const off = subscribe({
      hello: () => { api.events(task.id, lastId.current).then(merge); load() },   // 流刚接通：补齐连接前漏掉的
      task: (t) => { if (t.id === task.id) load() },
      input_request: (r) => { if (r.task_id === task.id) load() },
      action_review: (r) => { if (r.task_id === task.id) load() },
      receipt: (r) => { if (r.task_id === task.id) load() },
      event: (e: TaskEvent) => { if (e.task_id === task.id) merge([e]) },
    })
    load()
    api.events(task.id).then(merge)
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id])

  const nodes = useMemo(() => assemble(events), [events])

  useEffect(() => {
    const el = scroller.current
    if (el && stickBottom.current) el.scrollTop = el.scrollHeight
  }, [nodes.length, detail])

  const onScroll = () => {
    const el = scroller.current
    if (el) stickBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn() } catch (e) { alert(String(e)) } finally { setBusy(false) }
  }

  const reqById = new Map((detail?.input_requests ?? []).map((r) => [r.id, r]))
  const revById = new Map((detail?.action_reviews ?? []).map((r) => [r.id, r]))
  const t = detail?.task ?? task

  return (
    <div className="conv">
      <header className="conv-head">
        <div className="conv-title">
          <h1>{t.title}</h1>
          <span className={`tag ${tagCls(t.status)}`}>{STATUS_LABEL[t.status]}</span>
          {t.skill && <span className="tag">{t.skill}</span>}
          {t.parent_id && <a className="tag" href={`#/task/${t.parent_id}`}>子任务 ↑</a>}
        </div>
        <div className="conv-actions">
          {['backlog', 'interrupted', 'failed'].includes(t.status) && (
            <button className="btn small primary" disabled={busy} onClick={() => act(() => api.dispatch(t.id))}>
              {t.status === 'backlog' ? '派发' : t.status === 'interrupted' ? '从断点恢复' : '重试'}
            </button>
          )}
          {t.status === 'review' && <>
            <button className="btn small ok" disabled={busy} onClick={() => act(() => api.accept(t.id))}>验收通过</button>
            <button className="btn small" disabled={busy} onClick={() => act(() => api.dispatch(t.id))}>打回重做</button>
          </>}
          {!['done', 'cancelled'].includes(t.status) && <button className="btn small danger" disabled={busy} onClick={() => act(() => api.cancel(t.id))}>取消</button>}
          {['done', 'cancelled', 'failed'].includes(t.status) && (
            <button className="btn small danger" disabled={busy} onClick={() => { if (confirm('删除这条任务及其记录？')) act(async () => { await api.remove(t.id); location.hash = '' }) }}>删除</button>
          )}
          <button className={`btn small${showSystem ? ' on' : ''}`} onClick={() => setShowSystem((v) => !v)} title="显示状态变化等系统记录">细节</button>
          <button className="btn small" onClick={onToggleRight}>{rightOpen ? '收起侧栏' : '侧栏'}</button>
        </div>
      </header>

      <div className="conv-scroll" ref={scroller} onScroll={onScroll}>
        <div className="conv-body">
          <div className="msg user first"><div className="bubble"><Markdown text={t.prompt} /></div></div>
          {nodes.map((n) => {
            switch (n.kind) {
              case 'user': return <div key={n.id} className="msg user"><div className="bubble"><Markdown text={n.text} /></div></div>
              case 'assistant': return <div key={n.id} className="msg assistant"><Markdown text={n.text} /></div>
              case 'tool': return <ToolCard key={n.id} name={n.name} args={n.args} result={n.result} error={n.error} />
              case 'plan': return <PlanCard key={n.id} steps={n.steps} />
              case 'question': {
                const r = reqById.get(n.reqId) as InputRequest | undefined
                return r ? <QuestionCard key={n.id} req={r} busy={busy} onAnswer={(a) => act(() => api.answer(r.id, a))} /> : null
              }
              case 'approval': {
                const r = revById.get(n.revId) as ActionReview | undefined
                return r ? <ApprovalCard key={n.id} rev={r} busy={busy} onDecide={(d, c) => act(() => api.decide(r.id, d, c))} /> : null
              }
              case 'receipt': return <ReceiptCard key={n.id} r={n.receipt} taskId={t.id} />
              case 'subtask': return <SubtaskCard key={n.id} sub={n.sub} />
              case 'card':
                switch (n.card.type) {
                  case 'table': return <TableCard key={n.id} c={n.card} />
                  case 'chart': return <ChartCard key={n.id} c={n.card} />
                  case 'file': return <FileCard key={n.id} c={n.card} taskId={t.id} />
                  case 'markdown': return <MarkdownCard key={n.id} c={n.card} />
                  case 'data': return <DataCard key={n.id} c={n.card} taskId={t.id} />
                  default: return null
                }
              case 'system': return showSystem ? <SystemLine key={n.id} text={n.text} at={n.at} /> : null
            }
          })}
          {t.status === 'running' && <div className="thinking"><i /><i /><i />Agent 正在工作</div>}
        </div>
      </div>

      <Composer task={t} busy={busy} onSend={(text) => act(() => api.say(t.id, text))} />
    </div>
  )
}

function Composer({ task, busy, onSend }: { task: Task; busy: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState('')
  const hint = task.status === 'running' ? '正在跑，发的消息会在当前轮结束后送达'
    : task.status === 'waiting_input' ? '直接回答上面的问题，或在这里输入'
    : task.status === 'waiting_approval' ? '先在上面批准或拒绝；这里的消息会在决定后送达'
    : task.status === 'cancelled' ? '任务已取消，不能再发消息'
    : task.status === 'done' || task.status === 'review' ? '发消息会让 Agent 继续这个任务'
    : task.status === 'queued' || task.status === 'backlog' ? '任务启动后会一并送达'
    : ''
  const send = () => { if (text.trim()) { onSend(text.trim()); setText('') } }
  return (
    <div className="composer">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} disabled={task.status === 'cancelled'}
        placeholder="跟 Agent 说点什么，比如补充要求、追问、纠正方向…"
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send() }} />
      <div className="composer-row">
        <span className="hint">{hint}</span>
        <button className="btn primary" disabled={busy || !text.trim() || task.status === 'cancelled'} onClick={send}>发送 ⌘↵</button>
      </div>
    </div>
  )
}

export function tagCls(s: Task['status']): string {
  return ['waiting_input', 'waiting_approval', 'review'].includes(s) ? 'you'
    : ['running', 'queued', 'interrupted'].includes(s) ? 'wait'
    : s === 'done' ? 'ok' : ['failed', 'cancelled'].includes(s) ? 'bad' : ''
}
