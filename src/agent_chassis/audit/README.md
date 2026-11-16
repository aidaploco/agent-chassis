# `audit/` — the audit stream (D11)

Who decided what, when: an append-only record of **approval decisions**,
**escalations** (and approval reminders) and the **manifest overrides** in force.

## Files

- [`base.py`](base.py) — `AuditEvent` (what a row says) and the `AuditSink`
  protocol. `record()` returns `True` if written, `False` if the same
  `(kind, dedupe_key)` already existed.
- [`pgaudit.py`](pgaudit.py) — `PgAuditSink`: the `chassis_audit` table in the
  same Postgres as memory. `start()` creates the table, indexes and triggers
  under an advisory lock (safe with several worker replicas).
- [`overrides.py`](overrides.py) — `record_overrides()` writes each manifest
  override once per manifest version (keyed by the manifest's SHA-256).

## What the database guarantees

Triggers reject `UPDATE`, `DELETE` and `TRUNCATE`, so the application, or an
operator with a normal login, cannot rewrite history. A Postgres superuser can
still drop the triggers: that makes tampering deliberate and visible, not
impossible.

## Failure behaviour (the part to know)

| Entry | Store down | Why |
|---|---|---|
| Approval decision | **The activity raises and Temporal retries.** The task does not proceed until the row is written. | D11: the entry lands *before* the decision takes effect. An outage pauses approvals; it never lets one go unrecorded. |
| Escalation / reminder | Logged, the turn continues | Already acknowledged to the user and pushed to approvers |
| Manifest overrides | Written at worker start | A restart with the same manifest adds nothing |

Without `POSTGRES_URL` there is no store: everything is logged only and the
worker warns at start.

## Reading it

There is no read API yet; query the table:

```sql
select recorded_at, actor, action, subject, detail
from chassis_audit
where kind = 'approval' order by recorded_at desc;
```
