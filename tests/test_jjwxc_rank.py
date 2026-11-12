# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Check edition identity, original ranks and short-list coverage without a live site."""
import unittest
from unittest.mock import patch

from fagao import jjwxc_rank as rank
from fagao.collectors import CollectorError


def row(number, book_id):
    return f'''<tr><td>{number}</td><td>某作者</td>
        <td><a href="onebook.php?novelid={book_id}" rel="书介&lt;br&gt;第二行">作品 {book_id}</a></td>
        <td>原创-纯爱-幻想未来-剧情</td></td><td><font>完结</font></td>
        <td>402199</td><td>1,234,567</td><td>2025-02-01 12:39:00</td></tr>'''


def page(board, channel, entries):
    href = f'topten.php?orderstr={rank.BOARDS[board]}&amp;t={rank.CHANNELS[channel]}'
    headers = ''.join(f'<td><div>{label}</div></td>' for label in rank.HEADERS)
    return f'''<a href="{href}"><span class="current">{board}</span></a>
        <table><tr><td><a href="onebook.php?novelid=77">推荐</a></td></tr></table>
        <table><tbody><tr>{headers}</tr>{entries}</tbody></table>'''


class JinjiangRankingTests(unittest.TestCase):
    def test_monthly_is_real_branch_ranking_with_original_positions(self):
        html = page('月榜', '纯爱', row(1, 75) + row(3, 91))
        with patch.object(rank, 'fetch_page', return_value=html) as fetch:
            result = rank.collect({'board': '月榜', 'channel': '纯爱', 'category': '全部'})
        self.assertEqual(fetch.call_args.args[0], 'https://www.jjwxc.net/topten.php?orderstr=5&t=1')
        self.assertEqual(fetch.call_args.kwargs['encoding'], 'gb18030')
        self.assertEqual(result['board'], '月榜')
        self.assertIsNone(result['data_date'])
        self.assertEqual([b['rank'] for b in result['books']], [1, 3])
        self.assertEqual(result['books'][0]['url'], 'https://www.jjwxc.net/onebook.php?novelid=75')
        self.assertEqual(result['books'][0]['summary'], '书介 第二行')
        self.assertEqual([m['value'] for m in result['books'][0]['metrics']],
                         ['402199', '1234567', '2025-02-01 12:39:00'])
        self.assertTrue(result['complete'])

    def test_wrong_board_or_channel_is_not_saved_under_requested_label(self):
        for board, channel in [('字数榜', '言情'), ('总分榜', '纯爱')]:
            with self.subTest(board=board, channel=channel), patch.object(
                    rank, 'fetch_page', return_value=page(board, channel, row(1, 9))):
                with self.assertRaises(CollectorError):
                    rank.collect({})

    def test_no_navigation_results_or_untrusted_work_links(self):
        html = page('总分榜', '言情', row(1, 71))
        for bad in (html.replace('作品积分', '收藏'), html.replace('onebook.php?novelid=71', 'https://example.org/1'),
                    page('总分榜', '言情', row(1, 71) + row(2, 71))):
            with patch.object(rank, 'fetch_page', return_value=bad), self.assertRaises(CollectorError):
                rank.collect({})

    def test_cap_uses_the_first_hundred_and_marks_more_rows(self):
        html = page('总分榜', '言情', ''.join(row(n, n+1000) for n in range(1, 106)))
        with patch.object(rank, 'fetch_page', return_value=html):
            result = rank.collect({'limit': 5000})
        self.assertEqual([b['rank'] for b in result['books']], list(range(1, 101)))
        self.assertEqual(result['source_total'], 105)
        self.assertFalse(result['complete'])
