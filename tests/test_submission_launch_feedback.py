# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Expose safe process errors to the person who clicked Open, including empty launches."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fagao import submission_component as component
from fagao.store import AppError


class LaunchFeedbackTests(unittest.TestCase):
    def run_failure(self, popen, timeout=60):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(component, '_verified_component', return_value=root/'editor.exe'), \
                    patch.object(component, '_platform', return_value='win32'), \
                    patch.object(component.subprocess, 'Popen', popen), \
                    patch.object(component, 'HANDOFF_ACK_TIMEOUT', timeout), \
                    self.assertRaises(AppError) as error:
                component._open_component_locked(root, component._encode_handoff('system', None))
            self.assertEqual(list((root/'submission-handoff').iterdir()), [])
            return error.exception

    def test_windows_policy_and_missing_dependency_are_distinct(self):
        denied = OSError('private test filename')
        denied.winerror = 1260
        error = self.run_failure(Mock(side_effect=denied))
        self.assertEqual(error.code, 'submission_launch_blocked')
        self.assertIn('1260', error.message)
        self.assertNotIn('private test filename', error.message)
        missing = self.run_failure(Mock(return_value=Mock(poll=lambda: 0xc0000135)))
        self.assertEqual(missing.code, 'submission_component_unavailable')
        self.assertIn('0xC0000135', missing.message)

    def test_existing_instance_timeout_does_not_claim_draft_or_crash(self):
        timed_out = self.run_failure(Mock(return_value=Mock(poll=lambda: 0)), timeout=0)
        self.assertIn('超时', timed_out.message)
        self.assertNotIn('草稿', timed_out.message)
