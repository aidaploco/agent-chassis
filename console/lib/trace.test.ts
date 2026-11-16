import test from 'node:test'
import assert from 'node:assert/strict'
import { buildTrace } from './trace.ts'
import type { ChatResponse } from './types.ts'

const role = {
  entry_agent: 'supervisor',
  guardrail_details: [
    { name: 'message_length', stage: 'input' as const },
    { name: 'off_topic', stage: 'input' as const },
    { name: 'secret_leak', stage: 'output' as const },
  ],
}

function reply(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    conversation_id: 'lana-1',
    content: 'hi',
    agent: 'supervisor',
    turn_number: 1,
    escalated: false,
    guardrail_tripped: null,
    tools_called: [],
    ...overrides,
  }
}

const step = (trace: ReturnType<typeof buildTrace>, id: string) => trace.steps.find((s) => s.id === id)!

test('a clean turn passes both sides', () => {
  const trace = buildTrace(reply(), role, undefined)
  assert.equal(step(trace, 'input').state, 'passed')
  assert.equal(step(trace, 'input').detail, '2 passed')
  assert.equal(step(trace, 'output').state, 'passed')
  assert.equal(trace.tripped, null)
})

test('an input trip refuses the message and skips everything after it', () => {
  const trace = buildTrace(reply({ guardrail_tripped: 'off_topic' }), role, undefined)
  assert.equal(step(trace, 'input').state, 'blocked')
  assert.deepEqual(['agent', 'tools', 'output'].map((id) => step(trace, id).state), ['skipped', 'skipped', 'skipped'])
  assert.deepEqual(trace.tripped, { name: 'off_topic', stage: 'input' })
})

test('an output trip passes the input and withholds the reply', () => {
  const trace = buildTrace(reply({ guardrail_tripped: 'secret_leak', agent: 'billing', tools_called: ['get_subscription'] }), role, 'billing')
  assert.equal(step(trace, 'input').state, 'passed')
  assert.equal(step(trace, 'tools').chips[0], 'get_subscription')
  assert.equal(step(trace, 'output').state, 'blocked')
  assert.deepEqual(trace.tripped, { name: 'secret_leak', stage: 'output' })
})

test('a guardrail the role does not list is read as an output trip, never as a skipped agent', () => {
  const trace = buildTrace(reply({ guardrail_tripped: 'brand_new_pack' }), role, undefined)
  assert.equal(trace.tripped?.stage, 'output')
  assert.equal(step(trace, 'agent').state, 'info')
})

test('a hand-off is shown between the previous agent and the new one', () => {
  assert.equal(step(buildTrace(reply({ agent: 'billing' }), role, 'support'), 'agent').detail, 'support → billing')
  assert.equal(step(buildTrace(reply({ agent: 'billing' }), role, 'billing'), 'agent').detail, 'answered by billing')
})

test('on the first turn the entry agent is the one that handed off', () => {
  assert.equal(step(buildTrace(reply({ agent: 'support' }), role, undefined), 'agent').detail, 'supervisor → support')
  assert.equal(step(buildTrace(reply({ agent: 'supervisor' }), role, undefined), 'agent').detail, 'answered by supervisor')
})

test('an escalation notice is not claimed as output-checked', () => {
  const trace = buildTrace(reply({ escalated: true }), role, undefined)
  assert.equal(step(trace, 'output').state, 'skipped')
  assert.equal(trace.escalated, true)
})

test('on an escalated turn tools are requested, not called: a gated one never ran', () => {
  const escalated = buildTrace(reply({ escalated: true, tools_called: ['get_metrics', 'publish_report'] }), role, undefined)
  assert.equal(step(escalated, 'tools').detail, '2 requested')
  const normal = buildTrace(reply({ tools_called: ['get_metrics'] }), role, undefined)
  assert.equal(step(normal, 'tools').detail, '1 called')
})

test('a role with no guardrails shows none, not a false pass', () => {
  const trace = buildTrace(reply(), { entry_agent: 'reporter', guardrail_details: [] }, undefined)
  assert.equal(step(trace, 'input').state, 'none')
  assert.equal(step(trace, 'output').state, 'none')
})

test('before the role has loaded nothing is claimed as passed', () => {
  const trace = buildTrace(reply(), undefined, undefined)
  assert.equal(step(trace, 'input').state, 'none')
  assert.equal(step(trace, 'output').state, 'none')
})
