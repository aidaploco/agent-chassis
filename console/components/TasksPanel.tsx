'use client'

import { useCallback, useEffect, useState } from 'react'
import { Clock, Play } from 'lucide-react'
import { ApprovalCard, type Decision } from './ApprovalCard'
import { api } from '@/lib/api'
import { describeTaskState, findDecisionRow, relativeTime } from '@/lib/format'
import type { AuditRecord, EmployeeSummary, SelfDescription, TaskLive, TaskResult, TaskSummary } from '@/lib/types'

const CONFIRM_WITHIN_MS = 20_000

const TONE: Record<string, string> = {
  wait: 'border-accent/50 bg-accent/10 text-accent',
  run: 'border-line bg-transparent text-muted',
  ok: 'border-pass/40 bg-pass/10 text-pass',
  bad: 'border-block/50 bg-block/10 text-block',
}

function Badge({ status, state }: { status: string; state: string | null }) {
  const { label, tone } = describeTaskState(status, state)
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] ${TONE[tone]}`}>{label}</span>
}

function isLive(value: TaskLive | TaskResult): value is TaskLive {
  return 'state' in value
}

/** One task: its pending approvals while it waits, its result when it is done. */
function TaskDetail({ employeeId, workflowId, onChanged }: { employeeId: string; workflowId: string; onChanged: () => void }) {
  const [detail, setDetail] = useState<TaskLive | TaskResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const [decisions, setDecisions] = useState<Decision[]>([])
  const waiting = detail !== null && isLive(detail) && detail.state !== 'failed'
  const unconfirmed = decisions.some((d) => d.state === 'sent')

  // Delivering a decision only sends a signal. Say "recorded" once the audit log has the row,
  // and say so plainly if it never shows up (the task may have dropped the signal).
  useEffect(() => {
    if (!unconfirmed) return
    let cancelled = false
    async function confirm() {
      const result = await api<AuditRecord[]>(`/api/employees/${employeeId}/audit?kind=approval&limit=50`)
      if (cancelled) return
      const rows = result.ok && result.data ? result.data : []
      setDecisions((previous) =>
        previous.map((d) => {
          if (d.state !== 'sent') return d
          const row = findDecisionRow(rows, workflowId, d.callId)
          if (row) return { ...d, state: 'recorded', approver: row.actor }
          return Date.now() - d.sentAt > CONFIRM_WITHIN_MS ? { ...d, state: 'unconfirmed' } : d
        }),
      )
    }
    void confirm()
    const timer = setInterval(() => void confirm(), 2000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [unconfirmed, employeeId, workflowId])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const result = await api<TaskLive | TaskResult>(`/api/employees/${employeeId}/tasks/${encodeURIComponent(workflowId)}`)
      if (cancelled) return
      if (result.ok && result.data) {
        setDetail(result.data)
        setError(null)
      } else {
        setError(result.error)
      }
    }
    void load()
    const timer = waiting ? setInterval(() => void load(), 3000) : undefined
    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
  }, [employeeId, workflowId, waiting, tick])

  if (error) return <p className="text-sm text-block">{error}</p>
  if (!detail) return <p className="text-sm text-muted">Loading…</p>

  const confirmations = decisions.length > 0 && (
    <ul className="space-y-1" aria-label="Your decisions">
      {decisions.map((d) => (
        <li key={d.callId} className="rounded-lg border border-line bg-bg px-3 py-2 text-sm" role="status">
          <span className={d.approved ? 'text-pass' : 'text-block'}>{d.approved ? 'Approved' : 'Denied'}</span>{' '}
          <code className="font-mono text-xs">{d.tool}</code>:{' '}
          {d.state === 'recorded' && (
            <>
              recorded in the audit log as <strong>{d.approver}</strong>.
            </>
          )}
          {d.state === 'sent' && (
            <>
              sent as <strong>{d.approver}</strong>. Waiting for the task to record it…
            </>
          )}
          {d.state === 'unconfirmed' && (
            <span className="text-block">
              sent as <strong>{d.approver}</strong>, but no audit row appeared within 20 s. The task may already have moved on (a
              second tab decided it first, for instance). Check Records before relying on it.
            </span>
          )}
        </li>
      ))}
    </ul>
  )

  if (isLive(detail)) {
    if (detail.state === 'failed') return <p className="text-sm text-block">The task failed: {detail.error}</p>
    // A call you just decided can still be listed until the workflow has moved on.
    const stillWaiting = detail.pending.filter((item) => !decisions.some((d) => d.callId === item.call_id))
    return (
      <div className="space-y-2">
        {confirmations}
        {stillWaiting.map((item) => (
          <ApprovalCard
            key={item.call_id}
            employeeId={employeeId}
            workflowId={workflowId}
            item={item}
            onDecided={(decision) => {
              setDecisions((previous) => [...previous, decision])
              setTick((n) => n + 1)
              onChanged()
            }}
          />
        ))}
        {stillWaiting.length === 0 && (
          <p className="text-sm text-muted">
            {decisions.length > 0 ? 'Decision recorded. The task is finishing…' : 'Running… nothing is waiting for you yet.'}
          </p>
        )}
      </div>
    )
  }
  return (
    <div className="space-y-2 text-sm">
      {confirmations}
      <p className="whitespace-pre-wrap rounded-lg border border-line bg-bg p-3">{detail.content}</p>
      <p className="text-xs text-muted">
        {detail.approvals} approved · {detail.denials} denied · {detail.expired} expired
        {detail.escalated ? ' · ended as an escalation' : ''}
        {detail.guardrail_tripped ? ` · reply withheld by ${detail.guardrail_tripped}` : ''}
      </p>
    </div>
  )
}

export function TasksPanel({ employee, role }: { employee: EmployeeSummary; role: SelfDescription | undefined }) {
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [starting, setStarting] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const result = await api<TaskSummary[]>(`/api/employees/${employee.id}/tasks?limit=20`)
      if (cancelled) return
      if (result.ok && result.data) {
        setTasks(result.data)
        setError(null)
      } else {
        setError(result.error)
      }
    }
    void load()
    const timer = setInterval(() => void load(), 4000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [employee.id, tick])

  async function start(name: string) {
    setStarting(name)
    const result = await api<{ workflow_id: string }>(`/api/employees/${employee.id}/tasks`, { method: 'POST', json: { task: name } })
    setStarting(null)
    if (result.ok && result.data) {
      setSelected(result.data.workflow_id)
      setError(null)
      reload()
    } else {
      setError(result.error)
    }
  }

  return (
    <section className="rounded-2xl border border-line bg-card" aria-label={`Tasks of ${employee.displayName}`}>
      <header className="border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">Tasks and approvals</h2>
        <p className="mt-0.5 text-xs text-muted">
          A task runs on its own, not in a conversation. When it reaches an approval-gated tool
          {role && role.approval_required_tools.length > 0 && (
            <>
              {' '}
              (<code className="font-mono">{role.approval_required_tools.join(', ')}</code>)
            </>
          )}
          , it pauses until a person decides. Decisions here are recorded in the audit log as the console&apos;s token
          principal: a shared identity for now, since there is no per-person login.
        </p>
      </header>

      <div className="space-y-2 border-b border-line px-4 py-3">
        {(role?.tasks ?? []).map((task) => (
          <div key={task.name} className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => void start(task.name)}
              disabled={starting !== null}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition enabled:hover:opacity-90 disabled:opacity-50"
            >
              <Play size={13} aria-hidden /> {starting === task.name ? 'Starting…' : `Run ${task.name}`}
            </button>
            <span className="text-xs text-muted">
              by {task.agent}
              {task.schedules.length > 0 && (
                <>
                  {' '}
                  · <Clock size={11} className="inline" aria-hidden /> also runs on its own: <code className="font-mono">{task.schedules.join(', ')}</code>
                </>
              )}
            </span>
          </div>
        ))}
        {error && (
          <p className="text-xs text-block" role="alert">
            {error}
          </p>
        )}
      </div>

      <ul className="divide-y divide-line">
        {tasks === null && <li className="px-4 py-3 text-sm text-muted">Loading…</li>}
        {tasks?.length === 0 && <li className="px-4 py-3 text-sm text-muted">No task has run yet. Start one above.</li>}
        {tasks?.map((task) => (
          <li key={task.workflow_id} className="px-4 py-2.5">
            <button
              onClick={() => setSelected(selected === task.workflow_id ? null : task.workflow_id)}
              aria-expanded={selected === task.workflow_id}
              className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
            >
              <span className="text-sm font-medium">{task.task ?? 'task'}</span>
              <Badge status={task.status} state={task.state} />
              {task.pending_approvals > 0 && (
                <span className="text-xs text-accent">
                  {task.pending_approvals} decision{task.pending_approvals === 1 ? '' : 's'} needed
                </span>
              )}
              <span className="ml-auto text-xs text-muted">{relativeTime(task.started_at)}</span>
            </button>
            {selected === task.workflow_id && (
              <div className="mt-2">
                <TaskDetail employeeId={employee.id} workflowId={task.workflow_id} onChanged={reload} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
