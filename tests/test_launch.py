import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("fagao_launch", Path(__file__).resolve().parents[1] / "scripts/launch.py")
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class PreflightTests(unittest.TestCase):
    def test_current_features_ready_without_browser_automation(self):
        result = launch.preflight()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["features"]["heiyan"]["status"], "external_link")
        self.assertEqual(result["features"]["heiyan"]["url"], "https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7")
        self.assertEqual(result["features"]["legacy_heiyan_backend"]["status"], "not_required")
        self.assertFalse(result["network_verified"])
        self.assertEqual(result["network"]["inherited_proxy_environment"], "ignored")

    def test_missing_sqlite_is_detected_before_service_launch(self):
        original = launch.importlib.import_module
        def missing(name):
            if name == "sqlite3":
                raise ImportError("no sqlite extension")
            return original(name)
        with patch.object(launch.importlib, "import_module", side_effect=missing):
            with self.assertRaisesRegex(RuntimeError, "缺少 sqlite3"):
                launch.preflight()

    def test_broken_https_environment_is_detected(self):
        with patch("ssl.create_default_context", side_effect=OSError("missing CA runtime")):
            with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                launch.preflight()

    def test_missing_page_and_corrupt_character_map_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(launch, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "安装文件缺失"):
                    launch.preflight()
                for name in ("fagao/server.py", "fagao/store.py", "fagao/overview.py", "fagao/dashboard.py", "fagao/public_rankings.py", "fagao/network.py", "fagao/overseas.py", "fagao/overseas_readers.py", "web/index.html", "web/app.js", "web/styles.css"):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("fixture", encoding="utf-8")
                asset = root / "fagao/assets/fanqie-charmap.json"
                asset.parent.mkdir()
                for value in ({}, {"bad": "字"}, {"58670": "too long"}, ["字"]):
                    asset.write_text(json.dumps(value), encoding="utf-8")
                    with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "字表资源"):
                        launch.preflight()

    def test_check_flag_never_starts_service_or_opens_browser(self):
        with patch("sys.argv", ["launch.py", "--check"]), patch.object(launch, "ensure_running") as start, patch.object(launch.webbrowser, "open") as browser, patch("builtins.print"):
            self.assertEqual(launch.main(), 0)
        start.assert_not_called()
        browser.assert_not_called()


class EnvironmentIsolationTests(unittest.TestCase):
    def test_spawn_removes_session_proxies_and_keeps_runtime_environment(self):
        polluted = {key: "http://127.0.0.1:52586" for key in (
            "HTTP_PROXY", "http_proxy", "Http_Proxy", "HTTPS_PROXY", "https_proxy", "Https_Proxy",
            "ALL_PROXY", "all_proxy", "All_Proxy", "NO_PROXY", "no_proxy", "No_Proxy",
            "FTP_PROXY", "ftp_proxy", "Ftp_Proxy",
        )}
        polluted.update({"PATH": "/runtime/bin", "SSL_CERT_FILE": "/runtime/cert.pem", "FAGAO_NETWORK_PROXY": "http://127.0.0.1:9000", "FAGAO_NODE": "/runtime/node"})
        process = Mock(pid=12345)
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            network = {"status": "checking", "policy": "application_owned"}
            healthy = {"app": launch.APP, "data_dir": str(data), "network": network}
            with patch.dict(os.environ, polluted, clear=True), patch.object(launch, "health", side_effect=[None, None, healthy]), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process) as spawn:
                result = launch.ensure_running(data, 18376)
                child_env = spawn.call_args.kwargs["env"]
                self.assertFalse(set(key.lower() for key in child_env) & launch.PROXY_ENVIRONMENT_KEYS)
                for key in ("PATH", "SSL_CERT_FILE", "FAGAO_NETWORK_PROXY", "FAGAO_NODE"):
                    self.assertEqual(child_env[key], polluted[key])
                self.assertEqual(child_env["FAGAO_DATA_DIR"], str(data))
                self.assertEqual(child_env["PYTHONUNBUFFERED"], "1")
                self.assertEqual(dict(os.environ), polluted)
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["network"], network)
            self.assertEqual((data / "server.pid").read_text(), "12345")
            self.assertFalse((data / "launch-18376.lock").exists())

    def test_reusing_legacy_service_does_not_claim_its_network_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            healthy = {"app": launch.APP, "data_dir": str(data)}
            with patch.object(launch, "health", return_value=healthy), patch.object(launch.subprocess, "Popen") as spawn:
                result = launch.ensure_running(data, 18376)
            self.assertEqual(result["status"], "reused")
            self.assertEqual(result["network"]["status"], "unknown")
            spawn.assert_not_called()

    def test_reused_service_reports_its_own_network_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            network = {"status": "failed", "error_code": "proxy_connection_refused"}
            healthy = {"app": launch.APP, "data_dir": str(data), "network": network}
            with patch.object(launch, "health", return_value=healthy):
                result = launch.ensure_running(data, 18376)
            self.assertEqual(result["network"], network)

    def test_launch_readiness_uses_running_service_network_evidence(self):
        network = {"status": "unknown", "reason": "service_did_not_report_network_policy"}
        running = {"status": "reused", "url": "http://127.0.0.1:18376/", "network": network}
        with patch("sys.argv", ["launch.py", "--no-browser"]), patch.object(launch, "preflight", return_value={"status": "ready", "network_verified": False, "network": {"status": "local_ready"}}), patch.object(launch, "ensure_running", return_value=running), patch("builtins.print") as output:
            self.assertEqual(launch.main(), 0)
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["readiness"]["network"], network)
        self.assertFalse(payload["readiness"]["network_verified"])


if __name__ == "__main__":
    unittest.main()
