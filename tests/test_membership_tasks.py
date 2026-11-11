# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Membership boundaries for real HTTP requests and partially completed jobs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fagao import collectors, fanqie_content, network, public_rankings
from fagao.batch import collect_all_categories
from fagao.store import AppError


def expired():
    return AppError("membership_expired", "会员已到期，请续费后继续。", 403)


class NetworkGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stopped = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.server.paths.append(self.path)
                if self.path == "/redirect":
                    owner.stopped.set()
                    self.send_response(302)
                    self.send_header("Location", "/after-expiry")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.paths = []
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(lambda: (self.server.shutdown(), thread.join(timeout=2)))
        for replacement in (patch.object(network, "_directory", Path(self.temp.name)),
                            patch.object(network, "_system_proxies", return_value={}),
                            patch.object(network, "_system_bypass", return_value=False)):
            replacement.start()
            self.addCleanup(replacement.stop)

    def check(self):
        if self.stopped.is_set():
            raise expired()

    def read(self, path="/"):
        with network.build_opener().open(self.url + path, timeout=2) as response:
            return response.read()

    def test_permit_ends_before_next_request_without_disabling_diagnostics(self):
        with network.authorized_requests(self.check):
            self.assertEqual(self.read(), b"ok")
            self.stopped.set()
            with self.assertRaises(AppError) as caught:
                self.read("/blocked")
            self.assertEqual(caught.exception.code, "membership_expired")
        self.assertEqual(self.server.paths, ["/"])
        self.assertEqual(self.read("/diagnostic"), b"ok")

    def test_redirect_is_rechecked_and_never_sent_after_expiry(self):
        with network.authorized_requests(self.check), self.assertRaises(AppError) as caught:
            self.read("/redirect")
        self.assertEqual(caught.exception.code, "membership_expired")
        self.assertEqual(self.server.paths, ["/redirect"])

    def test_authorization_transport_suppression_restores_task_guard(self):
        self.stopped.set()
        with network.authorized_requests(self.check):
            with network.without_authorization():
                self.assertFalse(network.has_authorization())
                self.assertEqual(self.read("/permit"), b"ok")
            self.assertTrue(network.has_authorization())
            with self.assertRaises(AppError):
                self.read("/source")
        self.assertEqual(self.server.paths, ["/permit"])

    def test_pending_thread_checks_captured_account_before_starting_work(self):
        release = threading.Event()
        entered = threading.Event()
        work = Mock()

        def blocker():
            entered.set()
            release.wait(timeout=3)

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(blocker)
            self.assertTrue(entered.wait(timeout=1))
            with network.authorized_requests(self.check):
                future = pool.submit(network.authorized_callable(work))
            self.stopped.set()
            release.set()
            first.result(timeout=2)
            with self.assertRaises(AppError):
                future.result(timeout=2)
        work.assert_not_called()
        self.assertFalse(network.has_authorization())


class PartialTaskTests(unittest.TestCase):
    def setUp(self):
        self.stopped = False

    def check(self):
        if self.stopped:
            raise expired()

    def catalog(self):
        return {"platform": "fanqie", "book_id": "123456789", "title": "测试正文",
                "source": "https://fanqienovel.com/page/123456789", "status": "已完结",
                "catalog_chapters": 3, "catalog_complete": True,
                "chapters": [{"item_id": str(987654320 + i), "index": i,
                              "title": f"第 {i} 章", "url": f"https://fanqienovel.com/reader/{987654320+i}"}
                             for i in range(1, 4)]}

    def chapter(self, book_id, item, data_dir):
        return {"title": item["title"], "text": "真实已取得的合成测试正文。" * 8,
                "integrity": {"status": "verified", "actual_characters": 104,
                              "declared_word_count": 100, "reason": "已核对字数"}}

    def test_expiry_between_chapters_preserves_first_chapter_and_reason(self):
        def read_chapter(*args):
            result = self.chapter(*args)
            self.stopped = True
            return result

        with patch.object(fanqie_content, "catalog", return_value=self.catalog()), \
                patch.object(fanqie_content, "_chapter", side_effect=read_chapter) as read, \
                network.authorized_requests(self.check):
            result = fanqie_content.fetch_book_text("123456789", max_chapters=None)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertIn("真实已取得", result["text"])
        self.assertEqual(result["interrupted_reason"], "membership_expired")
        self.assertFalse(result["cancelled"])
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["complete"])

    def test_transport_expiry_inside_chapter_keeps_completed_chapters(self):
        count = 0

        def read_chapter(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise expired()
            return self.chapter(*args)

        with patch.object(fanqie_content, "catalog", return_value=self.catalog()), \
                patch.object(fanqie_content, "_chapter", side_effect=read_chapter):
            result = fanqie_content.fetch_book_text("123456789", max_chapters=None)
        self.assertEqual(count, 2)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertEqual(result["interrupted_reason"], "membership_expired")
        self.assertEqual(result["missing"], [])

    def test_no_text_expiry_raises_original_membership_error(self):
        self.stopped = True
        with patch.object(fanqie_content, "catalog") as source, \
                network.authorized_requests(self.check), self.assertRaises(AppError) as caught:
            fanqie_content.fetch_book_text("123456789")
        source.assert_not_called()
        self.assertEqual(caught.exception.code, "membership_expired")

    def test_batch_stops_after_saved_category_instead_of_treating_expiry_as_source_failure(self):
        saved = []

        def load(options):
            saved.append(options["category"])
            self.stopped = True
            return {"id": "saved-first", "platform": "fanqie", "mode": "long",
                    "channel": "女频", "board": "新书榜", "category": "古言脑洞",
                    "categories": ["古言脑洞", "悬疑"], "complete": True}

        with network.authorized_requests(self.check), self.assertRaises(AppError) as caught:
            collect_all_categories(SimpleNamespace(directory=Path("unused")), {}, load_category=load)
        self.assertEqual(saved, ["古言脑洞"])
        self.assertEqual(caught.exception.code, "membership_expired")

    def test_detail_worker_does_not_swallow_membership_denial_or_retry(self):
        data = {"rank": {"book_list": [{"bookId": "123456789", "currentPos": 1}], "rankVersion": "1700000000"}}
        html = '<a href="/rank/0_1_253">古言脑洞</a><script>window.__INITIAL_STATE__=' + json.dumps(data) + '</script>'

        def fetch(url, **kwargs):
            if "/rank/" in url:
                return html
            raise expired()

        with patch.object(collectors, "_fetch", side_effect=fetch) as request, \
                network.authorized_requests(self.check), self.assertRaises(AppError) as caught:
            collectors.collect("fanqie", {"max_pages": 1}, None)
        self.assertEqual(caught.exception.code, "membership_expired")
        self.assertEqual(request.call_count, 2)

    def test_official_heiyan_path_never_starts_legacy_browser(self):
        with patch.object(collectors, "_worker") as worker, network.authorized_requests(self.check):
            for operation in (lambda: collectors.collect("heiyan", {}, Path("unused")),
                              lambda: collectors.auth("heiyan", "login", Path("unused"))):
                with self.assertRaises(collectors.CollectorError) as caught:
                    operation()
                self.assertEqual(caught.exception.state, "external_link")
        worker.assert_not_called()


def public_fixture(path):
    channel = path.split("/")[1]
    today = datetime.now(timezone.utc).date().isoformat()
    if path.startswith("api/"):
        data = {"channel": channel, "board": "new", "date": today,
                "types": [{"type": "all", "category_count": 1, "book_count": 1}]}
    else:
        day = path.rsplit("/", 1)[1][:8]
        data = {"channel": channel, "board": "new", "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}",
                "categories": [{"name": "测试分类", "books": [{"title": "测试作品", "author": "测试作者",
                    "reads": "1万", "intro": "测试简介", "url": "https://fanqienovel.com/page/123456789"}]}]}
    return {"data": data, "url": public_rankings.RAW_ROOT + path,
            "source_revision": "git-blob-sha1:" + "a" * 40,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


class PublicTaskTests(unittest.TestCase):
    def test_expiry_after_index_stops_following_file_requests(self):
        stopped = threading.Event()

        def check():
            if stopped.is_set():
                raise expired()

        def fetch(path):
            result = public_fixture(path)
            stopped.set()
            return result

        with patch.object(public_rankings, "_fetch_json", side_effect=fetch) as request, \
                network.authorized_requests(check):
            result = public_rankings.sync_public_rankings()
        self.assertTrue(request.call_count)
        self.assertTrue(all(call.args[0].startswith("api/") for call in request.call_args_list))
        self.assertEqual(result["interrupted_reason"], "membership_expired")
        self.assertEqual(result["failures"], [])

    def test_expiry_during_progress_preserves_downloaded_snapshot(self):
        def progress(message):
            # Wait for a current snapshot: history without a matching current
            # feed is intentionally omitted by the existing source contract.
            if datetime.now(timezone.utc).date().isoformat() in message:
                raise expired()

        with patch.object(public_rankings, "_fetch_json", side_effect=public_fixture):
            result = public_rankings.sync_public_rankings(progress)
        self.assertTrue(result["snapshots"])
        self.assertEqual(result["interrupted_reason"], "membership_expired")
        self.assertEqual(result["failures"], [])


if __name__ == "__main__":
    unittest.main()
