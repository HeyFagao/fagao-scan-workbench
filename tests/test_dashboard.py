# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import unittest
import tempfile

from fagao.dashboard import SCATTER_LIMIT, quantile
from fagao.overview import build_overview
from fagao.store import Store


def book(ident, reads=20000, words=100000, title=None, **fields):
    metrics = []
    if reads is not None:
        metrics.append({'name': '在读', 'value': reads, 'unit': '人'})
    if words is not None:
        metrics.append({'name': '字数', 'value': words, 'unit': '字'})
    return {'id': str(ident), 'rank': int(ident) if str(ident).isdigit() and int(ident) > 0 else 1,
            'title': title or '重生后，我成了师尊', 'metrics': metrics, **fields}


def snapshot(ident='now', books=None, **fields):
    return {'id': ident, 'platform': 'fanqie', 'mode': 'long', 'channel': '女频',
            'category': '古言脑洞', 'board': '新书榜', 'period': None,
            'data_date': '2026-11-09', 'collected_at': '2026-11-09T12:00:00Z',
            'complete': True, 'books': books if books is not None else [book('1')], **fields}


def view(snapshots, platform='fanqie', channel='全部'):
    result = build_overview(snapshots)
    return next(panel for panel in result['platforms'] if panel['platform'] == platform)['dashboard']['views'][channel]


class DashboardTests(unittest.TestCase):
    def test_store_preserves_zero_metrics_through_real_dashboard_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            saved = store.save_snapshot(snapshot(books=[book('1', reads=0, words=0), book('2', reads=None, words=None)]))
            values = {m['name']: m['value'] for m in saved['books'][0]['metrics']}
            self.assertEqual(values, {'在读': '0', '字数': '0'})
            result = view(store.overview_history())
            self.assertEqual(result['kpis']['valid_primary_count'], 1)
            self.assertEqual(result['kpis']['primary_median'], 0)
            self.assertEqual(result['scatter']['total'], 1)
            self.assertEqual(result['scatter']['points'][0]['word_count'], 0)
            self.assertIsNone(result['kpis']['efficiency_median'])

    def test_quantiles_use_valid_samples_and_keep_true_zero(self):
        self.assertEqual(quantile([1, 2, 3, 4], .5), 2.5)
        self.assertEqual(quantile([1, 2, 3, 4], .9), 3.7)
        self.assertEqual(quantile([None, False, float('nan'), -1, 0], .5), 0)
        self.assertIsNone(quantile([None, float('inf')], .5))

    def test_reading_boards_do_not_contribute_chart_rows_or_samples(self):
        current = snapshot()
        reading = snapshot('read', board='阅读榜', books=[book('999', reads=10**10)])
        self.assertEqual(view([current, reading]), view([current]))

    def test_each_channel_deduplicates_after_filtering(self):
        female = snapshot('f', books=[book('same', reads=20000), book('f', reads=10000)])
        male = snapshot('m', channel='男频', category='玄幻脑洞', books=[book('same', reads=90000)],
                        collected_at='2026-11-09T13:00:00Z')
        female_view = view([female, male], channel='女频')
        male_view = view([female, male], channel='男频')
        all_view = view([female, male])
        self.assertEqual(female_view['kpis']['books'], 2)
        self.assertEqual(female_view['kpis']['primary_median'], 15000)
        self.assertEqual(male_view['kpis']['books'], 1)
        self.assertEqual(male_view['kpis']['primary_median'], 90000)
        self.assertEqual(all_view['kpis']['books'], 2)
        self.assertEqual(sum(row['count'] for row in all_view['categories']), 3)

    def test_missing_and_zero_metrics_have_different_denominators(self):
        report = view([snapshot(books=[book('1', reads=0, words=0), book('2', reads=100, words=10000),
                                      book('3', reads=None, words=None)])])
        self.assertEqual(report['kpis']['books'], 3)
        self.assertEqual(report['kpis']['valid_primary_count'], 2)
        self.assertEqual(report['kpis']['primary_median'], 50)
        self.assertEqual(report['kpis']['valid_words_count'], 2)
        self.assertEqual(report['kpis']['valid_efficiency_count'], 1)
        self.assertEqual(sum(item['count'] for item in report['primary_bins']), 2)
        self.assertEqual(report['scatter']['total'], 2)

    def test_histogram_boundaries_are_lower_inclusive_upper_exclusive(self):
        values = [0, 999, 1000, 9999, 10000, 49999, 50000, 99999, 100000, 499999, 500000]
        report = view([snapshot(books=[book(str(i), reads=value, words=value) for i, value in enumerate(values)])])
        self.assertEqual([row['count'] for row in report['primary_bins']], [2, 2, 2, 2, 2, 1])
        self.assertEqual(sum(row['count'] for row in report['word_bins']), len(values))
        self.assertAlmostEqual(sum(row['share'] for row in report['primary_bins']), 100, places=1)

    def test_blackhorse_thresholds_include_endpoints(self):
        rows = [book('1', reads=10000, words=80000), book('2', reads=10000, words=150000),
                book('3', reads=9999, words=100000), book('4', words=79999),
                book('5', words=150001), book('6', words=None)]
        result = view([snapshot(books=rows)])['blackhorse']
        self.assertEqual(result['eligible_count'], 2)
        self.assertEqual([row['book_id'] for row in result['items']], ['1', '2'])
        self.assertEqual(result['scored_count'], 0)
        self.assertTrue(all(row['score'] is None and row['delta'] is None for row in result['items']))

    def test_weighted_index_preserves_zero_and_negative_real_changes(self):
        previous = snapshot('old', books=[book('1', reads=15000), book('2', reads=20000), book('3', reads=50000)],
                            data_date='2026-11-06')
        current = snapshot(books=[book('1', reads=20000), book('2', reads=20000), book('3', reads=20000), book('4')])
        result = view([previous, current])['blackhorse']
        rows = {row['book_id']: row for row in result['items']}
        self.assertEqual(result['scored_count'], 3)
        self.assertEqual(rows['1']['score'], 1205)
        self.assertEqual(rows['2']['score'], 1200)
        self.assertEqual(rows['3']['score'], 1170)
        self.assertEqual(rows['3']['delta'], -30000)
        self.assertEqual(rows['2']['delta'], 0)
        self.assertEqual(rows['1']['interval_days'], 3)
        self.assertIsNone(rows['4']['score'])

    def test_index_uses_original_words_without_rounded_wan_units(self):
        previous = snapshot('old', books=[book('1', reads=19000, words=123456)], data_date='2026-11-08')
        current = snapshot(books=[book('1', reads=20000, words=123456)])
        row = view([current, previous])['blackhorse']['items'][0]
        self.assertEqual(row['score'], round(.6 * (20000 * 10000 / 123456) + 1000 / 1000, 2))

    def test_same_day_and_missing_dates_do_not_become_growth(self):
        prior = snapshot('old', books=[book('1', reads=15000)], collected_at='2026-11-09T10:00:00Z')
        for day in ['2026-11-09', None]:
            row = view([prior, snapshot(data_date=day)])['blackhorse']['items'][0]
            self.assertIsNone(row['score'])
            self.assertIsNone(row['delta'])

    def test_cross_category_history_uses_selected_reference_scope(self):
        old_a = snapshot('old-a', books=[book('same', reads=10000)], data_date='2026-11-08')
        new_a = snapshot('new-a', books=[book('same', reads=90000)])
        old_b = snapshot('old-b', category='现言脑洞', books=[book('same', reads=30000)], data_date='2026-11-08')
        new_b = snapshot('new-b', category='现言脑洞', books=[book('same', reads=20000)], collected_at='2026-11-09T14:00:00Z')
        row = view([old_a, new_a, old_b, new_b])['blackhorse']['items'][0]
        self.assertEqual(row['snapshot_id'], 'new-b')
        self.assertEqual(row['delta'], -10000)
        self.assertEqual(row['baseline_snapshot_id'], 'old-b')

    def test_history_not_limited_to_positive_top_twelve(self):
        before = snapshot('old', data_date='2026-11-08', books=[book(str(i), reads=20000) for i in range(1, 26)])
        after = snapshot(books=[book(str(i), reads=20000 + i if i < 25 else 19000) for i in range(1, 26)])
        result = view([before, after])['blackhorse']
        self.assertEqual(result['eligible_count'], 25)
        self.assertEqual(result['scored_count'], 25)

    def test_scatter_sampling_is_deterministic_and_keeps_extremes(self):
        rows = [book(str(i), reads=(i * 7919) % 500000, words=i * 1000) for i in range(1, 601)]
        first = view([snapshot(books=rows)])['scatter']
        second = view([snapshot(books=list(reversed(rows)))])['scatter']
        self.assertEqual(first, second)
        self.assertTrue(first['sampled'])
        self.assertEqual(first['total'], 600)
        self.assertLessEqual(len(first['points']), SCATTER_LIMIT)
        chosen = {item['book_id'] for item in first['points']}
        self.assertIn('1', chosen); self.assertIn('600', chosen)
        self.assertIn(str(max(rows, key=lambda row: row['metrics'][0]['value'])['id']), chosen)
        self.assertIn(str(min(rows, key=lambda row: row['metrics'][0]['value'])['id']), chosen)

    def test_other_platform_uses_tickets_and_never_read_efficiency(self):
        row = book('1', reads=None, metrics=[{'name': '月票', 'value': '1.2万', 'unit': '票'}, {'name': '字数', 'value': 200000, 'unit': '字'}])
        report = view([snapshot(platform='qidian', channel='男频', category='全部', board='月票榜', books=[row])], 'qidian')
        self.assertEqual(report['metric_label'], '月票')
        self.assertEqual(report['kpis']['primary_median'], 12000)
        self.assertIsNone(report['kpis']['efficiency_median'])
        self.assertFalse(report['blackhorse']['enabled'])

    def test_missing_metric_platform_still_has_real_title_statistics(self):
        report = view([snapshot(platform='ciweimao', channel='综合', category='全部', board='月票榜', period='月榜',
                                books=[book('1', reads=None, words=None, title='短书名'), book('2', reads=None, words=None)])], 'ciweimao')
        self.assertEqual(report['kpis']['books'], 2)
        self.assertIsNone(report['kpis']['primary_median'])
        self.assertEqual(sum(row['count'] for row in report['primary_bins']), 0)
        self.assertEqual(sum(row['count'] for row in report['title_length_bins']), 2)

    def test_pattern_denominator_is_channel_valid_titles(self):
        female = snapshot(books=[book('1'), book('2', title='普通标题'), book('3', title='书名暂未取得3')])
        male = snapshot('male', channel='男频', category='玄幻脑洞', books=[book('4')])
        report = view([female, male], channel='女频')
        pattern = next(row for row in report['patterns'] if row['label'] == '重启开场')
        self.assertEqual(pattern['count'], 1)
        self.assertEqual(pattern['share'], 50)
        self.assertEqual(report['kpis']['valid_title_count'], 2)

    def test_input_is_not_modified(self):
        records = [snapshot(), snapshot('old', data_date='2026-11-08')]
        original = copy.deepcopy(records)
        view(records)
        self.assertEqual(records, original)


if __name__ == '__main__':
    unittest.main()
