#!/usr/bin/env python3
# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Prepare the optional public recommendation browser before opening the workbench."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fagao import network
from fagao.collectors import CollectorError
from fagao.recommendation_runtime import PLAYWRIGHT_VERSION, ensure_runtime, tools_directory
from fagao.store import default_data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--retry', action='store_true', help='网络修复后重新准备浏览器环境')
    args = parser.parse_args()
    network.configure(args.data_dir)
    if args.retry:
        (tools_directory(args.data_dir) / 'last-failure.json').unlink(missing_ok=True)
    try:
        ensure_runtime(args.data_dir)
    except CollectorError as exc:
        print(json.dumps({'status': 'setup_required', 'code': exc.state, 'message': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': 'ready', 'playwright_version': PLAYWRIGHT_VERSION}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
