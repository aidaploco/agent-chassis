// Turns what the chassis returned for one reply into the steps the console draws.
// Pure on purpose (no React, no fetch) so it can be tested on its own, and so what
// the screen claims about a turn is decided in exactly one place.
//
// The chassis reports a reply, the agent that wrote it, the tools it called and the
// name of the guardrail that stopped the turn, if any. It does not report guardrails
// that PASSED, so a pass is inferred: a guardrail of that stage exists and none of
// them tripped. That inference is why a stage is only ever shown as "passed" when
// the turn got far enough to run it.
import type { ChatResponse, SelfDescription } from './types.ts'

export type StepState = 'passed' | 'blocked' | 'info' | 'skipped' | 'none'

export interface TraceStep {
  id: 'input' | 'agent' | 'tools' | 'output'
  title: string
  state: StepState
  /** One short line under the title. */
  detail: string
  /** Extra chips (tool names, guardrail names). */
  chips: string[]
}

export interface Trace {
  steps: TraceStep[]
  escalated: boolean
  /** Which guardrail ended the turn, and on which side. */
  tripped: { name: string; stage: 'input' | 'output' } | null
}

export function buildTrace(
  reply: ChatResponse,
  role: Pick<SelfDescription, 'guardrail_details' | 'entry_agent'> | undefined,
  previousAgent: string | undefined,
): Trace {
  const input = (role?.guardrail_details ?? []).filter((g) => g.stage === 'input').map((g) => g.name)
  const output = (role?.guardrail_details ?? []).filter((g) => g.stage === 'output').map((g) => g.name)

  const tripName = reply.guardrail_tripped
  // A trip is an input trip if the role lists it as an input guardrail. A name the
  // role does not list (the console is out of date) is treated as an output trip,
  // the safer reading: the reply was withheld rather than the message refused.
  const tripStage: 'input' | 'output' | null = tripName ? (input.includes(tripName) ? 'input' : 'output') : null
  const inputBlocked = tripStage === 'input'
  const outputBlocked = tripStage === 'output'

  const inputStep: TraceStep = inputBlocked
    ? { id: 'input', title: 'Input checks', state: 'blocked', detail: `refused by ${tripName}`, chips: input }
    : input.length === 0
      ? { id: 'input', title: 'Input checks', state: 'none', detail: 'none configured', chips: [] }
      : { id: 'input', title: 'Input checks', state: 'passed', detail: `${input.length} passed`, chips: input }

  let agentStep: TraceStep
  if (inputBlocked) {
    agentStep = { id: 'agent', title: 'Agent', state: 'skipped', detail: 'never reached the agent', chips: [] }
  } else {
    const handedOff = previousAgent !== undefined && previousAgent !== reply.agent
    const fromEntry = previousAgent === undefined && role?.entry_agent && role.entry_agent !== reply.agent
    const detail = handedOff
      ? `${previousAgent} → ${reply.agent}`
      : fromEntry
        ? `${role?.entry_agent} → ${reply.agent}`
        : `answered by ${reply.agent}`
    agentStep = { id: 'agent', title: 'Agent', state: 'info', detail, chips: [] }
  }

  const toolsStep: TraceStep = inputBlocked
    ? { id: 'tools', title: 'Tools', state: 'skipped', detail: '', chips: [] }
    : reply.tools_called.length === 0
      ? { id: 'tools', title: 'Tools', state: 'none', detail: 'none called', chips: [] }
      : {
          id: 'tools',
          title: 'Tools',
          state: 'info',
          // On an escalated turn the list includes the approval-gated call the chassis
          // discarded without running it, so it is a list of requests, not of actions.
          detail: `${reply.tools_called.length} ${reply.escalated ? 'requested' : 'called'}`,
          chips: reply.tools_called,
        }

  let outputStep: TraceStep
  if (inputBlocked) {
    outputStep = { id: 'output', title: 'Output checks', state: 'skipped', detail: '', chips: [] }
  } else if (outputBlocked) {
    outputStep = { id: 'output', title: 'Output checks', state: 'blocked', detail: `reply withheld by ${tripName}`, chips: output }
  } else if (reply.escalated) {
    // The escalation notice is chassis-written text and is not run through output checks.
    outputStep = { id: 'output', title: 'Output checks', state: 'skipped', detail: 'escalation notice, not checked', chips: [] }
  } else if (output.length === 0) {
    outputStep = { id: 'output', title: 'Output checks', state: 'none', detail: 'none configured', chips: [] }
  } else {
    outputStep = { id: 'output', title: 'Output checks', state: 'passed', detail: `${output.length} passed`, chips: output }
  }

  return {
    steps: [inputStep, agentStep, toolsStep, outputStep],
    escalated: reply.escalated,
    tripped: tripName && tripStage ? { name: tripName, stage: tripStage } : null,
  }
}
