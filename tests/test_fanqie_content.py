import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.fanqie_content import catalog, fetch_book_text, _plain_content
from fagao.store import AppError


BID = "123456789"
IDS = ["987654321", "987654322", "987654323"]
CONTENT = "窗外下着雨，她推开门，发现桌上多了一封来历不明的信。" * 8


def state(value):
    return "<script>window.__INITIAL_STATE__=" + json.dumps(value, ensure_ascii=False) + "</script>"


def sources(status=1, count=3, locked=None):
    page = state({"page": {"bookId": BID, "bookName": "测试小说", "creationStatus": status}})
    entries = [{"itemId": iid, "title": f"第 {i} 章", "needPay": 0, "isChapterLock": False,
                "volume_name": "第一卷", "realChapterOrder": str(i - 1)} for i, iid in enumerate(IDS[:count], 1)]
    if locked is not None:
        entries[locked]["needPay"] = "1"
    directory = json.dumps({"code": 0, "data": {"allItemIds": IDS[:count], "chapterListWithVolume": [entries]}})
    chapters = {iid: state({"reader": {"chapterData": {"bookId": BID, "itemId": iid,
                "title": f"第 {i} 章", "content": "<p>" + CONTENT + "</p>", "needPay": 0}}})
                for i, iid in enumerate(IDS[:count], 1)}

    def fetch(url):
        if "/page/" in url:
            return page
        if "/directory/" in url:
            return directory
        return chapters[url.rsplit("/", 1)[-1]]
    return fetch, chapters


class FanqieContentTests(unittest.TestCase):
    def test_directory_preserves_source_order_including_announcement_position(self):
        fetch, _ = sources()
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = catalog(BID)
        self.assertEqual([item["item_id"] for item in result["chapters"]], IDS)
        self.assertEqual(result["chapters"][0]["index"], 1)
        self.assertEqual(result["chapters"][0]["volume"], "第一卷")
        self.assertTrue(result["catalog_complete"])

    def test_known_font_mapping_decodes_entities_and_excludes_scripts(self):
        mapping = json.loads((Path(__file__).parents[1] / "fagao/assets/fanqie-charmap.json").read_text())
        code, character = next(iter(mapping.items()))
        raw = f"<p>&#{code};" + CONTENT + "</p><script>恶意脚本文本</script><p>第二段&nbsp;结尾</p>"
        text = _plain_content(raw)
        self.assertTrue(text.startswith(character))
        self.assertNotIn("恶意脚本", text)
        self.assertIn("\n\n第二段 结尾", text)

    def test_unknown_font_character_never_becomes_success(self):
        with self.assertRaisesRegex(CollectorError, "未识别"):
            _plain_content(CONTENT + "\ue999")

    def test_preview_is_range_complete_but_not_whole_book(self):
        fetch, _ = sources(status=0)
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID, max_chapters=2)
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["catalog_chapters"], 3)
        self.assertEqual([item["id"] for item in result["chapters"]], IDS[:2])
        self.assertIn("第 1—2 项", result["scope"])

    def test_full_finished_book_requires_entire_catalog(self):
        fetch, _ = sources(status=0)
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID, max_chapters=None)
        self.assertTrue(result["complete"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["missing"], [])

    def test_invalid_directory_id_schema_cannot_claim_finished_whole_book(self):
        fetch, _ = sources(status=0)
        def invalid_ids(url):
            response = fetch(url)
            if "/directory/" in url:
                data = json.loads(response)
                data["data"]["allItemIds"] = "invalid-non-list"
                return json.dumps(data)
            return response
        with patch("fagao.fanqie_content._fetch", side_effect=invalid_ids) as network:
            with self.assertRaisesRegex(CollectorError, "列表格式"):
                fetch_book_text(BID, max_chapters=None)
        self.assertFalse(any("/reader/" in call.args[0] and "/api/" not in call.args[0] for call in network.call_args_list))

    def test_current_serialized_catalog_is_not_claimed_as_finished_book(self):
        fetch, _ = sources(status=1)
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID, max_chapters=None)
        self.assertFalse(result["complete"])
        self.assertTrue(result["coverage_complete"])
        self.assertIn("仍连载", result["scope"])

    def test_catalog_permission_stops_before_requesting_locked_chapter(self):
        fetch, _ = sources(locked=1)
        with patch("fagao.fanqie_content._fetch", side_effect=fetch) as network:
            result = fetch_book_text(BID, max_chapters=3)
        requested = [call.args[0] for call in network.call_args_list]
        self.assertNotIn("https://fanqienovel.com/reader/" + IDS[1], requested)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertEqual(result["missing"][0]["id"], IDS[1])
        self.assertFalse(result["coverage_complete"])

    def test_reader_permission_is_checked_even_when_directory_is_public(self):
        fetch, chapters = sources()
        chapters[IDS[1]] = state({"reader": {"chapterData": {"bookId": BID, "itemId": IDS[1], "needPay": 1, "content": CONTENT}}})
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertIn("权限", result["missing"][0]["reason"])

    def test_wrong_book_identity_cannot_be_cached_or_imported(self):
        fetch, chapters = sources()
        chapters[IDS[0]] = state({"reader": {"chapterData": {"bookId": "111111111", "itemId": IDS[0], "content": CONTENT}}})
        with tempfile.TemporaryDirectory() as directory, patch("fagao.fanqie_content._fetch", side_effect=fetch):
            with self.assertRaisesRegex(CollectorError, "身份不匹配"):
                fetch_book_text(BID, directory)
            self.assertFalse((Path(directory) / "cache/fanqie-content/chapters").exists())

    def test_failure_preserves_only_successful_contiguous_range(self):
        fetch, _ = sources()
        def failing(url):
            if url.endswith(IDS[1]):
                raise CollectorError("网络失败", "network_error")
            return fetch(url)
        with patch("fagao.fanqie_content._fetch", side_effect=failing):
            result = fetch_book_text(BID)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertFalse(result["complete"])
        self.assertIn("后续章节未取得", result["scope"])

    def test_malformed_later_reader_preserves_earlier_text(self):
        fetch, chapters = sources()
        chapters[IDS[1]] = state({"reader": None})
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID)
        self.assertEqual(len(result["chapters"]), 1)
        self.assertFalse(result["complete"])
        self.assertIn("身份不匹配", result["missing"][0]["reason"])

    def test_cancel_returns_downloaded_chapter_without_claiming_completion(self):
        fetch, _ = sources()
        def progress(message):
            if "已取得 1/3" in message:
                raise AppError("cancelled", "用户取消")
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID, progress=progress)
        self.assertTrue(result["cancelled"])
        self.assertEqual(len(result["chapters"]), 1)
        self.assertFalse(result["coverage_complete"])

    def test_cancel_before_network_returns_no_text(self):
        def progress(message):
            raise AppError("cancelled", "用户取消")
        with patch("fagao.fanqie_content._fetch") as network:
            result = fetch_book_text(BID, progress=progress)
        network.assert_not_called()
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["text"], "")

    def test_repeat_fetch_uses_private_cache(self):
        fetch, _ = sources()
        with tempfile.TemporaryDirectory() as directory:
            with patch("fagao.fanqie_content._fetch", side_effect=fetch):
                first = fetch_book_text(BID, directory, max_chapters=1)
            with patch("fagao.fanqie_content._fetch") as network:
                second = fetch_book_text(BID, directory, max_chapters=1)
            network.assert_not_called()
            self.assertEqual(first["text"], second["text"])
            cached = next((Path(directory) / "cache/fanqie-content/chapters").glob("*.json"))
            record = json.loads(cached.read_text())
            record["value"]["text"] = "缓存损坏" * 30
            cached.write_text(json.dumps(record))
            with patch("fagao.fanqie_content._fetch", side_effect=fetch) as network:
                repaired = fetch_book_text(BID, directory, max_chapters=1)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(first["text"], repaired["text"])

    def test_start_range_and_invalid_inputs_are_explicit(self):
        fetch, _ = sources(status=0)
        with patch("fagao.fanqie_content._fetch", side_effect=fetch):
            result = fetch_book_text(BID, start_chapter=2, max_chapters=2)
        self.assertEqual([item["index"] for item in result["chapters"]], [2, 3])
        self.assertFalse(result["complete"])
        for book_id, count, start in (("../book", 3, 1), (BID, 0, 1), (BID, True, 1), (BID, 3, 0), (BID, 5001, 1)):
            with self.subTest(book_id=book_id, count=count, start=start), self.assertRaises(CollectorError):
                fetch_book_text(book_id, max_chapters=count, start_chapter=start)


if __name__ == "__main__":
    unittest.main()
