# 一键运行全部自测（离线、零 API 成本）
#
# 用法（在项目根目录）：
#     pwsh -File run_tests.ps1
#     powershell -ExecutionPolicy Bypass -File run_tests.ps1
#
# 本脚本只做环境前置检查，套件清单统一收敛到 tests/run_all_tests.py，
# 避免两处清单不同步。失败不中断，最后统一汇总并给出总耗时。

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host ""
Write-Host ("=" * 68)
Write-Host "  文档解析智能体 · 自测启动"
Write-Host ("=" * 68)

# --- 前置检查：解释器 ---
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "[fail] 找不到 python，请先安装 Python 3.10 或更高版本。" -ForegroundColor Red
    exit 1
}
$pyVersion = (& $python --version 2>&1) -join ' '
Write-Host "  解释器：$pyVersion"

# --- 前置检查：离线渲染库 ---
if (-not (Test-Path "assets\vendor\mermaid.min.js")) {
    Write-Host "[warn] 缺少 assets\vendor\mermaid.min.js，正在尝试下载…" -ForegroundColor Yellow
    & $python tools\fetch_mermaid.py
}

# --- 全部套件交给 tests/run_all_tests.py（清单与耗时汇总都在那里） ---
& $python tests\run_all_tests.py
exit $LASTEXITCODE
