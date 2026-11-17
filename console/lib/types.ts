// Shapes the browser sees. They mirror the chassis's own responses
// (ChatResponse and SelfDescription in src/agent_chassis) minus anything secret.

export interface ExampleMessage {
  label: string
  text: string
}

/** What the browser is told about an Employee: never the URL or the token. */
export interface EmployeeSummary {
  id: string
  blurb: string
  examples: ExampleMessage[]
  status: 'up' | 'down'
  displayName: string
  role: string
}

export interface Capability {
  pack: string
  instance_id: string
  tools: string[]
  required_secrets: string[]
  required_scopes: string[]
}

export interface GuardrailDetail {
  name: string
  stage: 'input' | 'output'
}

export interface AgentNode {
  name: string
  capabilities: string[]
  routes_to: string[]
}

export interface SelfDescription {
  employee: string
  role: string
  display_name: string
  tone: string
  model_tier: string
  channels: string[]
  agents: string[]
  capabilities: Capability[]
  guardrails: string[]
  guardrail_details: GuardrailDetail[]
  entry_agent: string
  agent_graph: AgentNode[]
  tasks: TaskDescription[]
  approval_required_tools: string[]
  recent_outcomes: string[]
}

export interface TaskDescription {
  name: string
  agent: string
  prompt: string
  /** Cron expressions, when the task also runs on its own. */
  schedules: string[]
}

export interface ChatResponse {
  conversation_id: string
  content: string
  agent: string
  turn_number: number
  escalated: boolean
  guardrail_tripped: string | null
  tools_called: string[]
}

/** One exchange in the chat, with what the chassis did to produce it. */
export interface Turn {
  id: string
  user: string
  /** Present once the chassis has answered. */
  reply?: ChatResponse
  /** The agent that answered the previous turn, to spot a hand-off. */
  previousAgent?: string
  error?: string
}

/** One row of the task list. */
export interface TaskSummary {
  workflow_id: string
  task: string | null
  /** Temporal's status: RUNNING, COMPLETED, FAILED, TERMINATED... */
  status: string
  /** For a RUNNING task: 'running' or 'waiting_on_human'. */
  state: string | null
  pending_approvals: number
  started_at: string | null
  closed_at: string | null
}

export interface ApprovalItem {
  call_id: string
  tool: string
  /** The tool call's arguments, as the model wrote them (usually JSON text). */
  args: string
}

/** A running (or failed) task, as GET /v1/tasks/{id} reports it. */
export interface TaskLive {
  task: string
  state: 'running' | 'waiting_on_human' | 'failed'
  pending: ApprovalItem[]
  error: string | null
}

/** A finished task's result. */
export interface TaskResult {
  task: string
  content: string
  agent: string
  escalated: boolean
  guardrail_tripped?: string | null
  approvals: number
  denials: number
  expired: number
}

export interface ApprovalDecision {
  status: string
  /** Who the decision was recorded as: the console's token principal. */
  approver: string
}

export interface AuditRecord {
  kind: 'approval' | 'escalation' | 'manifest_override'
  employee: string
  tenant: string
  subject: string
  actor: string
  action: string
  detail: Record<string, unknown>
  occurred_at: string
  recorded_at: string
}

export interface OutcomeRecord {
  event_version: number
  role: string
  tenant: string
  task_type: string
  conversation_id: string
  workflow_run_id: string
  resolution: string
  turns: number
  tool_calls: number
  wall_time_ms: number
  tokens_in: number
  tokens_out: number
  escalations: number
  guardrail_trips: number
  models_used: string[]
  started_at: string
  ended_at: string
  recorded_at: string
}
