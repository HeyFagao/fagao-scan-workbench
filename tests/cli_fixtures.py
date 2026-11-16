# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Explicit trusted Store injection for pre-membership business unit tests.

This helper is never shipped as a runtime authentication option. The official
CLI's unauthenticated process and account isolation have separate tests.
"""
import contextlib
import io
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace


def invoke_store_cli(store, arguments):
    from fagao.__main__ import main
    context = SimpleNamespace(store=store, require=lambda: None)
    runtime = SimpleNamespace(root_dir=Path(store.directory), capture=lambda **kwargs: context)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(arguments, runtime_factory=lambda directory: runtime)
    return CompletedProcess(arguments, code, output.getvalue(), '')
