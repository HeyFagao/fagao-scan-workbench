# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import unittest

from fagao.insights import build_insights, build_trends


def book(ident, rank=1, value=100, label='在读', unit='人'):
    return {'id': ident, 'title': '样本' + ident, 'rank': rank,
            'metrics': [{'name': label, 'value': value, 'unit': unit}]}


def snapshot(ident, day='2026-11-16', books=None, **fields):
    return {'id': ident, 'platform': 'fanqie', 'mode': 'long', 'channel': '女频',
            'category': '古言脑洞', 'board': '新书榜', 'period': None,
            'data_date': day, 'collected_at': '2026-11-16T10:00:00Z',
            'books': [book('a')] if books is None else books, **fields}


class RankingTrendTests(unittest.TestCase):
    def test_source_days_use_one_scope_and_never_mix_sources_or_channels(self):
        current = snapshot('now', books=[book('a', 2, 150)])
        history = [snapshot('day1', '2026-11-14', [book('a', 5, 70)]),
                   snapshot('day2', '2026-11-15', [book('a', 3, 100)]),
                   snapshot('wrong-category', '2026-11-15', [book('a', 1, 1)], category='年代'),
                   snapshot('wrong-channel', '2026-11-15', [book('a', 1, 1)], channel='男频'),
                   snapshot('wrong-source', '2026-11-15', [book('a', 1, 1)], source_id='github:example'),
                   snapshot('wrong-period', '2026-11-15', [book('a', 1, 1)], period='周榜')]
        before = copy.deepcopy([current, history])
        report = build_insights(current, iter(history))['trends']
        item = report['items'][0]
        self.assertEqual(report['basis'], 'source_date')
        self.assertEqual(report['baseline_snapshot_id'], 'day2')
        self.assertEqual([point['snapshot_id'] for point in item['points']], ['day1', 'day2', 'now'])
        self.assertEqual((item['rank_delta'], item['metric_delta'], item['metric_delta_rate']), (1, 50, 50))
        self.assertTrue(item['history_available'])
        self.assertEqual([current, history], before)

    def test_qimao_uses_real_collection_days_and_labels_them(self):
        def q(ident, collected, value, **fields):
            return snapshot(ident, None, [book('a', 2, value, '热度', '万')],
                            platform='qimao', board='大热榜', category='全部', period='日榜',
                            collected_at=collected, **fields)
        current = q('now', '2026-11-16T10:00:00Z', '25')
        history = [q('early', '2026-11-15T01:00:00Z', '10'),
                   q('latest-previous', '2026-11-15T12:00:00Z', '20'),
                   q('same-china-day', '2026-11-15T18:00:00Z', '22'),
                   q('future-same-day', '2026-11-16T11:00:00Z', '40')]
        report = build_trends(current, history)
        item = report['items'][0]
        self.assertEqual((report['basis'], report['basis_label']), ('collection_time', '采集日观察'))
        self.assertEqual(report['baseline_snapshot_id'], 'latest-previous')
        self.assertEqual([point['snapshot_id'] for point in item['points']], ['latest-previous', 'now'])
        self.assertTrue(all(point['data_date'] is None for point in item['points']))
        self.assertEqual(item['metric_delta'], 50000)

    def test_first_sample_and_new_sample_book_have_no_invented_delta(self):
        current = snapshot('now', books=[book('new')])
        initial = build_trends(current)
        self.assertFalse(initial['items'][0]['first_seen_in_sample'])
        self.assertFalse(initial['items'][0]['history_available'])
        self.assertIsNone(initial['items'][0]['rank_delta'])
        old = snapshot('old', '2026-11-15', [book('another')])
        report = build_trends(current, [old])
        item = report['items'][0]
        self.assertTrue(item['first_seen_in_sample'])
        self.assertIsNone(item['metric_delta'])
        self.assertEqual(len(item['points']), 1)

    def test_missing_or_wrong_unit_metrics_stay_missing_and_zero_baseline_has_no_rate(self):
        current = snapshot('now', books=[book('a', value=20), book('b', value='10万字'),
                                         book('c', value=True)])
        old = snapshot('old', '2026-11-15', [book('a', value=0), book('b'), book('c')])
        items = {item['book_id']: item for item in build_trends(current, [old])['items']}
        self.assertEqual(items['a']['metric_delta'], 20)
        self.assertIsNone(items['a']['metric_delta_rate'])
        self.assertIsNone(items['b']['metric_value'])
        self.assertIsNone(items['b']['metric_delta'])
        self.assertIsNone(items['c']['metric_value'])

    def test_same_edition_monthly_qimao_observations_do_not_compare_reset_months(self):
        common = {'platform': 'qimao', 'category': '全部', 'board': '收藏榜', 'period': '累计'}
        current = snapshot('now', '2026-11', [book('a', value=15, label='收藏数', unit='万')],
                           **common)
        old = snapshot('wrong-edition', '2026-10', [book('a', value=40, label='收藏数', unit='万')],
                       collected_at='2026-10-31T10:00:00Z', **common)
        report = build_trends(current, [old])
        self.assertIsNone(report['baseline_snapshot_id'])
        self.assertEqual(report['metric_label'], '收藏数')
        self.assertEqual(report['items'][0]['metric_value'], 150000)

    def test_cross_board_uses_same_observation_day_channel_and_source(self):
        current = snapshot('now')
        history = [snapshot('read', board='阅读榜'),
                   snapshot('different-topic', category='年代'),
                   snapshot('old-read', '2026-11-15', board='阅读榜'),
                   snapshot('other-channel', board='阅读榜', channel='男频'),
                   snapshot('other-source', board='阅读榜', source_id='github:example'),
                   snapshot('future', board='阅读榜', collected_at='2026-11-16T11:00:00Z')]
        cross = build_trends(current, history)['cross_board']['items']
        self.assertEqual(len(cross), 1)
        refs = cross[0]['appearances']
        self.assertEqual({item['snapshot_id'] for item in refs}, {'now', 'read', 'different-topic'})
        self.assertTrue(all(item['rank'] == 1 for item in refs))

    def test_no_valid_time_or_unsupported_platform_yields_empty_trends(self):
        self.assertEqual(build_trends(snapshot('now', None, collected_at='unknown'))['items'], [])
        self.assertEqual(build_trends(snapshot('now', platform='qidian'))['items'], [])


if __name__ == '__main__':
    unittest.main()
