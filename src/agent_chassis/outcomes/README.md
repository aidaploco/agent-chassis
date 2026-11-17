# `outcomes/` — the outcome feed (D12)

One row per finished conversation or task: the raw feed for usage, cost and
reliability metrics. The public contract is `OutcomeEvent`
([`contracts/outcome.py`](../contracts/outcome.py)), versioned by `event_version`
and pinned by a golden-serialization test.

## Files

- [`base.py`](base.py) — the `OutcomeSink` protocol. `record()` returns `True` if
  a row was written, `False` if one already existed for `(role, workflow_run_id)`.
- [`pgoutcomes.py`](pgoutcomes.py) — `PgOutcomeSink`: the `chassis_outcomes`
  table, one typed column per `OutcomeEvent` field, `CHECK` constraints on the
  resolution and the counts, append-only triggers, unique per run. The table
  setup and pool come from [`pgstore.py`](../pgstore.py), shared with the audit
  store.

## What happens when an outcome is emitted

The `emit_outcome` activity does four things, in this order:

1. **Logs the whole event** as an `outcome_event {...}` line, once (not per
   retry). It is the source a lost row can be backfilled from.
2. **Writes the row.** On a store failure it raises, so Temporal retries.
3. **Emits a span** named `outcome <task_type>` with the metrics as
   `outcome.*` attributes and `session.id` = the conversation id. A dedicated
   span rather than an event on an existing one: an outcome is emitted when a
   conversation idles out, when no request trace is active.
4. **Writes the memory entry**, only after the row landed, so retries do not
   duplicate it.

## Failure behaviour

An outcome is a metric, not an accountability record, so it never holds a
workflow open. The workflow retries the write for about three minutes (8
attempts, 2 s doubling to 60 s: enough for a Postgres restart), then logs an
error and **completes anyway**. The row is then missing, but the log line has the
full event. (Approvals are the opposite: the audit write is on their critical
path; see [`audit/`](../audit/).)

Without `POSTGRES_URL` there is no store: outcomes are only logged and
remembered, and the worker warns at start.

## Querying it

`GET /v1/outcomes` (authenticated; this Employee's rows only; `limit` up to 200)
returns the newest rows as JSON, and the dev console shows them. For anything
more, it is a table. Some starting points:

```sql
-- Tokens and conversations per role per day
select role, date_trunc('day', ended_at) as day, count(*) as outcomes,
       sum(tokens_in) as tokens_in, sum(tokens_out) as tokens_out
from chassis_outcomes group by 1, 2 order by 2 desc;

-- Resolution mix, and the escalation rate
select task_type, resolution, count(*),
       round(100.0 * count(*) / sum(count(*)) over (partition by task_type), 1) as pct
from chassis_outcomes group by 1, 2 order by 1, 2;

-- p95 wall time of conversations, by role
select role, percentile_cont(0.95) within group (order by wall_time_ms) as p95_ms
from chassis_outcomes where task_type = 'conversation' group by 1;
```

The table is append-only (triggers reject `UPDATE`, `DELETE` and `TRUNCATE`); a
Postgres superuser can still drop them.
