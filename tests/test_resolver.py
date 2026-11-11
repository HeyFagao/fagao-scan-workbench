# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from fagao.resolver import ResolutionError, resolve_reference


def snapshot(ident, *, platform="fanqie", mode="long", category="古言脑洞", channel="女频", board="new", day="2026-11-09", books=None):
    return {"id": ident, "platform": platform, "mode": mode, "category": category,
            "channel": channel, "board": board, "collected_at": day + "T08:00:00+08:00",
            "books": books or [{"id": "a", "title": "甲书", "rank": 2},
                               {"id": "b", "title": "乙书", "rank": 14},
                               {"id": "c", "title": "丙书", "rank": 7}]}


def fixture():
    s = snapshot("today")
    return {"active_mode": "long", "now": "2026-11-09T08:00:00+08:00",
            "snapshots": [s, snapshot("short", platform="heiyan", mode="short", category="书库", channel="短篇", board="library", books=[{"id": "s1", "title": "短篇甲", "rank": None}])],
            "views": {"long": {"snapshot_id": "today", "platform": "fanqie", "ordered_ids": ["c", "a", "b"], "selected_id": "c", "page": 1, "page_size": 2},
                      "short": {"snapshot_id": "short", "platform": "heiyan", "ordered_ids": ["s1"], "selected_id": "s1", "page": 1, "page_size": 20}},
            "history": [], "analysis_target": None}


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.ctx = fixture()

    def book(self, query):
        return resolve_reference(query, self.ctx)["book_id"]

    def error(self, query, code):
        with self.assertRaises(ResolutionError) as raised:
            resolve_reference(query, self.ctx)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_display_order_is_not_original_rank(self):
        self.assertEqual(self.book("分析第二本"), "a")
        self.assertEqual(self.book("分析番茄新书榜第14名"), "b")
        self.assertEqual(self.book("分析这本"), "c")

    def test_original_rank_ignores_filter(self):
        self.ctx["views"]["long"]["ordered_ids"] = ["a"]
        self.assertEqual(self.book("番茄新书榜第十四名"), "b")
        self.error("分析第二本", "index_not_found")

    def test_list_index_continues_across_pages(self):
        self.ctx["views"]["long"]["page"] = 2
        self.assertEqual(self.book("第二本"), "a")
        self.assertEqual(self.book("这一页第一本"), "b")
        self.error("这一页第二本", "index_not_found")
        self.error("这一页第三本", "index_not_found")

    def test_long_view_survives_switch_to_short(self):
        self.ctx["active_mode"] = "short"
        self.assertEqual(self.book("这本"), "s1")
        self.assertEqual(self.book("长篇第二本"), "a")
        self.assertEqual(self.book("番茄第二本"), "a")
        self.assertEqual(self.book("fanqie 新书榜第14名"), "b")

    def test_chinese_and_full_width_numbers(self):
        self.assertEqual(self.book("第十四名"), "b")
        self.assertEqual(self.book("第１４名"), "b")
        self.assertEqual(self.book("第两本"), "a")
        self.error("第零本", "invalid_index")
        self.error("第零名", "invalid_rank")
        self.error("第一百零一本", "index_not_found")

    def test_missing_rank_never_falls_back_to_row(self):
        self.error("第1名", "rank_not_found")
        self.assertEqual(self.book("第一本"), "c")

    def test_library_has_no_ranking(self):
        self.ctx["active_mode"] = "short"
        self.error("黑岩第1名", "rank_not_found")
        self.assertEqual(self.book("黑岩第一本"), "s1")

    def test_explicit_channel_overrides_current(self):
        self.ctx["snapshots"].append(snapshot("male", channel="男频", books=[{"id": "m", "rank": 14, "title": "男频甲"}]))
        self.assertEqual(self.book("番茄男频新书榜第14名"), "m")
        self.assertEqual(self.book("番茄新书榜第14名"), "b")

    def test_english_channel_does_not_match_inside_female(self):
        self.assertEqual(self.book("fanqie female new 第14名"), "b")

    def test_qidian_new_book_subtypes_are_distinct(self):
        self.ctx["snapshots"].extend([
            snapshot("q-sign", platform="qidian", channel="男频", board="签约新书榜"),
            snapshot("q-public", platform="qidian", channel="男频", board="公众新书榜"),
        ])
        self.error("起点新书榜第14名", "ambiguous_scope")
        result = resolve_reference("起点签约新书榜第14名", self.ctx)
        self.assertEqual(result["snapshot_id"], "q-sign")
        self.ctx["views"]["long"]["snapshot_id"] = "q-public"
        self.assertEqual(resolve_reference("起点新书榜第14名", self.ctx)["snapshot_id"], "q-public")

    def test_just_selected_reference_beats_analysis_pronoun(self):
        self.ctx["analysis_target"] = {"snapshot_id": "today", "book_id": "b"}
        self.assertEqual(self.book("分析刚才选的那本"), "c")

    def test_unknown_explicit_platform_cannot_use_current(self):
        self.error("起点新书榜第14名", "scope_not_found")
        self.error("起点第二本", "scope_not_found")

    def test_multiple_matching_categories_are_ambiguous_without_current_scope(self):
        self.ctx["views"]["long"]["snapshot_id"] = None
        self.ctx["snapshots"].append(snapshot("romance", category="现代言情"))
        err = self.error("番茄新书榜第14名", "ambiguous_scope")
        self.assertEqual(len(err.candidates), 2)
        self.assertEqual(resolve_reference("番茄古言脑洞新书榜第14名", self.ctx)["snapshot_id"], "today")

    def test_current_scope_does_not_leak_to_incompatible_explicit_scope(self):
        self.ctx["snapshots"].extend([
            snapshot("male1", channel="男频", category="都市"),
            snapshot("male2", channel="男频", category="玄幻"),
        ])
        self.error("番茄男频新书榜第14名", "ambiguous_scope")
        self.assertEqual(resolve_reference("番茄男频都市新书榜第14名", self.ctx)["snapshot_id"], "male1")

    def test_latest_same_scope_used_only_without_current_snapshot(self):
        self.ctx["snapshots"].append(snapshot("old", day="2026-11-08", books=[{"id": "old-book", "title": "旧书", "rank": 14}]))
        self.ctx["views"]["long"]["snapshot_id"] = None
        self.assertEqual(self.book("番茄新书榜第14名"), "b")
        self.ctx["views"]["long"]["snapshot_id"] = "old"
        self.assertEqual(self.book("番茄新书榜第14名"), "old-book")

    def test_yesterday_selection_pins_old_snapshot_before_analysis(self):
        self.ctx["snapshots"].append(snapshot("old", day="2026-11-08", books=[{"id": "old-book", "title": "旧书", "rank": 14}]))
        self.ctx["history"] = [{"snapshot_id": "old", "book_id": "old-book", "selected_at": "2026-11-08T23:20:00+08:00"}]
        result = resolve_reference("继续分析昨天选的那本", self.ctx)
        self.assertEqual((result["snapshot_id"], result["book_id"]), ("old", "old-book"))

    def test_yesterday_uses_client_timezone_not_host_utc(self):
        self.ctx["now"] = "2026-11-09T00:10:00+08:00"
        self.ctx["history"] = [{"snapshot_id": "today", "book_id": "a", "selected_at": "2026-11-08T15:55:00Z"}]
        self.assertEqual(self.book("昨天选的那本"), "a")
        self.ctx["history"][0]["selected_at"] = "2026-11-08T16:01:00Z"
        self.error("昨天选的那本", "history_not_found")

    def test_missing_iana_database_keeps_local_plus_nine_day_boundary(self):
        self.ctx["now"] = "2026-11-09T00:10:00+09:00"
        self.ctx["timezone"] = None
        for missing_zone in (KeyError("missing zone"), ZoneInfoNotFoundError("no tzdata")):
            with self.subTest(error=type(missing_zone).__name__):
                with patch("fagao.resolver.ZoneInfo", side_effect=missing_zone):
                    # 23:55 in Japan is yesterday, although it is 22:55 at +08.
                    self.ctx["history"] = [{"snapshot_id": "today", "book_id": "a",
                                            "selected_at": "2026-11-08T14:55:00Z"}]
                    self.assertEqual(self.book("昨天选的那本"), "a")
                    # 00:01 in Japan is today: the +08 fallback must not win.
                    self.ctx["history"][0]["selected_at"] = "2026-11-08T15:01:00Z"
                    self.error("昨天选的那本", "history_not_found")

    def test_yesterday_multiple_books_requires_disambiguation(self):
        self.ctx["history"] = [
            {"snapshot_id": "today", "book_id": "a", "selected_at": "2026-11-08T10:00:00+08:00"},
            {"snapshot_id": "today", "book_id": "b", "selected_at": "2026-11-08T11:00:00+08:00"}]
        err = self.error("昨天选的那本", "ambiguous_history")
        self.assertEqual(len(err.candidates), 2)
        self.assertEqual(self.book("昨天选的《甲书》"), "a")

    def test_yesterday_repeated_selection_of_one_book_is_unique(self):
        self.ctx["history"] = [
            {"snapshot_id": "today", "book_id": "a", "selected_at": "2026-11-08T10:00:00+08:00"},
            {"snapshot_id": "today", "book_id": "a", "selected_at": "2026-11-08T11:00:00+08:00"}]
        self.assertEqual(self.book("昨天选的那本"), "a")

    def test_followup_keeps_conversation_target(self):
        self.ctx["analysis_target"] = {"snapshot_id": "today", "book_id": "b"}
        self.assertEqual(self.book("它的金手指呢"), "b")
        self.assertEqual(self.book("继续分析开头"), "b")
        self.assertEqual(self.book("分析刚选的这本"), "c")
        self.assertEqual(self.book("长篇第二本"), "a")

    def test_explicit_platform_overrides_followup_target(self):
        self.ctx["analysis_target"] = {"snapshot_id": "today", "book_id": "b"}
        self.assertEqual(self.book("继续分析黑岩这本"), "s1")

    def test_stale_analysis_target_does_not_silently_change_book(self):
        self.ctx["analysis_target"] = {"snapshot_id": "missing", "book_id": "b"}
        self.error("继续分析它", "snapshot_not_found")

    def test_duplicate_original_rank_errors(self):
        self.ctx["snapshots"][0]["books"].append({"id": "d", "title": "丁书", "rank": 14})
        self.error("第14名", "ambiguous_rank")

    def test_missing_selection_or_stale_order_is_reported(self):
        self.ctx["views"]["long"]["selected_id"] = None
        self.error("分析这本", "selection_not_found")
        self.ctx["views"]["long"]["ordered_ids"] = ["missing"]
        self.error("第一本", "book_not_found")

    def test_title_is_exact_and_scoped(self):
        self.assertEqual(self.book("分析《乙书》"), "b")
        self.error("分析《未知》", "title_not_found")

    def test_snapshot_dict_supported_and_no_input_mutation(self):
        self.ctx["snapshots"] = {s["id"]: s for s in self.ctx["snapshots"]}
        before = copy.deepcopy(self.ctx)
        result = resolve_reference("第二本", self.ctx)
        self.assertEqual(self.ctx, before)
        result["book"]["title"] = "改变副本"
        self.assertEqual(self.ctx, before)

    def test_empty_no_data_and_ambiguous_requests(self):
        self.error("", "empty_query")
        self.error("番茄起点第二本", "ambiguous_platform")
        self.error("长篇短篇第二本", "ambiguous_mode")
        self.error("第二本第14名", "conflicting_reference")
        self.ctx["snapshots"] = []
        self.error("这本", "no_snapshots")


if __name__ == "__main__":
    unittest.main()
