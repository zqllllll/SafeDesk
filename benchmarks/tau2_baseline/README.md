# tau2 baseline runner

This folder contains the local orchestration layer for full tau2 baselines. It
does not replace tau2; it prepares task shards, runs them through the
`tau2_deerflow_adapter`, and keeps a manifest so interrupted runs can resume.

## 1. Create a manifest

From `benchmarks\tau2-bench`:

```powershell
$env:Path='C:\Users\86132\Desktop\SafeDesk\deer-flow-main\.tools\uv;' + $env:Path
$env:UV_CACHE_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR='C:\Users\86132\Desktop\SafeDesk\benchmarks\.uv-python'
$env:PYTHONUTF8='1'

uv run python ..\tau2_baseline\make_tau2_manifest.py `
  --run-id tau2_full_flash `
  --domains airline,retail,telecom `
  --shard-size airline=10 `
  --shard-size retail=10 `
  --shard-size telecom=25
```

This creates:

```text
benchmarks/runs/tau2_full_flash/
  config.json
  manifest.csv
  shards/<domain_NNNN>/task_ids.txt
  shards/<domain_NNNN>/config.json
```

## 2. Dry-run the planned commands

```powershell
uv run python ..\tau2_baseline\run_tau2_shards.py `
  ..\runs\tau2_full_flash `
  --dry-run `
  --max-shards 3
```

## 3. Run shards

Run only a small domain first:

```powershell
uv run python ..\tau2_baseline\run_tau2_shards.py `
  ..\runs\tau2_full_flash `
  --domains airline `
  --max-shards 1
```

Resume behavior:

- Completed shards are skipped.
- Shards with missing tasks or infrastructure errors are run again with
  `--auto-resume`.
- `reward=0` is recorded as model failure and is not treated as an infrastructure
  retry condition.

## 4. Aggregate metrics

`extract_tau2_metrics.py` can now accept a manifest, a simulation directory, or
a run directory. For baseline runs, use `--manifest` so old smoke-test results
are not mixed into the baseline.

```powershell
uv run python ..\metrics\extract_tau2_metrics.py `
  --manifest ..\runs\tau2_full_flash\manifest.csv `
  --run-id tau2_full_flash `
  --output-dir ..\results\tau2_full_flash
```

Only existing `result_path` entries are collected, so this command also works
for partial runs.

## Default model roles

```text
agent: deepseek-v4-flash through DeerFlow
user simulator: deepseek/deepseek-v4-flash through tau2/LiteLLM
NL assertion judge: deepseek/deepseek-v4-flash through tau2/LiteLLM
thinking: disabled by default
```
