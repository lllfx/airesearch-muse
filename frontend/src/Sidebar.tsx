import { useState } from 'react'
import { LANE_OF, LANE_TITLE, STATUS_LABEL, type Lane, type Task } from './types'

const ORDER: Lane[] = ['you', 'agent', 'queue', 'closed']

export function Sidebar({ tasks, selected, onSelect, onNew, engine }: {
  tasks: Task[]; selected: string | null; onSelect: (id: string) => void; onNew: () => void
  engine: { engine: string; alive: boolean } | null
}) {
  const [q, setQ] = useState('')
  const [showClosed, setShowClosed] = useState(false)
  const visible = tasks.filter((t) => !t.parent_id && (!q || t.title.includes(q) || t.prompt.includes(q)))

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="logo">Muse</span>
        <span className="engine" title={engine ? engine.engine : ''}>
          <i className={engine?.alive ? 'on' : 'off'} />{engine ? (engine.engine === 'MockEngine' ? '演示' : engine.alive ? 'dsh' : '离线') : '…'}
        </span>
      </div>
      <button className="btn primary wide" onClick={onNew}>新任务</button>
      <input className="search" placeholder="搜任务" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="lanes">
        {ORDER.map((lane) => {
          const items = visible.filter((t) => LANE_OF[t.status] === lane)
          if (lane === 'closed' && !showClosed) {
            return items.length ? <button key={lane} className="lane-toggle" onClick={() => setShowClosed(true)}>已结束 {items.length} ›</button> : null
          }
          if (items.length === 0 && lane !== 'you') return null
          return (
            <section key={lane} className={`lane ${lane}`}>
              <h2>{LANE_TITLE[lane]}<span className="n">{items.length}</span>
                {lane === 'closed' && <button className="link" onClick={() => setShowClosed(false)}>收起</button>}
              </h2>
              {items.length === 0 && <div className="empty">没有要你做的事</div>}
              {items.map((t) => <Row key={t.id} t={t} active={t.id === selected} onClick={() => onSelect(t.id)} />)}
            </section>
          )
        })}
      </div>
    </aside>
  )
}

function Row({ t, active, onClick }: { t: Task; active: boolean; onClick: () => void }) {
  const done = t.plan.filter((s) => s.status === 'done').length
  const cue = t.status === 'waiting_input' ? '问了你一个问题' : t.status === 'waiting_approval' ? '等你审批一个动作'
    : t.status === 'review' ? '等你验收' : t.status === 'interrupted' ? '被中断，可恢复' : t.status === 'failed' ? '失败' : ''
  return (
    <button className={`row${active ? ' active' : ''}`} onClick={onClick}>
      <div className="row-title">{t.title}{t.priority >= 2 && <b className="pri">!</b>}</div>
      <div className="row-meta">
        <span className={`dot ${LANE_OF[t.status]}`} />{cue || STATUS_LABEL[t.status]}
        {t.plan.length > 0 && <span className="steps-n">{done}/{t.plan.length}</span>}
      </div>
      {t.plan.length > 0 && <div className="steps">{t.plan.map((s, i) => <i key={i} className={s.status} />)}</div>}
    </button>
  )
}
