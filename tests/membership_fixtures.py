# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
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
