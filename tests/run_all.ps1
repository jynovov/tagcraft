#!/usr/bin/env pwsh
<#
.SYNOPSIS
    一条命令跑完 TagCraft 的全部测试套件。

.DESCRIPTION
    26 套件 + 1 套大模型重试测试 = 27 套。默认按"快的先跑"的顺序：
    纯单元 → 前端 DOM → 起真服务的链路 → 端到端。
    任何一套失败都会在最后汇总里标出来，退出码非 0。

.PARAMETER SkipE2E
    跳过两套端到端（progress / krea），它们最慢。

.PARAMETER Only
    只跑名字里包含这些关键字的套件，例如 -Only ui_test,frame

.EXAMPLE
    ./tests/run_all.ps1
    ./tests/run_all.ps1 -SkipE2E
    ./tests/run_all.ps1 -Only frame,rule_reflect
#>
param(
    [switch]$SkipE2E,
    [string[]]$Only = @()
)

$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root

# Python / Node：优先用环境变量，其次用项目里约定的绝对路径
$Python = $env:TAGCRAFT_PYTHON
if (-not $Python) { $Python = "C:\Users\Lenovo\AppData\Local\Programs\Python\Python314\python.exe" }
if (-not (Test-Path $Python)) { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
$Node = $env:TAGCRAFT_NODE
if (-not $Node) { $Node = "C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" }
if (-not (Test-Path $Node)) { $Node = (Get-Command node -ErrorAction SilentlyContinue).Source }

if (-not $Python) { Write-Host "找不到 python，可用 `$env:TAGCRAFT_PYTHON 指定" -ForegroundColor Red; exit 2 }
if (-not $Node) { Write-Host "找不到 node，可用 `$env:TAGCRAFT_NODE 指定" -ForegroundColor Red; exit 2 }

Write-Host ("python: " + $Python)
Write-Host ("node:   " + $Node)

$pyTests = @(
    "frame_test.py", "fallback_compress_test.py", "rule_reflect_test.py", "order_test.py",
    "whitelist_test.py", "cache_test.py", "lc_tools_test.py", "llm_retry_test.py",
    "smoke_test.py", "agent_test.py", "krea_test.py", "concept_test.py",
    "concept_confirm_test.py", "local_concept_test.py", "merge_concept_test.py",
    "plan_test.py", "plan_prefetch_test.py", "prefetch_trace_test.py",
    "job_store_test.py", "comfy_test.py"
)
$jsTests = @("ui_test.js", "ui_krea_test.js", "ui_plan_test.js", "ui_favorites_test.js", "ui_job_test.js")
$e2eTests = @("progress_e2e_test.js", "krea_e2e_test.js")

function Test-Selected([string]$name) {
    if (-not $Only -or $Only.Count -eq 0) { return $true }
    foreach ($k in $Only) { if ($name -like "*$k*") { return $true } }
    return $false
}

$results = @()
$sw = [System.Diagnostics.Stopwatch]::StartNew()

function Invoke-Suite([string]$label, [string]$exe, [string[]]$exeArgs, [string]$file) {
    if (-not (Test-Selected $file)) { return }
    Write-Host ("`n=== " + $label + " ===") -ForegroundColor Cyan
    $t = Get-Date
    & $exe @exeArgs $file 2>&1 | ForEach-Object { $_ }
    $code = $LASTEXITCODE
    $secs = [int]((Get-Date) - $t).TotalSeconds
    $script:results += [pscustomobject]@{ Suite = $label; Exit = $code; Seconds = $secs }
}

foreach ($s in $pyTests) { Invoke-Suite $s $Python @("-X", "utf8") (Join-Path "tests" $s) }
foreach ($s in $jsTests) { Invoke-Suite $s $Node @() (Join-Path "tests" $s) }
if (-not $SkipE2E) {
    # 端到端要真起服务，python 路径通过环境变量传下去
    $env:PY = $Python
    foreach ($s in $e2eTests) { Invoke-Suite $s $Node @() (Join-Path "tests" $s) }
}

$sw.Stop()
Write-Host "`n================ 汇总 ================" -ForegroundColor Yellow
$results | ForEach-Object {
    $mark = if ($_.Exit -eq 0) { "PASS" } else { "FAIL" }
    $color = if ($_.Exit -eq 0) { "Green" } else { "Red" }
    Write-Host ("  {0,-34} {1}  {2,4}s" -f $_.Suite, $mark, $_.Seconds) -ForegroundColor $color
}
$bad = @($results | Where-Object { $_.Exit -ne 0 })
Write-Host ("`n共 {0} 套，失败 {1} 套，用时 {2} 秒" -f $results.Count, $bad.Count, [int]$sw.Elapsed.TotalSeconds)
if ($bad.Count -gt 0) { exit 1 } else { exit 0 }
