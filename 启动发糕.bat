@echo off
chcp 65001 >nul
rem 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
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
