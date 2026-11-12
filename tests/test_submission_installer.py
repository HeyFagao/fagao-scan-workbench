# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
# Offline fixtures exercise the same installer used for extracted private components.
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import shutil
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

    def test_unverified_source_rejected_but_recognized_damaged_installation_is_replaced(self):
        self.dll.write_bytes(b"unlisted content")
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(self.root.exists())
        self.dll.write_bytes(b"first library")
        self.assertEqual(self.install(), "installed")
        self.assertEqual(self.install(), "already_installed")
        library = Path("FagaoSubmission/runtime.dll")
        extra = Path("FagaoSubmission/author-note.txt")
        (self.target / library).write_bytes(b"unlisted content")
        (self.target / extra).write_bytes(b"user note")
        self.assertEqual(self.install(), "installed")
        self.assertEqual((self.target / library).read_bytes(), b"first library")
        self.assertFalse((self.target / extra).exists())
        backup, = self.target.parent.glob("submission-repair-backup-*")
        self.assertEqual((backup / library).read_bytes(), b"unlisted content")
        self.assertEqual((backup / extra).read_bytes(), b"user note")
        self.assertEqual(self.install(), "already_installed")
        self.assertEqual(list(self.target.parent.glob("submission-repair-backup-*")), [backup])

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

    def test_failed_repair_restores_the_original_damaged_directory(self):
        self.install()
        library = self.target / "FagaoSubmission/runtime.dll"
        library.unlink()
        note = self.target / "FagaoSubmission/notes.txt"
        note.write_bytes(b"keep")
        rename = os.replace
        def fail_stage(old, new):
            if new == self.target and old.name.startswith(".submission-stage-"):
                raise OSError("simulated locked destination")
            rename(old, new)
        with patch.object(installer.os, "replace", side_effect=fail_stage), self.assertRaises(OSError):
            self.install()
        self.assertEqual(note.read_bytes(), b"keep")
        self.assertFalse(library.exists())
        self.assertFalse(list(self.target.parent.glob(".submission-stage-*")))
        self.assertEqual(self.install(), "installed")
        self.assertTrue(library.is_file())

    def test_missing_receipt_and_unrecognized_archive_do_not_adopt_the_directory(self):
        self.install()
        (self.target / "receipt.json").unlink()
        archive = self.target / "FagaoSubmission/resources/app.asar"
        archive.write_bytes(b"unknown edition")
        with self.assertRaises((OSError, ValueError)):
            self.install()
        self.assertEqual(archive.read_bytes(), b"unknown edition")
        self.assertFalse(list(self.target.parent.glob("submission-repair-backup-*")))

    def test_copied_receipt_alone_cannot_establish_a_managed_installation(self):
        self.install()
        exe = self.target / "FagaoSubmission/FagaoSubmission.exe"
        archive = self.target / "FagaoSubmission/resources/app.asar"
        exe.write_bytes(b"unrelated program")
        archive.write_bytes(b"unrelated application")
        with self.assertRaises((OSError, ValueError)):
            self.install()
        self.assertEqual(exe.read_bytes(), b"unrelated program")
        self.assertEqual(archive.read_bytes(), b"unrelated application")
        self.assertFalse(list(self.target.parent.glob("submission-repair-backup-*")))

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

    def test_open_window_refuses_replacement_and_same_build_stays_reusable(self):
        self.install()
        with patch.object(installer, "require_stopped", side_effect=installer.InstallError("请完整退出")):
            self.assertEqual(self.install(), "already_installed")
            self.dll.write_bytes(b"rebuild")
            self.pins.append(installer.fingerprint(self.source, "win32", "x64"))
            with self.assertRaisesRegex(installer.InstallError, "完整退出"):
                self.install()
        self.assertEqual((self.target / "FagaoSubmission/runtime.dll").read_bytes(), b"first library")
        self.assertEqual(installer.check_managed(self.target, "win32", "x64", self.pins), self.pins[0])

    def test_an_app_started_while_copying_leaves_existing_component_untouched(self):
        self.install()
        self.dll.write_bytes(b"rebuild")
        self.pins.append(installer.fingerprint(self.source, "win32", "x64"))
        with patch.object(installer, "require_stopped",
                          side_effect=[None, installer.InstallError("请完整退出")]):
            with self.assertRaisesRegex(installer.InstallError, "完整退出"):
                self.install()
        self.assertEqual(installer.check_managed(self.target, "win32", "x64", self.pins), self.pins[0])

    @unittest.skipUnless(sys.platform == "darwin", "macOS libproc")
    def test_macos_real_process_in_one_bundle_does_not_pin_another_copy(self):
        first = self.directory / "a/FagaoSubmission.app"
        other = self.directory / "b/FagaoSubmission.app"
        for app in (first, other):
            executable = app / "Contents/MacOS/FagaoSubmission"
            executable.parent.mkdir(parents=True)
            shutil.copyfile("/bin/sleep", executable)
            executable.chmod(0o700)
        # This file represents a stale PID hint. The decision uses the OS instead.
        (first / "stale.pid").write_text("1")
        self.assertFalse(installer.mac_component_in_use(first))
        with subprocess.Popen([str(first / "Contents/MacOS/FagaoSubmission"), "1"]) as process:
            self.assertTrue(installer.mac_component_in_use(first))
            self.assertFalse(installer.mac_component_in_use(other))
            with self.assertRaisesRegex(installer.InstallError, "完整退出"):
                installer.require_stopped(first, "darwin")
            process.wait(timeout=5)
        self.assertFalse(installer.mac_component_in_use(first))

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS signing and live PID lookup")
    def test_running_signed_app_is_retained_identically_until_normal_exit(self):
        from fagao import submission_component
        source = self.directory / "signed/FagaoSubmission.app"
        contents = source / "Contents"
        executable = contents / "MacOS/FagaoSubmission"
        archive = contents / "Resources/app.asar"
        executable.parent.mkdir(parents=True)
        archive.parent.mkdir(parents=True)
        shutil.copyfile("/bin/cat", executable)
        executable.chmod(0o700)
        (contents / "Info.plist").write_bytes(installer.plistlib.dumps({
            "CFBundlePackageType": "APPL", "CFBundleIdentifier": installer.BUNDLE_ID,
            "CFBundleVersion": "1", "CFBundleExecutable": "FagaoSubmission",
        }))
        def build(version):
            archive.write_text(version)
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(source)],
                           capture_output=True, check=True)
            return installer.fingerprint(source, "darwin", installer.host_architecture())
        old = build("old archive")
        def apply_build(pin, prior=()):
            return installer.install(source, self.root, "darwin", installer.host_architecture(), [pin], prior)
        self.assertEqual(apply_build(old), "installed")
        target = self.target / source.name
        with subprocess.Popen([str(target / executable.relative_to(source))], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL) as child:
            try:
                self.assertEqual(apply_build(old), "already_installed")
                new = build("new archive")
                with self.assertRaisesRegex(installer.InstallError, "完整退出"):
                    apply_build(new, [old])
                self.assertEqual(installer.tree_sha256(target, "darwin"), old["tree_sha256"])
                receipt = json.loads((self.target / "receipt.json").read_text())
                self.assertEqual(receipt, installer.receipt_for(old))
                self.assertIsNone(child.poll())
            finally:
                child.stdin.close()  # normal EOF, as opposed to signalling the process
                child.wait(timeout=5)
        self.assertEqual(apply_build(new, [old]), "installed")
        self.assertEqual(installer.tree_sha256(target, "darwin"), new["tree_sha256"])
        # Another browser request must wait for installation to finish before launch.
        with installer.component_lock(self.target.parent), patch.object(subprocess, "Popen") as launch:
            with self.assertRaises(submission_component.AppError):
                submission_component.open_component(self.root, "light", None)
            launch.assert_not_called()
