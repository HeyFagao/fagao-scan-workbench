# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Synthetic website identity for isolated application integration tests only."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from fagao.store import AppError


class SyntheticMembership:
    def __init__(self, root):
        self.root = Path(root)
        self.account = 'member-a'
        self.generation = 'session-a'
        self.allowed = True
        self.reason = 'active'

    def status(self, refresh=False):
        current = datetime.now(timezone.utc)
        return {'allowed': self.allowed, 'reason': self.reason,
                'account': {'id': self.account, 'display_name': '隔离测试会员'},
                'can_read_history': self.allowed or self.reason == 'expired',
                'access_mode': 'active' if self.allowed else 'read_only' if self.reason == 'expired' else 'locked',
                'generation': self.generation, 'policy_version': 'synthetic-test-only',
                'server_time': current.isoformat(),
                'membership_expires_at': (current + timedelta(days=1)).isoformat(),
                'permit_expires_at': (current + timedelta(minutes=15)).isoformat(),
                'identity_expires_at': (current + timedelta(minutes=15)).isoformat()}

    def require(self, account_id=None, operation='use', generation=None):
        if ((not self.allowed and not (self.reason == 'expired' and operation in ('read','history'))) or account_id not in (None, self.account)
                or generation not in (None, self.generation)):
            raise AppError('membership_' + (self.reason if not self.allowed else 'account_changed'),
                           '会员状态已变化，请重新连接。', 403)
        return self.status()

    def account_directory(self, account_id):
        return self.root / 'accounts' / hashlib.sha256(account_id.encode()).hexdigest()

    def logout(self):
        self.allowed = False
        self.reason = 'not_authenticated'
        self.generation = 'logged-out'
        return self.status()
