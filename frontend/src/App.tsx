import { useCallback, useEffect, useState } from 'react'
import { api, subscribe } from './api'
import { Board } from './Board'
import { NewTask } from './NewTask'
import { TaskDrawer } from './TaskDrawer'
import type { Task } from './types'

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [engine, setEngine] = useState<{ engine: string; alive: boolean } | null>(null)

  const reload = useCallback(() => api.listTasks().then(setTasks).catch(console.error), [])

  useEffect(() => {
    reload()
    fetch('/api/health').then((r) => r.json()).then(setEngine).catch(() => setEngine(null))
    return subscribe({
      task: (t: Task) => setTasks((prev) => (prev.some((x) => x.id === t.id) ? prev.map((x) => (x.id === t.id ? t : x)) : [t, ...prev])),
      task_deleted: ({ id }: { id: string }) => setTasks((prev) => prev.filter((x) => x.id !== id)),
    })
  }, [reload])

  const youCount = tasks.filter((t) => ['waiting_input', 'waiting_approval', 'review', 'interrupted', 'failed'].includes(t.status)).length

  return (
    <>
      <div className="top">
        <h1>Muse 投研任务台</h1>
        <span className="sub">{youCount > 0 ? `${youCount} 项等你处理` : '没有等你处理的事'}</span>
        <span className="spacer" />
        {engine && (
          <span className={`engine${engine.alive ? '' : ' off'}`}>
            引擎 <b>{engine.engine === 'MockEngine' ? '演示模式' : engine.alive ? 'dsh 在线' : 'dsh 离线'}</b>
          </span>
        )}
        <button className="btn primary" onClick={() => setCreating(true)}>新任务</button>
      </div>
      <Board tasks={tasks} selected={selected} onSelect={setSelected} />
      {selected && <TaskDrawer taskId={selected} onClose={() => setSelected(null)} onChanged={reload} />}
      {creating && <NewTask onClose={() => setCreating(false)} onCreated={(t) => { setCreating(false); setSelected(t.id) }} />}
    </>
  )
}
