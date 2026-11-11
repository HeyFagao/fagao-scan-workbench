#!/bin/bash
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
set -u
cd -- "$(dirname -- "$0")" || exit 1
PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "需要 Python 3.10 或更新版本。请把本文件夹交给 WorkBuddy，让它按 README.md 准备运行环境。"
  read -r -p "按回车关闭。" _
  exit 1
fi
"$PYTHON_BIN" scripts/launch.py
result=$?
if [ "$result" -ne 0 ]; then
  read -r -p "未能启动，保留上方提示交给 WorkBuddy。按回车关闭。" _
fi
exit "$result"
