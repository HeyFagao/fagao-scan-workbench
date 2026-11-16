# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Exercise the official Next data and its independently numbered public list."""
import json
import unittest
from unittest.mock import patch

from fagao import dianzhong_rank as source
from fagao.collectors import CollectorError


def fixture(page=1, pages=7, row_count=10, long_position=None, period='日榜'):
    rows = []
    links = []
    for position in range((page - 1) * 10 + 1, (page - 1) * 10 + row_count + 1):
        ident = str(11010000000 + position)
        rows.append({'bookId': ident, 'bookName': '测试书目' + str(position), 'author': '测试作者',
                     'coverWap': 'https://seoali.zqkanshu.com/fixture.jpg', 'introduction': '页面提供的简介',
                     'num': '12.5万热度', 'totalWordSize': '18000',
                     'bookTypeThree': ['长篇小说' if position == long_position else '精品故事']})
        links.append('<div class="RankBookList_rankItem__zYOGC"><a class="RankBookList_rankIndex__5b3Sj" '
                     f'href="/book/{ident}">{position}</a></div>')
    props = {'rankBook': rows, 'page': page, 'pages': pages, 'rankId': 1, 'rankType': 1,
             'rankStyle': 1 if period == '日榜' else 2,
             'rankPathName': {'rankTypeName': '男生', 'rankIdName': '畅销榜', 'styleName': period}}
    return ''.join(links) + '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
        {'props': {'pageProps': props}}, ensure_ascii=False) + '</script>'


class DianzhongRankTests(unittest.TestCase):
    def test_preserves_original_ranks_when_long_books_are_excluded(self):
        books, count, pages = source.parse_page(fixture(long_position=2), '男频', '畅销榜', '日榜', 1)
        self.assertEqual([book['rank'] for book in books], [1, *range(3, 11)])
        self.assertEqual((count, pages), (10, 7))
        self.assertEqual(books[0]['author'], '测试作者')
        self.assertEqual(books[0]['summary'], '页面提供的简介')
        self.assertEqual(books[0]['metrics'], [{'name': '热度', 'value': '12.5', 'unit': '万'},
                                             {'name': '字数', 'value': '18000', 'unit': '字'}])

    def test_rejects_unconfirmed_scope_or_rank_identity(self):
        for html, page, period in ((fixture(), 2, '日榜'), (fixture(), 1, '月榜'),
                                   (fixture().replace('>1</a>', '>99</a>'), 1, '日榜'),
                                   (fixture().replace('/book/11010000001', '/book/11019999999'), 1, '日榜')):
            with self.subTest(page=page, period=period), self.assertRaises(CollectorError):
                source.parse_page(html, '男频', '畅销榜', period, page)

    def test_paginates_only_to_requested_source_top_fifty(self):
        visited = []
        def fetch(url):
            page = 1 if url.endswith('1-1-1') else int(url.rsplit('/', 1)[1])
            visited.append(page)
            return fixture(page=page)
        with patch.object(source, 'fetch_page', side_effect=fetch), patch.object(source, '_pause') as pause:
            snapshot = source.collect({'limit': 50})
        self.assertEqual(visited, list(range(1, 6)))
        self.assertEqual(pause.call_count, 4)
        self.assertEqual(snapshot['books'][-1]['rank'], 50)
        self.assertIsNone(snapshot['data_date'])
        self.assertEqual(snapshot['pagination']['stop_reason'], 'requested_limit')

    def test_last_page_reports_actual_short_count_and_no_generated_date(self):
        visited = []
        def fetch(url):
            page = 1 if url.endswith('1-1-1') else 2
            visited.append(page)
            return fixture(page, 2, 3 if page == 2 else 10, long_position=2)
        with patch.object(source, 'fetch_page', side_effect=fetch), patch.object(source, '_pause'):
            snapshot = source.collect({})
        self.assertEqual(visited, [1, 2])
        self.assertEqual(len(snapshot['books']), 12)
        self.assertEqual(snapshot['source_rows_seen'], 13)
        self.assertEqual(snapshot['pagination']['stop_reason'], 'last_page')

    def test_stops_on_duplicate_book_or_changed_page_count(self):
        for response in (fixture(page=2).replace('11010000011', '11010000001'), fixture(page=2, pages=8)):
            with self.subTest(response=response[:80]), patch.object(source, 'fetch_page', side_effect=[fixture(), response]), \
                    patch.object(source, '_pause'), self.assertRaises(CollectorError):
                source.collect({})

    def test_rejects_unpublished_monthly_board(self):
        with patch.object(source, 'fetch_page') as fetch, self.assertRaises(CollectorError):
            source.collect({'board': '新书榜', 'period': '月榜'})
        fetch.assert_not_called()

    def test_later_source_failure_does_not_return_a_complete_snapshot(self):
        with patch.object(source, 'fetch_page', side_effect=[fixture(), CollectorError('维护中', 'network_error')]), \
                patch.object(source, '_pause'), self.assertRaises(CollectorError):
            source.collect({})


if __name__ == '__main__':
    unittest.main()
