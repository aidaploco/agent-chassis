import { NextResponse } from 'next/server'
import { forward, readObject, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

const MAX_MESSAGE = 8000

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee, params) => {
    const body = await readObject(request)
    const { conversation_id, message } = body ?? {}
    if (typeof conversation_id !== 'string' || typeof message !== 'string' || !message.trim()) {
      return NextResponse.json({ error: 'conversation_id and a non-empty message are required' }, { status: 400 })
    }
    if (message.length > MAX_MESSAGE) {
      return NextResponse.json({ error: `message is longer than ${MAX_MESSAGE} characters` }, { status: 413 })
    }
    // A conversation belongs to one Employee: its id carries the Employee's id, so a stale
    // tab can never write into another Employee's conversation.
    if (!conversation_id.startsWith(`${params.id}-`)) {
      return NextResponse.json({ error: `conversation_id must start with "${params.id}-"` }, { status: 400 })
    }
    return forward(employee, '/v1/messages', {
      method: 'POST',
      json: { conversation_id, message },
      timeoutMs: 120_000,
    })
  })
}
