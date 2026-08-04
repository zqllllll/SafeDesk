# AppWorld Qwen3-14B Baseline Runner

This runner follows AppWorld's simplified function-calling baseline shape:

- the same model predicts up to 20 task APIs before the agent starts;
- the official API Predictor prompt shape is used, API Docs are excluded from prediction,
  and `supervisor.complete_task` is always included;
- selected schemas stay fixed for the task;
- the official function-calling instructions and tutorial messages are used;
- parallel tool calls are accepted and all calls are executed serially;
- calls outside the selected schema receive a non-executed tool result and never reach AppWorld;
- the task stops at `supervisor__complete_task` or 50 model turns;
- predictor and agent tokens are recorded separately and included in total tokens.
- Qwen thinking is hard-disabled for all current SafeDesk benchmark runs.

Credentials are loaded from `.env`; use `.env.example` as the template. The real `.env` is ignored.

Smoke test:

```powershell
.\benchmarks\appworld-env\.venv\Scripts\python.exe `
  benchmarks\appworld_tool_adapter\run_appworld_function_calling.py `
  --dataset train --num-tasks 5 `
  --experiment-name appworld_qwen3_14b_baseline_train_smoke `
  --output-dir benchmarks\results\appworld_qwen3_14b_baseline_train_smoke
```

For full runs, use distinct output directories for `test_normal` and `test_challenge`, plus `--resume` after an interruption.

Official prompt sources:

- https://github.com/StonyBrookNLP/appworld/blob/main/experiments/prompts/api_predictor.txt
- https://github.com/StonyBrookNLP/appworld/blob/main/experiments/prompts/function_calling_agent/instructions.txt
- https://github.com/StonyBrookNLP/appworld/blob/main/experiments/prompts/function_calling_agent/demos.json
