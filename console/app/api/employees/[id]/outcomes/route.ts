import { boundedInt, forward, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  return withEmployee(request, context, async (employee) => {
    const limit = boundedInt(new URL(request.url).searchParams.get('limit'), 50, 1, 200)
    return forward(employee, `/v1/outcomes?limit=${limit}`)
  })
}
