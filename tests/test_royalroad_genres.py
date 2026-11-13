# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import json
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.overseas import OVERSEAS_PLATFORMS, collect_overseas


def sample():
    return [{'story': {'royalRoadId': 1000 + rank, 'title': f'Book {rank}', 'description': '$ab'},
             'rank': 51 - rank, 'categoryRanks': {'romance': {'rank': rank, 'source': 'observed',
             'observedAt': '$D2026-01-23T12:03:04.000Z'}}} for rank in range(50, 0, -1)]


def page(rows, genre='romance'):
    table = ['$', '$component', None, {'rankSortCategory': genre, 'rankings': rows}]
    return '<script>self.__next_f.push(' + json.dumps([1, 'ab:' + json.dumps(table) + '\n']) + ')</script>'


class RoyalRoadGenreTests(unittest.TestCase):
    def test_new_categories_and_scoped_observed_order_through_public_collector(self):
        platform = next(p for p in OVERSEAS_PLATFORMS if p['id'] == 'royalroad')
        self.assertEqual(platform['default_options']['category'], '全部')
        self.assertEqual(len(platform['categories']), 16)
        with patch('fagao.royalroad_genres._fetch', return_value=page(sample())) as fetch:
            result = collect_overseas('royalroad', {'category': '言情', 'limit': 3}, None)
        fetch.assert_called_once_with('https://rrtrack.app/?category=romance')
        self.assertEqual(result['category'], '言情')
        self.assertEqual(result['source_total'], 50)
        self.assertEqual(result['data_date'], '2026-01-23T12:03:04.000Z')
        self.assertEqual([b['id'] for b in result['books']], ['1001', '1002', '1003'])
        self.assertEqual([b['rank'] for b in result['books']], [1, 2, 3])
        self.assertEqual(result['books'][0]['summary'], '')

    def test_mismatched_partial_estimated_or_mixed_time_never_become_genre_rankings(self):
        failures = [page(sample()[:-1]), page(sample(), 'fantasy')]
        for change in [{'source': 'shadow'}, {'observedAt': '2026-01-25T12:03:04Z'}, {'rank': 1}]:
            rows = sample()
            rows[0]['categoryRanks']['romance'].update(change)
            failures.append(page(rows))
        for html in failures:
            with self.subTest(size=len(html)), patch('fagao.royalroad_genres._fetch',return_value=html):
                with self.assertRaises(CollectorError):
                    collect_overseas('royalroad', {'category': '言情'}, None)


if __name__ == '__main__':
    unittest.main()
