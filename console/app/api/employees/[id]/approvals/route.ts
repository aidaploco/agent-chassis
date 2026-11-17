import { NextResponse } from 'next/server'
import { forward, readObject, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

/** Approve or deny ONE pending call. The chassis also accepts "decide every pending
 *  call" (no call_id); this console never sends that: a person decides what they saw. */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => {
    const body = await readObject(request)
    const { workflow_id, call_id, approved, comment } = body ?? {}
    if (typeof workflow_id !== 'string' || !workflow_id) {
      return NextResponse.json({ error: 'workflow_id is required' }, { status: 400 })
    }
    if (typeof call_id !== 'string' || !call_id) {
      return NextResponse.json({ error: 'call_id is required: decide one call at a time' }, { status: 400 })
    }
    if (typeof approved !== 'boolean') {
      return NextResponse.json({ error: 'approved must be true or false' }, { status: 400 })
    }
    if (comment !== undefined && (typeof comment !== 'string' || comment.length > 1000)) {
      return NextResponse.json({ error: 'comment must be a string of at most 1000 characters' }, { status: 400 })
    }
    return forward(employee, '/v1/approvals', {
      method: 'POST',
      json: { workflow_id, call_id, approved, comment: comment ?? '' },
    })
  })
}
