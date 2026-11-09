import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


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
                for name in ("fagao/server.py", "fagao/store.py", "fagao/overview.py", "fagao/dashboard.py", "fagao/public_rankings.py", "fagao/overseas.py", "fagao/overseas_readers.py", "web/index.html", "web/app.js", "web/styles.css"):
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


if __name__ == "__main__":
    unittest.main()
