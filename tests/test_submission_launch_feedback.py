# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
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
