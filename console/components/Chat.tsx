'use client'

import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { RotateCcw, Send } from 'lucide-react'
import { TurnTrace } from './TurnTrace'
import { buildTrace } from '@/lib/trace'
import type { EmployeeSummary, SelfDescription, Turn } from '@/lib/types'

interface Props {
  employee: EmployeeSummary
  role: SelfDescription | undefined
  turns: Turn[]
  pending: boolean
  onSend: (text: string) => void
  onReset: () => void
}

export function Chat({ employee, role, turns, pending, onSend, onReset }: Props) {
  const [draft, setDraft] = useState('')
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, pending])

  function submit(text: string) {
    const value = text.trim()
    if (!value || pending) return
    setDraft('')
    onSend(value)
  }

  return (
    <section className="flex min-h-[32rem] flex-col rounded-2xl border border-line bg-card" aria-label={`Chat with ${employee.displayName}`}>
      <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="text-sm">
          <span className="font-semibold">{employee.displayName}</span>
          <span className="text-muted"> · conversation {turns.length === 0 ? 'not started' : `${turns.length} turn${turns.length === 1 ? '' : 's'}`}</span>
        </div>
        <button
          onClick={onReset}
          disabled={turns.length === 0 || pending}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-xs text-muted transition enabled:hover:border-accent enabled:hover:text-ink disabled:opacity-40"
        >
          <RotateCcw size={12} aria-hidden /> New conversation
        </button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4" role="log" aria-live="polite">
        {turns.length === 0 && (
          <div className="rounded-xl border border-dashed border-line p-5 text-sm text-muted">
            <p className="font-medium text-ink">Start by saying something to {employee.displayName}.</p>
            <p className="mt-1">
              Under every reply you will see what happened to get it: the input checks, which agent answered, any tools it
              used, and the output checks. Try one of the examples below, including the ones that try to break it.
            </p>
          </div>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="space-y-2">
            <div className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-sm text-white">
                {turn.user}
              </div>
            </div>
            {turn.error ? (
              <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-block/40 bg-block/10 px-3.5 py-2 text-sm text-block">
                {turn.error}
              </div>
            ) : turn.reply ? (
              <div className="max-w-[95%]">
                <div className="inline-block max-w-full rounded-2xl rounded-bl-md border border-line bg-bg px-3.5 py-2 text-sm">
                  <div className="prose-reply">
                    <Markdown>{turn.reply.content}</Markdown>
                  </div>
                </div>
                <TurnTrace trace={buildTrace(turn.reply, role, turn.previousAgent)} />
              </div>
            ) : (
              <div className="text-sm text-muted" aria-label="Waiting for a reply">
                {employee.displayName} is working on it…
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <div className="border-t border-line px-4 py-3">
        {employee.examples.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted">Try:</span>
            {employee.examples.map((example) => (
              <button
                key={example.label}
                onClick={() => submit(example.text)}
                disabled={pending || employee.status !== 'up'}
                title={example.text}
                className="rounded-full border border-line px-2.5 py-1 text-xs transition enabled:hover:border-accent enabled:hover:bg-accent/10 disabled:opacity-40"
              >
                {example.label}
              </button>
            ))}
          </div>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault()
            submit(draft)
          }}
          className="flex items-end gap-2"
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit(draft)
              }
            }}
            rows={2}
            placeholder={employee.status === 'up' ? `Message ${employee.displayName}…` : `${employee.displayName} is not answering`}
            disabled={employee.status !== 'up'}
            className="flex-1 resize-none rounded-xl border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-50"
            aria-label={`Message ${employee.displayName}`}
          />
          <button
            type="submit"
            disabled={pending || !draft.trim() || employee.status !== 'up'}
            className="inline-flex h-10 items-center gap-1.5 rounded-xl bg-accent px-4 text-sm font-medium text-white transition enabled:hover:opacity-90 disabled:opacity-40"
          >
            <Send size={14} aria-hidden /> Send
          </button>
        </form>
      </div>
    </section>
  )
}
