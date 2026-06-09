@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "PYTHON=%PROJECT_ROOT%.venv314\Scripts\python.exe"
set "APP=%PROJECT_ROOT%main.py"
set "QT_PLUGIN_PATH=%PROJECT_ROOT%.venv314\Lib\site-packages\PyQt5\Qt5\plugins"
set "QT_QPA_PLATFORM_PLUGIN_PATH=%PROJECT_ROOT%.venv314\Lib\site-packages\PyQt5\Qt5\plugins\platforms"
set "PATH=%PROJECT_ROOT%.venv314\Lib\site-packages\PyQt5\Qt5\bin;%PATH%"

if not exist "%PYTHON%" (
  echo Project Python environment not found.
  echo Please run setup_env.ps1 first.
  pause
  exit /b 1
)

"%PYTHON%" "%APP%"

if errorlevel 1 (
  echo.
  echo The client exited with an error.
  pause
)
