'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { formatDuration, relativeTime, summarizeAudit } from '@/lib/format'
import type { AuditRecord, EmployeeSummary, OutcomeRecord } from '@/lib/types'

type Which = 'outcomes' | 'audit'

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-muted">
          <tr>
            {head.map((h) => (
              <th key={h} className="whitespace-nowrap px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  )
}

const Cell = ({ children, mono }: { children: React.ReactNode; mono?: boolean }) => (
  <td className={`px-3 py-2 align-top ${mono ? 'font-mono' : ''}`}>{children}</td>
)

/** What this Employee has recorded: finished conversations and tasks (outcomes) and the
 *  audit log of approvals, escalations and manifest overrides. Read-only, and only this
 *  Employee's rows: the chassis scopes both. */
export function RecordsPanel({ employee }: { employee: EmployeeSummary }) {
  const [which, setWhich] = useState<Which>('outcomes')
  const [outcomes, setOutcomes] = useState<OutcomeRecord[] | null>(null)
  const [audit, setAudit] = useState<AuditRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      const path = which === 'outcomes' ? 'outcomes?limit=50' : 'audit?limit=50'
      const result = await api<OutcomeRecord[] | AuditRecord[]>(`/api/employees/${employee.id}/${path}`)
      if (cancelled) return
      if (!result.ok || !result.data) return setError(result.error)
      setError(null)
      if (which === 'outcomes') setOutcomes(result.data as OutcomeRecord[])
      else setAudit(result.data as AuditRecord[])
    }
    void load()
    const timer = setInterval(() => void load(), 8000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [employee.id, which])

  const rows = which === 'outcomes' ? outcomes : audit

  return (
    <section className="rounded-2xl border border-line bg-card" aria-label={`Records of ${employee.displayName}`}>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold">Records</h2>
          <p className="mt-0.5 text-xs text-muted">
            Only {employee.displayName}&apos;s own rows, newest first. Both tables are append-only: nothing here can be edited.
          </p>
        </div>
        <div role="tablist" aria-label="Which records" className="flex gap-1">
          {(['outcomes', 'audit'] as const).map((id) => (
            <button
              key={id}
              role="tab"
              aria-selected={which === id}
              onClick={() => setWhich(id)}
              className={`rounded-lg border px-3 py-1 text-xs ${which === id ? 'border-accent bg-accent/10' : 'border-line text-muted hover:border-accent/50'}`}
            >
              {id === 'outcomes' ? 'Finished work' : 'Audit log'}
            </button>
          ))}
        </div>
      </header>

      {error && (
        <p className="px-4 py-3 text-sm text-block" role="alert">
          {error}
        </p>
      )}
      {!error && rows === null && <p className="px-4 py-3 text-sm text-muted">Loading…</p>}
      {!error && rows?.length === 0 && <p className="px-4 py-3 text-sm text-muted">Nothing recorded yet.</p>}

      {!error && which === 'outcomes' && outcomes && outcomes.length > 0 && (
        <Table head={['Ended', 'Kind', 'Result', 'Turns', 'Tools', 'Escalations', 'Guardrail trips', 'Tokens in/out', 'Took']}>
          {outcomes.map((row) => (
            <tr key={row.workflow_run_id}>
              <Cell>{relativeTime(row.ended_at)}</Cell>
              <Cell mono>{row.task_type}</Cell>
              <Cell>{row.resolution}</Cell>
              <Cell>{row.turns}</Cell>
              <Cell>{row.tool_calls}</Cell>
              <Cell>{row.escalations}</Cell>
              <Cell>{row.guardrail_trips}</Cell>
              <Cell>
                {row.tokens_in}/{row.tokens_out}
              </Cell>
              <Cell>{formatDuration(row.wall_time_ms)}</Cell>
            </tr>
          ))}
        </Table>
      )}

      {!error && which === 'audit' && audit && audit.length > 0 && (
        <Table head={['Recorded', 'Kind', 'Action', 'By', 'About', 'Detail']}>
          {audit.map((row, index) => (
            <tr key={`${row.recorded_at}-${row.subject}-${index}`}>
              <Cell>{relativeTime(row.recorded_at)}</Cell>
              <Cell mono>{row.kind}</Cell>
              <Cell>{row.action}</Cell>
              <Cell mono>{row.actor}</Cell>
              <Cell mono>{row.subject.length > 28 ? `${row.subject.slice(0, 27)}…` : row.subject}</Cell>
              <Cell>{summarizeAudit(row)}</Cell>
            </tr>
          ))}
        </Table>
      )}
    </section>
  )
}
