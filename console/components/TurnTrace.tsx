import { ArrowRight, Check, Minus, ShieldAlert, UserRound } from 'lucide-react'
import type { StepState, Trace, TraceStep } from '@/lib/trace'

const STYLE: Record<StepState, string> = {
  passed: 'border-pass/40 bg-pass/10 text-pass',
  blocked: 'border-block/50 bg-block/10 text-block',
  info: 'border-accent/40 bg-accent/10 text-accent',
  skipped: 'border-line bg-transparent text-muted opacity-70',
  none: 'border-line bg-transparent text-muted',
}

function Icon({ state }: { state: StepState }) {
  if (state === 'passed') return <Check size={13} aria-hidden />
  if (state === 'blocked') return <ShieldAlert size={13} aria-hidden />
  return <Minus size={13} aria-hidden />
}

function Step({ step }: { step: TraceStep }) {
  return (
    <div className={`min-w-[7.5rem] flex-1 rounded-lg border px-2.5 py-1.5 ${STYLE[step.state]}`}>
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide">
        <Icon state={step.state} />
        {step.title}
      </div>
      <div className="mt-0.5 text-xs">{step.detail || (step.state === 'skipped' ? 'skipped' : '')}</div>
      {step.chips.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {step.chips.map((chip) => (
            <span key={chip} className="rounded bg-card/70 px-1.5 py-0.5 font-mono text-[10px] text-ink/80">
              {chip}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** What the chassis did to produce one reply: input checks, agent, tools, output checks. */
export function TurnTrace({ trace }: { trace: Trace }) {
  return (
    <div className="mt-2" aria-label="What happened on this turn">
      <ol className="flex flex-wrap items-stretch gap-1.5">
        {trace.steps.map((step, index) => (
          <li key={step.id} className="contents">
            {index > 0 && <ArrowRight size={14} className="mt-3 hidden shrink-0 text-muted sm:block" aria-hidden />}
            <Step step={step} />
          </li>
        ))}
      </ol>
      {trace.escalated && (
        <p className="mt-1.5 flex items-center gap-1.5 text-xs text-accent">
          <UserRound size={13} aria-hidden /> Escalated and recorded. The tools above were requested; a gated one was not run, and a
          conversation does not wait for approval (only tasks do).
        </p>
      )}
    </div>
  )
}
