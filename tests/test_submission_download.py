# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import hashlib
import io
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import submission_download as downloader


REPOSITORY = "https://github.com/ShiQi-Han03/fagao-scan-workbench"
URL = REPOSITORY + "/releases/download/submission-v1/FagaoSubmission-win32-x64.zip"
DATA = b"synthetic submission component for installer verification"
DIGEST = hashlib.sha256(DATA).hexdigest()


class SubmissionDownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.destination = self.directory / "component.zip"

    def download(self, url=URL, digest=DIGEST, size=len(DATA)):
        return downloader.download_component(url, self.destination, digest, size)

    def test_installs_only_the_complete_pinned_bytes(self):
        with patch.object(downloader, "_python_download",
                          side_effect=lambda url, path, size: path.write_bytes(DATA)):
            self.download()
        self.assertEqual(self.destination.read_bytes(), DATA)
        self.assertEqual(set(self.directory.iterdir()), {self.destination})

    def test_partial_or_substituted_transport_output_is_removed(self):
        for received in (DATA[1:], DATA + b"Z", b"x" * len(DATA)):
            with self.subTest(received=received), patch.object(
                    downloader, "_python_download",
                    side_effect=lambda url, path, size: path.write_bytes(received)):
                with self.assertRaises(downloader.ComponentDownloadError):
                    self.download()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_failed_download_leaves_preexisting_file_intact(self):
        self.destination.write_bytes(b"previous component")
        with patch.object(downloader, "_python_download", side_effect=URLError("offline")):
            with self.assertRaises(downloader.ComponentDownloadError):
                self.download()
        self.assertEqual(self.destination.read_bytes(), b"previous component")
        self.assertEqual(set(self.directory.iterdir()), {self.destination})

    def test_rejects_invalid_pins_before_network_access(self):
        with patch.object(downloader, "_python_download") as transport:
            for digest, size in [("", len(DATA)), ("0" * 63, len(DATA)),
                                 ("z" * 64, len(DATA)), (DIGEST, -1), (DIGEST, 0)]:
                with self.subTest(digest=digest, size=size), self.assertRaises(
                        downloader.ComponentDownloadError):
                    self.download(digest=digest, size=size)
            transport.assert_not_called()

    def test_only_official_release_paths_and_tls_asset_redirects(self):
        downloader._validate_source(URL)
        for url in [URL.replace("https:", "http:"), URL.replace("github.com/", "github.com.evil.test/"),
                    URL.replace("ShiQi-Han03", "other-org"), URL.replace("github.com", "user@github.com"),
                    URL.replace("github.com", "github.com:8443"),
                    URL.replace("/releases/download/", "/releases/latest/download/")]:
            with self.subTest(url=url), self.assertRaises(downloader.ComponentDownloadError):
                downloader._validate_source(url)

        for host in ["release-assets.githubusercontent.com", "objects.githubusercontent.com"]:
            downloader._validate_redirect(f"https://{host}/github-production-release-asset/123?sig=fake")
            for url in [f"http://{host}/x", f"https://{host}.evil.test/x",
                        f"https://user@{host}/x", f"https://{host}:444/x"]:
                with self.subTest(url=url), self.assertRaises(downloader.ComponentDownloadError):
                    downloader._validate_redirect(url)

    def test_native_curl_rejects_redirect_before_requesting_second_host(self):
        with patch.object(downloader, "_curl_head",
                          return_value=(302, "https://other.invalid/component")) as head, patch.object(
                downloader.subprocess, "Popen") as get, self.assertRaises(downloader.ComponentDownloadError):
            downloader._curl_download(URL, self.destination, len(DATA), "system-curl.exe")
        self.assertEqual(head.call_count, 1)
        get.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_native_transport_checks_each_head_then_streams_only_the_final_url(self):
        first = "https://objects.githubusercontent.com/synthetic"
        final = "https://release-assets.githubusercontent.com/example?sig=fake"
        calls = []
        replies = iter([b"302\n" + first.encode(), b"307\n" + final.encode(), b"200\n"])

        def head(command, **kwargs):
            self.assertIn("--head", command)
            self.assertIn("--disable", command)
            self.assertEqual(command[command.index("--proto") + 1], "=https")
            self.assertNotIn("--location", command)
            self.assertNotIn("-L", command)
            calls.append(command[command.index("--url") + 1])
            return subprocess.CompletedProcess(command, 0, next(replies))

        process = FakeCurlProcess(DATA)
        with patch.object(downloader, "_curl_environment", return_value={}), patch.object(
                downloader.subprocess, "run", side_effect=head), patch.object(
                downloader.subprocess, "Popen", return_value=process) as download:
            downloader._curl_download(URL, self.destination, len(DATA), "system-curl.exe")
        self.assertEqual(calls, [URL, first, final])
        command = download.call_args.args[0]
        self.assertEqual(command[command.index("--url") + 1], final)
        self.assertEqual(command[command.index("--max-filesize") + 1], str(len(DATA)))
        self.assertFalse({"-L", "--location", "-k", "--insecure"} & set(command))
        self.assertEqual(self.destination.read_bytes(), DATA)

    def test_native_oversized_output_is_cut_off_and_partial_file_removed(self):
        process = FakeCurlProcess(DATA + b"unexpected extra bytes")
        with patch.object(sys, "platform", "win32"), patch.object(
                downloader, "_python_download", side_effect=URLError("unavailable")), patch.object(
                downloader, "_windows_system_curl", return_value="system-curl.exe"), patch.object(
                downloader, "_curl_environment", return_value={}), patch.object(
                downloader, "_curl_head", return_value=(200, "")), patch.object(
                downloader.subprocess, "Popen", return_value=process), patch("time.sleep"):
            with self.assertRaises(downloader.ComponentDownloadError):
                self.download()
        self.assertTrue(process.killed)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_retryable_response_recovers_from_incomplete_asset(self):
        attempts = []

        def fetch(url, path, size):
            attempts.append(url)
            if len(attempts) == 1:
                path.write_bytes(DATA[:3])
                raise HTTPError(url, 503, "temporary outage", {}, None)
            path.write_bytes(DATA)

        with patch.object(sys, "platform", "darwin"), patch.object(
                downloader, "_python_download", side_effect=fetch), patch("time.sleep"):
            self.download()
        self.assertEqual(attempts, [URL, URL])
        self.assertEqual(self.destination.read_bytes(), DATA)
        self.assertEqual(set(self.directory.iterdir()), {self.destination})

    def test_outage_exhausts_finite_retries(self):
        with patch.object(sys, "platform", "darwin"), patch.object(
                downloader, "_python_download",
                side_effect=HTTPError(URL, 503, "temporary outage", {}, None)) as fetch, patch("time.sleep"):
            with self.assertRaises(downloader.ComponentDownloadError):
                self.download()
        self.assertGreater(fetch.call_count, 1)
        self.assertLessEqual(fetch.call_count, 4)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_windows_native_fallback_verifies_contents(self):
        for payload, succeeds in [(DATA, True), (b"!" * len(DATA), False)]:
            with self.subTest(succeeds=succeeds), patch.object(sys, "platform", "win32"), patch.object(
                    downloader, "_windows_system_curl", return_value="system-curl.exe"), patch.object(
                    downloader, "_python_download", side_effect=URLError(
                        ssl.SSLCertVerificationError("test store rejected certificate"))), patch.object(
                    downloader, "_curl_download",
                    side_effect=lambda url, path, size, exe: path.write_bytes(payload)) as native, patch("time.sleep"):
                if succeeds:
                    self.download()
                    self.assertEqual(self.destination.read_bytes(), DATA)
                    self.destination.unlink()
                else:
                    with self.assertRaises(downloader.ComponentDownloadError):
                        self.download()
                self.assertGreaterEqual(native.call_count, 1)
                self.assertEqual(list(self.directory.iterdir()), [])


class FakeCurlProcess:
    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.killed = False
        self.returncode = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.stdout.close()

    def wait(self, timeout=None):
        self.returncode = -1 if self.killed else 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1
