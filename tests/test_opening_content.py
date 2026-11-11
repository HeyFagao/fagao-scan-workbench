# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from fagao import opening_content as opening
from fagao.collectors import CollectorError, fetch_catalog, fetch_text
from fagao.server import WorkbenchServer
from fagao.store import AppError, Store
from fagao.__main__ import run_command


BID = "104500001"
IDS = [str(891010000 + i) for i in range(1, 15)]
BODY = "她打开窗户，发现昨夜丢失的钥匙挂在门边。" * 20
# Independent Node/OpenSSL AES-CBC fixture, not encrypted by the shipped adapter.
TRANSPORT_TEXT = '<p class="chapter">这是合成的测试正文，不对应真实小说。<span class="J_Num">9</span></p>'
TRANSPORT_KEYS = ["AAAAAAAAAAAAAAAAAAAAAA==", "EREREREREREREREREREREQ==", "IiIiIiIiIiIiIiIiIiIiIg=="]
TRANSPORT_CIPHER = "RERERERERERERERERERERFF2IJ+veWjjqF3Aa5ZV39MLGFN4f2O/S9YwdrAjBBHgYQoNDEecQh3e8EXrNsTHbJiuKMEGybfQS4KEt6QvXFsD8UX1yCmC4zlOc+w0ytPSu3RtkCq74hZvD6BhWDhpFdLJTW/vAZiWpflq57fYKgMVhZ6OxK46egVCVyRXVCBGOhT/zITtvLOK8PHjJc7ncIttcp14fWWrsa1RguIjJJxt1os7PH6Yh0DZ3a7Y348E"


def qstate(data, chapter_id=None, book_id=BID):
    path = f"/chapter/{book_id}/{chapter_id}/" if chapter_id else f"/book/{book_id}/catalog/"
    params = {"bookId": book_id, **({"chapterId": chapter_id} if chapter_id else {})}
    value = {"pageContext": {"hostname": "m.qidian.com", "urlPathname": path,
                            "routeParams": params, "pageProps": {"pageData": data}}}
    return '<script id="vite-plugin-ssr_pageContext" type="application/json">' + json.dumps(value) + "</script>"


def qsource(count=12, locked=None, declared=None):
    rows = [{"id": cid, "cN": f"第{i}章", "cnt": len(BODY), "sS": 0 if i == locked else 1}
            for i, cid in enumerate(IDS[:count], 1)]
    data = {"bookId": BID, "bookName": "测试书", "chapterTotalCnt": count, "vs": [{"vN": "正文", "cs": rows}]}
    pages = {}
    for i, cid in enumerate(IDS[:count], 1):
        pages[cid] = {"bookInfo": {"bookId": BID, "isVip": 1}, "riskInfo": {"returnCode": 0, "captchaURL": ""},
                      "chapterInfo": {"chapterId": cid, "chapterName": f"第{i}章", "content": "<p>" + BODY,
                                      "wordsCount": declared if declared is not None else len(BODY),
                                      "vipStatus": 0, "price": 0, "riskbe": {"be": 0}}}

    def fetch(url, **kwargs):
        if "/catalog/" in url:
            return qstate(data)
        cid = url.rstrip("/").rsplit("/", 1)[-1]
        return qstate(pages[cid], cid)
    return fetch, data, pages


def csource(count=12, locked=None, body=None):
    entries = []
    for i, cid in enumerate(IDS[:count], 1):
        icon = '<i class="icon-lock"></i>' if i == locked else ""
        entries.append(f'<li><a href="https://www.ciweimao.com/chapter/{cid}">{icon}第{i}章</a></li>')
    identity = f"<script>HB.book = {{book_id: {BID}, chapter_id: '', is_paid: 1}};</script>"
    catalog = identity + '<div class="book-catalog"><h3>测试书</h3><div class="book-chapter-box"><h4 class="sub-tit">公告与正文</h4><ul class="book-chapter-list">' + "".join(entries) + '</ul></div></div>'

    def fetch(url, **kwargs):
        if "/chapter-list/" in url:
            return catalog
        cid = url.rstrip("/").rsplit("/", 1)[-1]
        content = f'<p class="chapter">{BODY}<span class="J_Num">999999</span></p>' if body is None else body
        return f'''<script>HB.book = {{book_id: {BID}, chapter_id: {cid}, is_paid: 1}};</script>
<div id="J_BookCnt" data-id="{cid}"><div class="read-hd"><h1 class="chapter">第一章</h1><span>字数：{len(BODY)}</span></div>
<div id="J_BookRead"><div class="watermark"><p>不应进入正文</p></div>{content}</div></div>'''
    return fetch, catalog


def encrypted_csource(session=None, response=None, page_change=None):
    fetch, _ = csource(count=1, body="")
    def source(url, **kwargs):
        if url.endswith("/ajax_get_session_code"):
            return json.dumps(session if session is not None else {"code": 100000, "chapter_access_key": "Aa"})
        if url.endswith("/get_book_chapter_detail_info"):
            return json.dumps(response if response is not None else {"code": 100000, "chapter_content": TRANSPORT_CIPHER, "encryt_keys": TRANSPORT_KEYS})
        html = fetch(url)
        if "/chapter/" in url:
            html += '<script>HB.userinfo = {reader_id: 0}; HB.config.bookread.useImage = 0;</script>'
            html = html.replace(f"字数：{len(BODY)}", f"字数：{len('这是合成的测试正文，不对应真实小说。')}")
            if page_change:
                html = page_change(html)
        return html
    return source


class OpeningContentTest(unittest.TestCase):
    def test_http_error_response_is_closed_before_becoming_collector_error(self):
        url = f"https://m.qidian.com/book/{BID}/catalog/"
        for status, state in ((403, "forbidden"), (500, "network_error")):
            response = io.BytesIO(b"upstream error")
            failure = urllib.error.HTTPError(url, status, "error", {}, response)
            with self.subTest(status=status), patch.object(opening.network, "build_opener") as factory, patch.object(opening, "REQUEST_INTERVAL", 0):
                factory.return_value.open.side_effect = failure
                with self.assertRaises(CollectorError) as error:
                    opening._fetch(url)
                self.assertEqual(error.exception.state, state)
                self.assertTrue(response.closed)

    def test_normal_anonymous_ciweimao_transport_matches_independent_aes_fixture(self):
        self.assertEqual(opening._ciweimao_decode(TRANSPORT_CIPHER, TRANSPORT_KEYS, "Aa"), TRANSPORT_TEXT)
        with patch.object(opening, "_fetch", side_effect=encrypted_csource()) as fetch:
            result = opening.fetch_book_text("ciweimao", BID, max_chapters=1)
        self.assertEqual(len(fetch.call_args_list), 4)
        page, session, body = fetch.call_args_list[1:]
        self.assertIs(page.kwargs["opener"], session.kwargs["opener"])
        self.assertIs(session.kwargs["opener"], body.kwargs["opener"])
        self.assertEqual(session.kwargs["form"], {"chapter_id": IDS[0]})
        self.assertEqual(body.kwargs["form"], {"chapter_id": IDS[0], "chapter_access_key": "Aa"})
        self.assertIn("这是合成的测试正文", result["text"])
        self.assertNotIn("J_Num", result["text"])
        self.assertNotIn("chapter_access_key", json.dumps(result))
        self.assertTrue(result["coverage_complete"])

    def test_ciweimao_api_refusal_prevents_content_request(self):
        for session in ({"code": 200001}, {"code": 100000, "chapter_access_key": "Aa", "needPay": 1},
                        {"code": 100000, "chapter_access_key": "Aa", "chapter_id": IDS[1]},
                        {"code": 100000, "chapter_access_key": ""}):
            with self.subTest(session=session), patch.object(opening, "_fetch", side_effect=encrypted_csource(session=session)) as fetch:
                with self.assertRaises(CollectorError):
                    opening.fetch_book_text("ciweimao", BID, max_chapters=1)
            self.assertFalse(any(c.args[0].endswith("get_book_chapter_detail_info") for c in fetch.call_args_list))

    def test_ciweimao_content_refusal_invalid_payload_and_identity_fail_closed(self):
        for response in ({"code": 200002}, {"code": 100000, "price": 1},
                         {"code": 100000, "captcha_url": "https://example.invalid/"},
                         {"code": 100000, "book_id": "99999999"},
                         {"code": 100000, "chapter_content": TRANSPORT_CIPHER, "encryt_keys": []},
                         {"code": 100000, "chapter_content": "invalid", "encryt_keys": TRANSPORT_KEYS}):
            with self.subTest(response=response), patch.object(opening, "_fetch", side_effect=encrypted_csource(response=response)), self.assertRaises(CollectorError):
                opening.fetch_book_text("ciweimao", BID, max_chapters=1)

    def test_ciweimao_image_or_nonanonymous_reader_cannot_use_transport(self):
        for old, new in (("reader_id: 0", "reader_id: 123"), ("useImage = 0", "useImage = 1")):
            with self.subTest(old=old), patch.object(opening, "_fetch", side_effect=encrypted_csource(page_change=lambda html: html.replace(old, new))) as fetch:
                with self.assertRaises(CollectorError):
                    opening.fetch_book_text("ciweimao", BID, max_chapters=1)
            self.assertEqual(len(fetch.call_args_list), 2)

    def test_post_redirects_and_unexpected_post_fields_are_rejected(self):
        url = "https://www.ciweimao.com/chapter/ajax_get_session_code"
        request = urllib.request.Request(url, data=b"chapter_id=891010001")
        with self.assertRaises(CollectorError):
            opening._OfficialRedirect().redirect_request(request, None, 302, "", {}, url)
        with patch.object(opening.network, "build_opener") as opener:
            for form in ({"chapter_id": IDS[0], "buy": 1}, {"chapter_id": "bad"}):
                with self.subTest(form=form), self.assertRaises(CollectorError):
                    opening._fetch(url, form=form)
            opener.assert_not_called()

    def test_cli_catalog_dispatch_and_fetch_text_share_the_hard_limit(self):
        for platform in opening.PLATFORMS:
            task = {"platform": platform, "book_id": BID, "snapshot_id": "snapshot"}
            store = SimpleNamespace(directory="local-data", task=lambda unused: task)
            with patch("fagao.collectors.fetch_catalog", return_value={"platform": platform}) as fetch:
                self.assertEqual(run_command(SimpleNamespace(command="catalog", task="task"), store), {"platform": platform})
                fetch.assert_called_once_with(platform, BID, "local-data")
            with patch.object(opening, "_fetch") as fetch:
                for count, start, all_flag in ((10, 1, True), (11, 1, False), (3, 2, False)):
                    args = SimpleNamespace(command="fetch-text", task="task", chapters=count, start=start, all=all_flag)
                    with self.subTest(platform=platform, args=args), self.assertRaises(CollectorError):
                        run_command(args, store)
                fetch.assert_not_called()

    def test_qidian_official_order_and_only_ten_returned(self):
        source, _, _ = qsource()
        with patch.object(opening, "_fetch", side_effect=source):
            result = opening.catalog("qidian", BID)
        self.assertEqual(result["catalog_chapters"], 12)
        self.assertEqual([c["id"] for c in result["chapters"]], IDS[:10])
        self.assertEqual((result["download_start"], result["download_limit"]), (1, 10))

    def test_ciweimao_official_order_and_lock_marker(self):
        source, _ = csource(locked=2)
        with patch.object(opening, "_fetch", side_effect=source):
            result = opening.catalog("ciweimao", BID)
        self.assertEqual(result["catalog_chapters"], 12)
        self.assertEqual([c["index"] for c in result["chapters"]], list(range(1, 11)))
        self.assertFalse(result["chapters"][0]["locked"])
        self.assertTrue(result["chapters"][1]["locked"])

    def test_all_range_and_type_bypasses_rejected_before_network(self):
        with patch.object(opening, "_fetch") as fetch:
            for platform in opening.PLATFORMS:
                for count, start in [(None, 1), (11, 1), (0, 1), (-1, 1), (3, 2), (3, 0), (True, 1), (3, True), ("10", 1), (10.0, 1), (3, "1")]:
                    with self.subTest(platform=platform, count=count, start=start):
                        with self.assertRaises(CollectorError) as error:
                            fetch_text(platform, BID, None, max_chapters=count, start_chapter=start)
                        self.assertEqual(error.exception.state, "invalid_range")
            fetch.assert_not_called()

    def test_prefix_never_fetches_eleventh_chapter_or_claims_whole_work(self):
        source, _, _ = qsource()
        with patch.object(opening, "_fetch", side_effect=source) as fetch:
            result = fetch_text("qidian", BID, None, max_chapters=10)
        self.assertEqual(len(result["chapters"]), 10)
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["complete"])
        self.assertFalse(any(IDS[10] in c.args[0] for c in fetch.call_args_list))

    def test_short_catalog_uses_actual_request_count(self):
        source, _, _ = qsource(count=2)
        with patch.object(opening, "_fetch", side_effect=source):
            result = opening.fetch_book_text("qidian", BID, max_chapters=10)
        self.assertEqual(result["requested_chapters"], 2)
        self.assertEqual(result["catalog_chapters"], 2)
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["complete"])

    def test_locked_position_stops_without_requesting_it_or_a_replacement(self):
        for platform, source in [("qidian", qsource(locked=2)[0]), ("ciweimao", csource(locked=2)[0])]:
            with self.subTest(platform=platform), patch.object(opening, "_fetch", side_effect=source) as fetch:
                result = opening.fetch_book_text(platform, BID, max_chapters=10)
            self.assertEqual([c["index"] for c in result["chapters"]], [1])
            self.assertEqual(result["missing"][0]["index"], 2)
            self.assertEqual(len(fetch.call_args_list), 2)
            self.assertFalse(result["coverage_complete"])

    def test_qidian_catalog_identity_duplicates_and_count_checked(self):
        for mutate in [lambda d: d.update(bookId="999999999"),
                       lambda d: d.update(chapterTotalCnt=100),
                       lambda d: d["vs"][0]["cs"][1].update(id=IDS[0])]:
            source, data, _ = qsource()
            mutate(data)
            with patch.object(opening, "_fetch", side_effect=source), self.assertRaises(CollectorError):
                opening.catalog("qidian", BID)

    def test_qidian_route_host_and_reader_identity_checked(self):
        source, _, pages = qsource(count=1)
        for key, bad in [("hostname", "evil.example"), ("urlPathname", "/chapter/99999999/891010001/"),
                         ("routeParams", {"bookId": BID, "chapterId": IDS[1]})]:
            raw = json.loads(qstate(pages[IDS[0]], IDS[0]).split(">", 1)[1].rsplit("<", 1)[0])
            raw["pageContext"][key] = bad
            html = '<script id="vite-plugin-ssr_pageContext">' + json.dumps(raw) + '</script>'
            with self.subTest(key=key), self.assertRaises(CollectorError):
                opening._qidian_data(html, BID, IDS[0])
        pages[IDS[0]]["bookInfo"]["bookId"] = IDS[0]
        with patch.object(opening, "_fetch", side_effect=source), self.assertRaises(CollectorError):
            opening.fetch_book_text("qidian", BID, max_chapters=1)

    def test_paid_risk_encoded_empty_and_challenge_pages_not_saved(self):
        alterations = [("vipStatus", 1), ("price", 10), ("riskbe", {"be": 1}),
                       ("randomFont", "font-url"), ("content", "<p>\ue001"), ("content", ""),
                       ("content", "<p>请完成安全验证</p>")]
        for key, value in alterations:
            source, _, pages = qsource(count=1)
            pages[IDS[0]]["chapterInfo"][key] = value
            with self.subTest(key=key, value=value), patch.object(opening, "_fetch", side_effect=source), self.assertRaises(CollectorError):
                opening.fetch_book_text("qidian", BID, max_chapters=1)
        source, _, pages = qsource(count=1)
        pages[IDS[0]]["riskInfo"]["captchaURL"] = "https://captcha.example/"
        with patch.object(opening, "_fetch", side_effect=source), self.assertRaises(CollectorError):
            opening.fetch_book_text("qidian", BID, max_chapters=1)

    def test_source_shortfall_is_preserved_but_remaining_opening_window_is_read(self):
        source, _, _ = qsource(declared=len(BODY) + 10)
        with patch.object(opening, "_fetch", side_effect=source) as fetch:
            result = opening.fetch_book_text("qidian", BID, max_chapters=10)
        self.assertEqual(len(fetch.call_args_list), 11)
        self.assertEqual(result["integrity"]["status"], "partial")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(len(result["chapters"]), 10)
        self.assertFalse(any(IDS[10] in call.args[0] for call in fetch.call_args_list))

    def test_missing_exact_word_count_is_not_verified(self):
        source, data, pages = qsource(count=1)
        data["vs"][0]["cs"][0].pop("cnt")
        pages[IDS[0]]["chapterInfo"]["wordsCount"] = "0.5万"
        with patch.object(opening, "_fetch", side_effect=source):
            result = opening.fetch_book_text("qidian", BID, max_chapters=1)
        self.assertEqual(result["integrity"]["status"], "unverified")
        self.assertFalse(result["coverage_complete"])

    def test_cancel_and_membership_interruption_keep_the_first_chapter(self):
        for code in ("cancelled", "membership_expired"):
            source, _, _ = qsource()
            def progress(message):
                if "正在读取第 2 章" in message:
                    raise AppError(code, "已暂停")
            with self.subTest(code=code), patch.object(opening, "_fetch", side_effect=source):
                result = opening.fetch_book_text("qidian", BID, max_chapters=10, progress=progress)
            self.assertEqual(len(result["chapters"]), 1)
            self.assertFalse(result["coverage_complete"])
            self.assertEqual(result["cancelled"], code == "cancelled")
            if code.startswith("membership_"):
                self.assertEqual(result["interrupted_reason"], code)

    def test_ciweimao_body_excludes_watermark_and_comment_counts(self):
        source, _ = csource(count=1)
        with patch.object(opening, "_fetch", side_effect=source):
            result = opening.fetch_book_text("ciweimao", BID, max_chapters=1)
        self.assertNotIn("999999", result["text"])
        self.assertNotIn("不应进入正文", result["text"])
        self.assertEqual(result["integrity"]["actual_characters"], len(BODY))

    def test_ciweimao_wrong_identity_and_host_are_rejected(self):
        source, raw = csource(count=1)
        for html in (raw.replace(BID, "999999999"), raw.replace("www.ciweimao.com/chapter", "evil.example/chapter")):
            with patch.object(opening, "_fetch", return_value=html), self.assertRaises(CollectorError):
                opening.catalog("ciweimao", BID)
        def wrong_chapter(url, **kwargs):
            html = source(url)
            return html.replace(f'data-id="{IDS[0]}"', 'data-id="99999999"') if "/chapter/" in url else html
        with patch.object(opening, "_fetch", side_effect=wrong_chapter), self.assertRaises(CollectorError):
            opening.fetch_book_text("ciweimao", BID, max_chapters=1)

    def test_ciweimao_empty_and_paywall_not_marked_success(self):
        for body in ("", "<p>本章为VIP章节，请先订阅！</p>"):
            source, _ = csource(count=1, body=body)
            with self.subTest(body=body), patch.object(opening, "_fetch", side_effect=source), self.assertRaises(CollectorError):
                opening.fetch_book_text("ciweimao", BID, max_chapters=1)

    def test_paragraphs_scripts_hidden_nodes_and_ordinary_spans(self):
        text = opening._plain_content('<p>第一段<span>正文强调</span><p>第二段<script>忽略</script><span hidden>忽略</span><p style="display:none">忽略')
        self.assertEqual(text, "第一段正文强调\n\n第二段")

    def test_direct_and_redirect_urls_cannot_leave_original_official_resource(self):
        url = f"https://m.qidian.com/chapter/{BID}/{IDS[0]}/"
        handler = opening._OfficialRedirect()
        request = urllib.request.Request(url)
        for bad in ("https://evil.example/x", "https://m.qidian.com.evil.example/x", url.replace("https", "http"),
                    url.replace(IDS[0], IDS[1]), url + "?next=other", "https://www.ciweimao.com/chapter/123456789"):
            with self.subTest(url=bad), self.assertRaises(CollectorError):
                handler.redirect_request(request, None, 302, "", {}, bad)

    def test_cache_tampering_cannot_authorize_eleventh_chapter(self):
        source, _, _ = qsource()
        with tempfile.TemporaryDirectory() as directory, patch.object(opening, "_fetch", side_effect=source) as fetch:
            opening.catalog("qidian", BID, directory)
            path = opening._cache_path(directory, "qidian", BID)
            record = json.loads(path.read_text())
            first = record["value"]["chapters"][0]
            first.update(id=IDS[10], item_id=IDS[10], url=f"https://m.qidian.com/chapter/{BID}/{IDS[10]}/")
            path.write_text(json.dumps(record))
            result = opening.fetch_book_text("qidian", BID, directory, max_chapters=1)
        self.assertEqual(result["chapters"][0]["id"], IDS[0])
        self.assertFalse(any(IDS[10] in call.args[0] for call in fetch.call_args_list))
        self.assertEqual(sum("/catalog/" in call.args[0] for call in fetch.call_args_list), 2)

    def test_cache_is_bounded_and_does_not_store_body(self):
        source, _, _ = qsource(count=1)
        with tempfile.TemporaryDirectory() as directory, patch.object(opening, "_fetch", side_effect=source):
            result = opening.catalog("qidian", BID)
            for i in range(opening.CACHE_MAX_FILES + 5):
                opening._cache_write(directory, "qidian", str(10000000 + i), result)
            records = list((Path(directory) / "cache" / "opening-content").glob("*.json"))
            self.assertLessEqual(len(records), opening.CACHE_MAX_FILES)
            self.assertFalse(any(BODY in path.read_text() for path in records))


class OpeningHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(cls.tmp.name)
        cls.server = WorkbenchServer(("127.0.0.1", 0), cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]
        cls.snapshots = {platform: cls.store.save_snapshot({"platform": platform, "books": [{"id": BID, "title": "测试书"}]})
                         for platform in opening.PLATFORMS}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def post(self, endpoint, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", endpoint, json.dumps(payload), {"Content-Type": "application/json", "X-Fagao-Request": "1"})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_http_catalog_dispatch_and_identity(self):
        for platform, source in [("qidian", qsource()[0]), ("ciweimao", csource()[0])]:
            with self.subTest(platform=platform), patch.object(opening, "_fetch", side_effect=source):
                status, result = self.post("/api/catalog", {"snapshot_id": self.snapshots[platform]["id"], "book_id": BID})
            self.assertEqual(status, 200)
            self.assertEqual(result["platform"], platform)
            self.assertEqual(result["download_limit"], 10)
            self.assertEqual(len(result["chapters"]), 10)

    def test_http_range_bypasses_rejected_before_start(self):
        with patch.object(opening, "_fetch") as fetch:
            for platform in opening.PLATFORMS:
                for options in ({"all": True}, {"chapters": 11}, {"start_chapter": 2}, {"chapters": True}, {"chapters": None}):
                    with self.subTest(platform=platform, options=options):
                        status, result = self.post("/api/fetch-text", {"snapshot_id": self.snapshots[platform]["id"], "book_id": BID, **options})
                        self.assertEqual(status, 400)
                        self.assertEqual(result["error"]["code"], "invalid_range")
            fetch.assert_not_called()

    def test_http_new_platform_job_preserves_saved_prefix(self):
        source, _, _ = qsource(locked=3)
        with patch.object(opening, "_fetch", side_effect=source):
            status, result = self.post("/api/fetch-text", {"snapshot_id": self.snapshots["qidian"]["id"], "book_id": BID, "chapters": 10})
            self.assertEqual(status, 200)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job = self.store.job(result["job_id"])
                if job["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.01)
        self.assertEqual(job["status"], "completed", job)
        self.assertTrue(job["partial"])
        self.assertFalse(job["text"]["complete"])
        self.assertEqual([c["index"] for c in job["text"]["chapters"]], [1, 2])
        self.assertEqual(job["text"]["platform"], "qidian")


if __name__ == "__main__":
    unittest.main()
