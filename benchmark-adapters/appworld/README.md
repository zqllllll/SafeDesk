# agentgate-appworld

`agentgate-appworld` converts AppWorld's public function-calling API schemas and existing runtime traces into the framework-independent contracts in `agentgate-core`.

## Offline conversion semantics

- Every model-proposed Tool Call becomes an `ActionIR`, including non-executed and out-of-catalog calls.
- Every write action gets an `EffectRecord`.
- A normal tool return is only `APPLIED_UNVERIFIED`; only a future environment readback verifier may produce `VERIFIED`.
- A non-executed or missing-result write remains `PLANNED`.
- An explicit runtime error becomes `FAILED`.
- Every available Tool Result becomes `OBSERVED` evidence. A suppressed call result uses the `runtime` source, not `tool_result`.
- Credentials are recursively redacted from arguments, expected changes, actual changes, and evidence values.
- Hidden task metadata, ground truth, evaluator tests, and final benchmark scores are never read.

Legacy traces created before the runner recorded `executed` are recognized only when every Tool Result in the trace lacks that field. In that format, a recorded Tool Result is treated as executed and a `LEGACY_EXECUTION_FLAGS_INFERRED` diagnostic is emitted once. Missing Tool Results remain missing and are never inferred as executed.

AppWorld's public schemas do not provide side-effect metadata. Catalog v1 therefore uses an explicit read allowlist (`show`, `search`, and `get`) and treats every other operation family as a write. Unrecognized operations and out-of-catalog tools fail closed as critical writes.

For online SafeDesk execution, `AppWorldToolCatalog.to_core_catalog()` returns the framework-neutral catalog consumed by `assemble_agentgate()`. `AppWorldResultProjector` keeps IDs, status, paging fields, required task fields, and bounded collection entries while the complete result remains in Trace.

`supervisor__complete_task` is treated as a control-plane completion signal rather than a domain Effect. Completion Gate, not write-result text, decides whether the task may finish.

## CLI

From the SafeDesk workspace:

```powershell
uv run agentgate-appworld-convert `
  --input benchmarks/results/appworld_function_calling_flash_smoke_4/traces `
  --output benchmarks/results/appworld_function_calling_flash_smoke_4/analysis/agentgate_offline `
  --api-docs benchmarks/appworld-root/data/api_docs/function_calling `
  --run-id appworld_function_calling_flash_smoke_4
```

The converter writes one `*.agentgate.json` bundle per trace, one validated `catalog_snapshot.json`, and one `conversion_summary.json`. Existing outputs are skipped only when source hash, Catalog version, and converter version all match. Use `--overwrite` to rebuild current outputs.

The output is a replay artifact for SafeDesk development. It is not an AppWorld correctness score and cannot establish that a task or side effect was actually completed.

## State & Verification

`AppWorldEnvironmentVerifier` performs post-action readback through an injected public AppWorld API executor and explicit resource profiles. It never calls the evaluator or reads hidden task state.

Historical converted bundles can be audited conservatively:

```powershell
uv run agentgate-appworld-shadow-audit `
  --input benchmarks/results/agentgate_state_verification_shadow/converted/appworld_function_calling_flash_smoke_4 `
  --output benchmarks/results/agentgate_state_verification_shadow/audits/appworld_function_calling_flash_smoke_4
```

The audit reports `conservative_without_task_contract`. It identifies unsupported completion attempts and unverified effects but is not an AppWorld correctness evaluation.
