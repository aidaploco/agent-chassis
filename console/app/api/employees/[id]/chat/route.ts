import { NextResponse } from 'next/server'
import { callEmployee, findEmployee } from '@/lib/employees'

export const dynamic = 'force-dynamic'

const MAX_MESSAGE = 8000

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params
  const employee = await findEmployee(id)
  if (!employee) return NextResponse.json({ error: `unknown employee ${id}` }, { status: 404 })

  let payload: unknown
  try {
    payload = await request.json()
  } catch {
    return NextResponse.json({ error: 'body must be JSON' }, { status: 400 })
  }
  const { conversation_id, message } = (payload ?? {}) as Record<string, unknown>
  if (typeof conversation_id !== 'string' || typeof message !== 'string' || !message.trim()) {
    return NextResponse.json({ error: 'conversation_id and a non-empty message are required' }, { status: 400 })
  }
  if (message.length > MAX_MESSAGE) {
    return NextResponse.json({ error: `message is longer than ${MAX_MESSAGE} characters` }, { status: 413 })
  }
  // A conversation belongs to one Employee: its id carries the Employee's id, so
  // a stale tab can never write into another Employee's conversation.
  if (!conversation_id.startsWith(`${id}-`)) {
    return NextResponse.json({ error: `conversation_id must start with "${id}-"` }, { status: 400 })
  }

  const upstream = await callEmployee(employee, '/v1/messages', {
    method: 'POST',
    json: { conversation_id, message },
    timeoutMs: 120_000,
  })
  return NextResponse.json(upstream.body, { status: upstream.status })
}
