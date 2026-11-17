# `console/` — the dev console

A small web app for **seeing what the chassis does**. Pick an Employee, talk to it, and
under every reply see what happened to produce it: which input checks passed, which agent
answered, which tools it used, and whether the reply was checked or withheld. The side panel
shows who that Employee is and how a message travels through it.

It is a dev tool, not a product: it talks to the HTTP API of running Employees and adds
nothing to the chassis's runtime.

![The console showing Lana: a ticket-creating turn, then a refused prompt-injection attempt](../docs/images/console-lana.jpg)

## Run it

From the repo root, start two Employees side by side (Lana and Nova, each its own api and
worker on its own task queue, sharing one Temporal and one Postgres), then the console:

```bash
# .env needs OPENAI_API_KEY and TEMPORAL_PAYLOAD_CODEC_KEY, as for the default stack
docker compose -f docker-compose.console.yml up --build

cd console
npm ci
npm run dev          # http://localhost:3100
```

Use `docker-compose.console.yml` **or** the default `docker-compose.yml`, not both: they claim
the same host ports.

## What you see

- **The employee switcher.** Each Employee is a separate running service (D16: one role per
  worker), so switching is picking a different one, with no restart. Each keeps its own
  conversation, and a dot shows whether it is answering.
- **A trace under every reply**, four steps:
  1. *Input checks*: the guardrails that can refuse a message. Red and "refused by `x`" when
     one trips; everything after it is greyed out, because the agent never ran.
  2. *Agent*: who answered, and the hand-off (`supervisor → support`).
  3. *Tools*: what the agent called. On an escalated turn it says "requested" instead,
     because the list then includes the approval-gated call the chassis discarded without running.
  4. *Output checks*: the guardrails that can withhold a reply (D5). An escalation notice is
     fixed text the chassis writes, so it is shown as not checked.
- **The role panel**, read from the running chassis (`GET /v1/self`), not hand-drawn: the
  Employee's pipeline, its agents with their tools and who they can hand off to, and its
  recently finished conversations.
- **Tasks and approvals** (for an Employee that has tasks, like Nova). Run a task, watch it
  pause on its approval-gated tool, read exactly what it wants to do, and approve or deny
  that one call with a comment. The confirmation first says the decision was *sent* as the
  console's principal, and says *recorded* only once the audit log has the row: delivering
  an approval only sends a signal, and a task drops it if nothing is pending (a second tab
  decided the call first, for instance), in which case the card says so after 20 seconds.
  A task a schedule started shows up too. If the chassis had to cut a call's arguments
  short, the card says so, because you would otherwise approve something you cannot read in full.
- **Records**: this Employee's finished conversations and tasks, and its audit log
  (approvals with their actor and comment, escalations, manifest overrides). Read-only, and
  only that Employee's rows: the chassis scopes both.
- **Example messages**, including ones that try to break it (a prompt-injection attempt, an
  off-topic request, a cancellation without a reason).

![The console showing Nova: an error summary, then a publish request that ended as an escalation](../docs/images/console-nova.jpg)

Above, Nova summarized the errors (one tool), then was asked to publish a report: she asked
for the approval-gated `publish_report`, so the turn ended as an escalation to a human, and
the tools are labelled *requested* because the gated one never ran.

A guardrail that *passed* is not reported by the chassis, only one that tripped, so "passed"
is inferred: the role has guardrails on that side and none tripped. `lib/trace.ts` holds that
logic in one place, and `npm test` covers it. The inference can overstate in one case: when
the chassis falls back to its fixed "I wasn't able to complete that request" reply (a bad route
or an exhausted hop budget), it skips the output checks, but the console cannot tell and shows
them as passed.

## How it is built

Next.js 16 (App Router), React 19, TypeScript, Tailwind 4. The browser never talks to the
chassis: it calls this app's own routes (`app/api/employees/...`), and those add the bearer
token and forward the request. That is why it is Next.js and not a plain browser app: the
token stays on the server, and the chassis needs no CORS configuration.

| File | Role |
|---|---|
| `employees.json` | The Employees to show: id, base URL, a blurb and example messages. No secrets. |
| `lib/employees.ts` | Server-only: loads that file and calls an Employee with the token. |
| `app/api/employees/` | The proxy routes: list (with live status), `/{id}/self`, `/{id}/chat`, `/{id}/tasks`, `/{id}/approvals`, `/{id}/audit`, `/{id}/outcomes`. |
| `lib/guard.ts` | The host and origin check every route applies (below). |
| `lib/trace.ts` | Turns a reply plus the role into the four trace steps (pure, tested). |
| `components/` | The switcher, the chat, the trace strip and the role panel. |

Settings (`.env.example`): `CONSOLE_TOKEN` is the bearer token the server sends (default
`dev-console-token`, the same constant `docker-compose.console.yml` configures),
`CONSOLE_EMPLOYEES` points at a different employees file, and `CONSOLE_ALLOWED_HOSTS` lists
the `Host` values the console answers to (default `127.0.0.1:3100,localhost:3100`). If you change
the port or open the console under another name, every route answers 403 until you list that
host there.

## Why it can safely approve things

The console has no login of its own and, with approvals, it can now decide something. It
listens on loopback only, but a web page open in the same browser can still send requests to
`127.0.0.1`. Every route therefore checks the `Host` header (a DNS-rebinding page cannot
arrange the right one) and, for anything that changes something, that the request comes
from the console's own origin and is JSON (a cross-site form cannot send either). It also
refuses the chassis's "decide every pending call" form: a person decides one call at a time,
having seen it. Decisions are recorded as the console's token principal (`console`): one
shared identity, since there is no per-person login yet.

![The console's Tasks and approvals view: a run waiting for approval, showing the report Nova wants to publish with Approve and Deny](../docs/images/console-tasks.jpg)

![The console's audit log for Nova: approvals and denials with their actor and comment, escalations and manifest overrides](../docs/images/console-audit.jpg)

## Limits

- **Dev only, and the console itself has no login.** It forwards whatever the browser asks
  to the Employees using one server-held token, so anyone who can open the console can use
  that token. Both the console (`--hostname 127.0.0.1`) and every port in
  `docker-compose.console.yml` are bound to loopback so that only this machine can. The
  compose file turns authentication on with a published token because Nova's task and
  approval endpoints refuse to serve without it (D21). Do not change the bindings or put
  either behind a proxy.
- **Nova's schedule is live.** Her manifest has a daily 08:00 cron, so in this stack her
  worker starts a report task that waits for an approval (up to 8 hours). It shows up under
  Tasks and approvals; decide it or let it expire. It is Nova behaving as designed.
- **Expiry is not exercised here.** A task nobody answers is denied by the system after
  `expire_after` (8 hours for Nova); that path is covered by the chassis's own fast-expiry
  test, not by this console.
- **A denied approval ends the task as an escalation**, which is how the chassis treats it:
  the final reply says a human will follow up.
- **One conversation per Employee at a time**, kept in the browser tab and lost on reload.
- **A withheld reply** (an output guardrail tripping) is covered by unit tests of the trace
  logic and by the chassis's own tests, but has not been rendered live in the console, because
  the example roles' output checks do not trip on ordinary replies.
