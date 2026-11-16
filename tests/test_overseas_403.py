# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Verify the real collector's official-source -> independent-feed transition."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.overseas_readers import collect_readers


def public_feed():
    return {"snapshot": {"id": 51, "captured_at": datetime.now(timezone.utc).isoformat(),
                         "total_items": 1, "source": "rising_stars"},
            "total": 1, "rankings": [{"fiction_id": "123", "rank": 1, "title": "An English Book",
                                      "url": "https://www.royalroad.com/fiction/123/an-english-book",
                                      "followers": 123, "views": 456, "tags": ["Fantasy"]}]}


class RoyalRoadFallbackIntegrationTests(unittest.TestCase):
    def test_official_403_uses_rst_and_publishes_accurate_provenance(self):
        pages = []
        def progress(message, **kwargs):
            if kwargs:
                pages.append(kwargs)
        with patch("fagao.overseas_readers._fetch", side_effect=CollectorError("平台返回 HTTP 403。", "forbidden")) as official, \
             patch("fagao.royalroad_fallback._fetch_document", return_value=public_feed()) as rst:
            result = collect_readers("royalroad", {}, progress=progress)
        official.assert_called_once()
        rst.assert_called_once_with()
        self.assertEqual(result["platform"], "royalroad")
        self.assertEqual(result["source_id"], "rst:doomlabs")
        self.assertEqual(result["books"][0]["id"], "123")
        self.assertEqual(result["books"][0]["summary"], "")
        self.assertIn("HTTP 403", " ".join(result["warnings"]))
        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0]["has_more"])

    def test_official_network_failure_also_uses_independent_feed(self):
        with patch("fagao.overseas_readers._fetch", side_effect=CollectorError("连接超时。", "network_error")), \
             patch("fagao.royalroad_fallback._fetch_document", return_value=public_feed()):
            result = collect_readers("royalroad", {})
        self.assertEqual(result["source_id"], "rst:doomlabs")

    def test_parse_failure_does_not_silently_change_source(self):
        with patch("fagao.overseas_readers._fetch", return_value="<html>changed</html>"), \
             patch("fagao.royalroad_fallback._fetch_document") as rst:
            with self.assertRaises(CollectorError):
                collect_readers("royalroad", {})
        rst.assert_not_called()

    def test_successful_official_source_does_not_contact_rst(self):
        html = '<div class="fiction-list-item"><h2 class="fiction-title"><a href="/fiction/123/book">English Book</a></h2></div>'
        with patch("fagao.overseas_readers._fetch", return_value=html), \
             patch("fagao.royalroad_fallback._fetch_document") as rst:
            result = collect_readers("royalroad", {})
        self.assertEqual(result["source_id"], "direct")
        rst.assert_not_called()

    def test_both_sources_fail_error_preserves_each_cause_without_empty_publish(self):
        pages = []
        def progress(message, **kwargs):
            if kwargs:
                pages.append(kwargs)
        with patch("fagao.overseas_readers._fetch", side_effect=CollectorError("平台返回 HTTP 403。", "forbidden")), \
             patch("fagao.royalroad_fallback._fetch_document", side_effect=CollectorError("RST 公开数据返回 HTTP 503。", "network_error")) as rst:
            with self.assertRaises(CollectorError) as ctx:
                collect_readers("royalroad", {}, progress=progress)
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertIn("HTTP 503", str(ctx.exception))
        self.assertIn("已有榜单仍保留", str(ctx.exception))
        self.assertEqual(ctx.exception.state, "network_error")
        self.assertEqual(pages, [])
        rst.assert_called_once()

    def test_invalid_rst_data_is_rejected_before_any_snapshot(self):
        pages = []
        def progress(message, **kwargs):
            if kwargs:
                pages.append(kwargs)
        data = public_feed()
        data["snapshot"]["source"] = "unrelated_list"
        with patch("fagao.overseas_readers._fetch", side_effect=CollectorError("HTTP 403", "forbidden")), \
             patch("fagao.royalroad_fallback._fetch_document", return_value=data):
            with self.assertRaises(CollectorError) as ctx:
                collect_readers("royalroad", {}, progress=progress)
        self.assertEqual(ctx.exception.state, "source_changed")
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertEqual(pages, [])


if __name__ == "__main__":
    unittest.main()
