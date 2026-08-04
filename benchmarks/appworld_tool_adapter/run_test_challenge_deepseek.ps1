param(
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $workspace "benchmarks\appworld-env\.venv\Scripts\python.exe"
$runner = Join-Path $workspace "benchmarks\appworld_tool_adapter\run_appworld_function_calling.py"
$experiment = "appworld_deepseek_v4_flash_no_thinking_test_challenge_aligned"
$resultRoot = Join-Path $workspace "benchmarks\results\$experiment"
$processes = @()

for ($shard = 0; $shard -lt 4; $shard++) {
    $shardDir = Join-Path $resultRoot "shards\shard_$shard"
    New-Item -ItemType Directory -Force -Path $shardDir | Out-Null
    $arguments = @(
        $runner,
        "--dataset", "test_challenge",
        "--num-shards", "4",
        "--shard-index", "$shard",
        "--max-tasks", "10000",
        "--experiment-name", $experiment,
        "--model", "deepseek-v4-flash",
        "--max-turns", "50",
        "--parallel-tool-calls",
        "--api-base", "https://api.deepseek.com/v1",
        "--api-key-env", "DEEPSEEK_API_KEY",
        "--output-dir", $shardDir
    )
    if ($Resume) {
        $arguments += "--resume"
    }
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $arguments `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $shardDir "runner.stdout.log") `
        -RedirectStandardError (Join-Path $shardDir "runner.stderr.log") `
        -PassThru
    $processes += [pscustomobject]@{
        shard = $shard
        pid = $process.Id
        selected_tasks = if ($shard -eq 0) { 105 } else { 104 }
        output_dir = $shardDir
    }
}

$processes | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $resultRoot "processes.json")
$processes | Format-Table -AutoSize
