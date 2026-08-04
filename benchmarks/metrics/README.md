# Benchmark metrics

`extract_tau2_metrics.py` converts tau2 `results.json` files into a compact
per-task CSV plus a run-level JSON summary.

Example from `benchmarks\tau2-bench`:

```powershell
uv run python ..\metrics\extract_tau2_metrics.py `
  .\data\simulations\deerflow_tau_telecom5_deerflow_v4 `
  .\data\simulations\deerflow_tau_airline5_deerflow_v4 `
  .\data\simulations\deerflow_tau_retail5_deerflow_v4 `
  --run-id deerflow_tau_small_15_v4 `
  --output-dir ..\results\deerflow_tau_small_15_v4
```

Token scope: `total_tokens` is the sum of `usage.prompt_tokens` and
`usage.completion_tokens` on messages stored in the tau2 simulation trajectory.
Evaluator or judge calls are included only if tau2 stores them as trajectory
messages.

Additional derived fields:

| Field | Meaning |
| --- | --- |
| `experiment_id` | Shared id for the extracted experiment/run group. |
| `run_id` | Per-task run id, using tau2's simulation id when available. |
| `partial_completion` | Conservative field. False for fully successful tasks; blank unless a goal-level partial-completion classifier is available. |
| `premature_finish` | Conservative field. True only for failed `agent_stop`; blank for failed `user_stop`. |
| `num_invalid_tool_calls` | Tool responses marked with `error=true` or an `Error:` payload. |
| `num_duplicate_tool_calls` | Repeated identical non-write tool calls, counted by tool name plus normalized arguments. |
| `num_duplicate_write_actions` | Repeated identical write tool calls, counted by tool name plus normalized arguments. |
| `num_other_tool_calls` | Assistant tool calls not classified as evaluator read/write action checks. |
| `num_evaluator_checks` | Benchmark evaluator DB plus environment assertion checks available in `reward_info`. |
| `num_evaluator_failures` | Failed benchmark evaluator DB/environment checks. |
| `num_recovery_attempts` | Assistant tool-call attempts made after a failed tool response. |
| `recovery_success` | True/false only when recovery was attempted; blank otherwise. |
| `num_unintended_side_effects` | Conservative field. Zero for fully successful tasks; blank unless a trace-level diff identifies extra or unrelated mutations. |
| `infra_error_type` | Heuristic infrastructure category: timeout, rate limit, model service, sandbox, or unknown. |
| `trace_path` | Path to a separate complete trajectory file. Blank for monolithic tau2 `results.json` output. |

These fields are deterministic heuristics over tau2 trajectories and
`reward_info`; they are meant for triage and aggregate analysis, not as a
replacement for manual review of important failures.
