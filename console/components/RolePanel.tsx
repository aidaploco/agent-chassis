import { ArrowDown, Bot, Cpu, Shield, Wrench } from 'lucide-react'
import type { SelfDescription } from '@/lib/types'

function Chip({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'accent' }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
        tone === 'accent' ? 'bg-accent/15 text-accent' : 'bg-line/60 text-ink/80'
      }`}
    >
      {children}
    </span>
  )
}

function Stage({ icon, title, children }: { icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-bg px-3 py-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold">
        <span className="text-muted">{icon}</span>
        {title}
      </div>
      {children && <div className="mt-1.5 flex flex-wrap gap-1">{children}</div>}
    </div>
  )
}

const Down = () => <ArrowDown size={14} className="mx-auto my-0.5 text-muted" aria-hidden />

/** Who this Employee is and how a message travels through it. Everything here is
 *  read from the running chassis (/v1/self): it is the manifest, not a hand-drawn diagram. */
export function RolePanel({ role, loading }: { role: SelfDescription | undefined; loading: boolean }) {
  if (!role) {
    return (
      <aside className="rounded-2xl border border-line bg-card p-4 text-sm text-muted">
        {loading ? 'Loading the role…' : 'The role could not be loaded. Is the Employee running?'}
      </aside>
    )
  }
  const input = role.guardrail_details.filter((g) => g.stage === 'input')
  const output = role.guardrail_details.filter((g) => g.stage === 'output')
  const toolsOf = (capabilityIds: string[]) =>
    role.capabilities.filter((c) => capabilityIds.includes(c.instance_id)).flatMap((c) => c.tools)

  return (
    <aside className="space-y-4" aria-label="Role">
      <section className="rounded-2xl border border-line bg-card p-4">
        <h2 className="text-base font-semibold">{role.display_name}</h2>
        <p className="mt-0.5 text-sm text-muted">{role.role}</p>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted">Tone</dt>
          <dd>{role.tone}</dd>
          <dt className="text-muted">Model tier</dt>
          <dd>{role.model_tier}</dd>
          <dt className="text-muted">Channels</dt>
          <dd>{role.channels.join(', ')}</dd>
        </dl>
      </section>

      <section className="rounded-2xl border border-line bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">How a message travels</h3>
        <div className="text-center text-xs text-muted">message</div>
        <Down />
        <Stage icon={<Shield size={13} aria-hidden />} title="Input checks (can refuse the message)">
          {input.length === 0 ? <span className="text-xs text-muted">none configured</span> : input.map((g) => <Chip key={g.name}>{g.name}</Chip>)}
        </Stage>
        <Down />
        <Stage icon={<Bot size={13} aria-hidden />} title={`Agents (starts with ${role.entry_agent})`}>
          {role.agents.map((name) => (
            <Chip key={name} tone={name === role.entry_agent ? 'accent' : 'plain'}>
              {name}
            </Chip>
          ))}
        </Stage>
        <Down />
        <Stage icon={<Shield size={13} aria-hidden />} title="Output checks (can withhold the reply)">
          {output.length === 0 ? <span className="text-xs text-muted">none configured</span> : output.map((g) => <Chip key={g.name}>{g.name}</Chip>)}
        </Stage>
        <Down />
        <div className="text-center text-xs text-muted">reply</div>
      </section>

      <section className="rounded-2xl border border-line bg-card p-4">
        <h3 className="mb-2 text-sm font-semibold">Agents and tools</h3>
        <ul className="space-y-3">
          {role.agent_graph.map((agent) => {
            const tools = toolsOf(agent.capabilities)
            return (
              <li key={agent.name} className="text-xs">
                <div className="flex items-center gap-1.5 font-semibold">
                  <Cpu size={12} className="text-muted" aria-hidden /> {agent.name}
                  {agent.name === role.entry_agent && <Chip tone="accent">entry</Chip>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <Wrench size={11} className="text-muted" aria-hidden />
                  {tools.length === 0 ? <span className="text-muted">no tools</span> : tools.map((t) => <Chip key={t}>{t}</Chip>)}
                </div>
                {agent.routes_to.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-1 text-muted">
                    hands off to {agent.routes_to.map((t) => <Chip key={t}>{t}</Chip>)}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </section>

      {role.recent_outcomes.length > 0 && (
        <section className="rounded-2xl border border-line bg-card p-4">
          <h3 className="mb-2 text-sm font-semibold">Recently finished</h3>
          <ul className="space-y-1 text-xs text-muted">
            {role.recent_outcomes.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  )
}
