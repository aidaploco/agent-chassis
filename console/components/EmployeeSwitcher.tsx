import type { EmployeeSummary } from '@/lib/types'

interface Props {
  employees: EmployeeSummary[]
  selected: string | null
  onSelect: (id: string) => void
}

/** The way to pick which Employee you are talking to. Each is its own api+worker
 *  pair in the chassis; picking one switches the chat and the role panel. */
export function EmployeeSwitcher({ employees, selected, onSelect }: Props) {
  return (
    <div role="tablist" aria-label="Employee" className="flex flex-wrap gap-2">
      {employees.map((employee) => {
        const active = employee.id === selected
        return (
          <button
            key={employee.id}
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(employee.id)}
            className={`group flex min-w-[15rem] items-center gap-3 rounded-xl border px-3.5 py-2.5 text-left transition ${
              active ? 'border-accent bg-accent/10 shadow-sm' : 'border-line bg-card hover:border-accent/50'
            }`}
          >
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                active ? 'bg-accent text-white' : 'bg-line text-ink'
              }`}
              aria-hidden
            >
              {employee.displayName.slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-2 text-sm font-semibold">
                {employee.displayName}
                <span
                  className={`inline-flex items-center gap-1 text-[11px] font-normal ${
                    employee.status === 'up' ? 'text-pass' : 'text-block'
                  }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${employee.status === 'up' ? 'bg-pass' : 'bg-block'}`} />
                  {employee.status === 'up' ? 'online' : 'not answering'}
                </span>
              </span>
              <span className="block truncate text-xs text-muted">{employee.blurb || employee.role}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
