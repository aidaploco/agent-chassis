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
  recent_outcomes: string[]
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
