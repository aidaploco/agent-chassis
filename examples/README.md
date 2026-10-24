# `examples/` — real roles as permanent fixtures (D20)

These are not throwaway demos. They are the chassis's **acceptance
instruments** and stay in the repo permanently (D20): the whole product claim is
"a new Employee is a manifest + packs, never chassis code," and these prove it
across three deliberately different role shapes.

| Role | Shape | Exercises |
|---|---|---|
| [`skeleton/`](skeleton/) | One agent, one trivial capability, no routing | The **walking skeleton** (M3.5): channel → workflow → adapter → outcome → trace end-to-end, including pod-kill-and-resume. The default role in `docker-compose.yml`. |
| [`lana/`](lana/) | Supervisor + billing + support, full routing graph, guardrails, memory | Customer service — billing, support and routing expressed as a manifest. Ships a ported eval suite ([`lana/evals/`](lana/evals/)). Runs on `ConversationWorkflow`. |
| [`nova/`](nova/) | Single reporter agent, an approval-gated tool, a daily cron | Ops/reporting on `TaskWorkflow` (D6) with human approval (D10). Same builtin guardrail packs as Lana at *different* thresholds — the acceptance matrix. |

## Anatomy of a role

```
<role>/
  manifest.yaml     the Employee — everything declarative
  packs/*.py        capability packs; each exposes a module-level PACKS list
  evals/*.yaml      (optional) bundled eval cases
```

Read them in order — skeleton → Lana → Nova — for increasing complexity. The
packs are mock-backed on purpose (stateless handlers, no shared module state so
runs can't contaminate each other); a real backend swaps the handler bodies and
nothing else. Note that a "destructive" tool like Nova's `publish_report` is
gated in the **manifest** (`escalation.approval_required_tools`), *not* in the
pack code — the same pack composed into another role could run it freely.

## Run one

```sh
ROLE_DIR=./examples/lana docker compose up --build
```

See the [repository README](../README.md#quickstart) for the full quickstart.
