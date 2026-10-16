import { useMemo, useState } from 'react'
import { api } from './api'
import { Markdown } from './md'
import type { ActionReview, FormField, InputRequest } from './types'

/* ---------- 工具调用（bash / read / write / 问财 …） ---------- */

const TOOL_LABEL: Record<string, string> = {
  bash: '命令', pwsh: '命令', read: '读文件', read_image: '看图', write: '写文件', edit: '改文件', grep: '搜索', glob: '找文件',
  web_search: '搜网页', web_fetch: '抓网页', skill: '加载技能', todo_write: '待办', delegate: '子代理',
}

function prettyArgs(name: string, raw: string): string {
  try {
    const a = JSON.parse(raw)
    if (name === 'bash' || name === 'pwsh') return a.command ?? raw
    if (['read', 'write', 'edit', 'read_image'].includes(name)) return a.file_path ?? a.path ?? raw
    if (name === 'grep') return `${a.pattern ?? ''}${a.path ? ' in ' + a.path : ''}`
    if (name === 'skill') return a.name ?? raw
    if (name.startsWith('mcp__')) return a.query ?? a.task ?? a.command ?? raw
    return raw
  } catch { return raw }
}

export function ToolCard({ name, args, result, error }: { name: string; args: string; result?: string; error?: boolean }) {
  const [open, setOpen] = useState(false)
  const short = name.replace(/^mcp__[^_]+__/, '')
  const label = TOOL_LABEL[short] ?? (name.startsWith('mcp__') ? name.split('__')[1] : short)
  const running = result === undefined
  return (
    <div className={`tool${error ? ' err' : ''}${running ? ' running' : ''}`}>
      <button className="tool-row" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="tool-label">{label}</span>
        <code className="tool-args">{prettyArgs(short, args)}</code>
        <span className="tool-state">{running ? '运行中' : error ? '失败' : '完成'}</span>
      </button>
      {open && (
        <div className="tool-detail">
          {args && <pre className="args">{args}</pre>}
          {result !== undefined && <pre className="out">{result}</pre>}
        </div>
      )}
    </div>
  )
}

/* ---------- 计划 ---------- */

export function PlanCard({ steps }: { steps: { title: string; status: string }[] }) {
  const done = steps.filter((s) => s.status === 'done').length
  return (
    <div className="card plan-card">
      <div className="card-head"><span>计划</span><span className="muted">{done}/{steps.length}</span></div>
      <ul className="plan">{steps.map((s, i) => <li key={i} className={s.status}>{s.title}</li>)}</ul>
    </div>
  )
}

/* ---------- 提问（快捷选项 / 表单 / 自由文本） ---------- */

export function QuestionCard({ req, busy, onAnswer }: { req: InputRequest; busy: boolean; onAnswer: (a: string) => void }) {
  const [text, setText] = useState('')
  const [form, setForm] = useState<Record<string, any>>({})
  const answered = !!req.answered_at
  const hasForm = req.fields.length > 0

  const answerView = useMemo(() => {
    if (!answered) return null
    if (!hasForm) return req.answer
    try { const o = JSON.parse(req.answer); return req.fields.map((f) => `${f.label}：${Array.isArray(o[f.name]) ? o[f.name].join('、') : o[f.name] ?? '—'}`).join('　') } catch { return req.answer }
  }, [answered, hasForm, req])

  const formValid = req.fields.every((f) => !f.required || (f.type === 'multichoice' ? (form[f.name]?.length ?? 0) > 0 : form[f.name] !== undefined && form[f.name] !== ''))
  const submitForm = () => onAnswer(JSON.stringify(form))

  return (
    <div className={`card ask${answered ? ' settled' : ''}`}>
      <div className="card-head"><span>Agent 问你</span>{answered && <span className="muted">已回答</span>}</div>
      <div className="q">{req.question}</div>
      {answered ? <div className="answer">你答：{answerView}</div> : (
        <>
          {req.options.length > 0 && !hasForm && (
            <div className="opts">{req.options.map((o) => <button key={o} className="btn small" disabled={busy} onClick={() => onAnswer(o)}>{o}</button>)}</div>
          )}
          {hasForm && (
            <div className="form">
              {req.fields.map((f) => <Field key={f.name} f={f} value={form[f.name]} onChange={(v) => setForm((p) => ({ ...p, [f.name]: v }))} />)}
              {req.options.length > 0 && <div className="opts">{req.options.map((o) => <button key={o} className="btn small" disabled={busy} onClick={() => onAnswer(JSON.stringify({ ...form, _choice: o }))}>{o}</button>)}</div>}
              <div className="actions"><button className="btn primary" disabled={busy || !formValid} onClick={submitForm}>提交并继续</button></div>
            </div>
          )}
          {!hasForm && (
            <>
              <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="或者自己写一段回答…" />
              <div className="actions"><button className="btn primary" disabled={busy || !text.trim()} onClick={() => onAnswer(text.trim())}>回答并继续</button></div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function Field({ f, value, onChange }: { f: FormField; value: any; onChange: (v: any) => void }) {
  return (
    <label className="field">
      <span>{f.label}{f.required && <b> *</b>}</span>
      {f.type === 'choice' ? (
        <div className="opts">{f.options.map((o) => <button key={o} type="button" className={`btn small${value === o ? ' on' : ''}`} onClick={() => onChange(o)}>{o}</button>)}</div>
      ) : f.type === 'multichoice' ? (
        <div className="opts">{f.options.map((o) => {
          const sel: string[] = value ?? []
          return <button key={o} type="button" className={`btn small${sel.includes(o) ? ' on' : ''}`} onClick={() => onChange(sel.includes(o) ? sel.filter((x) => x !== o) : [...sel, o])}>{o}</button>
        })}</div>
      ) : (
        <input type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  )
}

/* ---------- 审批 ---------- */

export function ApprovalCard({ rev, busy, onDecide }: { rev: ActionReview; busy: boolean; onDecide: (d: 'approved' | 'rejected', c: string) => void }) {
  const [c, setC] = useState('')
  let payload = rev.payload
  try { payload = JSON.stringify(JSON.parse(rev.payload), null, 2) } catch { /* 原样 */ }
  const decided = !!rev.decided_at
  return (
    <div className={`card ask${decided ? ' settled' : ''}`}>
      <div className="card-head"><span>Agent 想执行：{rev.action}</span>{decided && <span className={rev.decision === 'approved' ? 'ok-text' : 'bad-text'}>{rev.decision === 'approved' ? '已批准' : '已拒绝'}</span>}</div>
      {rev.reason && <div className="why">理由：{rev.reason}</div>}
      <pre className="payload">{payload}</pre>
      {decided ? (rev.comment && <div className="answer">你说：{rev.comment}</div>) : (
        <>
          <textarea value={c} onChange={(e) => setC(e.target.value)} placeholder="给 Agent 的说明（可选）" />
          <div className="actions">
            <button className="btn danger" disabled={busy} onClick={() => onDecide('rejected', c)}>拒绝</button>
            <button className="btn ok" disabled={busy} onClick={() => onDecide('approved', c)}>批准</button>
          </div>
        </>
      )}
    </div>
  )
}

/* ---------- 回执 / 文件 / 子任务 / 系统行 ---------- */

export function ReceiptCard({ r, taskId }: { r: Record<string, any>; taskId: string }) {
  return (
    <div className="card receipt">
      <div className="card-head"><span>回执 · {r.kind}</span></div>
      <div>{r.summary}</div>
      {r.path && <a className="file-link" href={api.fileUrl(taskId, r.path)} target="_blank" rel="noreferrer">{r.path}</a>}
      {r.meta && <div className="muted small">{r.meta}</div>}
    </div>
  )
}

export function FileCard({ c, taskId }: { c: Record<string, any>; taskId: string }) {
  const url = api.fileUrl(taskId, c.path)
  const img = /\.(png|jpe?g|gif|webp|svg)$/i.test(c.path)
  return (
    <div className="card file">
      <div className="card-head"><span>{c.title || c.path}</span><a href={url} target="_blank" rel="noreferrer">打开</a></div>
      {img ? <img src={url} alt={c.title || c.path} /> : <a className="file-link" href={url} target="_blank" rel="noreferrer">{c.path}</a>}
    </div>
  )
}

export function SubtaskCard({ sub }: { sub: Record<string, any> }) {
  const finished = ['done', 'review', 'failed', 'cancelled'].includes(sub.status)
  return (
    <a className={`card subtask${finished ? ' settled' : ''}`} href={`#/task/${sub.id}`}>
      <div className="card-head"><span>子任务 · {sub.title}</span><span className="muted">{finished ? (sub.status === 'failed' ? '失败' : '已完成') : '进行中'}</span></div>
      {sub.summary && <div className="muted">{sub.summary}</div>}
    </a>
  )
}

export function SystemLine({ text, at }: { text: string; at: string }) {
  return <div className="sysline"><span>{new Date(at).toLocaleTimeString('zh-CN', { hour12: false })}</span>{text}</div>
}

export function MarkdownCard({ c }: { c: Record<string, any> }) {
  return <div className="card note"><div className="card-head"><span>{c.title}</span></div><Markdown text={c.markdown} /></div>
}

/* ---------- 表格 ---------- */

export function TableCard({ c }: { c: Record<string, any> }) {
  const [all, setAll] = useState(false)
  const rows: any[][] = c.rows ?? []
  const shown = all ? rows : rows.slice(0, 12)
  return (
    <div className="card table-card">
      <div className="card-head"><span>{c.title}</span><span className="muted">{rows.length} 行</span></div>
      <div className="table-wrap">
        <table>
          <thead><tr>{(c.columns ?? []).map((h: string, i: number) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>{shown.map((r, i) => <tr key={i}>{r.map((v: any, j: number) => <td key={j} className={typeof v === 'number' ? 'num' : ''}>{fmt(v)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {rows.length > 12 && <button className="link" onClick={() => setAll((v) => !v)}>{all ? '收起' : `展开全部 ${rows.length} 行`}</button>}
      {c.note && <div className="muted small">{c.note}</div>}
    </div>
  )
}

function fmt(v: any): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 4 : 2)
  return String(v)
}

/* ---------- 图表：内联 SVG，折线 / 柱状 ---------- */

const PALETTE = ['#2347C5', '#0F6E56', '#B8741A', '#A3322A', '#534AB7', '#1D9E75']

export function ChartCard({ c }: { c: Record<string, any> }) {
  const x: any[] = c.x ?? []
  const series: { name: string; values: number[] }[] = c.series ?? []
  const W = 640, H = 240, L = 48, R = 16, T = 18, B = 40
  const all = series.flatMap((s) => s.values).filter((v) => typeof v === 'number' && isFinite(v))
  const minV = c.chart_type === 'bar' ? Math.min(0, ...all) : Math.min(...all)
  const maxV = Math.max(...all)
  const span = maxV - minV || 1
  const px = (i: number) => L + (x.length <= 1 ? (W - L - R) / 2 : (i / (x.length - 1)) * (W - L - R))
  const py = (v: number) => T + (1 - (v - minV) / span) * (H - T - B)
  const ticks = 4
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => minV + (span * i) / ticks)
  const labelEvery = Math.max(1, Math.ceil(x.length / 8))
  const [hover, setHover] = useState<number | null>(null)

  return (
    <div className="card chart-card">
      <div className="card-head"><span>{c.title}</span>{c.y_label && <span className="muted">{c.y_label}</span>}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={c.title}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const fx = ((e.clientX - r.left) / r.width) * W
          const i = Math.round(((fx - L) / (W - L - R)) * (x.length - 1))
          setHover(i >= 0 && i < x.length ? i : null)
        }}>
        {tickVals.map((v, i) => <g key={i}><line x1={L} x2={W - R} y1={py(v)} y2={py(v)} className="grid" /><text x={L - 6} y={py(v) + 4} textAnchor="end" className="axis">{fmt(v)}</text></g>)}
        {x.map((lab, i) => (i % labelEvery === 0 || i === x.length - 1) && <text key={i} x={px(i)} y={H - B + 16} textAnchor="middle" className="axis">{String(lab)}</text>)}
        {c.chart_type === 'bar' ? series.map((s, si) => {
          const n = series.length
          const slot = (W - L - R) / Math.max(x.length, 1)
          const bw = Math.max(2, (slot * 0.7) / n)
          return s.values.map((v, i) => {
            const cx = L + slot * i + slot / 2 + (si - (n - 1) / 2) * bw
            const y0 = py(Math.max(0, minV)), y1 = py(v)
            return <rect key={i} x={cx - bw / 2} y={Math.min(y0, y1)} width={bw} height={Math.abs(y0 - y1)} fill={PALETTE[si % PALETTE.length]} opacity={hover === null || hover === i ? 1 : 0.45} />
          })
        }) : series.map((s, si) => (
          <g key={si}>
            <polyline fill="none" stroke={PALETTE[si % PALETTE.length]} strokeWidth={1.8} points={s.values.map((v, i) => `${px(i)},${py(v)}`).join(' ')} />
            {hover !== null && <circle cx={px(hover)} cy={py(s.values[hover])} r={3.5} fill={PALETTE[si % PALETTE.length]} />}
          </g>
        ))}
        {hover !== null && c.chart_type !== 'bar' && <line x1={px(hover)} x2={px(hover)} y1={T} y2={H - B} className="cursor" />}
      </svg>
      <div className="legend">
        {series.map((s, i) => <span key={i}><i style={{ background: PALETTE[i % PALETTE.length] }} />{s.name}{hover !== null && <b> {fmt(s.values[hover])}</b>}</span>)}
        {hover !== null && <span className="muted">{String(x[hover])}</span>}
      </div>
      {c.note && <div className="muted small">{c.note}</div>}
    </div>
  )
}

/* ---------- 问财数据 ---------- */

export function DataCard({ c, taskId }: { c: Record<string, any>; taskId: string }) {
  const [open, setOpen] = useState(false)
  const preview: any[] = c.preview ?? []
  const cols = preview.length && typeof preview[0] === 'object' ? Object.keys(preview[0]).slice(0, 8) : []
  const KIND: Record<string, string> = { market: '行情', shareholder: '股东股本', announcement: '公告', news: '新闻', report: '研报' }
  return (
    <div className="card data-card">
      <div className="card-head">
        <span>{c.source} · {KIND[c.kind] ?? c.kind}</span>
        <span className="muted">{c.count} 条 · <a href={api.fileUrl(taskId, c.path)} target="_blank" rel="noreferrer">原始响应</a></span>
      </div>
      <div className="query">“{c.query}”</div>
      {cols.length > 0 && (
        <>
          <button className="link" onClick={() => setOpen((v) => !v)}>{open ? '收起预览' : `预览前 ${preview.length} 条`}</button>
          {open && (
            <div className="table-wrap">
              <table>
                <thead><tr>{cols.map((h) => <th key={h}>{h}</th>)}</tr></thead>
                <tbody>{preview.map((r, i) => <tr key={i}>{cols.map((h) => <td key={h}>{fmt(r[h])}</td>)}</tr>)}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
