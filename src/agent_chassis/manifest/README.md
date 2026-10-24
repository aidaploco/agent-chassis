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
channels:   [ http | slack ]
guardrails: [ { pack, config } ]             # overrides must satisfy the pack's bounds
memory:     { substrate, collection }
escalation: { approvers_channel, remind_after, expire_after, approval_required_tools }
kpis:       { task_types }
tasks:      [ { name, agent, prompt } ]      # TaskWorkflow units (D6)
schedules:  [ { name, cron, task } ]         # cron triggers referencing tasks
```

The full annotated schema is in [`schema.py`](schema.py); working examples are
in [`../../../examples/`](../../../examples/) (skeleton → Lana → Nova, in
increasing complexity). The authoritative prose is *Manifest schema v1* in
[`DESIGN.md`](../../../DESIGN.md).
