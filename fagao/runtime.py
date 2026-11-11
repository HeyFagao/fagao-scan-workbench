# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Account-bound application contexts shared by HTTP and WorkBuddy entrypoints.

The installation root is never used as an implicit member's database. Existing
anonymous data stays untouched until a separate, explicit claim is implemented.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .store import AppError, Store, default_data_dir


def resolve_root_directory(value=None):
    return Path(value or default_data_dir()).expanduser().resolve()


@dataclass(frozen=True)
class AccountContext:
    account_id: str
    generation: str
    store: Store
    jobs: object
    require: Callable
    operation: str = 'use'


class WorkbenchRuntime:
    def __init__(self, root_dir=None, membership=None, *, recover_jobs=False):
        self.root_dir = resolve_root_directory(root_dir)
        if membership is None:
            from .membership import MembershipClient
            membership = MembershipClient(self.root_dir)
        self.membership = membership
        self._lock = threading.RLock()
        self._contexts = {}
        self._recover_jobs = recover_jobs
        self._recovered_accounts = set()

    def capture(self, operation='use', expected_account=None, expected_generation=None):
        """Capture a fixed identity; workers retain it across later account changes."""
        status = self.membership.require(account_id=expected_account, operation=operation, generation=expected_generation)
        account_id = status.get('account_id') or (status.get('account') or {}).get('id')
        generation = status.get('generation')
        if not isinstance(account_id, str) or not account_id or not isinstance(generation, str) or not generation:
            raise AppError('membership_verification_unavailable', '会员身份校验未完成，请重新连接账号。', 503)
        key = (account_id, generation)
        needs_legacy_decision = operation not in ('read', 'history', 'claim_legacy', 'legacy_status', 'skip_legacy')
        def legacy_ready():
            from .legacy_claim import require_legacy_decision
            return require_legacy_decision(self, account_id, generation)
        if needs_legacy_decision:
            legacy_ready()
        def account_directory():
            directory = self.membership.account_directory(account_id)
            if any((directory / name).is_symlink() for name in
                   ('workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm')):
                raise AppError('membership_storage_error', '账号资料路径无效，未打开数据库。', 503)
            return directory
        def require_operation():
            status = self.membership.require(account_id=account_id, generation=generation, operation=operation)
            account_directory()
            if needs_legacy_decision:
                legacy_ready()
            return status
        with self._lock:
            if key not in self._contexts:
                from .server import JobManager
                def require():
                    status = self.membership.require(account_id=account_id, generation=generation, operation='use')
                    account_directory()
                    legacy_ready()
                    return status
                require_operation()
                store = Store(account_directory())
                # A short-lived CLI must never mark the live HTTP worker's jobs
                # interrupted. Only the process owning the HTTP scheduler does
                # startup recovery, once per account database.
                if self._recover_jobs and account_id not in self._recovered_accounts:
                    store.recover_jobs()
                    self._recovered_accounts.add(account_id)
                jobs = JobManager(store, require_access=require)
                self._contexts[key] = AccountContext(account_id, generation, store, jobs, require)
            context = self._contexts[key]
            require_operation()
            return AccountContext(account_id, generation, context.store, context.jobs, require_operation, operation)

    def validate_identity(self, account_id, generation):
        status = self.membership.status()
        current = status.get('account_id') or (status.get('account') or {}).get('id')
        if not account_id or not generation or current != account_id or status.get('generation') != generation:
            raise AppError('membership_account_changed', '账号已切换，请刷新工作台后重试。', 403)
        return status

    def cancellation_context(self, account_id, generation):
        """An expired member may stop their own existing jobs, without reading data."""
        status = self.validate_identity(account_id, generation)
        key = (account_id, status.get('generation'))
        with self._lock:
            context = self._contexts.get(key)
        if context is None:
            raise AppError('job_not_found', '当前账号没有可取消的运行任务。', 404)
        return context

    def stop_all_jobs(self):
        """Stop new work, while allowing already obtained partials to be saved."""
        with self._lock:
            contexts = list(self._contexts.values())
        for context in contexts:
            with context.jobs.lock:
                ids = list(context.jobs.active.values())
            for job_id in ids:
                context.jobs.cancel(job_id)

    def stop_account_jobs(self, account_id, generation):
        """A completed logout may only cancel jobs from its captured identity."""
        with self._lock:
            context = self._contexts.get((account_id, generation))
        if context is None:
            return
        with context.jobs.lock:
            ids = list(context.jobs.active.values())
        for job_id in ids:
            context.jobs.cancel(job_id)
