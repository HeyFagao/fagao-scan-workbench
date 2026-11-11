# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
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
