import test from 'node:test'
import assert from 'node:assert/strict'
import { argsAreTruncated, findDecisionRow, describeTaskState, formatDuration, parseArgs, relativeTime, summarizeAudit } from './format.ts'

test('durations read as a person would say them', () => {
  assert.equal(formatDuration(240), '240 ms')
  assert.equal(formatDuration(1500), '1.5 s')
  assert.equal(formatDuration(42_000), '42 s')
  assert.equal(formatDuration(125_000), '2 m 5 s')
  assert.equal(formatDuration(3_900_000), '1 h 5 m')
  assert.equal(formatDuration(-1), '—')
})

test('relative times', () => {
  const now = Date.parse('2026-11-16T12:00:00Z')
  assert.equal(relativeTime('2026-11-16T11:59:58Z', now), 'just now')
  assert.equal(relativeTime('2026-11-16T11:59:00Z', now), '1 m ago')
  assert.equal(relativeTime('2026-11-16T09:00:00Z', now), '3 h ago')
  assert.equal(relativeTime(null, now), '—')
  assert.equal(relativeTime('garbage', now), '—')
})

test('approval arguments become readable rows', () => {
  assert.deepEqual(parseArgs('{"title":"Daily Ops Report","count":3}'), [
    { key: 'title', value: 'Daily Ops Report' },
    { key: 'count', value: '3' },
  ])
})

test('arguments that are not a JSON object are shown as they are, never dropped', () => {
  assert.deepEqual(parseArgs('just some text'), [{ key: 'arguments', value: 'just some text' }])
  assert.deepEqual(parseArgs('[1,2]'), [{ key: 'arguments', value: '[1,2]' }])
})

test('audit rows are summarised per kind', () => {
  assert.equal(summarizeAudit({ kind: 'approval', action: 'approved', detail: { tool: 'publish_report', comment: 'ship it' } }), 'publish_report · “ship it”')
  assert.equal(summarizeAudit({ kind: 'escalation', action: 'escalated', detail: { reason: 'approval required: publish_report' } }), 'approval required: publish_report')
  assert.equal(summarizeAudit({ kind: 'manifest_override', action: 'override', detail: { setting: 'threshold', value: 0.7, default: 0.5 } }), 'threshold = 0.7 (default 0.5)')
})

test('a long summary is clipped', () => {
  const long = 'x'.repeat(300)
  assert.ok(summarizeAudit({ kind: 'escalation', action: 'escalated', detail: { reason: long } }).length <= 90)
})

test('arguments the chassis cut short are recognised, and only those', () => {
  assert.equal(argsAreTruncated('{"body":"abc"}… [truncated: 120 more characters not shown]'), true)
  assert.equal(argsAreTruncated('{"body":"abc"}'), false)
  // A report that merely MENTIONS the marker in the middle is not truncated.
  assert.equal(argsAreTruncated('say [truncated: 5 more characters not shown] then continue'), false)
})

test('a decision counts as recorded only when its own audit row exists', () => {
  const rows = [
    { kind: 'approval' as const, subject: 'task-1', actor: 'console', action: 'approved', detail: { call_id: 'call_a' } },
    { kind: 'escalation' as const, subject: 'task-1', actor: 'nova', action: 'escalated', detail: { call_id: 'call_b' } },
  ]
  assert.deepEqual(findDecisionRow(rows, 'task-1', 'call_a'), { actor: 'console', action: 'approved' })
  assert.equal(findDecisionRow(rows, 'task-1', 'call_b'), null) // an escalation is not a decision
  assert.equal(findDecisionRow(rows, 'task-2', 'call_a'), null) // another task's call
  assert.equal(findDecisionRow([], 'task-1', 'call_a'), null)
})

test('task states', () => {
  assert.deepEqual(describeTaskState('RUNNING', 'waiting_on_human'), { label: 'waiting for approval', tone: 'wait' })
  assert.deepEqual(describeTaskState('RUNNING', null), { label: 'running', tone: 'run' })
  assert.deepEqual(describeTaskState('COMPLETED', null), { label: 'finished', tone: 'ok' })
  assert.equal(describeTaskState('FAILED', null).tone, 'bad')
})
