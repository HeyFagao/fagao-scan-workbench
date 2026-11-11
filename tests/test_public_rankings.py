# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import hashlib
import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
import urllib.error

from fagao.collectors import CollectorError
from fagao import public_rankings as public


def fixture(channel="female", source_date="2026-11-09"):
    return {"channel": channel, "board": "new", "date": source_date,
            "categories": [{"name": "测试分类", "books": [
                {"title": "样本甲", "author": "作者甲", "reads": "9.4万", "intro": "测试简介",
                 "cover": "https://example.com/cover.jpg", "url": "https://fanqienovel.com/page/123456"},
                {"title": "样本乙", "author": "作者乙", "reads": "0", "intro": "",
                 "cover": "http://127.0.0.1/private", "url": "https://fanqienovel.com/page/123457"}]}]}


def normalize(document):
    return public.normalize_snapshot(document, "female", "2026-11-09",
        source_url=public.RAW_ROOT + "data/female/new/20261109.json",
        source_revision="git-blob-sha1:" + "a" * 40, fetched_at="2026-11-09T12:00:00+00:00")


def fake_fetch(path):
    channel = path.split("/")[1]
    if path.startswith("api/"):
        data = {"channel": channel, "board": "new", "date": "2026-11-09", "types": [
            {"type": "all", "category_count": 1, "book_count": 2}]}
    else:
        value = path.rsplit("/", 1)[1][:8]
        data = fixture(channel, f"{value[:4]}-{value[4:6]}-{value[6:8]}")
    return {"data": data, "url": public.RAW_ROOT + path,
            "source_revision": "git-blob-sha1:" + "a" * 40, "fetched_at": "2026-11-09T12:00:00+00:00"}


class Response(io.BytesIO):
    def __init__(self, content, url, headers=None):
        super().__init__(content)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


class PublicRankingsTests(unittest.TestCase):
    def test_normalize_preserves_observed_metrics_and_unknown_rank_words(self):
        snapshot = normalize(fixture())[0]
        self.assertEqual(snapshot["source_id"], public.SOURCE_ID)
        self.assertEqual(snapshot["mode"], "long")
        self.assertFalse(snapshot["complete"])
        self.assertIsNone(snapshot["source_total"])
        self.assertEqual(snapshot["data_date"], "2026-11-09")
        self.assertEqual(snapshot["provenance"]["scope"], "female/new/测试分类")
        self.assertEqual(snapshot["books"][0]["metrics"], [{"name": "在读", "value": "9.4万", "unit": "人"}])
        self.assertEqual(snapshot["books"][1]["metrics"][0]["value"], "0")
        self.assertEqual(snapshot["books"][1]["cover_url"], "")
        for position, book in enumerate(snapshot["books"], 1):
            self.assertIsNone(book["rank"])
            self.assertEqual(book["source_position"], position)
            self.assertNotIn("word_count", book)
            self.assertNotIn("字数", str(book["metrics"]))

    def test_missing_reads_not_converted_to_zero(self):
        data = fixture(); data["categories"][0]["books"][0].pop("reads")
        self.assertEqual(normalize(data)[0]["books"][0]["metrics"], [])

    def test_wrong_date_channel_reading_and_bad_identity_rejected(self):
        for key, value in (("date", "2026-11-08"), ("date", "2026-02-30"),
                           ("channel", "male"), ("board", "read")):
            with self.subTest(key=key, value=value):
                data = fixture(); data[key] = value
                with self.assertRaises(CollectorError): normalize(data)
        for url in ("https://example.com/page/123456", "http://fanqienovel.com/page/123456", "", "https://fanqienovel.com.evil/page/123456"):
            data = fixture(); data["categories"][0]["books"][0]["url"] = url
            with self.assertRaises(CollectorError): normalize(data)

    def test_duplicate_title_does_not_merge_distinct_ids(self):
        data = fixture(); data["categories"][0]["books"][1]["title"] = "样本甲"
        self.assertEqual(len(normalize(data)[0]["books"]), 2)

    def test_identical_id_deduplicated_but_conflicting_id_rejected(self):
        data = fixture(); rows = data["categories"][0]["books"]
        rows.append(deepcopy(rows[0]))
        result = normalize(data)[0]
        self.assertEqual(len(result["books"]), 2)
        self.assertIn("重复作品", result["warnings"][-1])
        rows[-1]["reads"] = "100万"
        with self.assertRaises(CollectorError): normalize(data)

    def test_invalid_structure_and_coverage_change_rejected(self):
        for changed in ([], {}, [None]):
            data = fixture(); data["categories"] = changed
            with self.assertRaises(CollectorError): normalize(data)
        data = fixture(); data["categories"].append(deepcopy(data["categories"][0]))
        with self.assertRaises(CollectorError): normalize(data)
        data = fixture(); data["categories"][0]["books"] *= 11
        with self.assertRaises(CollectorError): normalize(data)
        data = fixture(); data["categories"][0]["books"][0]["reads"] = "预计100万"
        with self.assertRaises(CollectorError): normalize(data)

    def test_indexes_validate_coverage_and_do_not_follow_embedded_urls(self):
        document = fake_fetch("api/female/new/index.json")["data"]
        document["types"][0]["url"] = "http://127.0.0.1/secret"
        self.assertEqual(public._index(document, "female")[1:], (1, 2))
        for count in (True, 0, 5000):
            data = deepcopy(document); data["types"][0]["book_count"] = count
            with self.assertRaises(CollectorError): public._index(data, "female")

    def test_sync_six_fixed_requests_exact_seven_days_and_old_first(self):
        messages = []
        with patch.object(public, "_fetch_json", side_effect=fake_fetch) as mocked:
            result = public.sync_public_rankings(messages.append)
        self.assertEqual(mocked.call_count, 6)
        paths = {call.args[0] for call in mocked.call_args_list}
        self.assertEqual(paths, {f"api/{c}/new/index.json" for c in ("female", "male")} |
                         {f"data/{c}/new/{day}.json" for c in ("female", "male") for day in ("20261102", "20261109")})
        self.assertEqual([s["data_date"] for s in result["snapshots"]], ["2026-11-02"] * 2 + ["2026-11-09"] * 2)
        self.assertEqual(len(messages), 4)
        self.assertEqual(result["failures"], [])
        self.assertEqual([c["history_date"] for c in result["coverage"]], ["2026-11-02"] * 2)

    def test_missing_history_keeps_current_and_never_backfills_zero(self):
        def fetch(path):
            if "20261102" in path: raise CollectorError("missing", "not_found")
            return fake_fetch(path)
        with patch.object(public, "_fetch_json", side_effect=fetch): result = public.sync_public_rankings()
        self.assertEqual(len(result["snapshots"]), 2)
        self.assertTrue(all(s["data_date"] == "2026-11-09" for s in result["snapshots"]))
        self.assertTrue(all(c["history_date"] is None for c in result["coverage"]))
        self.assertEqual(len(result["failures"]), 2)

    def test_missing_current_does_not_publish_old_snapshot_as_latest(self):
        def fetch(path):
            if path.startswith("data/") and "20261109" in path: raise CollectorError("missing", "not_found")
            return fake_fetch(path)
        with patch.object(public, "_fetch_json", side_effect=fetch): result = public.sync_public_rankings()
        self.assertEqual(result["snapshots"], [])
        self.assertEqual(result["coverage"], [])

    def test_index_and_current_file_mismatch_discards_channel(self):
        def fetch(path):
            record = fake_fetch(path)
            if path.startswith("api/"): record["data"]["types"][0]["book_count"] = 3
            return record
        with patch.object(public, "_fetch_json", side_effect=fetch): result = public.sync_public_rankings()
        self.assertEqual(result["snapshots"], [])
        self.assertEqual(len(result["failures"]), 2)

    def test_caller_cancellation_propagates(self):
        def cancel(message): raise ValueError("cancelled")
        with patch.object(public, "_fetch_json", side_effect=fake_fetch):
            with self.assertRaisesRegex(ValueError, "cancelled"): public.sync_public_rankings(cancel)

    def test_fetch_limits_and_exact_content_revision(self):
        path = "api/female/new/index.json"; url = public.RAW_ROOT + path
        content = b'{"date":"2026-11-09"}'
        with patch.object(public.network, "build_opener") as builder:
            builder.return_value.open.return_value = Response(content, url)
            result = public._fetch_json(path)
            self.assertEqual(builder.return_value.open.call_args.kwargs["timeout"], public.TIMEOUT_SECONDS)
        expected = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        self.assertEqual(result["source_revision"], "git-blob-sha1:" + expected)
        for bad in ("../secret", "api/male/read/index.json", "https://evil.example/x", "data/female/new/20261109.json?x=1"):
            with self.assertRaises(CollectorError): public._fetch_json(bad)
        for content, headers in ((b"bad-json", {}), (b"[]", {}), (b"{}", {"Content-Length": str(public.MAX_BYTES + 1)})):
            with patch.object(public.network, "build_opener") as builder:
                builder.return_value.open.return_value = Response(content, url, headers)
                with self.assertRaises(CollectorError): public._fetch_json(path)
        with patch.object(public, "MAX_BYTES", 4), patch.object(public.network, "build_opener") as builder:
            builder.return_value.open.return_value = Response(b'{"a":123}', url)
            with self.assertRaises(CollectorError): public._fetch_json(path)

    def test_redirect_and_socket_errors_never_fall_back_to_untrusted_urls(self):
        with self.assertRaises(CollectorError):
            public._NoRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/")
        with patch.object(public.network, "build_opener") as builder:
            builder.return_value.open.side_effect = TimeoutError()
            with self.assertRaises(CollectorError) as caught: public._fetch_json("api/male/new/index.json")
        self.assertEqual(caught.exception.state, "network_error")


if __name__ == "__main__":
    unittest.main()
