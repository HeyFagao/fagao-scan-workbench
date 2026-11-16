# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Regression checks for the ordinary public Hongguo chart's SSR structure."""
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fagao import hongguo_rank as hg
from fagao.collectors import CollectorError


def source_row(rank):
    ident = str(7700000000000000000 + rank)
    return {'id': ident, 'seriesId': ident, 'rank': rank, 'title': '样本剧' + str(rank),
            'cover': 'https://p3-novel.byteimg.com/novel-pic/sample~tplv-shrink:640:0.image',
            'href': '/detail?series_id=' + ident, 'playHref': '/player/' + ident,
            'rankType': 'hot_play', 'reservationOnly': False,
            'primaryMetricText': '10504万热度', 'heatText': '10504万热度',
            'statusTags': ['新剧'], 'scoreText': '9.5分', 'favoriteText': '75.7万收藏',
            'likeText': '156万点赞', 'tags': ['AI剧', '宫廷'], 'description': '真实结构的简介样本。',
            'episodeCount': 113, 'openEpisodeCount': 3, 'episodeVids': ['unused']}


def source_page(slug='hot-drama', page=1, total_pages=5, count=20, mutate=None):
    url = hg.ORIGIN + '/rank/' + slug + ('?page=' + str(page) if page > 1 else '')
    pagination = {'pageNum': page, 'totalPages': total_pages}
    if page < total_pages:
        pagination['nextUrl'] = hg.ORIGIN + '/rank/' + slug + '?page=' + str(page + 1)
    data = {'canonicalUrl': url, 'rankKey': 'hongguo', 'pageNum': page,
            'updatedText': '11月16日已更新·基于红果内观看/互动等综合热度排序', 'isCrawler': False,
            'content': {'isSuccess': True,
                        'rankList': [source_row(i) for i in range((page - 1) * 20 + 1,
                                                                  (page - 1) * 20 + count + 1)],
                        'pagination': pagination}}
    if mutate:
        mutate(data)
    state = {'loaderData': {'rank_layout': None, 'rank_' + slug + '/page': data}}
    return '<html><script>_ROUTER_DATA = ' + json.dumps(state, ensure_ascii=False) + ';</script></html>'


class HongguoRankTests(unittest.TestCase):
    def test_public_row_keeps_real_identity_poster_description_and_metrics(self):
        books, pages, label = hg.parse_page(source_page(), 'hot-drama', 1)
        book = books[0]
        self.assertEqual((len(books), pages), (20, 5))
        self.assertEqual(book['id'], '7700000000000000001')
        self.assertEqual(book['url'], hg.ORIGIN + '/detail?series_id=7700000000000000001')
        self.assertEqual(book['rank'], 1)
        self.assertEqual(book['rank_semantics'], 'original_rank')
        self.assertEqual(book['summary'], '真实结构的简介样本。')
        self.assertTrue(book['cover_url'].endswith('~tplv-shrink:640:0.image'))
        self.assertEqual(book['metrics'], [{'name': '热度', 'value': '10504', 'unit': '万'},
                                           {'name': '评分', 'value': '9.5', 'unit': ''},
                                           {'name': '收藏', 'value': '75.7', 'unit': '万'},
                                           {'name': '点赞', 'value': '156', 'unit': '万'},
                                           {'name': '集数', 'value': 113, 'unit': '集'}])
        self.assertNotIn('playHref', book)
        self.assertNotIn('episodeVids', book)
        self.assertIn('已更新', label)

    def test_each_board_is_bound_to_its_requested_official_route(self):
        for board, slug in hg.BOARDS.items():
            with self.subTest(board=board):
                books, _, _ = hg.parse_page(source_page(slug), slug, 1)
                self.assertEqual(len(books), 20)
                with self.assertRaises(CollectorError):
                    hg.parse_page(source_page(slug), 'unrelated', 1)

    def test_wrong_ranking_page_or_next_page_is_rejected(self):
        mutations = [lambda d: d.update(canonicalUrl=hg.ORIGIN + '/rank/hot-ai-drama'),
                     lambda d: d.update(pageNum=2),
                     lambda d: d['content']['pagination'].update(pageNum=2),
                     lambda d: d['content']['pagination'].update(totalPages=6),
                     lambda d: d['content']['pagination'].update(nextUrl='https://example.org/'),
                     lambda d: d['content'].update(isSuccess=False)]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(CollectorError):
                hg.parse_page(source_page(mutate=mutate), 'hot-drama', 1)

    def test_duplicate_and_mismatched_source_ids_or_ranks_are_rejected(self):
        def change(field, value):
            return lambda data: data['content']['rankList'][1].update({field: value})
        for mutate in (change('rank', 1), change('href', '/detail?series_id=666'),
                       change('id', '666'), change('seriesId', '666')):
            with self.subTest(mutate=mutate), self.assertRaises(CollectorError):
                hg.parse_page(source_page(mutate=mutate), 'hot-drama', 1)

    def test_missing_or_non_json_payload_is_never_executed(self):
        for html in ('<h1>验证访问</h1>', '<script>_ROUTER_DATA = alert("oops")</script>'):
            with self.subTest(html=html), self.assertRaises(CollectorError):
                hg.parse_page(html, 'hot-drama', 1)

    def test_unavailable_images_and_metrics_remain_missing(self):
        row = source_row(1)
        row.update(cover='https://example.org/tracker', heatText='暂无数据', scoreText='', episodeCount=0)
        book = hg._book(row)
        self.assertEqual(book['cover_url'], '')
        self.assertNotIn('热度', [m['name'] for m in book['metrics']])
        self.assertNotIn('评分', [m['name'] for m in book['metrics']])
        self.assertNotIn('集数', [m['name'] for m in book['metrics']])

    def collect(self, **options):
        self.urls = []
        def fetch(url, **kwargs):
            self.urls.append(url)
            self.assertEqual(kwargs['headers']['User-Agent'], hg.PUBLIC_AGENT)
            page = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
            return source_page(page=page)
        with patch.object(hg, 'fetch_page', side_effect=fetch), patch.object(hg, '_pause') as pause:
            snapshot = hg.collect(options)
        return snapshot, pause

    def test_default_top100_paces_only_the_four_needed_next_pages(self):
        snapshot, pause = self.collect()
        self.assertEqual([b['rank'] for b in snapshot['books']], list(range(1, 101)))
        self.assertEqual(pause.call_count, 4)
        self.assertEqual(len(self.urls), 5)
        self.assertEqual(snapshot['pagination']['stop_reason'], 'last_page')
        self.assertTrue(snapshot['complete'])
        self.assertIsNone(snapshot['data_date'])
        self.assertEqual(snapshot['source_id'], 'hongguo:hot-drama')
        self.assertIn('已更新', snapshot['source_label_date'])

    def test_limit50_stops_after_three_pages_and_preserves_original_rank(self):
        snapshot, pause = self.collect(limit=50)
        self.assertEqual(len(snapshot['books']), 50)
        self.assertEqual(snapshot['books'][-1]['rank'], 50)
        self.assertEqual(pause.call_count, 2)
        self.assertEqual(len(self.urls), 3)
        self.assertEqual(snapshot['pagination']['stop_reason'], 'requested_limit')

    def test_max_pages_is_an_actual_partial_stop(self):
        snapshot, _ = self.collect(max_pages=1)
        self.assertEqual(len(snapshot['books']), 20)
        self.assertEqual(snapshot['pagination']['pages_fetched'], 1)
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['pagination']['stop_reason'], 'max_pages')

    def test_short_real_last_page_preserves_actual_count(self):
        with patch.object(hg, 'fetch_page', return_value=source_page(total_pages=1, count=8)):
            snapshot = hg.collect({})
        self.assertEqual(len(snapshot['books']), 8)
        self.assertTrue(snapshot['complete'])
        self.assertEqual(snapshot['pagination']['stop_reason'], 'last_page')

    def test_label_or_same_id_changed_during_paging_stops_before_new_snapshot(self):
        for mutate in (lambda d: d.update(updatedText='日期标签变化'),
                       lambda d: d['content']['rankList'][0].update(
                           id='7700000000000000001', seriesId='7700000000000000001',
                           href='/detail?series_id=7700000000000000001')):
            def fetch(url, **kwargs):
                return source_page() if '?' not in url else source_page(page=2, mutate=mutate)
            with self.subTest(mutate=mutate), patch.object(hg, 'fetch_page', side_effect=fetch), \
                    patch.object(hg, '_pause'), self.assertRaises(CollectorError):
                hg.collect({})

    def test_pause_checks_permit_between_each_second_and_before_next_request(self):
        with patch.object(hg.time, 'sleep') as sleep, patch.object(hg.network, 'check_authorization') as check:
            hg._pause(None, '下一页')
        self.assertEqual(sleep.call_count, 15)
        self.assertEqual(check.call_count, 15)
        with patch.object(hg.time, 'sleep'), patch.object(hg.network, 'check_authorization',
                                                     side_effect=RuntimeError('cancelled')):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                hg._pause(None, '下一页')


if __name__ == '__main__':
    unittest.main()
