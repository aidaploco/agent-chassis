import { NextResponse } from 'next/server'
import { boundedInt, forward, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

const KINDS = new Set(['approval', 'escalation', 'manifest_override'])

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => {
    const query = new URL(request.url).searchParams
    const limit = boundedInt(query.get('limit'), 50, 1, 200)
    const kind = query.get('kind')
    if (kind !== null && !KINDS.has(kind)) {
      return NextResponse.json({ error: `kind must be one of ${[...KINDS].join(', ')}` }, { status: 400 })
    }
    return forward(employee, `/v1/audit?limit=${limit}${kind ? `&kind=${kind}` : ''}`)
  })
}
