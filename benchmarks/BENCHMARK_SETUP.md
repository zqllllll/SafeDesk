# Benchmark setup notes

Date: 2026-07-08

This directory keeps benchmark environments separate from DeerFlow and future
SafeDesk code.

## tau2-bench

Path:

```text
C:\Users\86132\Desktop\SafeDesk\benchmarks\tau2-bench
```

Environment:

```text
Python: 3.12.13 via uv
Install command: uv sync
Core text domains installed: mock, airline, retail, telecom
Voice/knowledge/gym extras: not installed
```

Configuration:

```text
.env contains DEEPSEEK_API_KEY
```

Verified:

```powershell
$env:PYTHONUTF8='1'
uv run tau2 check-data
uv run tau2 run --domain mock --agent-llm deepseek/deepseek-chat --user-llm deepseek/deepseek-chat --num-trials 1 --num-tasks 1 --max-concurrency 1 --save-to smoke_deepseek_chat --log-level INFO
```

Result:

```text
mock/create_task_1 passed
reward: 1.0
output: data/simulations/smoke_deepseek_chat/results.json
```

Small-sample status:

```text
airline, 5 tasks:
  save: data/simulations/airline_smoke_deepseek_chat/results.json
  average reward: 0.8000
  DB match: 4/5

retail, 5 tasks with tau2 default NL assertion judge:
  save: data/simulations/retail_smoke_deepseek_chat/results.json
  result: tasks 0 and 1 passed, tasks 2-4 became infra errors

retail, 5 tasks with NL assertion judge temporarily set to deepseek/deepseek-chat:
  save: data/simulations/retail_smoke_deepseek_chat_nlfix_full5/results.json
  average reward: 0.8000
  DB match: 5/5

telecom, 5 tasks:
  save: data/simulations/telecom_smoke_deepseek_chat/results.json
  average reward: 1.0000
  DB match: 5/5
```

Important model note:

```text
LiteLLM deepseek/deepseek-v4-pro returned reasoning_content but empty content
in the minimal test. For tau2's built-in llm_agent, use deepseek/deepseek-chat
for official smoke runs unless we add a custom response adapter.

For DeerFlow baseline, deepseek-v4-pro remains usable because DeerFlow uses
PatchedChatDeepSeek.
```

Retail NL assertion judge note:

```text
Some retail tasks include RewardType.NL_ASSERTION. tau2 evaluates those with
DEFAULT_LLM_NL_ASSERTIONS in src\tau2\config.py, currently hard-coded to
gpt-4.1-2025-04-14. The CLI --agent-llm and --user-llm flags do not override
this evaluator model.

Without OPENAI_API_KEY, retail tasks that hit NL assertions can fail as
infrastructure_error even when the agent/user models are DeepSeek. A temporary
monkeypatch setting tau2.evaluator.evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS
to deepseek/deepseek-chat removed the infra errors.

For future repeatable local runs, either add a small local config override for
DEFAULT_LLM_NL_ASSERTIONS or run through a wrapper script that patches the NL
assertion judge before importing run_domain.
```

Next tau2 commands:

```powershell
uv run tau2 run --domain airline --agent-llm deepseek/deepseek-chat --user-llm deepseek/deepseek-chat --num-trials 1 --num-tasks 5 --max-concurrency 1 --save-to airline_smoke
uv run tau2 run --domain retail --agent-llm deepseek/deepseek-chat --user-llm deepseek/deepseek-chat --num-trials 1 --num-tasks 5 --max-concurrency 1 --save-to retail_smoke
uv run tau2 run --domain telecom --agent-llm deepseek/deepseek-chat --user-llm deepseek/deepseek-chat --num-trials 1 --num-tasks 5 --max-concurrency 1 --save-to telecom_smoke
```

## tau2 DeerFlow adapter

Path:

```text
C:\Users\86132\Desktop\SafeDesk\benchmarks\tau2_deerflow_adapter
```

Purpose:

```text
Register a tau2 HalfDuplexAgent named deerflow_tau_agent.
tau2 still executes benchmark tools and computes rewards.
The adapter only asks the selected LLM backend for assistant text/tool calls.
```

Implemented backends:

```text
litellm:
  Control backend using tau2's normal LiteLLM generator.

deerflow:
  Uses DeerFlow's create_chat_model() and config.yaml.
  In local low-disk mode it reuses DeerFlow backend .venv site-packages instead
  of duplicating langchain/deerflow dependencies into tau2's .venv.
```

Verified:

```text
mock with litellm backend:
  save: data/simulations/deerflow_tau_mock_litellm/results.json
  reward: 1.0

mock with deerflow backend and model deepseek-v4:
  save: data/simulations/deerflow_tau_mock_deerflow_v4_costfix/results.json
  reward: 1.0

telecom 1 task with deerflow backend and model deepseek-v4:
  save: data/simulations/deerflow_tau_telecom1_deerflow_v4/results.json
  reward: 1.0

telecom 5 tasks with deerflow backend and model deepseek-v4:
  save: data/simulations/deerflow_tau_telecom5_deerflow_v4/results.json
  average reward: 1.0000
  DB match: 5/5

airline 5 tasks with deerflow backend and model deepseek-v4:
  save: data/simulations/deerflow_tau_airline5_deerflow_v4/results.json
  average reward: 1.0000
  DB match: 5/5

retail 5 tasks with deerflow backend, model deepseek-v4, and DeepSeek NL judge:
  save: data/simulations/deerflow_tau_retail5_deerflow_v4/results.json
  average reward: 0.8000
  DB match: 5/5
  failed task: 3 failed NL_ASSERTION by saying/listing 12 t-shirt variants
    instead of clearly answering 10 available t-shirt options.
```

Run command:

```powershell
$env:Path='C:\Users\86132\Desktop\SafeDesk\deer-flow-main\.tools\uv;' + $env:Path
$env:UV_CACHE_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-python'
$env:PYTHONUTF8='1'
uv run python ..\tau2_deerflow_adapter\run_tau2_deerflow.py --domain mock --backend deerflow --model deepseek-v4 --num-tasks 1 --max-concurrency 1 --save-to deerflow_tau_mock_deerflow_v4
```

Known limitation:

```text
Agent cost is currently recorded as 0.0 for DeerFlow backend calls because the
adapter does not yet price LangChain/DeerFlow usage metadata. Reward metrics are
valid; cost metrics are placeholders for adapter runs.
```

## Metrics extraction

Path:

```text
C:\Users\86132\Desktop\SafeDesk\benchmarks\metrics\extract_tau2_metrics.py
```

Purpose:

```text
Convert tau2 results.json files into:
  metrics_summary.csv: one row per task/trial
  run_summary.json: aggregate reward/pass/db/token/duration/failure counts
```

Token scope:

```text
total_tokens is the sum of usage.prompt_tokens + usage.completion_tokens on
messages stored in tau2 simulation trajectories.

Evaluator or judge calls are included only if tau2 stores them as trajectory
messages. The current DeerFlow adapter small-sample token count covers the
agent and user simulator messages in the trajectory.
```

Generated small-sample metrics:

```text
output: C:\Users\86132\Desktop\SafeDesk\benchmarks\results\deerflow_tau_small_15_v4
tasks: 15
average reward: 0.9333
pass rate: 0.9333
DB match rate: 1.0000
infra errors: 0
total tokens: 1,304,790
avg tokens per task: 86,986
failure counts: none=14, nl_assertion_failed=1
```

## AppWorld

Paths:

```text
C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-env
C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root
```

Environment:

```text
Python: 3.11.15 via uv
Package: appworld==0.1.3.post1
Data root: benchmarks\appworld-root
Cache root: benchmarks\appworld-root\.cache
```

Install commands used:

```powershell
uv venv --python 3.11 .venv
uv pip install appworld
$env:APPWORLD_ROOT='C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root'
$env:APPWORLD_CACHE='C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root\.cache'
.\.venv\Scripts\appworld.exe install
.\.venv\Scripts\appworld.exe download data --root 'C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root'
```

Verified:

```text
Data downloaded successfully.
App unit tests: 1553 passed.
Single ground-truth task smoke passed:
  task_id: 82e2fac_1
  success: True
```

Windows limitation:

```text
appworld verify tasks fails on native Windows because AppWorld uses
signal.SIGALRM for timeout control, which Windows does not support.

For official AppWorld full verification and benchmark runs, prefer WSL/Linux.
For adapter smoke on native Windows, construct AppWorld with timeout_seconds=None.
```

Smoke command used:

```powershell
$env:APPWORLD_ROOT='C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root'
$env:APPWORLD_CACHE='C:\Users\86132\Desktop\SafeDesk\benchmarks\appworld-root\.cache'
.\.venv\Scripts\python.exe -c "from appworld import AppWorld, load_task_ids; task_id=load_task_ids('train')[0]; print('task_id', task_id); w=AppWorld(task_id=task_id, experiment_name='smoke', timeout_seconds=None, ground_truth_mode='full'); code=w.task.ground_truth.compiled_solution_code+'\nsolution(apis, requester)'; w.execute(code); tr=w.evaluate(); print('success', tr.success); w.close()"
```

## Adapter plan

Short-term:

```text
1. Run tau2 official smoke for airline, retail, telecom.
2. Write DeerFlowTauAgent as a tau2 HalfDuplexAgent wrapper.
3. Run tau2 mock with DeerFlow instead of built-in llm_agent.
4. Run AppWorld through direct Python API first, then evaluate MCP option.
5. Only after DeerFlow baseline is stable, introduce SafeDesk components.
```

Keep DeerFlow `agents_api.enabled` disabled unless testing custom agent
management specifically. These benchmarks do not require that UI feature.
