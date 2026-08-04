# tau2 DeerFlow adapter

This adapter registers a tau2 `HalfDuplexAgent` named `deerflow_tau_agent`.
tau2 still owns the benchmark environment and executes all tools. The adapter
only converts tau2 messages/tools into an LLM call and converts the assistant
response back into tau2 `AssistantMessage` / `ToolCall` objects.

`Tau2PolicyAdapter` is independently importable without loading the tau2 runtime. It converts only explicitly reviewed telecom, airline, or retail policy mappings into deterministic AgentGate `PolicyRule` objects; natural-language policy text is never auto-enforced.

Smoke command from `benchmarks\tau2-bench`:

```powershell
$env:Path='C:\Users\86132\Desktop\SafeDesk\deer-flow-main\.tools\uv;' + $env:Path
$env:UV_CACHE_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-python'
$env:PYTHONUTF8='1'
uv run python ..\tau2_deerflow_adapter\run_tau2_deerflow.py --domain mock --backend deerflow --num-tasks 1 --max-concurrency 1 --save-to deerflow_tau_mock_flash
```

Use `--backend deerflow --model deepseek-v4-flash` for the DeerFlow model
factory. The runner disables model thinking by default to control token cost;
pass `--thinking` only for explicit reasoning-mode experiments. In local
low-disk mode the runner reuses DeerFlow's existing backend `.venv`
site-packages by adding them to `sys.path`, avoiding a duplicate install in the
tau2 environment.

Default LLM roles:

```text
agent: deepseek-v4-flash through DeerFlow
user simulator: deepseek/deepseek-v4-flash through tau2/LiteLLM
NL assertion judge: deepseek/deepseek-v4-flash through tau2/LiteLLM
```

Verified runs:

```text
mock / litellm / deepseek-chat:
  data/simulations/deerflow_tau_mock_litellm/results.json
  reward: 1.0

mock / deerflow / deepseek-v4:
  data/simulations/deerflow_tau_mock_deerflow_v4_costfix/results.json
  reward: 1.0

telecom 1 task / deerflow / deepseek-v4:
  data/simulations/deerflow_tau_telecom1_deerflow_v4/results.json
  reward: 1.0

telecom 5 tasks / deerflow / deepseek-v4:
  data/simulations/deerflow_tau_telecom5_deerflow_v4/results.json
  average reward: 1.0000

airline 5 tasks / deerflow / deepseek-v4:
  data/simulations/deerflow_tau_airline5_deerflow_v4/results.json
  average reward: 1.0000

retail 5 tasks / deerflow / deepseek-v4:
  data/simulations/deerflow_tau_retail5_deerflow_v4/results.json
  average reward: 0.8000
```

Retail note: the runner patches tau2's NL assertion judge to
`deepseek/deepseek-v4-flash` by default, because tau2's built-in default is an
OpenAI model and `--agent-llm` does not override that evaluator.

Cost note: DeerFlow backend calls currently record agent cost as `0.0`. Reward
and DB/action metrics are valid; cost reporting needs a later pricing mapper for
LangChain/DeerFlow usage metadata.
