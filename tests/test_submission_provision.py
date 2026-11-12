# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts import provision_submission as provision


class SubmissionProvisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'workbench'
        source = self.root / 'FagaoSubmission'
        (source / 'resources').mkdir(parents=True)
        (source / 'FagaoSubmission.exe').write_bytes(b'isolated program')
        (source / 'resources/app.asar').write_bytes(b'synthetic application')
        (source / 'ffmpeg.dll').write_bytes(b'isolated runtime')
        self.archive = self.root / 'release.zip'
        with ZipFile(self.archive, 'w') as z:
            for path in source.rglob('*'):
                if path.is_file():
                    z.write(path, path.relative_to(self.root).as_posix())
            z.writestr('THIRD_PARTY_LICENSES/NOTICE.txt', 'test')
        self.pin = provision.component.fingerprint(source, 'win32', 'x64')
        self.pin.update(archive_url='https://github.com/ShiQi-Han03/fagao-scan-workbench/releases/download/fixture/component.zip',
                        archive_sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                        size_bytes=self.archive.stat().st_size)
        manifest = self.root / 'pins.json'
        manifest.write_text(json.dumps({'version': 1, 'builds': [self.pin]}))
        patcher = patch.object(provision.component, 'PINS', manifest)
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, **kwargs):
        return provision.provision(self.data, system='win32', arch='x64', **kwargs)

    def test_downloaded_component_and_offline_reinstallation(self):
        def download(url, path, checksum, size):
            self.assertEqual((url, checksum, size),
                             (self.pin['archive_url'], self.pin['archive_sha256'], self.pin['size_bytes']))
            path.write_bytes(self.archive.read_bytes())
        with patch.object(provision, 'download_component', side_effect=download) as fetch:
            self.assertEqual(self.install()['status'], 'installed')
            self.assertEqual(self.install()['status'], 'already_installed')
            self.assertEqual(fetch.call_count, 1)
        self.assertTrue((self.data / 'components/submission/receipt.json').is_file())
        self.assertFalse(list((self.data / 'components').glob('.submission-download-*')))

    def test_normal_update_repairs_missing_files_and_keeps_user_data(self):
        for missing in ('FagaoSubmission/ffmpeg.dll', 'FagaoSubmission/FagaoSubmission.exe',
                        'FagaoSubmission/resources/app.asar', 'receipt.json'):
            with self.subTest(missing=missing):
                self.data = self.root / ('workbench-' + Path(missing).name)
                self.install(archive=self.archive)
                component_dir = self.data / 'components/submission'
                drafts = self.data / 'local-submissions'
                drafts.mkdir()
                draft = drafts / 'draft'
                draft.write_bytes(b'own work')
                (component_dir / missing).unlink()
                with patch.object(provision, 'download_component',
                                  side_effect=lambda url, path, sha, size: path.write_bytes(self.archive.read_bytes())) as fetch:
                    result = self.install()
                fetch.assert_called_once()
                self.assertEqual(result['status'], 'installed')
                self.assertEqual(provision.component.check_managed(component_dir, 'win32', 'x64', [self.pin])['tree_sha256'],
                                 self.pin['tree_sha256'])
                self.assertEqual(draft.read_bytes(), b'own work')

    def test_local_archive_mismatch_does_not_switch_and_creates_no_residue(self):
        contents = bytearray(self.archive.read_bytes())
        contents[-1] ^= 1
        self.archive.write_bytes(contents)
        with patch.object(provision, 'download_component') as download, self.assertRaises(ValueError):
            self.install(archive=self.archive)
        download.assert_not_called()
        self.assertEqual(list((self.data / 'components').iterdir()), [])

    def test_only_supported_targets_require_a_native_binary(self):
        self.assertIsNone(provision.selected_build([self.pin], 'linux', 'x64'))
        with self.assertRaises(ValueError):
            provision.selected_build([], 'win32', 'x64')
        with self.assertRaises(ValueError):
            provision.selected_build([self.pin,self.pin], 'win32', 'x64')
        with self.assertRaises(ValueError):
            provision.selected_build([dict(self.pin,size_bytes=True)], 'win32', 'x64')
