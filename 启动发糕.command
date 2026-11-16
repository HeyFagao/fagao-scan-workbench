#!/bin/bash
# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
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
