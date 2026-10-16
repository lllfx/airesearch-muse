import { useState } from 'react'
import { api } from './api'
import type { Task } from './types'

const SKILLS = [
  { v: '', label: '不指定（让 Agent 自己判断）' },
  { v: 'stock-research', label: 'stock-research · 单只股票基本面 + 因子研究' },
]

export function NewTask({ onClose, onCreated }: { onClose: () => void; onCreated: (t: Task) => void }) {
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
  const [skill, setSkill] = useState('stock-research')
  const [priority, setPriority] = useState(1)
  const [review, setReview] = useState(true)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      onCreated(await api.createTask({ title: title.trim() || prompt.slice(0, 40), prompt: prompt.trim(), skill, priority, requires_review: review, dispatch: true }))
    } catch (err) { alert(String(err)) } finally { setBusy(false) }
  }

  return (
    <div className="modal" onClick={onClose}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>交代一件事</h2>
        <label>标题
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例：研究 600519 贵州茅台" autoFocus />
        </label>
        <label>要做什么
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} required
            placeholder="把目标、范围和你在意的点写清楚。例：研究 600519 最近 6 个季度的盈利质量，重点看 ROE 趋势和估值是否合理，给出看多 / 看空 / 中性结论。" />
        </label>
        <div className="row">
          <label>用哪套流程
            <select value={skill} onChange={(e) => setSkill(e.target.value)}>{SKILLS.map((s) => <option key={s.v} value={s.v}>{s.label}</option>)}</select>
          </label>
          <label>优先级
            <select value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
              <option value={0}>低</option><option value={1}>普通</option><option value={2}>高</option>
            </select>
          </label>
        </div>
        <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> 做完先给我验收，再算完成</label>
        <div className="actions">
          <button type="button" className="btn" onClick={onClose}>取消</button>
          <button type="submit" className="btn primary" disabled={busy || !prompt.trim()}>交给 Agent</button>
        </div>
      </form>
    </div>
  )
}
