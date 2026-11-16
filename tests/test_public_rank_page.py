# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
import io
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from fagao import public_rank_page as page


class PublicPageTests(unittest.TestCase):
    url = 'https://b.faloo.com/SR_1.html'

    def test_reads_chinese_page_through_workbench_network(self):
        response = io.BytesIO('小说排行榜'.encode('gbk'))
        response.headers = {}
        response.status = 200
        response.geturl = lambda: self.url
        with patch.object(page.network, 'build_opener') as build:
            build.return_value.open.return_value = response
            self.assertEqual(page.fetch_page(self.url, encoding='gb18030'), '小说排行榜')
        self.assertTrue(response.closed)

    def test_blocked_domain_and_revoked_session_never_open(self):
        with patch.object(page.network, 'build_opener') as build:
            with self.assertRaises(page.CollectorError):
                page.fetch_page('https://example.com/unknown')
            with page.network.authorized_requests(Mock(side_effect=PermissionError('session changed'))):
                with self.assertRaises(PermissionError):
                    page.fetch_page(self.url)
            build.assert_not_called()

    def test_forbidden_page_is_not_saved_as_a_ranking(self):
        with patch.object(page.network, 'build_opener') as build:
            build.return_value.open.side_effect = HTTPError(self.url, 403, 'Forbidden', {}, io.BytesIO())
            with self.assertRaises(page.CollectorError) as failure:
                page.fetch_page(self.url)
            self.assertEqual(failure.exception.state, 'forbidden')
