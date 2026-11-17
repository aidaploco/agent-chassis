import { forward, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => forward(employee, '/v1/self', { timeoutMs: 8000 }))
}
