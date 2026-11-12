# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from fagao import submission_component as component
from fagao.store import AppError
from scripts import install_submission_component as installer


class SubmissionComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'components' / 'submission'

    def install_fixture(self, platform):
        if platform == 'darwin':
            bundle = self.base / 'FagaoSubmission.app' / 'Contents'
            self.executable = bundle / 'MacOS' / 'FagaoSubmission'
            self.asar = bundle / 'Resources' / 'app.asar'
        else:
            bundle = self.base / 'FagaoSubmission'
            self.executable = bundle / 'FagaoSubmission.exe'
            self.asar = bundle / 'resources' / 'app.asar'
        self.executable.parent.mkdir(parents=True)
        self.asar.parent.mkdir(parents=True)
        self.executable.write_bytes(b'synthetic local executable')
        self.executable.chmod(0o700)
        self.asar.write_bytes(b'synthetic local application')
        if platform == 'win32':
            (bundle / 'ffmpeg.dll').write_bytes(b'synthetic media runtime')
            pin = installer.fingerprint(bundle, 'win32', 'x64')
            patcher = patch.object(installer, 'read_pins', return_value=[pin])
            patcher.start()
            self.addCleanup(patcher.stop)
        if platform == 'darwin':
            (bundle / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': component.BUNDLE_ID, 'CFBundleExecutable': 'FagaoSubmission'}))
        receipt = {'version': 1, 'platform': platform, 'edition': 'personal',
                   'executable_sha256': hashlib.sha256(self.executable.read_bytes()).hexdigest(),
                   'asar_sha256': hashlib.sha256(self.asar.read_bytes()).hexdigest()}
        (self.base / 'receipt.json').write_text(json.dumps(receipt))

    def test_absent_component_reports_unavailable_without_creating_any_files(self):
        with patch.object(component, '_platform', return_value='darwin'):
            self.assertFalse(component.component_status(self.root)['available'])
            with self.assertRaises(AppError) as error:
                component.open_component(self.root, 'dark', None)
        self.assertEqual(error.exception.code, 'submission_component_unavailable')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_incomplete_windows_runtime_cannot_report_ready_or_launch(self):
        self.install_fixture('win32')
        with patch.object(component, '_platform', return_value='win32'), patch.object(subprocess, 'Popen') as launch:
            self.assertTrue(component.component_status(self.root)['available'])
            (self.executable.parent / 'ffmpeg.dll').unlink()
            status = component.component_status(self.root)
            self.assertFalse(status['available'])
            self.assertIn('修复', status['message'])
            with self.assertRaises(AppError):
                component.open_component(self.root, 'system', None)
            launch.assert_not_called()

    def test_unsupported_windows_architecture_identifies_the_platform_even_without_a_component(self):
        with patch.object(component.sys, 'platform', 'win32'), patch.object(installer, 'host_architecture', return_value='arm64'):
            result = component.component_status(self.root)
        self.assertFalse(result['available'])
        self.assertIn('架构暂不支持', result['message'])
        self.assertNotIn('更新', result['message'])

    def test_verified_mac_bundle_uses_nonce_only_private_envelope_and_acknowledgement(self):
        self.install_fixture('darwin')
        draft = {'work_title': '稿甲', 'pen_name': '笔甲', 'subject': '问候',
                 'body': '仅供本机交接的正文', 'id': 'local_id', 'revision': 2}
        def consume(argv, *, env, **kwargs):
            self.assertEqual(Path(argv[0]), self.executable)
            self.assertEqual(len(argv), 2)
            self.assertRegex(argv[1], r'^--fagao-handoff=[a-f0-9]{32}$')
            nonce = argv[1].split('=')[1]
            directory = Path(env['FAGAO_SUBMISSION_HANDOFF_DIR'])
            self.assertEqual(directory, self.root / 'submission-handoff')
            self.assertEqual(Path(env['FAGAO_SUBMISSION_PROFILE_DIR']), self.root / 'submission-native-profile')
            self.assertNotIn('FAGAO_TEST_PRIVATE', env)
            self.assertNotIn('NODE_OPTIONS', env)
            handoff = directory / (nonce + '.json')
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(handoff.stat().st_mode), 0o600)
            self.assertEqual(json.loads(handoff.read_bytes()), {
                'version': 1, 'appearance': 'eye-care', 'draft': {
                    'title': '稿甲', 'pen_name': '笔甲', 'subject': '问候', 'body': draft['body']}})
            handoff.unlink()
            (directory / (nonce + '.accepted')).touch(mode=0o600)
            return Mock()
        with patch.object(component, '_platform', return_value='darwin'), \
             patch.dict(os.environ, {'FAGAO_TEST_PRIVATE': 'test', 'NODE_OPTIONS': '--inspect'}), \
             patch.object(subprocess, 'run') as codesign, patch.object(subprocess, 'Popen', side_effect=consume):
            self.assertEqual(component.open_component(self.root, 'eye-care', draft), {'opened': True})
        self.assertEqual(codesign.call_args.args[0][:4], ['/usr/bin/codesign', '--verify', '--deep', '--strict'])
        self.assertEqual(list((self.root / 'submission-handoff').iterdir()), [])

    def test_windows_replaced_application_or_tampered_receipt_is_rejected(self):
        self.install_fixture('win32')
        with patch.object(component, '_platform', return_value='win32'), patch.object(subprocess, 'Popen') as start:
            self.assertTrue(component.component_status(self.root)['available'])
            original = self.asar.read_bytes()
            self.asar.write_bytes(original + b'.')
            self.assertFalse(component.component_status(self.root)['available'])
            self.asar.write_bytes(original)
            path = self.base / 'receipt.json'
            receipt = json.loads(path.read_text())
            receipt['edition'] = 'studio'
            path.write_text(json.dumps(receipt))
            with self.assertRaises(AppError):
                component.open_component(self.root, 'system', None)
            start.assert_not_called()

    def test_receiver_removing_envelope_without_ack_is_failure_and_cleanup(self):
        self.install_fixture('win32')
        def consume_without_ack(argv, **kwargs):
            nonce = argv[1].split('=')[1]
            (self.root / 'submission-handoff' / (nonce + '.json')).unlink()
            return Mock(poll=Mock(return_value=1))
        with patch.object(component, '_platform', return_value='win32'), \
             patch.object(subprocess, 'Popen', side_effect=consume_without_ack):
            with self.assertRaises(AppError) as error:
                component.open_component(self.root, 'system', None)
        self.assertEqual(error.exception.code, 'submission_handoff_failed')
        self.assertEqual(list((self.root / 'submission-handoff').iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'symlink and POSIX permissions')
    def test_refuses_symlinked_handoff_and_untrusted_bundle(self):
        self.install_fixture('darwin')
        actual = self.root / 'target'
        actual.mkdir(mode=0o700)
        (self.root / 'submission-handoff').symlink_to(actual, target_is_directory=True)
        with patch.object(component, '_platform', return_value='darwin'), \
             patch.object(subprocess, 'run'), patch.object(subprocess, 'Popen') as start:
            with self.assertRaises(AppError):
                component.open_component(self.root, 'light', None)
            self.assertEqual(list(actual.iterdir()), [])
            self.executable.chmod(0o777)
            self.assertFalse(component.component_status(self.root)['available'])
            start.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions')
    def test_rejects_studio_bundle_and_signing_failure(self):
        self.install_fixture('darwin')
        info = self.base / 'FagaoSubmission.app' / 'Contents' / 'Info.plist'
        original = info.read_bytes()
        info.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'studio', 'CFBundleExecutable': 'FagaoSubmission'}))
        with patch.object(component, '_platform', return_value='darwin'), \
             patch.object(subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'codesign')), \
             patch.object(subprocess, 'Popen') as launch:
            self.assertFalse(component.component_status(self.root)['available'])
            info.write_bytes(original)
            with self.assertRaises(AppError):
                component.open_component(self.root, 'system', None)
            launch.assert_not_called()

    def test_bad_input_does_not_reach_native_even_when_component_present(self):
        self.install_fixture('win32')
        normal = dict(work_title='甲', pen_name='', subject='', body='')
        with patch.object(component, '_platform', return_value='win32'), patch.object(subprocess, 'Popen') as start:
            for appearance, draft in (('invalid', None), ('dark', {**normal, 'work_title': '🚀' * 151})):
                with self.subTest(appearance=appearance), self.assertRaises(AppError):
                    component.open_component(self.root, appearance, draft)
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
