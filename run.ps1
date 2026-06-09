$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv314\Scripts\python.exe"
$QtRoot = Join-Path $ProjectRoot ".venv314\Lib\site-packages\PyQt5\Qt5"

if (-not (Test-Path -LiteralPath $Python)) {
  Write-Host "未找到项目环境，请先运行 .\setup_env.ps1" -ForegroundColor Yellow
  exit 1
}

$env:QT_PLUGIN_PATH = Join-Path $QtRoot "plugins"
$env:QT_QPA_PLATFORM_PLUGIN_PATH = Join-Path $QtRoot "plugins\platforms"
$env:PATH = (Join-Path $QtRoot "bin") + ";" + $env:PATH
& $Python (Join-Path $ProjectRoot "main.py")
