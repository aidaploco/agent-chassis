# `manifest/` — role manifest schema v1 + loader (D7)

A role manifest is the one YAML file that *is* an Employee. This package turns
it into a validated, typed [`RoleManifest`](schema.py) — or fails closed with a
message naming the offending field.

## Files

- [`schema.py`](schema.py) — the pydantic v1 schema. Every model sets
  `extra="forbid"`, so an unknown field *anywhere* in the manifest is an error.
  Cross-field rules (routing edges reference declared agents, capabilities are
  declared before use, schedules reference declared tasks, expiry after
  reminder, …) live in the `_cross_references` validator on `RoleManifest`.
- [`loader.py`](loader.py) — `load_manifest(path)`: read YAML, validate,
  raise `ManifestError` with every offending path listed. No partial loads.

## Why identifiers are constrained

`employee.name`, agent names, and pack instance ids must match
`^[a-z][a-z0-9_]{2,40}$` (the `Identifier` type) and are **frozen once
deployed**. Temporal derives activity names from them (D1); a rename is a new
deployment, not an edit. The schema makes that stability a *validated* property
rather than a convention someone has to remember.

## The shape (abridged)

```yaml
schema_version: 1
employee:   { name, role, persona: { display_name, tone } }
runtime:    { model_tier, temperature, max_output_tokens, per_agent, history_budget }
capabilities: [ { pack, id?, config } ]      # config validated by the pack's own model
routing:    { entry, agents, edges, max_hops_per_message }
channels:   [ http | slack ]                 # only these two; slack also mounts the webhook route
guardrails: [ { pack, config } ]             # input and output packs together; overrides must satisfy the pack's bounds
memory:     { substrate: pgvector | none, collection }
escalation: { approvers_channel, remind_after, expire_after, approval_required_tools }
kpis:       { task_types }                   # `conversation` and/or declared task names
tasks:      [ { name, agent, prompt } ]      # TaskWorkflow units (D6)
schedules:  [ { name, cron, task } ]         # cron triggers referencing tasks
```

## What fails closed

The loader and the registry reject, with the offending path named: an unknown
field anywhere; a channel other than `http` / `slack`; a memory substrate other
than `pgvector` / `none`; an `approvers_channel` that is not an enabled channel;
`kpis.task_types` that omit a declared task or list a type nothing emits; routing
edges, agents, tasks or schedules that reference undeclared names; and (at
pack-resolution time, `packs/registry.py`) an `approval_required_tools` entry no
composed capability provides, which would otherwise leave the real tool
ungated. A pack's `required_secrets` are checked when the worker starts.

`approvers_channel` decides where escalations, approval requests and reminders
are pushed: `slack` posts them to `SLACK_APPROVERS_WEBHOOK_URL`; `http` is
pull-based, so they are only logged and approvers poll `GET /v1/tasks/{id}`
(the worker warns at start).

The full annotated schema is in [`schema.py`](schema.py); working examples are
in [`../../../examples/`](../../../examples/) (skeleton → Lana → Nova, in
increasing complexity). The authoritative prose is *Manifest schema v1* in
[`DESIGN.md`](../../../DESIGN.md).
