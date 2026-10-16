import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

export function Markdown({ text, className }: { text: string; className?: string }) {
  const html = DOMPurify.sanitize(marked.parse(text) as string, { ADD_ATTR: ['target'] })
  return <div className={`md${className ? ' ' + className : ''}`} dangerouslySetInnerHTML={{ __html: html }} />
}
