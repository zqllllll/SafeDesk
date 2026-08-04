# AppWorld Test Challenge Baseline Analysis

## Experiment validity

- Dataset: `test_challenge` (417 tasks, 139 complete scenarios)
- Model: `deepseek-v4-flash`
- Thinking: disabled
- API predictor and agent use the same model
- API limit: 20 predicted APIs per task
- Out-of-schema calls receive a tool result but are not executed
- Final infrastructure errors: 0
- All four shards completed and the final states were re-evaluated offline

Eleven Windows GBK failures were re-run with interpreter-level UTF-8. Four of
those tasks passed after the infrastructure fix. The final numbers below include
the repaired results.

## Final scores

| Metric | Result |
| --- | ---: |
| Task Goal Completion | 174 / 417 (41.73%) |
| Scenario Goal Completion | 41 / 139 (29.50%) |
| Evaluator Test Pass Rate | 2218 / 3348 (66.25%) |
| Difficulty 1 Task Completion | 51 / 72 (70.83%) |
| Difficulty 2 Task Completion | 60 / 150 (40.00%) |
| Difficulty 3 Task Completion | 63 / 195 (32.31%) |

## Main failure patterns

- 243 tasks failed overall.
- 90 tasks never called `complete_task`; none of them passed.
- 91 tasks reached 50 turns; only one passed.
- 153 tasks called `complete_task` but still failed evaluation. This is the
  clearest signal that completion-time verification is weak.
- 237 failed tasks passed at least one evaluator test. This suggests substantial
  partial progress, but some passed tests are no-op invariants and should not be
  treated as confirmed partial completion without trace review.
- 217 tasks attempted 435 out-of-schema calls. All 435 were rejected and not
  executed. These tasks passed at 23.50%, compared with 61.50% for tasks without
  an out-of-schema attempt. This is correlation, not a causal estimate, but it
  shows that API prediction/schema coverage is a major difficulty marker.
- 179 tasks produced 987 duplicate calls. Their pass rate was 15.64%, compared
  with 61.34% for tasks without duplicates. Long loops are strongly associated
  with failure.
- 40 tasks produced 128 duplicate write actions, which is a safety and
  idempotency concern even when the benchmark result eventually passes.

## Efficiency

| Group | Mean turns | Mean tool calls | Mean tokens |
| --- | ---: | ---: | ---: |
| Successful tasks | 9.67 | 18.96 | 130,653 |
| Failed tasks | 28.46 | 46.63 | 737,614 |

- Total tokens: 201,973,864
- Input tokens: 195,563,935
- Output tokens: 6,409,929
- Read calls were 87.45% of all executed tool calls.
- Median tokens per task: 136,548; maximum: 7,128,790.
- At CNY 1/M input and CNY 2/M output, estimated cost is CNY 208.38.
- At CNY 3/M input and CNY 6/M output, estimated cost is CNY 625.15.

The main cost problem is failed-task looping, not successful execution.

## Measurement notes

- Eight historical duration values were negative because AppWorld time freezing
  interfered with the previous timer. They are excluded from duration aggregates.
- The runner now uses the Windows monotonic tick counter for future runs.
- The current average duration, 105.52 seconds, is based on 409 valid duration
  values and should be treated as secondary to task and scenario completion.

## Baseline interpretation

This is a valid plain function-calling baseline, not a SafeDesk result. The main
improvement targets for SafeDesk are completion-time verification, recovery from
read loops, API/schema recovery, duplicate-action control, and context/token
management. Improvements should be reported against both task and scenario goal
completion while keeping the same model and benchmark settings.
