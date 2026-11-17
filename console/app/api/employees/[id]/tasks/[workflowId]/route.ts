import { forward, withEmployee } from '@/lib/route'

export const dynamic = 'force-dynamic'

/** One task: live state and the pending approvals while it runs, its reply once done. */
export async function GET(request: Request, context: { params: Promise<{ id: string; workflowId: string }> }) {
  return withEmployee(request, context, async (employee, params) => {
    // Workflow ids of scheduled runs contain ':' and timestamps: encode, never concatenate raw.
    return forward(employee, `/v1/tasks/${encodeURIComponent(params.workflowId ?? '')}`)
  })
}
