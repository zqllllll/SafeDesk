# Tau2 GLM-5 No-Thinking Baseline Metrics

## Scope

- Agent: `glm-5`, thinking disabled
- User simulator and judge: `deepseek-v4-flash`, thinking disabled
- Airline: 50 tasks
- Retail: 114 tasks
- Telecom base: 114 tasks
- Total: 278 tasks

## Core Metrics

| Domain | Tasks | Passed | Pass Rate | DB Match Rate | Infra Errors | Avg Duration | Avg Turns | P95 Turns | Max Turns | Avg Tool Calls | Avg Tokens | Total Tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Airline | 50 | 32 | 64.00% | 66.00% | 0 | 63.71 s | 15.38 | 23 | 28 | 7.08 | 65,337 | 3,266,832 |
| Retail | 114 | 91 | 79.82% | 82.46% | 0 | 67.41 s | 17.81 | 26 | 35 | 7.84 | 79,419 | 9,053,789 |
| Telecom | 114 | 113 | 99.12% | 23.68% | 0 | 96.25 s | 37.95 | 64 | 76 | 6.61 | 253,553 | 28,905,094 |
| Overall | 278 | 236 | 84.89% | 55.40% | 0 | 78.57 s | 25.63 | 58 | 76 | 7.20 | 148,294 | 41,225,715 |

`DB Match Rate` is a raw evaluator component and is not equivalent to the final pass rate, especially for Telecom.

## Failure Breakdown

| Failure Type | Airline | Retail | Telecom | Overall |
| --- | ---: | ---: | ---: | ---: |
| DB mismatch | 17 | 20 | 1 | 38 |
| NL assertion failed | 0 | 3 | 0 | 3 |
| Communication failed | 1 | 0 | 0 | 1 |
| Infrastructure error | 0 | 0 | 0 | 0 |
| Total failed | 18 | 23 | 1 | 42 |

## Reliability Metrics

| Metric | Airline | Retail | Telecom | Overall |
| --- | ---: | ---: | ---: | ---: |
| Confirmed premature finish | 0 | 0 | 0 | 0 |
| Premature-finish unknown | 18 | 23 | 1 | 42 |
| Confirmed partial completion | 0 | 0 | 0 | 0 |
| Partial-completion unknown | 18 | 23 | 1 | 42 |
| Max-turn terminations | 0 | 0 | 0 | 0 |
| Max-Turn Rate | 0.00% | 0.00% | 0.00% | 0.00% |
| Duplicate write actions | 0 | 0 | 0 | 0 |
| Duplicate-write task rate | 0.00% | 0.00% | 0.00% | 0.00% |
| Invalid calls | 1 | 23 | 1 | 25 |
| Invalid-call task rate | 2.00% | 15.79% | 0.88% | 7.19% |
| Invalid Call Rate (calls/tool calls) | 0.2825% | 2.5727% | 0.1326% | 1.2488% |
| Duplicate non-write calls | 0 | 4 | 7 | 11 |
| Duplicate-call task rate | 0.00% | 3.51% | 4.39% | 3.24% |
| Duplicate Call Rate (calls/tool calls) | 0.0000% | 0.4474% | 0.9284% | 0.5495% |
| Recovery attempts | 0 | 4 | 1 | 5 |
| Successful recoveries | 0 | 2 | 1 | 3 |
| Confirmed unintended side effects | 0 | 0 | 0 | 0 |
| Side-effect unknown | 18 | 23 | 1 | 42 |

## Definitions and Caveats

- Confirmed false-completion rate uses `premature_finish=true`. Failed `user_stop` runs remain blank because user-simulator termination does not prove that the agent falsely claimed completion. The current result is therefore 0 confirmed cases with 42 unknown cases, not proof of a true 0% false-completion rate.
- Max-Turn Rate counts runs terminated by `max_steps`. All 278 result files use `max_steps=200`; all tasks ended with `user_stop`, so no max-turn termination occurred.
- Invalid Call Rate is `num_invalid_tool_calls / num_tool_calls`. Invalid calls are tool results marked as errors or beginning with `Error:`.
- Duplicate-write task rate is the share of tasks with at least one repeated identical write signature. No duplicate write was observed, so both task-level and call-level rates are zero.
- Token totals sum prompt and completion usage stored in Tau2 trajectory messages. They cannot be converted reliably into cost because agent, user, and judge tokens are not separated by model role in this export.
- `num_read_tool_calls` and `num_write_tool_calls` are derived from evaluator action checks, while `num_tool_calls` counts actual assistant tool calls. They must not be expected to partition total tool calls.
- Partial completion and unintended side effects are conservative fields: successful tasks are false/zero; failed tasks remain blank without a goal-level classifier or trace-level state diff.

## Source Files

- `benchmarks/results/tau2_airline_retail_full_glm5_no_thinking/metrics_summary.csv`
- `benchmarks/results/tau2_telecom_base_glm5_no_thinking_114/metrics_summary.csv`
