# `lana/` — the customer-service Employee

The V1 reference role: a customer-service Employee's billing/support/
supervisor behavior expressed as a **manifest + packs**. Proves the chassis can
carry a real, multi-agent conversational role.

## Shape

- **Routing graph:** a `supervisor` front door routes to `billing` or `support`
  specialists and back (D4). The supervisor runs on the `standard` tier — a
  deliberate choice: on `light` it misrouted pricing/plan questions.
- **Guardrails:** the builtin trio (message length, prompt-injection,
  off-topic) at Lana-specific thresholds/topic.
- **Memory:** `pgvector`, collection `lana`.
- **Runs on:** `ConversationWorkflow` (long-lived chat).

## Files

- [`manifest.yaml`](manifest.yaml) — the role.
- [`packs/billing.py`](packs/billing.py), [`packs/support.py`](packs/support.py)
  — the two capability packs. Mock-backed and **stateless** on purpose so eval
  cases can't contaminate each other; a real backend swaps the handler bodies.
  Note the cancel-discipline: `cancel_subscription` requires a
  customer-stated reason and must never be invented — enforced by both the
  instructions and the tool description.
- [`evals/cases.yaml`](evals/cases.yaml) — the behavior suite (D17),
  including the multi-turn and cancel-discipline cases the original harness
  couldn't see.

## Run it

```sh
ROLE_DIR=./examples/lana docker compose up --build
python -m agent_chassis.evals http://localhost:8001 examples/lana/evals
```
