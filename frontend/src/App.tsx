import { useCallback, useEffect, useState } from 'react'
import { api, subscribe } from './api'
import { Conversation } from './Conversation'
import { NewTask } from './NewTask'
import { RightPanel } from './RightPanel'
import { Sidebar } from './Sidebar'
import type { Task } from './types'

function hashTask(): string | null {
  const m = location.hash.match(/^#\/task\/([^/]+)/)
  return m ? m[1] : null
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [selected, setSelected] = useState<string | null>(hashTask())
  const [creating, setCreating] = useState(false)
  const [engine, setEngine] = useState<{ engine: string; alive: boolean } | null>(null)
  const [rightOpen, setRightOpen] = useState(true)

  const reload = useCallback(() => api.listTasks().then(setTasks).catch(console.error), [])

  useEffect(() => {
    reload()
    api.health().then(setEngine).catch(() => setEngine(null))
    const onHash = () => setSelected(hashTask())
    window.addEventListener('hashchange', onHash)
    const off = subscribe({
      task: (t: Task) => setTasks((prev) => (prev.some((x) => x.id === t.id) ? prev.map((x) => (x.id === t.id ? t : x)) : [t, ...prev])),
      task_deleted: ({ id }: { id: string }) => setTasks((prev) => prev.filter((x) => x.id !== id)),
    })
    return () => { off(); window.removeEventListener('hashchange', onHash) }
  }, [reload])

  const select = (id: string | null) => { location.hash = id ? `#/task/${id}` : '' }
  const current = tasks.find((t) => t.id === selected) ?? null

  return (
    <div className={`app${rightOpen ? '' : ' right-closed'}`}>
      <Sidebar tasks={tasks} selected={selected} onSelect={select} onNew={() => setCreating(true)} engine={engine} />
      <main className="center">
        {current ? (
          <Conversation key={current.id} task={current} onToggleRight={() => setRightOpen((v) => !v)} rightOpen={rightOpen} />
        ) : (
          <Welcome onNew={() => setCreating(true)} count={tasks.length} />
        )}
      </main>
      {rightOpen && <RightPanel task={current} tasks={tasks} onSelect={select} />}
      {creating && <NewTask onClose={() => setCreating(false)} onCreated={(t) => { setCreating(false); select(t.id) }} />}
    </div>
  )
}

function Welcome({ onNew, count }: { onNew: () => void; count: number }) {
  return (
    <div className="welcome">
      <h1>交代一件事，然后去忙别的。</h1>
      <p>Agent 会先给你计划，拿不准的地方停下来问你，要外发的动作先找你批，做完留回执等你验收。{count === 0 ? '现在还没有任务。' : '左边选一个任务继续。'}</p>
      <button className="btn primary" onClick={onNew}>新任务</button>
      <div className="examples">
        <span>试试：</span>
        <em>研究 600519 最近 6 个季度的盈利质量</em>
        <em>对比光伏板块前五家的毛利率趋势</em>
        <em>每个交易日盘后盯一遍涨停板，异常的告诉我</em>
      </div>
    </div>
  )
}
