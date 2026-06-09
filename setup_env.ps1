$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectRoot ".venv314"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host "未找到 Python Launcher py.exe，请确认 Python 3.14 已安装。" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
  py -3.14 -m venv $EnvPath
}

$Python = Join-Path $EnvPath "Scripts\python.exe"
& $Python -m pip install --upgrade pip setuptools wheel
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $Python -c "import sys, PyQt5, numpy, cv2, PIL; print(sys.version); print('PyQt5/NumPy/OpenCV/Pillow ready')"
