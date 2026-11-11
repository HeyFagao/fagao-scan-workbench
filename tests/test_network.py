# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from fagao import network


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/check")
            self.end_headers()
            return
        if self.path in ("/other-host", "/other-host-denied"):
            self.send_response(302)
            suffix = "/denied" if self.path.endswith("denied") else "/check"
            self.send_header("Location", f"http://localhost:{self.server.server_port}{suffix}")
            self.end_headers()
            return
        if self.path == "/denied":
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        if self.path == "/cookie":
            self.send_header("Set-Cookie", "session=ordinary; Path=/")
        self.end_headers()
        content = self.headers.get("Cookie", "") if self.path == "/check" else self.server.label
        self.wfile.write(content.encode())


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for replacement in (patch.object(network, "_directory", self.directory),
                            patch.object(network, "_last_request", None),
                            patch.object(network, "_system_proxies", return_value={}),
                            patch.object(network, "_system_bypass", return_value=False)):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.server, self.url = self.make_server("origin")

    def make_server(self, label):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.label, server.requests = label, []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: (server.shutdown(), thread.join(timeout=2)))
        return server, f"http://127.0.0.1:{server.server_port}"

    def dead_proxy(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return f"http://127.0.0.1:{probe.getsockname()[1]}"

    def read(self, url=None, opener=None):
        with (opener or network.build_opener()).open(url or self.url, timeout=1) as response:
            return response.read().decode()

    def test_dead_inherited_proxy_is_ignored_without_configure(self):
        dead = self.dead_proxy()
        with patch.dict(os.environ, {"HTTP_PROXY": dead, "https_proxy": dead, "ALL_PROXY": dead,
                                     "hTtP_pRoXy": dead, "NO_PROXY": "*"}):
            self.assertEqual(self.read(), "origin")
            self.assertEqual(network.status()["last_request"]["route"], "direct")

    def test_configure_removes_case_variants_and_preserves_runtime(self):
        values = {"HTTP_PROXY": "x", "Https_Proxy": "x", "ALL_PROXY": "x", "ftp_proxy": "x",
                  "NO_PROXY": "*", "PATH": "/runtime/bin", "FAGAO_DATA_DIR": str(self.directory),
                  "SSL_CERT_FILE": "/trusted/ca.pem", "FAGAO_NETWORK_PROXY": "app-owned"}
        with patch.dict(os.environ, values, clear=True):
            network.configure(self.directory)
            self.assertEqual(dict(os.environ), {key: value for key, value in values.items()
                                               if key.lower() not in network._PROXY_ENV})

    def test_environment_no_proxy_cannot_bypass_explicit_application_proxy(self):
        proxy, address = self.make_server("proxy")
        network.save_settings(self.directory, "proxy", address)
        with patch.dict(os.environ, {"NO_PROXY": "*", "http_proxy": self.dead_proxy()}):
            self.assertEqual(self.read(), "proxy")
        self.assertEqual(len(proxy.requests), 1)
        self.assertEqual(self.server.requests, [])

    def test_current_system_proxy_is_resolved_on_every_request(self):
        first, first_address = self.make_server("first")
        second, second_address = self.make_server("second")
        opener = network.build_opener()
        with patch.object(network, "_system_proxies", side_effect=[{"http": first_address}, {"http": second_address}]):
            self.assertEqual(self.read(opener=opener), "first")
            self.assertEqual(self.read(opener=opener), "second")
        self.assertEqual(len(first.requests), 1)
        self.assertEqual(len(second.requests), 1)

    def test_refused_loopback_system_proxy_retries_direct_with_fresh_request(self):
        request = urllib.request.Request(self.url + "/check?private=secret", headers={"Proxy-Authorization": "Basic secret"})
        with patch.object(network, "_system_proxies", return_value={"http": self.dead_proxy()}):
            with network.build_opener().open(request, timeout=1) as response:
                self.assertEqual(response.read(), b"origin")
        self.assertEqual(request.host, f"127.0.0.1:{self.server.server_port}")
        self.assertEqual(self.server.requests[0][0], "/check?private=secret")
        self.assertNotIn("Proxy-Authorization", self.server.requests[0][1])
        result = network.status()["last_request"]
        self.assertEqual(result["outcome"], "connected")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["route"], "direct")

    def test_explicit_refused_proxy_never_silently_uses_direct(self):
        network.save_settings(self.directory, "proxy", self.dead_proxy())
        with self.assertRaises(network.NetworkError):
            self.read()
        self.assertEqual(self.server.requests, [])
        self.assertFalse(network.status()["last_request"]["fallback"])

    def test_http_tls_remote_proxy_and_post_errors_do_not_trigger_retry(self):
        cases = [
            ("http://127.0.0.1:1234", "GET", urllib.error.HTTPError(self.url, 403, "denied", {}, None)),
            ("http://127.0.0.1:1234", "GET", urllib.error.URLError(ssl.SSLCertVerificationError("certificate invalid"))),
            ("http://remote.invalid:1234", "GET", urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))),
            ("http://127.0.0.1:1234", "POST", urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))),
        ]
        for address, method, error in cases:
            with self.subTest(address=address, method=method, error=type(error).__name__):
                with patch.object(network, "_system_proxies", return_value={"http": address}), \
                        patch.object(network._Opener, "_open", side_effect=error) as request:
                    with self.assertRaises(urllib.error.URLError):
                        network.build_opener().open(urllib.request.Request(self.url, method=method))
                    self.assertEqual(request.call_count, 1)

    def test_cookie_session_and_redirect_handler_survive_new_openers(self):
        jar = CookieJar()
        opener = network.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.read(self.url + "/cookie", opener)
        self.assertEqual(self.read(self.url + "/redirect", opener), "session=ordinary")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                raise ValueError("redirect blocked")

        with self.assertRaisesRegex(ValueError, "redirect blocked"):
            self.read(self.url + "/redirect", network.build_opener(NoRedirect()))

    def test_settings_change_is_live_and_invalid_file_does_not_fall_back(self):
        opener = network.build_opener()
        proxy, address = self.make_server("configured")
        network.save_settings(self.directory, "proxy", address)
        self.assertEqual(self.read(opener=opener), "configured")
        network.save_settings(self.directory, "direct")
        self.assertEqual(self.read(opener=opener), "origin")
        (self.directory / "network.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(network.NetworkError):
            self.read(opener=opener)
        self.assertEqual(network.status()["configuration"], "invalid")

    def test_malformed_system_proxy_fails_with_safe_diagnostic(self):
        for value in ("socks5://host:80", "http://user:password@host:80", "http://host:bad", 12):
            with self.subTest(value=value), patch.object(network, "_system_proxies", return_value={"http": value}):
                with self.assertRaises(network.NetworkError):
                    self.read()
        self.assertEqual(self.server.requests, [])
        self.assertNotIn("password", json.dumps(network.status()))

    def test_redirect_diagnostic_tracks_final_target_and_timestamp(self):
        self.read(self.url + "/other-host")
        result = network.status()["last_request"]
        self.assertEqual(result["target"], "localhost")
        self.assertRegex(result["checked_at"], r"\d{4}-\d{2}-\d{2}T")
        with self.assertRaises(urllib.error.HTTPError):
            self.read(self.url + "/other-host-denied")
        result = network.status()["last_request"]
        self.assertEqual(result["target"], "localhost")
        self.assertEqual(result["error_type"], "HTTPError")

    def test_proxy_configuration_rejects_credentials_and_url_payloads_atomically(self):
        network.save_settings(self.directory, "direct")
        original = (self.directory / "network.json").read_bytes()
        for address in ("http://user:secret@host:80", "socks5://host:90", "http://host:80/path",
                        "http://host:80/?token=secret", "http://host:80/#secret", "http://host:0",
                        "http://host:99999", "http://host:80\n", "http://bad\\host:80"):
            with self.subTest(address=address), self.assertRaises(network.NetworkError):
                network.save_settings(self.directory, "proxy", address)
            self.assertEqual((self.directory / "network.json").read_bytes(), original)

    def test_failure_logs_are_redacted(self):
        error = urllib.error.URLError(OSError(errno.ETIMEDOUT, "https://user:password@private/path?token=secret"))
        with patch.object(network._Opener, "_open", side_effect=error), self.assertLogs("fagao.network", logging.WARNING) as captured:
            with self.assertRaises(network.NetworkError):
                self.read(self.url + "/private?token=secret")
        logged = " ".join(captured.output) + json.dumps(network.status())
        for forbidden in ("password", "token", "secret", "/private", "user:"):
            self.assertNotIn(forbidden, logged)
        self.assertIn("TimeoutError", logged)
        self.assertIn(str(errno.ETIMEDOUT), logged)

    def test_system_getters_are_explicit_and_do_not_use_environment_getter(self):
        # setUp patches _system_proxies, so invoke the original implementation
        # through a module-level saved reference below.
        for platform, name in (("darwin", "getproxies_macosx_sysconf"), ("win32", "getproxies_registry")):
            with self.subTest(platform=platform), patch.object(network.sys, "platform", platform), \
                    patch.object(network.urllib.request, name, return_value={"http": "http://system:80"}, create=True), \
                    patch.object(network.urllib.request, "getproxies", side_effect=AssertionError("environment read")):
                self.assertEqual(SYSTEM_PROXIES(), {"http": "http://system:80"})


SYSTEM_PROXIES = network._system_proxies


if __name__ == "__main__":
    unittest.main()
