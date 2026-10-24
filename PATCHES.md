# Workflow patch ledger (D14)

Every behavior-affecting change to workflow-deterministic code ships behind
`workflow.patched("<change-slug>")`. Removal follows Temporal's two-step
(`patched` → `deprecate_patch` → delete) once old runs have drained — bounded
by the conversation idle timeout plus margin.

| Patch id | Introduced | Earliest removal | Status |
|---|---|---|---|
| _none active_ | | | |
