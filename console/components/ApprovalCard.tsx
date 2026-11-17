'use client'

import { useState } from 'react'
import { Check, ShieldQuestion, X } from 'lucide-react'
import { api } from '@/lib/api'
import { argsAreTruncated, parseArgs } from '@/lib/format'
import type { ApprovalDecision, ApprovalItem } from '@/lib/types'

export interface Decision {
  callId: string
  tool: string
  approved: boolean
  /** Who the chassis said it sent the decision as: the console's token principal. */
  approver: string
  sentAt: number
  /** 'sent': the signal was delivered. 'recorded': the audit log has the row. 'unconfirmed':
   *  no audit row appeared in time, e.g. the task had already moved on. */
  state: 'sent' | 'recorded' | 'unconfirmed'
}

interface Props {
  employeeId: string
  workflowId: string
  item: ApprovalItem
  /** Called once the chassis accepted the decision. The parent keeps the confirmation:
   *  this card goes away as soon as the call stops being pending. */
  onDecided: (decision: Decision) => void
}

/** One approval-gated tool call waiting for a person. Decides exactly this call. */
export function ApprovalCard({ employeeId, workflowId, item, onDecided }: Props) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState<'approve' | 'deny' | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function decide(approved: boolean) {
    setBusy(approved ? 'approve' : 'deny')
    setError(null)
    const result = await api<ApprovalDecision>(`/api/employees/${employeeId}/approvals`, {
      method: 'POST',
      json: { workflow_id: workflowId, call_id: item.call_id, approved, comment },
    })
    setBusy(null)
    if (!result.ok || !result.data) {
      setError(result.error ?? 'the decision was not accepted')
      return
    }
    onDecided({
      callId: item.call_id,
      tool: item.tool,
      approved,
      approver: result.data.approver,
      sentAt: Date.now(),
      state: 'sent',
    })
  }

  return (
    <div className="rounded-xl border border-accent/40 bg-accent/5 p-3">
      <div className="flex items-center gap-1.5 text-sm font-semibold">
        <ShieldQuestion size={15} className="text-accent" aria-hidden />
        Needs your decision: <code className="font-mono text-xs">{item.tool}</code>
      </div>
      {argsAreTruncated(item.args) && (
        <p className="mt-2 rounded-lg border border-block/50 bg-block/10 p-2 text-xs text-block" role="alert">
          Part of this call is not shown: it is longer than the chassis passes on. You would be deciding on something
          you cannot read in full. Deny it, or read the whole call where it was produced, unless you are sure.
        </p>
      )}
      <dl className="mt-2 space-y-1 rounded-lg border border-line bg-card p-2 text-xs">
        {parseArgs(item.args).map((row) => (
          <div key={row.key} className="grid grid-cols-[6rem_1fr] gap-2">
            <dt className="text-muted">{row.key}</dt>
            <dd className="whitespace-pre-wrap break-words">{row.value}</dd>
          </div>
        ))}
      </dl>
      <label className="mt-2 block text-xs text-muted" htmlFor={`comment-${item.call_id}`}>
        Comment (kept with the decision)
      </label>
      <input
        id={`comment-${item.call_id}`}
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        maxLength={1000}
        placeholder="Why?"
        className="mt-1 w-full rounded-lg border border-line bg-bg px-2.5 py-1.5 text-sm outline-none focus:border-accent"
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={() => void decide(true)}
          disabled={busy !== null}
          className="inline-flex items-center gap-1.5 rounded-lg bg-pass px-3 py-1.5 text-sm font-medium text-black transition enabled:hover:opacity-90 disabled:opacity-50"
        >
          <Check size={14} aria-hidden /> {busy === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button
          onClick={() => void decide(false)}
          disabled={busy !== null}
          className="inline-flex items-center gap-1.5 rounded-lg border border-block px-3 py-1.5 text-sm font-medium text-block transition enabled:hover:bg-block/10 disabled:opacity-50"
        >
          <X size={14} aria-hidden /> {busy === 'deny' ? 'Denying…' : 'Deny'}
        </button>
        <span className="text-xs text-muted">Decides this one call.</span>
      </div>
      {error && (
        <p className="mt-2 text-xs text-block" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
