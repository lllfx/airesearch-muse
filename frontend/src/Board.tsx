import { LANE_OF, STATUS_LABEL, type Lane, type Task } from './types'

const LANES: { key: Lane; title: string; hint: string }[] = [
  { key: 'you', title: '等你处理', hint: '回答、审批、验收或恢复' },
  { key: 'agent', title: 'Agent 进行中', hint: '' },
  { key: 'queue', title: '排队', hint: '' },
  { key: 'closed', title: '已结束', hint: '' },
]

export function Board({ tasks, selected, onSelect }: { tasks: Task[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="board">
      {LANES.map((lane) => {
        const items = tasks.filter((t) => LANE_OF[t.status] === lane.key)
        return (
          <div key={lane.key} className={`lane ${lane.key}`}>
            <h2>{lane.title} <span className="n">{items.length}</span></h2>
            {items.length === 0 && <div className="empty">{lane.key === 'you' ? '暂时没有需要你的事。' : lane.key === 'queue' ? '点右上角「新任务」交代一件事。' : '—'}</div>}
            {items.map((t) => <Card key={t.id} t={t} active={t.id === selected} onClick={() => onSelect(t.id)} />)}
          </div>
        )
      })}
    </div>
  )
}

function Card({ t, active, onClick }: { t: Task; active: boolean; onClick: () => void }) {
  const done = t.plan.filter((s) => s.status === 'done').length
  return (
    <button className={`card${active ? ' active' : ''}`} onClick={onClick}>
      <div className="title"><span>{t.title}</span>{t.priority >= 2 && <span className="pri" title="高优先级">!</span>}</div>
      <div className="meta">
        <span>{STATUS_LABEL[t.status]}</span>
        {t.skill && <span>{t.skill}</span>}
        {t.plan.length > 0 && <span>{done}/{t.plan.length} 步</span>}
      </div>
      {t.plan.length > 0 && (
        <div className="steps" aria-label={`计划进度 ${done}/${t.plan.length}`}>
          {t.plan.map((s, i) => <i key={i} className={s.status} title={s.title} />)}
        </div>
      )}
      <Cue t={t} />
      {t.summary && !cueFor(t) && <div className="summary">{t.summary}</div>}
    </button>
  )
}

function cueFor(t: Task): { cls: string; text: string } | null {
  switch (t.status) {
    case 'waiting_input': return { cls: 'you', text: 'Agent 提了一个问题，等你回答' }
    case 'waiting_approval': return { cls: 'you', text: '有一个外发动作等你审批' }
    case 'review': return { cls: 'you', text: '已完成，等你验收' }
    case 'interrupted': return { cls: 'wait', text: '上次运行被中断，可以恢复' }
    case 'failed': return { cls: 'bad', text: t.error ? `失败：${t.error.slice(0, 80)}` : '失败' }
    default: return null
  }
}

function Cue({ t }: { t: Task }) {
  const c = cueFor(t)
  return c ? <div className={`cue ${c.cls}`}>{c.text}</div> : null
}
