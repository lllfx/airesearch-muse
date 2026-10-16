import { useEffect, useRef, useState } from 'react'
import { api, subscribe } from './api'
import { STATUS_LABEL, type ActionReview, type InputRequest, type TaskDetail, type TaskEvent } from './types'

export function TaskDrawer({ taskId, onClose, onChanged }: { taskId: string; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<TaskDetail | null>(null)
  const [events, setEvents] = useState<TaskEvent[]>([])
  const [busy, setBusy] = useState(false)
  const lastId = useRef(0)

  const load = () => api.getTask(taskId).then(setD).catch(console.error)

  useEffect(() => {
    lastId.current = 0
    setEvents([])
    load()
    api.events(taskId).then((evs) => { setEvents(evs); lastId.current = evs.at(-1)?.id ?? 0 })
    return subscribe({
      task: (t) => { if (t.id === taskId) load() },
      input_request: (r) => { if (r.task_id === taskId) load() },
      action_review: (r) => { if (r.task_id === taskId) load() },
      receipt: (r) => { if (r.task_id === taskId) load() },
      event: (e: TaskEvent) => { if (e.task_id === taskId && e.id > lastId.current) { lastId.current = e.id; setEvents((p) => [...p, e]) } },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!d) return null
  const { task } = d
  const pendingQ = d.input_requests.find((r) => !r.answered_at)
  const pendingA = d.action_reviews.find((r) => !r.decided_at)

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn(); await load(); onChanged() } catch (e) { alert(String(e)) } finally { setBusy(false) }
  }

  const tagCls = ['waiting_input', 'waiting_approval', 'review'].includes(task.status) ? 'you'
    : ['running', 'queued', 'interrupted'].includes(task.status) ? 'wait'
    : task.status === 'done' ? 'ok' : ['failed', 'cancelled'].includes(task.status) ? 'bad' : ''

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={task.title}>
        <header>
          <h2>{task.title} <span className={`tag ${tagCls}`}>{STATUS_LABEL[task.status]}</span></h2>
          <div className="row">
            {task.skill && <span className="tag">{task.skill}</span>}
            {task.retries > 0 && <span className="tag">已重试 {task.retries}/{task.max_retries}</span>}
            {['backlog', 'interrupted', 'failed'].includes(task.status) && (
              <button className="btn small primary" disabled={busy} onClick={() => act(() => api.dispatch(task.id))}>
                {task.status === 'backlog' ? '派发' : task.status === 'interrupted' ? '从断点恢复' : '重试'}
              </button>
            )}
            {task.status === 'review' && <>
              <button className="btn small ok" disabled={busy} onClick={() => act(() => api.accept(task.id))}>验收通过</button>
              <button className="btn small" disabled={busy} onClick={() => act(() => api.dispatch(task.id))}>打回重做</button>
            </>}
            {!['done', 'cancelled'].includes(task.status) && (
              <button className="btn small danger" disabled={busy} onClick={() => act(() => api.cancel(task.id))}>取消</button>
            )}
            {['done', 'cancelled', 'failed'].includes(task.status) && (
              <button className="btn small danger" disabled={busy} onClick={() => { if (confirm('删除这条任务及其记录？')) act(async () => { await api.remove(task.id); onClose() }) }}>删除</button>
            )}
            <button className="btn small close" onClick={onClose}>关闭</button>
          </div>
          {pendingQ && <AskInput req={pendingQ} busy={busy} onSubmit={(a) => act(() => api.answer(pendingQ.id, a))} />}
          {pendingA && <AskApproval rev={pendingA} busy={busy} onDecide={(dec, c) => act(() => api.decide(pendingA.id, dec, c))} />}
        </header>

        <div className="body">
          <section>
            <h3>交代的任务</h3>
            <div className="prompt">{task.prompt}</div>
          </section>

          {task.plan.length > 0 && (
            <section>
              <h3>计划</h3>
              <ul className="plan">{task.plan.map((s, i) => <li key={i} className={s.status}>{s.title}</li>)}</ul>
            </section>
          )}

          {task.summary && (
            <section>
              <h3>Agent 最近一句</h3>
              <div style={{ whiteSpace: 'pre-wrap' }}>{task.summary}</div>
            </section>
          )}

          <section>
            <h3>回执 {d.receipts.length > 0 && <span className="n">({d.receipts.length})</span>}</h3>
            {d.receipts.length === 0 ? <div style={{ color: 'var(--ink-3)' }}>还没有产出。任务做完后，每一份报告、数据文件或外发动作都会在这里留一条凭证。</div>
              : d.receipts.map((r) => (
                <div key={r.id} className="receipt">
                  <span className="kind">{r.kind}</span>
                  <div>
                    <div>{r.summary}</div>
                    {r.path && <div className="meta"><a href={api.fileUrl(task.id, r.path)} target="_blank" rel="noreferrer">{r.path}</a></div>}
                    {r.meta && <div className="meta">{r.meta}</div>}
                  </div>
                </div>
              ))}
          </section>

          {(d.input_requests.some((r) => r.answered_at) || d.action_reviews.some((r) => r.decided_at)) && (
            <section>
              <h3>已处理的问题与审批</h3>
              {d.input_requests.filter((r) => r.answered_at).map((r) => (
                <div key={r.id} className="receipt"><span className="kind" style={{ color: 'var(--you)' }}>问答</span><div>{r.question}<div className="meta">你答：{r.answer}</div></div></div>
              ))}
              {d.action_reviews.filter((r) => r.decided_at).map((r) => (
                <div key={r.id} className="receipt"><span className="kind" style={{ color: r.decision === 'approved' ? 'var(--ok)' : 'var(--bad)' }}>{r.decision === 'approved' ? '已批准' : '已拒绝'}</span><div>{r.action}<div className="meta">{r.comment || r.reason}</div></div></div>
              ))}
            </section>
          )}

          <section>
            <h3>时间线</h3>
            <Ledger events={events} />
          </section>
        </div>
      </aside>
    </>
  )
}

function AskInput({ req, busy, onSubmit }: { req: InputRequest; busy: boolean; onSubmit: (a: string) => void }) {
  const [a, setA] = useState('')
  return (
    <div className="ask">
      <div className="q">Agent 问：{req.question}</div>
      {req.options.length > 0 && (
        <div className="opts">{req.options.map((o) => <button key={o} className="btn small" disabled={busy} onClick={() => onSubmit(o)}>{o}</button>)}</div>
      )}
      <textarea value={a} onChange={(e) => setA(e.target.value)} placeholder="或者自己写一段回答…" />
      <div className="actions"><button className="btn primary" disabled={busy || !a.trim()} onClick={() => onSubmit(a.trim())}>回答并继续</button></div>
    </div>
  )
}

function AskApproval({ rev, busy, onDecide }: { rev: ActionReview; busy: boolean; onDecide: (d: 'approved' | 'rejected', c: string) => void }) {
  const [c, setC] = useState('')
  let payload = rev.payload
  try { payload = JSON.stringify(JSON.parse(rev.payload), null, 2) } catch { /* 原样显示 */ }
  return (
    <div className="ask">
      <div className="q">Agent 想执行：{rev.action}</div>
      {rev.reason && <div className="why">理由：{rev.reason}</div>}
      <pre>{payload}</pre>
      <textarea value={c} onChange={(e) => setC(e.target.value)} placeholder="给 Agent 的说明（可选）" />
      <div className="actions">
        <button className="btn danger" disabled={busy} onClick={() => onDecide('rejected', c)}>拒绝</button>
        <button className="btn ok" disabled={busy} onClick={() => onDecide('approved', c)}>批准</button>
      </div>
    </div>
  )
}

const KIND_LABEL: Record<string, string> = {
  'board/status': '状态', 'board/plan': '计划', 'board/input_requested': '提问', 'board/input_answered': '回答',
  'board/approval_requested': '申请审批', 'board/approval_decided': '审批', 'board/receipt': '回执',
  'session/assistant': 'Agent', 'session/tool_call': '调用', 'session/tool_result': '结果', 'session/turn_end': '轮次结束',
  'session/compaction_summary': '压缩上下文', 'session/llm_retry': '模型重试',
}

function Ledger({ events }: { events: TaskEvent[] }) {
  if (events.length === 0) return <div style={{ color: 'var(--ink-3)' }}>还没有记录。</div>
  return (
    <ul className="ledger">
      {events.map((e) => {
        const p = e.payload
        const t = new Date(e.created_at).toLocaleTimeString('zh-CN', { hour12: false })
        let body: React.ReactNode
        switch (e.kind) {
          case 'board/status': body = <>{STATUS_LABEL[p.from as keyof typeof STATUS_LABEL] ?? p.from} → {STATUS_LABEL[p.to as keyof typeof STATUS_LABEL] ?? p.to}{p.note && <span className="k"> · {p.note}</span>}</>; break
          case 'board/plan': body = <>{(p.steps as { title: string }[]).map((s) => s.title).join(' → ')}</>; break
          case 'board/input_requested': body = <>{p.question}</>; break
          case 'board/input_answered': body = <>{p.answer}</>; break
          case 'board/approval_requested': body = <>{p.action}{p.reason && ` — ${p.reason}`}</>; break
          case 'board/approval_decided': body = <>{p.decision === 'approved' ? '批准' : '拒绝'}{p.comment && ` — ${p.comment}`}</>; break
          case 'board/receipt': body = <>{p.kind} {p.path && `· ${p.path}`} — {p.summary}</>; break
          case 'session/assistant': body = <pre>{p.text}</pre>; break
          case 'session/tool_call': body = <>{p.name} <pre>{p.arguments}</pre></>; break
          case 'session/tool_result': body = <span className={p.error ? 'err' : ''}>{p.name} <pre>{p.text}</pre></span>; break
          case 'session/turn_end': body = <>{typeof p.reason === 'object' ? p.reason?.kind : p.reason}</>; break
          default: body = <>{JSON.stringify(p)}</>
        }
        return (
          <li key={e.id}>
            <span className="t">{t}</span>
            <span><span className={`k${e.kind.startsWith('board/') ? ' board' : ''}`}>{KIND_LABEL[e.kind] ?? e.kind}</span>{body}</span>
          </li>
        )
      })}
    </ul>
  )
}
