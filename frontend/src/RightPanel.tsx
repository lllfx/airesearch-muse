import { useEffect, useState } from 'react'
import { api, subscribe } from './api'
import { STATUS_LABEL, type LibraryItem, type MemoryItem, type Receipt, type Schedule, type Task } from './types'

type Tab = 'task' | 'library' | 'memory' | 'schedules'

export function RightPanel({ task, tasks, onSelect }: { task: Task | null; tasks: Task[]; onSelect: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>('task')
  useEffect(() => { if (task) setTab('task') }, [task?.id])
  return (
    <aside className="right">
      <nav className="tabs">
        {(['task', 'library', 'memory', 'schedules'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)} disabled={t === 'task' && !task}>
            {{ task: '本任务', library: '资料库', memory: '记忆', schedules: '定时' }[t]}
          </button>
        ))}
      </nav>
      <div className="right-body">
        {tab === 'task' && task && <TaskPanel task={task} tasks={tasks} onSelect={onSelect} />}
        {tab === 'library' && <LibraryPanel />}
        {tab === 'memory' && <MemoryPanel />}
        {tab === 'schedules' && <SchedulePanel />}
      </div>
    </aside>
  )
}

function TaskPanel({ task, tasks, onSelect }: { task: Task; tasks: Task[]; onSelect: (id: string) => void }) {
  const [receipts, setReceipts] = useState<Receipt[]>([])
  useEffect(() => {
    const load = () => api.getTask(task.id).then((d) => setReceipts(d.receipts)).catch(() => {})
    load()
    return subscribe({ receipt: (r) => { if (r.task_id === task.id) load() } })
  }, [task.id])
  const children = tasks.filter((t) => t.parent_id === task.id)
  const done = task.plan.filter((s) => s.status === 'done').length
  return (
    <>
      <section>
        <h3>计划 <span className="muted">{done}/{task.plan.length}</span></h3>
        {task.plan.length === 0 ? <div className="muted">Agent 还没发布计划</div> : <ul className="plan">{task.plan.map((s, i) => <li key={i} className={s.status}>{s.title}</li>)}</ul>}
      </section>
      <section>
        <h3>产出 <span className="muted">{receipts.length}</span></h3>
        {receipts.length === 0 ? <div className="muted">每份报告、数据文件或外发动作都会在这里留一条凭证。</div>
          : receipts.map((r) => (
            <div key={r.id} className="r-item">
              <span className="kind">{r.kind}</span>
              <div><div>{r.summary}</div>{r.path && <a href={api.fileUrl(task.id, r.path)} target="_blank" rel="noreferrer">{r.path}</a>}</div>
            </div>
          ))}
      </section>
      {children.length > 0 && (
        <section>
          <h3>子任务 <span className="muted">{children.length}</span></h3>
          {children.map((c) => <button key={c.id} className="r-link" onClick={() => onSelect(c.id)}>{c.title}<span className="muted">{STATUS_LABEL[c.status]}</span></button>)}
        </section>
      )}
      <section>
        <h3>信息</h3>
        <dl className="kv">
          <dt>状态</dt><dd>{STATUS_LABEL[task.status]}</dd>
          <dt>创建</dt><dd>{new Date(task.created_at).toLocaleString('zh-CN', { hour12: false })}</dd>
          {task.started_at && <><dt>开始</dt><dd>{new Date(task.started_at).toLocaleString('zh-CN', { hour12: false })}</dd></>}
          {task.finished_at && <><dt>结束</dt><dd>{new Date(task.finished_at).toLocaleString('zh-CN', { hour12: false })}</dd></>}
          {task.retries > 0 && <><dt>重试</dt><dd>{task.retries}/{task.max_retries}</dd></>}
          <dt>会话</dt><dd className="mono">{task.session_id || '—'}</dd>
          {task.error && <><dt>错误</dt><dd className="bad-text">{task.error}</dd></>}
        </dl>
      </section>
    </>
  )
}

function LibraryPanel() {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<LibraryItem[]>([])
  useEffect(() => {
    const load = () => api.library(q).then(setItems).catch(() => {})
    load()
    return subscribe({ library: () => load() })
  }, [q])
  return (
    <>
      <input className="search" placeholder="搜资料库" value={q} onChange={(e) => setQ(e.target.value)} />
      {items.length === 0 ? <div className="muted">Agent 用 muse_library_add 存下的研究结论会出现在这里，跨任务可检索。</div>
        : items.map((it) => (
          <div key={it.id} className="lib-item">
            <div className="lib-title"><a href={`#/task/${it.task_id}`}>{it.title}</a><span className="muted">{new Date(it.created_at).toLocaleDateString('zh-CN')}</span></div>
            <div>{it.summary}</div>
            <div className="tags">{it.tags.map((t) => <span key={t} className="tag">{t}</span>)}{it.path && <span className="muted">{it.path}</span>}</div>
          </div>
        ))}
    </>
  )
}

function MemoryPanel() {
  const [items, setItems] = useState<MemoryItem[]>([])
  const [k, setK] = useState(''); const [v, setV] = useState('')
  const load = () => api.memory().then(setItems).catch(() => {})
  useEffect(() => { load(); return subscribe({ memory: () => load() }) }, [])
  return (
    <>
      <div className="muted">Agent 在任务之间共享的事实：偏好的数据源、关注列表、口径约定。你也可以直接改。</div>
      {items.map((m) => (
        <div key={m.key} className="mem-item">
          <div className="mem-key">{m.key}<button className="link" onClick={() => api.memoryDelete(m.key).then(load)}>删除</button></div>
          <div>{m.value}</div>
        </div>
      ))}
      <div className="mem-add">
        <input placeholder="key，如 pm.watchlist" value={k} onChange={(e) => setK(e.target.value)} />
        <input placeholder="value" value={v} onChange={(e) => setV(e.target.value)} />
        <button className="btn small" disabled={!k.trim()} onClick={() => api.memoryPut(k.trim(), v).then(() => { setK(''); setV(''); load() })}>保存</button>
      </div>
    </>
  )
}

function SchedulePanel() {
  const [items, setItems] = useState<Schedule[]>([])
  const [f, setF] = useState({ title: '', prompt: '', every: 'weekdays 15:30', skill: '' })
  const load = () => api.schedules().then(setItems).catch(() => {})
  useEffect(() => { load(); return subscribe({ schedule: () => load(), schedule_deleted: () => load() }) }, [])
  return (
    <>
      {items.length === 0 && <div className="muted">定时任务到点会自动创建一条新任务，结果出现在左边列表里。</div>}
      {items.map((s) => (
        <div key={s.id} className={`sch-item${s.enabled ? '' : ' off'}`}>
          <div className="lib-title"><span>{s.title}</span><span className="muted">{s.every}</span></div>
          <div className="muted small">下次 {new Date(s.next_run_at).toLocaleString('zh-CN', { hour12: false })}{s.last_task_id && <> · <a href={`#/task/${s.last_task_id}`}>上次结果</a></>}</div>
          <div className="actions-row">
            <button className="link" onClick={() => api.scheduleToggle(s.id).then(load)}>{s.enabled ? '暂停' : '启用'}</button>
            <button className="link danger" onClick={() => { if (confirm('删除这个定时？')) api.scheduleDelete(s.id).then(load) }}>删除</button>
          </div>
        </div>
      ))}
      <div className="sch-add">
        <input placeholder="标题，如 盘后盯涨停板" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <textarea placeholder="每次要做什么" value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} rows={3} />
        <div className="two">
          <input placeholder="30m / 2h / daily 09:30 / weekdays 15:30" value={f.every} onChange={(e) => setF({ ...f, every: e.target.value })} />
          <input placeholder="skill（可选）" value={f.skill} onChange={(e) => setF({ ...f, skill: e.target.value })} />
        </div>
        <button className="btn small primary" disabled={!f.title.trim() || !f.prompt.trim()} onClick={() => api.scheduleCreate(f).then(() => { setF({ title: '', prompt: '', every: 'weekdays 15:30', skill: '' }); load() }).catch((e) => alert(String(e)))}>添加定时</button>
      </div>
    </>
  )
}
