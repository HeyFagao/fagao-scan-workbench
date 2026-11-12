# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
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
