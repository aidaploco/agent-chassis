import { NextResponse } from 'next/server'
import { boundedInt, forward, readObject, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

/** Recent task runs of this Employee, including ones a schedule started. */
export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => {
    const limit = boundedInt(new URL(request.url).searchParams.get('limit'), 20, 1, 50)
    return forward(employee, `/v1/tasks?limit=${limit}`)
  })
}

/** Start one of the Employee's declared tasks. */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => {
    const body = await readObject(request)
    const task = body?.task
    const prompt = body?.prompt_override
    if (typeof task !== 'string' || !/^[a-z][a-z0-9_]{2,40}$/.test(task)) {
      return NextResponse.json({ error: 'task must be a task name' }, { status: 400 })
    }
    if (prompt !== undefined && typeof prompt !== 'string') {
      return NextResponse.json({ error: 'prompt_override must be a string' }, { status: 400 })
    }
    return forward(employee, '/v1/tasks', {
      method: 'POST',
      json: prompt === undefined ? { task } : { task, prompt_override: prompt },
    })
  })
}
