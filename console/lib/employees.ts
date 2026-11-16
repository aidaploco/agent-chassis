import 'server-only'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import type { ExampleMessage } from './types'

export interface EmployeeConfig {
  id: string
  baseUrl: string
  blurb: string
  examples: ExampleMessage[]
}

const ID = /^[a-z0-9_]+$/ // also becomes part of a conversation id, so no '-' or '/'

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** Reads the employee list. Fails loudly on a malformed file: a console that
 *  quietly shows no Employees is harder to debug than an error. */
export async function loadEmployees(): Promise<EmployeeConfig[]> {
  const file = process.env.CONSOLE_EMPLOYEES ?? path.join(process.cwd(), 'employees.json')
  // A config file chosen at run time, read on purpose: tell the bundler not to trace the project.
  const parsed: unknown = JSON.parse(await readFile(/*turbopackIgnore: true*/ file, 'utf8'))
  if (!isObject(parsed) || !Array.isArray(parsed.employees)) {
    throw new Error(`${file}: expected an object with an "employees" array`)
  }
  return parsed.employees.map((raw: unknown, index: number): EmployeeConfig => {
    if (!isObject(raw) || typeof raw.id !== 'string' || typeof raw.baseUrl !== 'string') {
      throw new Error(`${file}: employees[${index}] needs a string "id" and "baseUrl"`)
    }
    if (!ID.test(raw.id)) {
      throw new Error(`${file}: employee id ${JSON.stringify(raw.id)} must match ${ID}`)
    }
    return {
      id: raw.id,
      baseUrl: raw.baseUrl.replace(/\/+$/, ''),
      blurb: typeof raw.blurb === 'string' ? raw.blurb : '',
      examples: Array.isArray(raw.examples) ? (raw.examples as ExampleMessage[]) : [],
    }
  })
}

export async function findEmployee(id: string): Promise<EmployeeConfig | undefined> {
  return (await loadEmployees()).find((employee) => employee.id === id)
}

export interface Upstream {
  ok: boolean
  status: number
  body: unknown
}

/** Calls an Employee's chassis api with the server-held token. Never throws:
 *  an unreachable or misbehaving Employee comes back as a status and a body the
 *  UI can show, not as a crashed route. */
export async function callEmployee(
  employee: EmployeeConfig,
  pathname: string,
  init: { method?: 'GET' | 'POST'; json?: unknown; timeoutMs: number },
): Promise<Upstream> {
  const token = process.env.CONSOLE_TOKEN ?? 'dev-console-token'
  try {
    const response = await fetch(`${employee.baseUrl}${pathname}`, {
      method: init.method ?? 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init.json === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: init.json === undefined ? undefined : JSON.stringify(init.json),
      signal: AbortSignal.timeout(init.timeoutMs),
      cache: 'no-store',
    })
    const text = await response.text()
    let body: unknown
    try {
      body = JSON.parse(text)
    } catch {
      // The chassis answers an unhandled failure (e.g. the model provider
      // refusing a request) with a bare 500 that is not JSON.
      body = { error: `${employee.id} returned HTTP ${response.status}`, detail: text.slice(0, 300) }
    }
    return { ok: response.ok, status: response.status, body }
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    return { ok: false, status: 502, body: { error: `cannot reach ${employee.id} at ${employee.baseUrl}`, detail: reason } }
  }
}
