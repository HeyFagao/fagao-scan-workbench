# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import tempfile
import unittest

from fagao.resolver import ResolutionError, resolve_reference
from fagao.store import Store


def snapshot(sid, platform="fanqie", *, title="甲书", author="作者甲", bid="a"):
    return {"id": sid, "platform": platform, "mode": "long", "channel": "男频",
            "board": "新书榜", "collected_at": "2026-11-10T08:00:00+08:00",
            "books": [{"id": bid, "title": title, "author": author, "rank": 1},
                      {"id": "b", "title": "乙书", "author": "作者乙", "rank": 2}]}


def favorite(sid, bid="a", platform="fanqie", when="2026-11-10T09:00:00+08:00"):
    return {"snapshot_id": sid, "book_id": bid, "platform": platform, "created_at": when}


class FavoriteResolverTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {"snapshots": [snapshot("saved"), snapshot("current")],
                    "favorites": [favorite("saved")], "active_mode": "long",
                    "views": {"long": {"snapshot_id": "current", "selected_id": "b", "ordered_ids": ["a", "b"]}},
                    "analysis_target": {"snapshot_id": "current", "book_id": "b"},
                    "now": "2026-11-10T10:00:00+08:00", "timezone": "Asia/Shanghai"}

    def resolved(self, query):
        result = resolve_reference(query, self.ctx)
        return result["snapshot_id"], result["book_id"]

    def error(self, query, code):
        with self.assertRaises(ResolutionError) as raised:
            resolve_reference(query, self.ctx)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_recent_favorite_pins_saved_snapshot_over_browsing_and_session(self):
        for query in ("分析我刚收藏的这本", "分析刚才收藏的那本", "继续分析最近收藏的这本"):
            with self.subTest(query=query):
                self.assertEqual(self.resolved(query), ("saved", "a"))
        self.assertEqual(self.resolved("分析刚选的这本"), ("current", "b"))
        self.assertEqual(self.resolved("它的开头呢"), ("current", "b"))

    def test_explicit_title_uses_favorites_outside_current_platform(self):
        self.ctx["snapshots"].append(snapshot("qidian", "qidian", title="起点甲书"))
        self.ctx["favorites"].append(favorite("qidian", platform="qidian", when="2026-11-10T09:30:00+08:00"))
        self.assertEqual(self.resolved("分析我收藏的《起点甲书》"), ("qidian", "a"))
        self.assertEqual(self.resolved("分析我收藏的《甲书》"), ("saved", "a"))
        self.assertEqual(self.resolved("分析我刚收藏的这本"), ("qidian", "a"))
        self.assertEqual(self.resolved("分析番茄最近收藏的这本"), ("saved", "a"))

    def test_quoted_title_is_not_a_platform_category_or_favorite_instruction(self):
        self.ctx["snapshots"][0]["books"][0]["title"] = "我收藏的起点短篇新书"
        self.assertEqual(self.resolved("分析我收藏的《我收藏的起点短篇新书》"), ("saved", "a"))
        self.ctx["snapshots"][1]["books"][1]["title"] = "我收藏的乙书"
        self.assertEqual(self.resolved("分析《我收藏的乙书》"), ("current", "b"))

    def test_no_favorite_never_falls_back_to_current_selection(self):
        self.ctx["favorites"] = []
        for query in ("分析我刚收藏的这本", "分析我收藏的《乙书》"):
            self.assertIn("先在工作台", self.error(query, "favorite_not_found").message)
        self.ctx["snapshots"] = []
        self.error("分析我刚收藏的这本", "favorite_not_found")

    def test_unfavorited_title_is_not_resolved_from_saved_rankings(self):
        self.error("分析我收藏的《乙书》", "favorite_not_found")

    def test_same_title_asks_only_for_platform_then_accepts_it(self):
        self.ctx["snapshots"].append(snapshot("qidian", "qidian"))
        self.ctx["favorites"].append(favorite("qidian", platform="qidian"))
        error = self.error("分析我收藏的《甲书》", "ambiguous_favorite")
        self.assertIn("补充平台", error.message)
        self.assertNotIn("作者", error.message)
        self.assertEqual({c["platform"] for c in error.candidates}, {"fanqie", "qidian"})
        self.assertEqual(self.resolved("分析起点我收藏的《甲书》"), ("qidian", "a"))

    def test_same_platform_title_can_be_disambiguated_by_author(self):
        self.ctx["snapshots"].append(snapshot("other-author", author="作者丙", bid="c"))
        self.ctx["favorites"].append(favorite("other-author", bid="c"))
        error = self.error("分析我收藏的《甲书》", "ambiguous_favorite")
        self.assertIn("补充作者", error.message)
        self.assertEqual(self.resolved("分析我收藏的《甲书》，作者：作者丙"), ("other-author", "c"))
        self.error("分析我收藏的《甲书》，作者：不存在", "favorite_not_found")

    def test_multiple_favorites_without_recency_or_title_require_book_name(self):
        self.ctx["favorites"].append(favorite("saved", "b", when="2026-11-10T09:30:00+08:00"))
        self.assertIn("请说书名", self.error("分析我收藏的这本", "ambiguous_favorite").message)
        self.assertEqual(self.resolved("分析我最后收藏的这本"), ("saved", "b"))

    def test_tied_or_unknown_recency_does_not_guess(self):
        self.ctx["favorites"].append(favorite("saved", "b"))
        self.error("分析我刚收藏的这本", "ambiguous_favorite")
        self.ctx["favorites"][1]["created_at"] = None
        self.error("分析我刚收藏的这本", "ambiguous_favorite")

    def test_missing_latest_snapshot_does_not_fall_back_to_older_favorite(self):
        self.ctx["favorites"].append(favorite("missing", "b", when="2026-11-10T09:30:00+08:00"))
        self.error("分析我刚收藏的这本", "snapshot_not_found")

    def test_yesterdays_favorite_uses_favorite_time_not_selection_history(self):
        self.ctx["favorites"][0]["created_at"] = "2026-11-09T15:50:00Z"
        self.ctx["history"] = [{"snapshot_id": "current", "book_id": "b", "selected_at": "2026-11-09T22:00:00+08:00"}]
        self.assertEqual(self.resolved("分析我昨天收藏的这本"), ("saved", "a"))
        self.ctx["favorites"][0]["created_at"] = "2026-11-09T16:10:00Z"
        self.error("分析我昨天收藏的这本", "favorite_not_found")


class FavoritePersistenceTests(unittest.TestCase):
    def test_favorite_survives_restart_and_pins_task_until_explicit_retargeting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            raw = copy.deepcopy(snapshot("ignored"))
            raw["source_url"] = "https://fanqienovel.com/rank/0_1_253"
            saved = store.save_snapshot(raw)
            store.toggle_favorite(saved["id"], "a", True)
            store.set_view({"mode": "long", "platform": "fanqie", "snapshot_id": saved["id"], "selected_id": "b"})
            store.prepare_task({"snapshot_id": saved["id"], "book_id": "b"}, "分析这本", "other-chat")
            store = Store(directory)
            result = resolve_reference("分析我收藏的《甲书》", store.context("favorites-chat"))
            task = store.prepare_task(result, "分析我收藏的《甲书》", "favorites-chat")
            self.assertEqual((task["snapshot_id"], task["book_id"]), (saved["id"], "a"))
            store.toggle_favorite(saved["id"], "a", False)
            with self.assertRaises(ResolutionError) as raised:
                resolve_reference("分析我刚收藏的这本", store.context("favorites-chat"))
            self.assertEqual(raised.exception.code, "favorite_not_found")
            self.assertEqual(resolve_reference("它的开头呢", store.context("favorites-chat"))["book_id"], "a")
            self.assertEqual(resolve_reference("它的开头呢", store.context("other-chat"))["book_id"], "b")


if __name__ == "__main__":
    unittest.main()
