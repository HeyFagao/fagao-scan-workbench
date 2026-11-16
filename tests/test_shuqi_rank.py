# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Real list markup, explicit source positions, and bounded Shuqi pagination."""
import unittest
from unittest.mock import patch

from fagao import shuqi_rank as source
from fagao.collectors import CollectorError


def fixture(page=1, last_page=50, count=10):
    cards = []
    for position in range((page - 1) * 10 + 1, (page - 1) * 10 + count + 1):
        ident = str(9000000 + position)
        cards.append(f'<li class="clearfix"><a href="/book/{ident}.html"><img src="https://img-tailor.11222.cn/fixture.jpg"></a>'
                     f'<div class="ranklinst-bk"><a href="/book/{ident}.html"><h3>测试书目{position}</h3></a>'
                     '<p class="ranklist-autor"><span class="bkuser-icon"><a>测试作者</a></span>'
                     '<span class="bkcate-icon">都市脑洞</span></p><p class="ranklist-des">来源简介</p>'
                     '<p class="ranklist-tag"><span><a>故事标签</a></span></p></div></li>')
    next_link = f'<a href="/ranklist?rank=boyClick&page={page + 1}">下一页</a>' if page < last_page else ''
    return ('<div class="nav active">男频点击榜</div><ul class="ranklist-ul">' + ''.join(cards) + '</ul>'
            f'<div class="comp-web-pages"><span class="cp-wp-page active">{page}</span>{next_link}</div>')


class ShuqiRankTests(unittest.TestCase):
    def test_public_list_preserves_metadata_and_labels_list_order(self):
        books, next_url = source.parse_page(fixture(), '男频', '点击榜', 1, source.ORIGIN + '/ranklist?rank=boyClick')
        self.assertEqual(books[0]['author'], '测试作者')
        self.assertEqual(books[0]['summary'], '来源简介')
        self.assertEqual(books[0]['tags'], ['都市脑洞', '故事标签'])
        self.assertEqual(books[0]['metrics'], [])
        self.assertEqual(books[0]['rank_semantics'], 'source_list_position')
        self.assertEqual(next_url, source.ORIGIN + '/ranklist?rank=boyClick&page=2')

    def test_rejects_wrong_scope_page_and_external_pagination(self):
        for html in (fixture().replace('男频点击榜', '女频点击榜'),
                     fixture().replace('active">1</span>', 'active">2</span>'),
                     fixture().replace('/ranklist?rank=boyClick&page=2', 'https://example.invalid/evil'),
                     fixture().replace('rank=boyClick&page=2', 'rank=girlClick&page=2')):
            with self.subTest(html=html[:100]), self.assertRaises(CollectorError):
                source.parse_page(html, '男频', '点击榜', 1, source.ORIGIN + '/ranklist?rank=boyClick')

    def test_top_fifty_uses_five_pages_and_waits_between_pages(self):
        visits = []
        def fetch(url, **kwargs):
            self.assertEqual(kwargs['headers']['User-Agent'], source.READER_AGENT)
            page = 1 if '&page=' not in url else int(url.rsplit('=', 1)[1])
            visits.append(page)
            return fixture(page)
        with patch.object(source, 'fetch_page', side_effect=fetch), patch.object(source, '_pause') as pause:
            snapshot = source.collect({'limit': 50})
        self.assertEqual(visits, list(range(1, 6)))
        self.assertEqual(pause.call_count, 4)
        self.assertEqual(len(snapshot['books']), 50)
        self.assertEqual(snapshot['rank_semantics'], 'source_list_position')
        self.assertEqual(snapshot['pagination']['stop_reason'], 'requested_limit')
        self.assertIsNone(snapshot['data_date'])

    def test_bounds_large_requests_to_one_hundred(self):
        with patch.object(source, 'fetch_page', side_effect=[fixture(page) for page in range(1, 11)]), \
                patch.object(source, '_pause'):
            snapshot = source.collect({'limit': 999, 'max_pages': 100})
        self.assertEqual(len(snapshot['books']), 100)
        self.assertEqual(snapshot['requested_count'], 100)

    def test_short_last_page_and_duplicate_book_failure(self):
        with patch.object(source, 'fetch_page', side_effect=[fixture(1, 2), fixture(2, 2, 3)]), \
                patch.object(source, '_pause'):
            snapshot = source.collect({})
        self.assertEqual(len(snapshot['books']), 13)
        self.assertEqual(snapshot['pagination']['stop_reason'], 'last_page')
        duplicate = fixture(2).replace('9000011', '9000001')
        with patch.object(source, 'fetch_page', side_effect=[fixture(), duplicate]), \
                patch.object(source, '_pause'), self.assertRaises(CollectorError):
            source.collect({})

    def test_unknown_period_never_requests_a_page(self):
        with patch.object(source, 'fetch_page') as fetch, self.assertRaises(CollectorError):
            source.collect({'period': '月榜'})
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
