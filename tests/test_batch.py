# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import unittest
from unittest.mock import Mock, patch

from fagao.batch import collect_all_categories, search_snapshots
from fagao.collectors import CollectorError
from fagao.store import AppError


def result(category="古言脑洞", channel="女频", board="新书榜", **changes):
    return dict(platform="fanqie", mode="long", channel=channel, category=category,
                board=board, categories=["古言脑洞", "宫斗宅斗", "古代言情"], complete=True,
                books=[{"id": "same-source-id", "title": "隔离测试书目", "rank": 1}], **changes)


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.store = Mock()
        self.store.directory = "/unused-local-data"

        def save(data):
            snapshot = copy.deepcopy(data)
            snapshot["id"] = "snapshot-" + str(len(self.saved) + 1)
            snapshot["books_count"] = len(snapshot["books"])
            self.saved.append(snapshot)
            return copy.deepcopy(snapshot)
        self.store.save_snapshot.side_effect = save

    def test_sequential_categories_have_separate_ranked_snapshots(self):
        calls = []

        def fetch(platform, options, directory, progress):
            # A category must finish saving before the following one starts.
            self.assertEqual(len(calls), len(self.saved))
            self.assertEqual(platform, "fanqie")
            self.assertEqual(directory, self.store.directory)
            self.assertEqual(options["max_pages"], 100)
            self.assertNotIn("limit", options)
            calls.append(options["category"])
            return result(options["category"])

        options = {}
        with patch("fagao.batch.collect", side_effect=fetch):
            batch = collect_all_categories(self.store, options)
        self.assertEqual(options, {})
        self.assertEqual(calls, ["古言脑洞", "宫斗宅斗", "古代言情"])
        self.assertTrue(batch["complete"])
        self.assertEqual(len(batch["snapshots"]), 3)
        self.assertTrue(all("books" not in item for item in batch["snapshots"]))
        self.assertEqual([item["books"][0]["rank"] for item in self.saved], [1, 1, 1])
        self.assertEqual(len({item["id"] for item in self.saved}), 3)
        self.store.set_view.assert_not_called()

    def test_options_preserved_and_discovery_stays_in_requested_channel(self):
        options = {"platform": "fanqie", "channel": "male", "board": "read", "category": "都市脑洞",
                   "limit": 1000, "max_pages": 7, "detail_budget": 22}
        original = copy.deepcopy(options)

        def fetch(platform, opts, directory, progress):
            self.assertEqual((opts["channel"], opts["board"]), ("男频", "阅读榜"))
            self.assertEqual((opts["limit"], opts["max_pages"], opts["detail_budget"]), (1000, 7, 22))
            self.assertNotIn("platform", opts)
            data = result(opts["category"], "男频", "阅读榜")
            data["categories"] = ["都市脑洞", "玄幻脑洞", "玄幻脑洞", None, " "]
            return data

        with patch("fagao.batch.collect", side_effect=fetch) as fetcher:
            batch = collect_all_categories(self.store, options)
        self.assertEqual(options, original)
        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(batch["categories"], ["都市脑洞", "玄幻脑洞"])
        self.assertEqual([item["channel"] for item in self.saved], ["男频", "男频"])

    def test_one_failed_category_does_not_stop_later_categories(self):
        old = result("旧分类")
        old["id"] = "old-snapshot"
        self.saved.append(copy.deepcopy(old))

        def fetch(platform, options, directory, progress):
            if options["category"] == "宫斗宅斗":
                raise CollectorError("暂时无法取得该分类。")
            return result(options["category"])

        with patch("fagao.batch.collect", side_effect=fetch):
            batch = collect_all_categories(self.store, {})
        self.assertFalse(batch["complete"])
        self.assertEqual([item["category"] for item in batch["snapshots"]], ["古言脑洞", "古代言情"])
        self.assertEqual(batch["failed_categories"][0]["category"], "宫斗宅斗")
        self.assertEqual(self.saved[0], old)

    def test_save_failure_is_reported_without_combining_categories(self):
        save = self.store.save_snapshot.side_effect

        def fail_one(data):
            if data["category"] == "宫斗宅斗":
                raise AppError("invalid_snapshot", "测试保存失败。")
            return save(data)

        self.store.save_snapshot.side_effect = fail_one
        with patch("fagao.batch.collect", side_effect=lambda _, opts, *args: result(opts["category"])):
            batch = collect_all_categories(self.store, {})
        self.assertEqual(batch["failed_categories"][0]["code"], "invalid_snapshot")
        self.assertEqual(len(batch["snapshots"]), 2)

    def test_first_failure_never_uses_old_channel_navigation(self):
        with patch("fagao.batch.collect", side_effect=CollectorError("首个分类失败。")) as fetch:
            batch = collect_all_categories(self.store, {"channel": "男频"})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(batch["categories"], ["玄幻脑洞"])
        self.assertFalse(batch["discovery_complete"])
        self.assertFalse(batch["complete"])
        self.store.save_snapshot.assert_not_called()
        self.store.state.assert_not_called()

    def test_scope_mismatch_is_not_saved(self):
        with patch("fagao.batch.collect", return_value=result(channel="男频")):
            batch = collect_all_categories(self.store, {})
        self.assertEqual(batch["failed_categories"][0]["code"], "batch_scope_mismatch")
        self.assertFalse(batch["complete"])
        self.store.save_snapshot.assert_not_called()

    def test_missing_navigation_and_partial_source_are_incomplete(self):
        data = result()
        data["categories"] = []
        with patch("fagao.batch.collect", return_value=data) as fetch:
            batch = collect_all_categories(self.store, {})
        self.assertFalse(batch["discovery_complete"])
        self.assertFalse(batch["complete"])
        self.assertEqual(fetch.call_count, 1)

        def fetch_partial(platform, options, directory, progress):
            data = result(options["category"])
            data["complete"] = options["category"] != "宫斗宅斗"
            return data
        with patch("fagao.batch.collect", side_effect=fetch_partial):
            batch = collect_all_categories(self.store, {})
        self.assertFalse(batch["complete"])
        self.assertEqual(batch["incomplete_categories"], ["宫斗宅斗"])
        self.assertEqual(batch["failed_categories"], [])

    def test_progress_interrupt_and_cancellation_are_not_swallowed(self):
        def interrupt(message):
            raise RuntimeError("用户中止进度处理")
        with patch("fagao.batch.collect") as fetch:
            with self.assertRaisesRegex(RuntimeError, "用户中止"):
                collect_all_categories(self.store, {}, interrupt)
            fetch.assert_not_called()
        with patch("fagao.batch.collect", side_effect=AppError("cancelled", "取消采集")):
            with self.assertRaises(AppError):
                collect_all_categories(self.store, {})

    def test_invalid_platform_and_options_do_not_collect(self):
        with patch("fagao.batch.collect") as fetch:
            for options in (None, [], {"platform": "qidian"}, {"mode": "short"}, {"channel": "全部"}):
                with self.subTest(options=options), self.assertRaises(AppError):
                    collect_all_categories(self.store, options)
            fetch.assert_not_called()


def snapshot(sid, platform="fanqie", stamp="2026-11-09T08:00:00Z", category="古言脑洞", books=None):
    return {"id": sid, "platform": platform, "channel": "女频", "category": category,
            "board": "新书榜", "collected_at": stamp, "data_date": stamp,
            "books": books if books is not None else [{"id": "same", "title": "同一本书", "author": "作者甲", "rank": 14}]}


class SearchTest(unittest.TestCase):
    def test_platform_identity_and_all_occurrences_are_preserved(self):
        older = snapshot("old", stamp="2026-11-08T08:00:00Z")
        newer = snapshot("new", category="宫斗宅斗")
        newer["books"][0].update(title="更新书名", rank=2)
        other_platform = snapshot("qidian", platform="qidian")
        snapshots = [older, other_platform, newer]
        original = copy.deepcopy(snapshots)
        found = search_snapshots(snapshots, "作者甲")
        self.assertEqual(found["total"], 2)
        item = next(item for item in found["items"] if item["platform"] == "fanqie")
        self.assertEqual((item["title"], item["snapshot_id"]), ("更新书名", "new"))
        self.assertEqual(item["selection"], {"snapshot_id": "new", "book_id": "same"})
        self.assertNotIn("rank", item)
        self.assertEqual([(occ["snapshot_id"], occ["rank"]) for occ in item["occurrences"]], [("new", 2), ("old", 14)])
        item["occurrences"][0]["category"] = "只修改返回结果"
        self.assertEqual(snapshots, original)

    def test_old_title_match_retains_new_and_old_sources(self):
        old = snapshot("old", stamp="2026-11-08T08:00:00Z")
        new = snapshot("new")
        new["books"][0]["title"] = "完全不同的新书名"
        found = search_snapshots([new, old], "同一本书")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["matched_snapshot_ids"], ["old"])
        self.assertEqual(len(found["items"][0]["occurrences"]), 2)

    def test_title_author_matches_precede_secondary_matches(self):
        data = snapshot("all", books=[
            {"id": "summary", "title": "乙", "summary": "关键词", "rank": 1},
            {"id": "title", "title": "关键词甲", "rank": 14},
            {"id": "author", "title": "丙", "author": "关键词作者", "rank": 20},
            {"id": "tags", "title": "丁", "tags": ["关键词"], "rank": 30},
        ])
        found = search_snapshots([data], "关键词", limit=2)
        self.assertEqual(found["total"], 4)
        self.assertEqual([item["book_id"] for item in found["items"]], ["title", "author"])

    def test_casefold_category_match_duplicates_and_missing_full_snapshots(self):
        data = snapshot("full", category="Fantasy", books=[{"id": 12345, "title": "Title", "rank": 1}])
        found = search_snapshots([data, copy.deepcopy(data), {"id": "metadata", "platform": "fanqie"}], "FANTASY")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["book_id"], "12345")
        self.assertEqual(len(found["items"][0]["occurrences"]), 1)

    def test_limits_empty_queries_and_invalid_arguments(self):
        self.assertEqual(search_snapshots([], "   ")["items"], [])
        self.assertEqual(search_snapshots([snapshot("a")], "")["total"], 1)
        for limit in (0, -1, 1001, True, "2"):
            with self.subTest(limit=limit), self.assertRaises(AppError):
                search_snapshots([], "q", limit)
        with self.assertRaises(AppError):
            search_snapshots([], None)


if __name__ == "__main__":
    unittest.main()
