# `packs/` — the reuse mechanism

A **pack** is a self-contained, reusable unit that roles compose through the
manifest — the thing that makes "new Employee = new YAML" possible. Packs are
chassis-side and vendor-free (D2): a pack's tools are plain async callables;
the runtime adapter converts them to vendor tools at build time.

## Two kinds of pack

| Kind | Base class | Contributes | Runs as |
|---|---|---|---|
| **Capability** | `CapabilityPack` | Tools + an instruction fragment + a config model + its own eval suite | Tools run in the agent turn (as activities, or in-workflow if `pure=True`). |
| **Guardrail** | `GuardrailPack` | A `check()` verdict on an inbound message | A chassis pre-flight activity, before the turn (D5). |

A pack bundles *everything* it needs: tools, the instruction text that teaches
an agent to use them, a `PackConfig` model whose field constraints *are* the
allowed manifest overrides, its required secrets/scopes, and its regression
eval cases (D17 — declared via `eval_cases_dir()`; the eval runner does not read
bundled suites yet). `required_secrets` are enforced: the worker refuses to
start when a composed pack's secret is unset. `required_scopes` is
informational. Tool names starting `final_result` are reserved by the runtime
and rejected at build.

## Files

- [`base.py`](base.py) — `CapabilityPack`, `GuardrailPack`, `ToolSpec`,
  `PackConfig`, `GuardrailVerdict`. Start here to write a pack.
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
  strict `FLAG` / `PASS`: an empty, refused or ambiguous reply trips the
  guardrail (fail closed) with a stated reason. The judged message is handed over
  as quoted data inside `<message>` tags, with tag-like text defanged, so a
  message that says "write me a poem" cannot be obeyed by the guardrail model.
  `GuardrailPack.check` takes any `PackConfig`; subclasses narrow it inside.

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
