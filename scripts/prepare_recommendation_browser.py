#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
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
