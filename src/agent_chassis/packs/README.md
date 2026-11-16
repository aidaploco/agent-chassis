# `packs/` — the reuse mechanism

A **pack** is a self-contained, reusable unit that roles compose through the
manifest — the thing that makes "new Employee = new YAML" possible. Packs are
chassis-side and vendor-free (D2): a pack's tools are plain async callables;
the runtime adapter converts them to vendor tools at build time.

## Three kinds of pack

| Kind | Base class | Contributes | Runs as |
|---|---|---|---|
| **Capability** | `CapabilityPack` | Tools + an instruction fragment + a config model + its own eval suite | Tools run in the agent turn (as activities, or in-workflow if `pure=True`). |
| **Guardrail** | `GuardrailPack` | A `check()` verdict on an inbound message | A chassis pre-flight activity, before the turn (D5). |
| **Output guardrail** | `OutputGuardrailPack` | A `check()` verdict on a final reply | A chassis activity on the reply, before it is returned (D5). |

A pack bundles *everything* it needs: tools, the instruction text that teaches
an agent to use them, a `PackConfig` model whose field constraints *are* the
allowed manifest overrides, its required secrets/scopes, and its regression
eval cases (D17 — declared via `eval_cases_dir()`; the eval runner does not read
bundled suites yet). `required_secrets` are enforced: the worker refuses to
start when a composed pack's secret is unset. `required_scopes` is
informational. Tool names starting `final_result` are reserved by the runtime
and rejected at build.

## Files

- [`base.py`](base.py) — `CapabilityPack`, `GuardrailPack`,
  `OutputGuardrailPack` / `OutputReply`, `ToolSpec`, `PackConfig`,
  `GuardrailVerdict`. Start here to write a pack.
- [`registry.py`](registry.py) — `PackRegistry`: discovers packs and
  `resolve()`s a manifest against them, validating every `config` block and
  recording each override vs the pack default (`OverrideRecord`, the D11 audit
  feed, written to the audit table once per manifest version). Fails closed on
  unknown packs, invalid config, and an `approval_required_tools` entry that no
  composed pack provides.
- [`composition.py`](composition.py) — pure string building: assembles one
  agent's system prompt from persona + pack instruction fragments + the routing
  contract. No runtime involved, so it's trivially testable.
- [`guardrails.py`](guardrails.py) — the **builtin guardrail trio** (message
  length, prompt-injection, off-topic), promoted to chassis builtins because
  every role wants them and the acceptance matrix runs the *same* pack at
  different thresholds across roles. LLM-backed checks go through
  [`utils/llm.py`](../utils/llm.py), never the agent framework. Their verdict is
  requested as a JSON object (`{"verdict": "FLAG"}` / `{"verdict": "PASS"}`) in
  the provider's JSON mode, with the message sent unwrapped: a model in JSON
  mode cannot answer "write me a poem" with a poem. An empty, refused, ambiguous
  or non-conforming reply trips the guardrail (fail closed) with a stated reason.
  (Wrapping the message in tags was tried and made the model refuse ordinary
  greetings; see D5.) The Anthropic SDK has no JSON mode, so there the same
  object is requested in the prompt and a bare `FLAG` / `PASS` is also read.
  `GuardrailPack.check` takes any `PackConfig`; subclasses narrow it inside.
- [`output_guardrails.py`](output_guardrails.py) — the **builtin output
  guardrails**: `reply_length` (`max_chars`, default 4000), `secret_leak`
  (credential values the process holds in key/token/secret/password/webhook
  variables, connection-string passwords, and well-known token shapes) and
  `prompt_leak` (a verbatim run of `min_run_words`, default 30, from the agent's
  own composed instructions). Deterministic by design: they run on every reply,
  so an LLM judge here would add its cost and error rate to every answer. A
  guardrail's reason and detail are logged and traced, so they must say what
  tripped and never quote the reply. A manifest lists input and output
  guardrails together in `guardrails`; the pack's class picks the side, and a
  name is unique across both.

## Tools run at least once

A tool runs as a Temporal activity (unless `pure=True`). If the worker dies after
the tool did its work but before Temporal recorded the result, the call runs again
from the start. Measured: a ticket-creating tool, killed right after it committed,
created a second ticket on retry. A tool with a side effect (create, charge, send,
cancel) must therefore tolerate being run twice.

`idempotency_key()` (from `agent_chassis.packs`) returns a string that is the same on
every attempt of one call and different for every other call. Pass it to the system
the tool writes to as its idempotency key, or derive the record's id from it, so a
retry finds the work done instead of repeating it:

```python
from agent_chassis.packs import idempotency_key


async def create_ticket(email: str, subject: str) -> str:
    key = idempotency_key()  # None for a pure tool or outside an activity
    ticket = tickets_api.create(email, subject, idempotency_key=key)
    return f"Ticket {ticket.id} created"
```

The chassis does not enforce this: a tool that ignores the key is still at-least-once.
Read-only tools need nothing.

## How a pack reaches a role

Three registration mechanisms, chosen by *who wrote the code* (D20):

1. **Chassis builtins** — `memory`, `introspection`, the guardrail trio.
   Registered explicitly in [`role_loader.py`](../role_loader.py).
2. **Entry points** — third-party packs advertised on the
   `agent_chassis.packs` entry-point group (`registry.discover()`).
3. **`PACKS_DIR`** — a directory of `.py` files, each exposing a module-level
   `PACKS` list (`registry.load_directory()`). This is how tenant-custom packs
   arrive via a mounted directory (e.g. a Kubernetes ConfigMap). Note (D20): this code runs with full
   worker-process privileges — the chassis does not sandbox it; the deployment's
   per-tenant isolation is the trust boundary.
