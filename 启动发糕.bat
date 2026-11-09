@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0scripts\launch.py"
  goto :result
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  python "%~dp0scripts\launch.py"
  goto :result
)
echo 需要 Python 3.10 或更新版本。请把本文件夹交给 WorkBuddy，让它按 README.md 准备运行环境。
pause
exit /b 1
:result
if errorlevel 1 (
  echo 未能启动，请保留上方提示交给 WorkBuddy。
  pause
  exit /b 1
)
exit /b 0
