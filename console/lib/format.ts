// Small, pure helpers for showing tasks, approvals and records. No React, so they are
// tested on their own.
import type { AuditRecord } from './types.ts'

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} m ${Math.round(seconds - minutes * 60)} s`
  const hours = Math.floor(minutes / 60)
  return `${hours} h ${minutes - hours * 60} m`
}

export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return '—'
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return '—'
  const seconds = Math.round((now - then) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds} s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86_400)} d ago`
}

/** What an approval-gated tool was asked to do, as rows a person can read. The model's
 *  arguments are usually JSON text; anything else is shown as it is. */
export function parseArgs(args: string): { key: string; value: string }[] {
  try {
    const parsed: unknown = JSON.parse(args)
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      return Object.entries(parsed).map(([key, value]) => ({
        key,
        value: typeof value === 'string' ? value : JSON.stringify(value),
      }))
    }
  } catch {
    // not JSON: fall through
  }
  return [{ key: 'arguments', value: args }]
}

/** The audit row proving a decision on one call was recorded, if there is one. Delivering an
 *  approval only sends a signal: the task drops it when nothing is pending (a second tab got
 *  there first, say), so "recorded" must come from the audit log, not from the delivery. */
export function findDecisionRow(
  rows: Pick<AuditRecord, 'kind' | 'subject' | 'detail' | 'actor' | 'action'>[],
  workflowId: string,
  callId: string,
): { actor: string; action: string } | null {
  const row = rows.find((r) => r.kind === 'approval' && r.subject === workflowId && r.detail.call_id === callId)
  return row ? { actor: row.actor, action: row.action } : null
}

/** True when the chassis cut a call's arguments short (it says so in the text). A person
 *  must be told: they would otherwise approve something they cannot read in full. */
export function argsAreTruncated(args: string): boolean {
  return /\[truncated: \d+ more characters not shown\]\s*$/.test(args)
}

const clip = (text: string, length = 90) => (text.length > length ? `${text.slice(0, length - 1)}…` : text)

/** One line saying what an audit row is about. */
export function summarizeAudit(row: Pick<AuditRecord, 'kind' | 'action' | 'detail'>): string {
  const d = row.detail
  const text = (value: unknown) => (typeof value === 'string' ? value : value === undefined ? '' : JSON.stringify(value))
  if (row.kind === 'approval') {
    const tool = text(d.tool)
    const comment = text(d.comment)
    return clip([tool, comment ? `“${comment}”` : ''].filter(Boolean).join(' · '))
  }
  if (row.kind === 'escalation') return clip(text(d.reason) || 'escalated')
  if (row.kind === 'manifest_override') {
    return clip(`${text(d.setting)} = ${text(d.value)} (default ${text(d.default)})`)
  }
  return clip(Object.entries(d).map(([k, v]) => `${k}=${text(v)}`).join(' '))
}

/** A task-list state as words a person uses. */
export function describeTaskState(status: string, state: string | null): { label: string; tone: 'wait' | 'run' | 'ok' | 'bad' } {
  if (status === 'RUNNING') {
    return state === 'waiting_on_human' ? { label: 'waiting for approval', tone: 'wait' } : { label: 'running', tone: 'run' }
  }
  if (status === 'COMPLETED') return { label: 'finished', tone: 'ok' }
  return { label: status.toLowerCase(), tone: 'bad' }
}
