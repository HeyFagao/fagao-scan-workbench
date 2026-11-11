# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Runtime wiring: real collector boundary, async startup and persisted CLI policy."""
from __future__ import annotations

import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

from fagao import collectors, network
from fagao.server import WorkbenchServer
from fagao.store import Store

ROOT = Path(__file__).resolve().parents[1]


class ProbeIntegrationTests(unittest.TestCase):
    def test_probe_uses_collector_transport_with_five_second_timeout(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"<html>source page</html>")
        with patch.object(network, "build_opener", return_value=opener), patch.object(collectors, "_fetch", wraps=collectors._fetch) as fetch:
            result = collectors.probe_network()
        self.assertEqual(result["status"], "reachable")
        fetch.assert_called_once_with("https://fanqienovel.com/rank/0_1_253", timeout=5, read_body=False)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, result["target"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertIn("其他来源", result["message"])

    def test_probe_closes_response_without_reading_a_stalled_body(self):
        class HeadersOnlyResponse(io.BytesIO):
            def read(self, *args, **kwargs):
                raise AssertionError("startup probe must not wait for the response body")

        response = HeadersOnlyResponse(b"never read this body")
        opener = Mock()
        opener.open.return_value = response
        with patch.object(network, "build_opener", return_value=opener):
            result = collectors.probe_network()
        self.assertEqual(result["status"], "reachable")
        self.assertTrue(response.closed)

    def test_probe_maps_access_refusal_without_exposing_response_details(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "https://fanqienovel.com/rank/0_1_253?token=private", 403,
            "private-response-detail", {}, None,
        )
        with patch.object(network, "build_opener", return_value=opener):
            result = collectors.probe_network()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "forbidden")
        self.assertIn("403", result["message"])
        self.assertNotIn("private", json.dumps(result))
        opener.open.assert_called_once()

    def test_probe_maps_raw_transport_error_without_proxy_credentials(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("http://secret-user:secret-pass@127.0.0.1:52586 connection refused")
        with patch.object(network, "build_opener", return_value=opener):
            result = collectors.probe_network()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "network_error")
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn("上次结果仍保留", result["message"])

    def test_probe_keeps_application_network_diagnostic(self):
        opener = Mock()
        message = "工作台配置的代理无法连接，请检查网络配置；已有结果仍保留。"
        opener.open.side_effect = network.NetworkError(message)
        with patch.object(network, "build_opener", return_value=opener):
            result = collectors.probe_network()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "network_error")
        self.assertEqual(result["message"], message)


class ObservedServer(WorkbenchServer):
    """Signal the worker's state assignment without timing-based polling."""
    def __init__(self, *args, **kwargs):
        self.probe_failed = threading.Event()
        super().__init__(*args, **kwargs)

    @property
    def network_probe(self):
        return self._observed_probe

    @network_probe.setter
    def network_probe(self, value):
        self._observed_probe = value
        if value.get("status") == "failed":
            self.probe_failed.set()


class ServerNetworkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = ObservedServer(("127.0.0.1", 0), Store(self.temporary.name))
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.http_thread.join(timeout=2)
        self.temporary.cleanup()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_startup_probe_never_blocks_local_health_or_reports_failed_as_ready(self):
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        failed = {"status": "failed", "code": "network_error", "message": "连接失败；已有资料保留。"}

        def blocking_probe():
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test failed to release probe")
            return failed

        def start_probe():
            self.server.start_network_probe()
            returned.set()

        with patch.object(collectors, "probe_network", side_effect=blocking_probe):
            caller = threading.Thread(target=start_probe, daemon=True)
            caller.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertTrue(returned.wait(timeout=2), "start_network_probe waited for the remote source")
                self.assertFalse(release.is_set())
                status, health = self.get("/api/health")
                self.assertEqual(status, 200)
                self.assertEqual(health["app"], "fagao-scan-workbench")
                self.assertEqual(health["network"]["probe"]["status"], "checking")
                status, diagnostic = self.get("/api/network")
                self.assertEqual(status, 200)
                self.assertEqual(diagnostic["probe"]["status"], "checking")
            finally:
                release.set()
                caller.join(timeout=2)
            self.assertTrue(self.server.probe_failed.wait(timeout=2))
        status, health = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["network"]["probe"], failed)
        status, diagnostic = self.get("/api/network")
        self.assertEqual(status, 200)
        self.assertEqual(diagnostic["probe"], failed)

    def test_unexpected_probe_failure_returns_safe_diagnostic(self):
        with patch.object(collectors, "probe_network", side_effect=RuntimeError("private-token-and-proxy-password")):
            self.server.start_network_probe()
            self.assertTrue(self.server.probe_failed.wait(timeout=2))
        status, diagnostic = self.get("/api/network")
        self.assertEqual(status, 200)
        self.assertEqual(diagnostic["probe"]["status"], "failed")
        self.assertEqual(diagnostic["probe"]["code"], "diagnostic_error")
        self.assertNotIn("private-token", json.dumps(diagnostic))

    def test_probe_deadline_reports_timeout_and_ignores_late_success(self):
        entered = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        timers = []

        class ManualTimer:
            def __init__(self, interval, callback):
                self.interval = interval
                self.callback = callback
                self.started = False
                self.daemon = False
                timers.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                cancelled.set()

        def blocked_then_successful_probe():
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test failed to release probe")
            return {"status": "reachable", "message": "late success"}

        with patch("fagao.server.threading.Timer", side_effect=ManualTimer), patch.object(collectors, "probe_network", side_effect=blocked_then_successful_probe):
            self.server.start_network_probe(timeout=10)
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertEqual(len(timers), 1)
                timer = timers[0]
                self.assertEqual(timer.interval, 10)
                self.assertTrue(timer.started)
                self.assertTrue(timer.daemon)
                self.assertFalse(release.is_set())
                timer.callback()
                status, diagnostic = self.get("/api/network")
                self.assertEqual(status, 200)
                self.assertEqual(diagnostic["probe"]["status"], "failed")
                self.assertEqual(diagnostic["probe"]["code"], "probe_timeout")
                self.assertFalse(cancelled.is_set())
            finally:
                release.set()
            self.assertTrue(cancelled.wait(timeout=2), "late worker never reached its finish path")
        status, health = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["network"]["probe"]["status"], "failed")
        self.assertEqual(health["network"]["probe"]["code"], "probe_timeout")


class NetworkCLIIntegrationTests(unittest.TestCase):
    def run_cli(self, directory, *arguments):
        environment = os.environ.copy()
        environment.update(HTTP_PROXY="http://127.0.0.1:52586", HTTPS_PROXY="http://127.0.0.1:52586")
        return subprocess.run(
            [sys.executable, "-m", "fagao", "--data-dir", directory, *arguments],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
        )

    def test_cli_policy_survives_a_new_process_without_inherited_session_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            written = self.run_cli(directory, "network-config", "--mode", "proxy", "--proxy-url", "http://127.0.0.1:9000")
            self.assertEqual(written.returncode, 0, written.stderr)
            settings = json.loads(written.stdout)
            self.assertEqual(settings["mode"], "proxy")
            self.assertEqual(settings["proxy"], "127.0.0.1:9000")
            environment = os.environ.copy()
            environment.update(HTTP_PROXY="http://127.0.0.1:52586", HTTPS_PROXY="http://127.0.0.1:52586")
            loaded = subprocess.run(
                [sys.executable, "-c", "import json,os,sys; from fagao import network; print(json.dumps({'network':network.configure(sys.argv[1]),'session_proxy_present': any(k.lower() in ('http_proxy','https_proxy') for k in os.environ)}))", directory],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            result = json.loads(loaded.stdout)
            self.assertEqual(result["network"]["mode"], "proxy")
            self.assertEqual(result["network"]["proxy"], "127.0.0.1:9000")
            self.assertFalse(result["session_proxy_present"])

    def test_cli_rejects_credentials_without_echoing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory, "network-config", "--mode", "proxy", "--proxy-url", "http://private-user:private-password@127.0.0.1:9000")
            self.assertEqual(result.returncode, 1)
            error = json.loads(result.stdout)["error"]
            self.assertIn("不能包含账号", error["message"])
            self.assertNotIn("private-user", result.stdout + result.stderr)
            self.assertNotIn("private-password", result.stdout + result.stderr)
            self.assertFalse((Path(directory) / "network.json").exists())


if __name__ == "__main__":
    unittest.main()
