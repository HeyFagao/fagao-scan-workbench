# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
# Offline fixtures exercise the same installer used for extracted private components.
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "install_submission_component", Path(__file__).resolve().parents[1] / "scripts/install_submission_component.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SubmissionInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "data"
        self.source = self.directory / "portable"
        (self.source / "resources").mkdir(parents=True)
        (self.source / "FagaoSubmission.exe").write_bytes(b"test program")
        (self.source / "resources/app.asar").write_bytes(b"test app")
        self.dll = self.source / "runtime.dll"
        self.dll.write_bytes(b"first library")
        self.pins = [installer.fingerprint(self.source, "win32", "x64")]
        self.target = self.root / "components/submission"

    def install(self):
        return installer.install(self.source, self.root, "win32", "x64", self.pins)

    def test_file_outside_receipt_changes_block_first_or_repeat_install(self):
        self.dll.write_bytes(b"unlisted content")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(self.root.exists())
        self.dll.write_bytes(b"first library")
        self.assertEqual(self.install(), "installed")
        self.assertEqual(self.install(), "already_installed")
        (self.target / "FagaoSubmission/runtime.dll").write_bytes(b"unlisted content")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual((self.target / "FagaoSubmission/runtime.dll").read_bytes(), b"unlisted content")

    def test_install_changed_non_receipt_file_updates_tree_and_preserves_user_data(self):
        self.install()
        self.dll.write_bytes(b"next library")
        self.pins.append(installer.fingerprint(self.source, "win32", "x64"))
        personal = self.root / "submission-native-profile"
        personal.mkdir()
        (personal / "draft").write_bytes(b"draft")
        self.assertEqual(self.install(), "installed")
        self.assertEqual((self.target / "FagaoSubmission/runtime.dll").read_bytes(), self.dll.read_bytes())
        self.assertEqual((personal / "draft").read_bytes(), b"draft")

    def test_unrelated_directory_or_malformed_receipt_kept(self):
        self.target.mkdir(parents=True)
        (self.target / "existing-notes").write_bytes(b"keep")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual((self.target / "existing-notes").read_bytes(), b"keep")
        (self.target / "existing-notes").unlink()
        self.target.rmdir()
        self.install()
        receipt = self.target / "receipt.json"
        receipt.write_text(json.dumps({"edition": "studio"}))
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(json.loads(receipt.read_text()), {"edition": "studio"})

    def test_failed_activation_restores_previous_version_and_removes_staging(self):
        self.install()
        self.dll.write_bytes(b"second version")
        self.pins.append(installer.fingerprint(self.source, "win32", "x64"))
        rename = os.replace
        def fail_stage(old, new):
            if new == self.target and old.name.startswith(".submission-stage-"):
                raise OSError("simulated locked destination")
            rename(old, new)
        with patch.object(installer.os, "replace", side_effect=fail_stage), self.assertRaises(OSError):
            self.install()
        self.assertEqual((self.target / "FagaoSubmission/runtime.dll").read_bytes(), b"first library")
        self.assertFalse((self.target.parent / ".submission-previous").exists())
        self.assertFalse(list(self.target.parent.glob(".submission-stage-*")))

    @unittest.skipIf(os.name == "nt", "test creates Unix framework links")
    def test_accepts_internal_mac_links_and_disallows_external_windows_and_broken_links(self):
        alias = self.source / "framework-link"
        alias.symlink_to("runtime.dll")
        self.assertRegex(installer.tree_sha256(self.source, "darwin"), r"^[0-9a-f]{64}$")
        with self.assertRaises(installer.InstallError):
            installer.tree_sha256(self.source, "win32")
        alias.unlink()
        alias.symlink_to("../outside")
        (self.directory / "outside").write_bytes(b"private")
        with self.assertRaises(installer.InstallError):
            installer.tree_sha256(self.source, "darwin")
        (self.directory / "outside").unlink()
        with self.assertRaises(installer.InstallError):
            installer.tree_sha256(self.source, "darwin")

    def test_refuses_studio_bundle_id_before_installation(self):
        app = self.directory / "wrong.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(installer.plistlib.dumps(
            {"CFBundleIdentifier": "test.studio", "CFBundleExecutable": "FagaoSubmission"}))
        with self.assertRaises(installer.InstallError):
            installer.verify_macos(app)

    def test_recovers_interrupted_replacement_of_known_component(self):
        self.install()
        previous = self.target.parent / ".submission-previous"
        self.target.rename(previous)
        self.assertEqual(self.install(), "already_installed")
        self.assertTrue(self.target.is_dir())
        self.assertFalse(previous.exists())


    def test_superseded_build_can_be_upgraded_but_cannot_be_selected_for_install(self):
        self.install()
        old = self.pins.copy()
        self.dll.write_bytes(b"approved replacement")
        new = [installer.fingerprint(self.source, "win32", "x64")]
        self.assertEqual(installer.install(self.source, self.root, "win32", "x64", new, old), "installed")
        self.dll.write_bytes(b"first library")
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.root, "win32", "x64", new, old)
