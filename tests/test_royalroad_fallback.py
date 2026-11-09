"""RST public-feed validation and fallback publication, without external I/O."""
from datetime import datetime, timedelta, timezone
import io
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from fagao.collectors import CollectorError
from fagao.royalroad_fallback import (API_URL, MAX_BYTES, _fetch_document,
                                     collect_royalroad_fallback, normalize_rst_snapshot)


def fixture(count=50):
    return {
        "snapshot": {"id": 31000, "captured_at": datetime.now(timezone.utc).isoformat(),
                     "total_items": count, "source": "rising_stars"},
        "rankings": [{"fiction_id": str(1000 + rank), "rank": rank,
                      "title": f"English Fantasy {rank}",
                      "url": f"https://www.royalroad.com/fiction/{1000 + rank}/english-fantasy",
                      "cover_url": f"https://www.royalroadcdn.com/public/covers/{1000 + rank}.jpg",
                      "tags": ["Fantasy", "LitRPG"], "followers": rank * 100, "views": rank * 1000}
                     for rank in range(1, count + 1)], "total": count}


class RstSnapshotTests(unittest.TestCase):
    def test_real_source_identity_date_and_original_text_are_preserved(self):
        data = fixture()
        fetched = "2026-11-09T23:00:00+00:00"
        result = normalize_rst_snapshot(data, fetched_at=fetched, reason="forbidden")
        self.assertEqual(len(result["books"]), 50)
        self.assertEqual(result["source_id"], "rst:doomlabs")
        self.assertEqual(result["source_revision"], "rst-snapshot:31000")
        self.assertEqual(result["provenance"]["source_name"], "RST 新星榜公开快照")
        self.assertEqual(result["provenance"]["rank_semantics"], "rst_recorded_rank")
        self.assertEqual(result["collected_at"], fetched)
        self.assertEqual(result["data_date"], data["snapshot"]["captured_at"].replace("+00:00", "Z"))
        book = result["books"][0]
        self.assertEqual(book["id"], "1001")
        self.assertEqual(book["rank"], 1)
        self.assertEqual(book["title"], "English Fantasy 1")
        self.assertEqual(book["source_tags"], ["Fantasy", "LitRPG"])
        self.assertIn("奇幻", book["tags"])
        self.assertEqual((book["author"], book["summary"]), ("", ""))
        self.assertIn("简介", " ".join(result["warnings"]))
        self.assertTrue(result["complete"])

    def test_limit_is_honored_after_feed_validation(self):
        result = normalize_rst_snapshot(fixture(), {"limit": 10, "max_pages": 100})
        self.assertEqual((len(result["books"]), result["source_total"]), (10, 50))
        self.assertEqual(result["pagination"]["stop_reason"], "requested_limit")
        self.assertTrue(result["complete"])

    def test_publish_one_snapshot_without_details_or_extra_pages(self):
        calls = []
        def progress(message, **kwargs):
            if kwargs:
                calls.append(kwargs)
        with patch("fagao.royalroad_fallback._fetch_document", return_value=fixture()) as fetch:
            result = collect_royalroad_fallback(progress=progress)
        fetch.assert_called_once_with()
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["has_more"])
        self.assertEqual(calls[0]["snapshot"], result)

    def test_cancellation_from_consumer_propagates_without_retry(self):
        def progress(message, **kwargs):
            if kwargs:
                raise RuntimeError("cancelled")
        with patch("fagao.royalroad_fallback._fetch_document", return_value=fixture()) as fetch:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                collect_royalroad_fallback(progress=progress)
        fetch.assert_called_once_with()

    def test_partial_source_is_not_called_full_top50(self):
        result = normalize_rst_snapshot(fixture(3))
        self.assertFalse(result["complete"])
        self.assertEqual(result["pagination"]["stop_reason"], "source_incomplete")
        self.assertIn("仅提供 3 本", " ".join(result["warnings"]))

    def test_old_source_keeps_its_date_and_warns(self):
        data = fixture()
        old = datetime.now(timezone.utc) - timedelta(days=3)
        data["snapshot"]["captured_at"] = old.isoformat()
        result = normalize_rst_snapshot(data)
        self.assertTrue(result["data_date"].startswith(old.date().isoformat()))
        self.assertIn("24 小时", " ".join(result["warnings"]))

    def test_rejects_missing_naive_future_or_invalid_source_time(self):
        for value in [None, "", "today", "2026-01-01", "2026-11-09T11:00:00",
                      (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()]:
            with self.subTest(value=value):
                data = fixture()
                data["snapshot"]["captured_at"] = value
                with self.assertRaises(CollectorError):
                    normalize_rst_snapshot(data)

    def test_rejects_source_scope_and_count_changes(self):
        cases = [[], {}, {"snapshot": {}}]
        for key, value in [("source", "other_list"), ("id", True), ("total_items", 49)]:
            data = fixture()
            data["snapshot"][key] = value
            cases.append(data)
        data = fixture(); data["total"] = 49; cases.append(data)
        data = fixture(); data["rankings"] = []; cases.append(data)
        for data in cases:
            with self.subTest(data=str(data)[:120]), self.assertRaises(CollectorError):
                normalize_rst_snapshot(data)

    def test_rejects_duplicate_book_rank_gaps_and_url_mismatches(self):
        for key, value in [("fiction_id", "1002"), ("rank", 2), ("rank", 51), ("rank", True),
                           ("url", "https://evil.example/fiction/1001/"),
                           ("url", "https://www.royalroad.com/fiction/9999/"),
                           ("title", ""), ("tags", [1]), ("followers", -1), ("views", True)]:
            data = fixture()
            data["rankings"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(CollectorError):
                normalize_rst_snapshot(data)

    def test_validation_includes_rows_outside_requested_limit(self):
        data = fixture()
        data["rankings"][-1]["rank"] = 1
        with self.assertRaises(CollectorError):
            normalize_rst_snapshot(data, {"limit": 10})

    def test_missing_metrics_are_not_zero_filled(self):
        data = fixture()
        data["rankings"][0].update(followers=None, views=None, tags=None, cover_url=None)
        book = normalize_rst_snapshot(data)["books"][0]
        self.assertEqual(book["metrics"], [])
        self.assertEqual(book["source_tags"], [])
        self.assertEqual(book["cover_url"], "")

    def test_unordered_rows_are_sorted_by_source_rank(self):
        data = fixture()
        data["rankings"].reverse()
        self.assertEqual([b["rank"] for b in normalize_rst_snapshot(data)["books"]], list(range(1, 51)))


class RstTransportTests(unittest.TestCase):
    def response(self, content):
        response = io.BytesIO(content)
        response.headers = {}
        response.geturl = lambda: API_URL
        return response

    def test_get_uses_public_api_and_application_owned_transport(self):
        data = fixture()
        response = self.response(json.dumps(data).encode())
        opener = MagicMock()
        opener.open.return_value = response
        with patch("fagao.royalroad_fallback.network.build_opener", return_value=opener):
            self.assertEqual(_fetch_document(), data)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, API_URL)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertNotIn("Authorization", dict(request.header_items()))

    def test_429_is_not_retried(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError(API_URL, 429, "Rate Limited", {}, io.BytesIO(b"{}"))
        with patch("fagao.royalroad_fallback.network.build_opener", return_value=opener):
            with self.assertRaises(CollectorError) as ctx:
                _fetch_document()
        self.assertEqual(ctx.exception.state, "rate_limited")
        self.assertEqual(opener.open.call_count, 1)

    def test_oversized_and_non_json_responses_fail_without_publishing(self):
        for body in [b"x" * (MAX_BYTES + 1), b"<html>Unavailable</html>"]:
            opener = MagicMock()
            opener.open.return_value = self.response(body)
            with patch("fagao.royalroad_fallback.network.build_opener", return_value=opener):
                with self.assertRaises(CollectorError):
                    _fetch_document()


if __name__ == "__main__":
    unittest.main()
