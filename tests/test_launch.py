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
                for name in ("fagao/server.py", "fagao/store.py", "fagao/overview.py", "fagao/dashboard.py", "fagao/public_rankings.py", "fagao/network.py", "fagao/overseas.py", "fagao/overseas_readers.py", "fagao/royalroad_fallback.py", "fagao/scribble_import.py", "fagao/opening_content.py", "fagao/reader_crypto.py", "fagao/_vendor/__init__.py", "fagao/_vendor/pyaes/__init__.py", "fagao/_vendor/pyaes/aes.py", "fagao/_vendor/pyaes/blockfeeder.py", "fagao/_vendor/pyaes/util.py", "web/index.html", "web/app.js", "web/styles.css"):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("fixture", encoding="utf-8")
                asset = root / "fagao/assets/fanqie-charmap.json"
                asset.parent.mkdir()
                for value in ({}, {"bad": "字"}, {"58670": "too long"}, ["字"]):
                    asset.write_text(json.dumps(value), encoding="utf-8")
                    with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "字表资源"):
                        launch.preflight()

    def test_missing_opening_reader_is_detected_before_service_launch(self):
        original = Path.is_file
        for name in ("fagao/opening_content.py", "fagao/reader_crypto.py", "fagao/_vendor/pyaes/aes.py"):
            missing = launch.ROOT / name
            with self.subTest(name=name), patch.object(Path, "is_file", lambda path: False if path == missing else original(path)):
                with self.assertRaisesRegex(RuntimeError, "安装文件缺失"):
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
            healthy = {"app": launch.APP, "source_root": str(launch.ROOT), "data_dir": str(data), "network": network}
            with patch.dict(os.environ, polluted, clear=True), patch.object(launch, "health", side_effect=[None, None, healthy, healthy]), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process) as spawn:
                # Windows normalizes environment keys; compare the actual
                # parent environment before/after, not the raw fixture mapping.
                parent_environment = dict(os.environ)
                self.assertEqual({key.lower() for key in parent_environment} & launch.PROXY_ENVIRONMENT_KEYS,
                                 launch.PROXY_ENVIRONMENT_KEYS)
                result = launch.ensure_running(data, 18376)
                child_env = spawn.call_args.kwargs["env"]
                self.assertFalse(set(key.lower() for key in child_env) & launch.PROXY_ENVIRONMENT_KEYS)
                for key in ("PATH", "SSL_CERT_FILE", "FAGAO_NETWORK_PROXY", "FAGAO_NODE"):
                    self.assertEqual(child_env[key], polluted[key])
                self.assertEqual(child_env["FAGAO_DATA_DIR"], str(data))
                self.assertEqual(child_env["PYTHONUNBUFFERED"], "1")
                self.assertEqual(child_env["PYTHONIOENCODING"], "utf-8")
                self.assertEqual(dict(os.environ), parent_environment)
            self.assertEqual(result["status"], "started")
            self.assertEqual(result["network"], network)
            self.assertEqual((data / "server.pid").read_text(), "12345")
            self.assertFalse((data / "launch-18376.lock").exists())

    def test_reusing_legacy_service_does_not_claim_its_network_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            healthy = {"app": launch.APP, "source_root": str(launch.ROOT), "data_dir": str(data)}
            with patch.object(launch, "health", return_value=healthy), patch.object(launch.subprocess, "Popen") as spawn:
                result = launch.ensure_running(data, 18376)
            self.assertEqual(result["status"], "reused")
            self.assertEqual(result["network"]["status"], "unknown")
            spawn.assert_not_called()

    def test_reused_service_reports_its_own_network_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            network = {"status": "failed", "error_code": "proxy_connection_refused"}
            healthy = {"app": launch.APP, "source_root": str(launch.ROOT), "data_dir": str(data), "network": network}
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


class ManagedWindowsLaunchTests(unittest.TestCase):
    def healthy(self, data):
        return {"app": launch.APP, "source_root": str(launch.ROOT), "data_dir": str(data),
                "network": {"policy": "application_owned"}}

    def denied(self):
        error = PermissionError("CreateProcess denied")
        error.winerror = 5
        return error

    def test_windows_requests_permitted_job_breakaway_and_preserves_argv(self):
        with tempfile.TemporaryDirectory(prefix="发糕 空格 ") as directory:
            data = Path(directory)
            process = Mock(pid=123)
            process.poll.return_value = None
            healthy = self.healthy(data)
            with patch.object(launch, "IS_WINDOWS", True), patch.object(launch, "health", side_effect=[None, None, healthy, healthy]), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process) as spawn, patch.object(launch.time, "sleep"):
                result = launch.ensure_running(data, 18376)
            command = spawn.call_args.args[0]
            options = spawn.call_args.kwargs
            self.assertEqual(command[-2:], ["--data-dir", str(data)])
            self.assertEqual(options["creationflags"], 0x8 | 0x200 | 0x01000000)
            self.assertNotIn("shell", options)
            self.assertNotIn("start_new_session", options)
            self.assertEqual(result["lifecycle"]["mode"], "windows_breakaway")
            self.assertFalse(result["lifecycle"]["host_survival_verified"])
            self.assertEqual(result["lifecycle"]["follow_up"], "verify_in_separate_tool_call")
            self.assertEqual(result["log_path"], str(data / "logs/workbench.log"))

    def test_denied_breakaway_returns_native_host_action_without_retry_or_pid(self):
        with tempfile.TemporaryDirectory(prefix="发糕 用户 ") as directory:
            data = Path(directory)
            with patch.object(launch, "IS_WINDOWS", True), patch.object(launch, "windows_in_job", return_value=True), patch.object(launch, "health", return_value=None), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", side_effect=self.denied()) as spawn:
                with self.assertRaises(launch.NativeBackgroundRequired) as error:
                    launch.ensure_running(data, 18376)
            spawn.assert_called_once()
            value = error.exception.result
            self.assertEqual(value["status"], "background_required")
            self.assertEqual(value["code"], "native_background_required")
            self.assertEqual(value["action"]["type"], "run_in_background")
            self.assertEqual(value["action"]["argv"], [launch.sys.executable, str(launch.ROOT / "scripts/launch.py"), "--data-dir", str(data), "--port", "18376", "--foreground", "--no-browser"])
            self.assertEqual(value["action"]["cwd"], str(launch.ROOT))
            self.assertEqual(value["action"]["health_url"], "http://127.0.0.1:18376/api/health")
            self.assertFalse((data / "server.pid").exists())
            self.assertFalse((data / "launch-18376.lock").exists())

    def test_access_denied_outside_job_is_not_misreported_as_host_lifetime(self):
        for in_job in (False, None):
            with self.subTest(in_job=in_job), patch.object(launch, "IS_WINDOWS", True), patch.object(launch, "windows_in_job", return_value=in_job), patch.object(launch.subprocess, "Popen", side_effect=self.denied()) as spawn:
                with self.assertRaises(PermissionError):
                    launch.spawn_service(["python", "-m", "fagao.server"], {}, Path.cwd(), 18376, foreground=False)
                spawn.assert_called_once()

    def test_main_emits_structured_background_required_and_does_not_open_browser(self):
        data = Path.cwd()
        with patch("sys.argv", ["launch.py", "--no-browser"]), patch.object(launch, "preflight", return_value={"status": "ready"}), patch.object(launch, "ensure_running", side_effect=launch.NativeBackgroundRequired(data, 18376)), patch.object(launch.webbrowser, "open") as browser, patch("builtins.print") as output:
            self.assertEqual(launch.main(), 3)
        value = json.loads(output.call_args.args[0])
        self.assertEqual(value["status"], "background_required")
        self.assertEqual(value["readiness"]["status"], "ready")
        self.assertFalse(value["browser_opened"])
        browser.assert_not_called()

    def test_foreground_is_owned_by_host_and_releases_startup_lock_before_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            healthy = self.healthy(data)
            process = Mock(pid=456)
            process.poll.side_effect = [None, None, 0]
            def wait():
                self.assertFalse((data / "launch-18376.lock").exists())
                self.assertEqual((data / "server.pid").read_text(), "456")
                return 0
            process.wait.side_effect = wait
            with patch.object(launch, "IS_WINDOWS", True), patch.object(launch, "health", side_effect=[None, None, healthy, healthy]), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process) as spawn, patch.object(launch.time, "sleep"), patch("builtins.print") as output:
                self.assertEqual(launch.run_foreground(data, 18376, {"status": "ready"}), 0)
            options = spawn.call_args.kwargs
            self.assertNotIn("creationflags", options)
            self.assertNotIn("start_new_session", options)
            value = json.loads(output.call_args.args[0])
            self.assertEqual(value["pid"], 456)
            self.assertEqual(value["lifecycle"]["mode"], "host_managed_foreground")
            self.assertFalse(value["browser_opened"])
            process.terminate.assert_not_called()

    def test_foreground_main_does_not_invoke_detached_launcher_or_browser(self):
        with patch("sys.argv", ["launch.py", "--foreground", "--no-browser"]), patch.object(launch, "preflight", return_value={"status": "ready"}), patch.object(launch, "run_foreground", return_value=0) as managed, patch.object(launch, "ensure_running") as detached, patch.object(launch.webbrowser, "open") as browser:
            self.assertEqual(launch.main(), 0)
        managed.assert_called_once()
        detached.assert_not_called()
        browser.assert_not_called()

    def test_foreground_reuses_matching_service_without_waiting_or_stopping_it(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with patch.object(launch, "health", return_value=self.healthy(data)), patch.object(launch.subprocess, "Popen") as spawn, patch.object(launch, "stop_owned_child") as stop, patch("builtins.print") as output:
                self.assertEqual(launch.run_foreground(data, 18376, {"status": "ready"}), 0)
            self.assertEqual(json.loads(output.call_args.args[0])["status"], "reused")
            spawn.assert_not_called()
            stop.assert_not_called()

    def test_foreground_closes_its_own_child_if_host_output_pipe_breaks(self):
        process = Mock()
        result = {"status": "started", "network": {}, "pid": 123}
        with patch.object(launch, "_ensure_running", return_value=(result, process)), patch.object(launch, "emit_json", side_effect=BrokenPipeError()), patch.object(launch, "stop_owned_child") as stop:
            with self.assertRaises(BrokenPipeError):
                launch.run_foreground(Path.cwd(), 18376, {"status": "ready"})
        stop.assert_called_once_with(process)
        process.wait.assert_not_called()

    def test_dead_child_is_not_reported_ready_even_with_a_health_response(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            process = Mock(pid=987, returncode=7)
            process.poll.return_value = 7
            with patch.object(launch, "health", side_effect=[None, None, self.healthy(data)]), patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "退出码 7"):
                    launch.ensure_running(data, 18376)
            self.assertFalse((data / "server.pid").exists())
            process.terminate.assert_not_called()

    def test_transient_health_needs_two_consecutive_live_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            healthy = self.healthy(data)
            process = Mock(pid=654)
            process.poll.return_value = None
            with patch.object(launch, "health", side_effect=[None, None, healthy, None, healthy, healthy]) as check, patch.object(launch, "listening", return_value=False), patch.object(launch.subprocess, "Popen", return_value=process), patch.object(launch.time, "sleep"):
                result = launch.ensure_running(data, 18376)
            self.assertEqual(result["status"], "started")
            self.assertEqual(check.call_count, 6)

    def test_other_release_is_not_reused_or_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            state = {**self.healthy(data), "source_root": str(data / "other-release")}
            with patch.object(launch, "health", return_value=state), patch.object(launch.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(RuntimeError, "另一版本"):
                    launch.ensure_running(data, 18376)
            spawn.assert_not_called()

    def test_protocol_does_not_depend_on_console_unicode_support(self):
        with patch("builtins.print") as output:
            launch.emit_json({"path": "C:\\用户 空格\\发糕🍰"})
        raw = output.call_args.args[0]
        self.assertTrue(raw.isascii())
        self.assertEqual(json.loads(raw)["path"], "C:\\用户 空格\\发糕🍰")


if __name__ == "__main__":
    unittest.main()
