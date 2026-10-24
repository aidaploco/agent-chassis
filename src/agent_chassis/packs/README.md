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
eval cases (D17 — the tests travel with the capability).

## Files

- [`base.py`](base.py) — `CapabilityPack`, `GuardrailPack`, `ToolSpec`,
  `PackConfig`, `GuardrailVerdict`. Start here to write a pack.
- [`registry.py`](registry.py) — `PackRegistry`: discovers packs and
  `resolve()`s a manifest against them, validating every `config` block and
  recording each override vs the pack default (`OverrideRecord`, the D11 audit
  feed). Fails closed on unknown packs or invalid config.
- [`composition.py`](composition.py) — pure string building: assembles one
  agent's system prompt from persona + pack instruction fragments + the routing
  contract. No runtime involved, so it's trivially testable.
- [`guardrails.py`](guardrails.py) — the **builtin guardrail trio** (message
  length, prompt-injection, off-topic), promoted to chassis builtins because
  every role wants them and the acceptance matrix runs the *same* pack at
  different thresholds across roles. LLM-backed checks go through
  [`utils/llm.py`](../utils/llm.py), never the agent framework.

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
