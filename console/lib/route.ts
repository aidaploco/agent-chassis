import 'server-only'
import { NextResponse } from 'next/server'
import { checkRequest } from './guard'
import { callEmployee, findEmployee, type EmployeeConfig } from './employees'

type Context = { params: Promise<Record<string, string>> }

/** Every API route goes through this: the host/origin guard first, then the
 *  Employee lookup. The handler only runs for a request that passed both. */
export async function withEmployee(
  request: Request,
  context: Context,
  handler: (employee: EmployeeConfig, params: Record<string, string>) => Promise<Response>,
): Promise<Response> {
  const refused = checkRequest(request)
  if (refused) return refused
  const params = await context.params
  const employee = await findEmployee(params.id ?? '')
  if (!employee) return NextResponse.json({ error: `unknown employee ${params.id}` }, { status: 404 })
  return handler(employee, params)
}

export async function forward(
  employee: EmployeeConfig,
  pathname: string,
  init: { method?: 'GET' | 'POST'; json?: unknown; timeoutMs?: number } = {},
): Promise<Response> {
  const upstream = await callEmployee(employee, pathname, { timeoutMs: 15_000, ...init })
  return NextResponse.json(upstream.body, { status: upstream.status })
}

/** A body that is a JSON object, or null. */
export async function readObject(request: Request): Promise<Record<string, unknown> | null> {
  try {
    const body: unknown = await request.json()
    return typeof body === 'object' && body !== null && !Array.isArray(body) ? (body as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** An integer query parameter within [min, max], else the default. */
export function boundedInt(value: string | null, fallback: number, min: number, max: number): number {
  const parsed = value === null ? NaN : Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback
}
