# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import submission_archive as archive


class SubmissionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.zip_path = self.root / "app.zip"
        self.output = self.root / "stage"

    def make(self, files):
        with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("THIRD_PARTY_LICENSES/NOTICE.txt", "thank you")
            for name, mode, content in files:
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = mode << 16
                zf.writestr(member, content)

    def test_windows_implicit_directories_and_notices(self):
        self.make([("FagaoSubmission/FagaoSubmission.exe", stat.S_IFREG | 0o755, b"demo"),
                   ("FagaoSubmission/resources/app.asar", stat.S_IFREG | 0o644, b"data")])
        result = archive.extract_component_archive(self.zip_path, self.output, "win32")
        self.assertEqual((result / "resources/app.asar").read_bytes(), b"data")
        self.assertEqual((self.output / "THIRD_PARTY_LICENSES/NOTICE.txt").read_text(), "thank you")
        with self.assertRaises(ValueError):
            archive.extract_component_archive(self.zip_path, self.output, "win32")
        self.assertEqual((result / "FagaoSubmission.exe").read_bytes(), b"demo")

    @unittest.skipIf(os.name == "nt", "macOS filesystem test")
    def test_framework_chain_and_executable_mode(self):
        prefix = "FagaoSubmission.app/Contents/Frameworks/Demo.framework/"
        self.make([(prefix + "Demo", stat.S_IFLNK | 0o755, b"Versions/Current/Demo"),
                   (prefix + "Versions/Current", stat.S_IFLNK | 0o755, b"A"),
                   (prefix + "Versions/A/Demo", stat.S_IFREG | 0o755, b"framework")])
        archive.extract_component_archive(self.zip_path, self.output, "darwin")
        executable = self.output / prefix / "Demo"
        self.assertTrue(executable.is_symlink())
        self.assertEqual(executable.read_bytes(), b"framework")
        self.assertEqual(executable.stat().st_mode & 0o111, 0o111)

    def test_nonportable_or_ambiguous_trees_abort_before_publication(self):
        base = "FagaoSubmission"
        for entries in [
                [base + "/resources/App", base + "/Resources/as"], [base + "/a", base + "/a/b"],
                [base + "/..", base + "/file"], [base + "/aux.txt"], ["unlisted/x"],
                [base + "/a:b"], [base + "/a\\b"], [base + "/trailing "]]:
            with self.subTest(entries=entries):
                self.make([(name, stat.S_IFREG | 0o644, b"content") for name in entries])
                with self.assertRaises(ValueError):
                    archive.extract_component_archive(self.zip_path, self.output, "win32")
                self.assertEqual(list(self.output.iterdir()), [])

    def test_truncated_oversized_or_missing_notice(self):
        self.make([("FagaoSubmission/x", stat.S_IFREG | 0o644, b"payload")])
        with patch.object(archive, "MAX_TOTAL", 1), self.assertRaises(ValueError):
            archive.extract_component_archive(self.zip_path, self.output, "win32")
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            zf.writestr("FagaoSubmission/x", b"without notices")
        with self.assertRaises(ValueError):
            archive.extract_component_archive(self.zip_path, self.output, "win32")
        self.zip_path.write_bytes(b"incomplete")
        with self.assertRaises(ValueError):
            archive.extract_component_archive(self.zip_path, self.output, "win32")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_links_forbidden_in_plain_files_and_license_tree(self):
        for system, base, location, dest in [
            ("darwin", "FagaoSubmission.app", "THIRD_PARTY_LICENSES/link", "NOTICE.txt"),
            ("darwin", "FagaoSubmission.app", "FagaoSubmission.app/link", "../outside"),
            ("win32", "FagaoSubmission", "FagaoSubmission/link", "demo"),
        ]:
            with self.subTest(system=system, location=location):
                self.make([(base + "/demo", stat.S_IFREG | 0o644, b"data"),
                           (location, stat.S_IFLNK | 0o755, dest.encode())])
                with self.assertRaises(ValueError):
                    archive.extract_component_archive(self.zip_path, self.output, system)
                self.assertEqual(list(self.output.iterdir()), [])
