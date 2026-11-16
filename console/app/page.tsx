'use client'

import { useEffect, useState } from 'react'
import { Chat } from '@/components/Chat'
import { EmployeeSwitcher } from '@/components/EmployeeSwitcher'
import { RolePanel } from '@/components/RolePanel'
import type { ChatResponse, EmployeeSummary, SelfDescription, Turn } from '@/lib/types'

interface Conversation {
  id: string
  turns: Turn[]
}

const newConversation = (employeeId: string): Conversation => ({ id: `${employeeId}-${crypto.randomUUID()}`, turns: [] })

function describeError(body: unknown, fallback: string): string {
  if (typeof body === 'object' && body !== null) {
    const { error, detail } = body as { error?: unknown; detail?: unknown }
    if (typeof error === 'string') return typeof detail === 'string' && detail ? `${error}: ${detail}` : error
  }
  return fallback
}

export default function Home() {
  const [employees, setEmployees] = useState<EmployeeSummary[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [roles, setRoles] = useState<Record<string, SelfDescription>>({})
  const [conversations, setConversations] = useState<Record<string, Conversation>>({})
  const [pending, setPending] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const response = await fetch('/api/employees', { cache: 'no-store' })
        const body: unknown = await response.json()
        if (!response.ok) throw new Error(describeError(body, `HTTP ${response.status}`))
        if (cancelled) return
        const list = body as EmployeeSummary[]
        setEmployees(list)
        setLoadError(null)
        setSelected((current) => current ?? list[0]?.id ?? null)
      } catch (error) {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error))
      }
    }
    void load()
    const timer = setInterval(() => void load(), 15_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const current = employees?.find((employee) => employee.id === selected)
  const currentUp = current?.status === 'up'

  // Load the role once the Employee is up (and again if it comes back after being down).
  useEffect(() => {
    if (!selected || !currentUp || roles[selected]) return
    let cancelled = false
    void fetch(`/api/employees/${selected}/self`, { cache: 'no-store' })
      .then((response) => (response.ok ? (response.json() as Promise<SelfDescription>) : null))
      .then((role) => {
        if (!cancelled && role) setRoles((previous) => ({ ...previous, [selected]: role }))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [selected, currentUp, roles])

  async function send(text: string) {
    if (!selected) return
    const employeeId = selected
    const conversation = conversations[employeeId] ?? newConversation(employeeId)
    const lastReply = [...conversation.turns].reverse().find((turn) => turn.reply)?.reply
    const turn: Turn = { id: crypto.randomUUID(), user: text, previousAgent: lastReply?.agent }

    const update = (change: (turns: Turn[]) => Turn[]) =>
      setConversations((previous) => {
        const base = previous[employeeId] ?? conversation
        return { ...previous, [employeeId]: { ...base, turns: change(base.turns) } }
      })

    update((turns) => [...turns, turn])
    setPending((previous) => ({ ...previous, [employeeId]: true }))
    try {
      const response = await fetch(`/api/employees/${employeeId}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversation.id, message: text }),
      })
      const body: unknown = await response.json().catch(() => null)
      const settled: Turn = response.ok
        ? { ...turn, reply: body as ChatResponse }
        : { ...turn, error: describeError(body, `The Employee answered HTTP ${response.status}.`) }
      update((turns) => turns.map((t) => (t.id === turn.id ? settled : t)))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      update((turns) => turns.map((t) => (t.id === turn.id ? { ...turn, error: message } : t)))
    } finally {
      setPending((previous) => ({ ...previous, [employeeId]: false }))
    }
  }

  function reset() {
    if (!selected) return
    setConversations((previous) => ({ ...previous, [selected]: newConversation(selected) }))
  }

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <h1 className="text-lg font-semibold">agent-chassis console</h1>
        <p className="text-sm text-muted">
          Each Employee below is its own running service. Pick one, talk to it, and see what the chassis did on every turn.
        </p>
      </header>

      {loadError && (
        <div className="mb-4 rounded-xl border border-block/40 bg-block/10 p-3 text-sm text-block" role="alert">
          Could not load the Employees: {loadError}
        </div>
      )}
      {employees && employees.length > 0 && employees.every((employee) => employee.status === 'down') && (
        <div className="mb-4 rounded-xl border border-line bg-card p-3 text-sm" role="status">
          No Employee is answering. Start the stack from the repo root:
          <code className="ml-1 rounded bg-line/60 px-1.5 py-0.5 text-xs">docker compose -f docker-compose.console.yml up --build</code>
        </div>
      )}

      {employees && <EmployeeSwitcher employees={employees} selected={selected} onSelect={setSelected} />}

      {current && (
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <Chat
            key={current.id}
            employee={current}
            role={roles[current.id]}
            turns={(conversations[current.id] ?? { turns: [] }).turns}
            pending={pending[current.id] === true}
            onSend={(text) => void send(text)}
            onReset={reset}
          />
          <RolePanel role={roles[current.id]} loading={currentUp} />
        </div>
      )}
    </main>
  )
}
