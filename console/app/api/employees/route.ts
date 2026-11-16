import { NextResponse } from 'next/server'
import { callEmployee, loadEmployees } from '@/lib/employees'
import type { EmployeeSummary } from '@/lib/types'

export const dynamic = 'force-dynamic'

/** Every configured Employee, with whether it is answering right now and who it
 *  says it is. The base URL and the token stay on the server. */
export async function GET() {
  try {
    const employees = await loadEmployees()
    const summaries = await Promise.all(
      employees.map(async (employee): Promise<EmployeeSummary> => {
        const self = await callEmployee(employee, '/v1/self', { timeoutMs: 4000 })
        const body = self.body as { display_name?: string; role?: string }
        return {
          id: employee.id,
          blurb: employee.blurb,
          examples: employee.examples,
          status: self.ok ? 'up' : 'down',
          displayName: self.ok && body.display_name ? body.display_name : employee.id,
          role: self.ok && body.role ? body.role : '',
        }
      }),
    )
    return NextResponse.json(summaries)
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 })
  }
}
