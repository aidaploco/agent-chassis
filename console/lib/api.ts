// Browser-side calls to the console's own routes. Every state-changing call is JSON and
// is sent from the console's own origin, which is what the server-side guard requires.

export interface ApiResult<T> {
  ok: boolean
  status: number
  data: T | null
  error: string | null
}

function describe(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null) {
    const { error, detail } = body as { error?: unknown; detail?: unknown }
    if (typeof error === 'string') return typeof detail === 'string' && detail ? `${error}: ${detail}` : error
    if (typeof detail === 'string') return detail
  }
  return `HTTP ${status}`
}

export async function api<T>(path: string, init?: { method?: 'POST'; json?: unknown }): Promise<ApiResult<T>> {
  try {
    const response = await fetch(path, {
      method: init?.method ?? 'GET',
      cache: 'no-store',
      headers: init?.json === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: init?.json === undefined ? undefined : JSON.stringify(init.json),
    })
    const body: unknown = await response.json().catch(() => null)
    return response.ok
      ? { ok: true, status: response.status, data: body as T, error: null }
      : { ok: false, status: response.status, data: null, error: describe(body, response.status) }
  } catch (error) {
    return { ok: false, status: 0, data: null, error: error instanceof Error ? error.message : String(error) }
  }
}
