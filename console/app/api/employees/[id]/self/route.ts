import { NextResponse } from 'next/server'
import { callEmployee, findEmployee } from '@/lib/employees'

export const dynamic = 'force-dynamic'

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params
  const employee = await findEmployee(id)
  if (!employee) return NextResponse.json({ error: `unknown employee ${id}` }, { status: 404 })
  const upstream = await callEmployee(employee, '/v1/self', { timeoutMs: 8000 })
  return NextResponse.json(upstream.body, { status: upstream.status })
}
