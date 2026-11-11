# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import copy
import unittest

from fagao.insights import build_insights


def book(ident, title='重生后，我在仙界当师尊', rank=1, reads=100, words=10000):
    metrics = []
    if reads is not None:
        metrics.append({'name': '在读', 'value': reads, 'unit': '人'})
    if words is not None:
        metrics.append({'name': '字数', 'value': words, 'unit': '字'})
    return {'id': ident, 'title': title, 'rank': rank, 'metrics': metrics}


def snapshot(ident='now', data_date='2026-11-09', books=None, **fields):
    return {'id': ident, 'platform': 'fanqie', 'mode': 'long', 'channel': '女频',
            'category': '古言脑洞', 'board': '新书榜', 'period': None,
            'data_date': data_date, 'collected_at': '2026-11-09T10:00:00Z',
            'books': [book('a')] if books is None else books, **fields}


class InsightTests(unittest.TestCase):
    def test_missing_metrics_are_not_zero_and_total_is_not_audience(self):
        data = snapshot(books=[book('a', reads=0, words=0), book('b', reads=None, words=None)])
        report = build_insights(data)
        self.assertEqual(report['sample']['books'], 2)
        self.assertEqual(report['sample']['read_count_books'], 1)
        self.assertEqual(report['sample']['total_reads'], 0)
        self.assertEqual(report['sample']['word_count_books'], 1)
        self.assertEqual(report['efficiency'], [])
        self.assertIn('不是去重读者人数', report['methods'][0])
        empty = build_insights(snapshot(books=[book('a', reads=None, words=None)]))
        self.assertIsNone(empty['sample']['total_reads'])
        self.assertIsNone(empty['sample']['total_words'])

    def test_persisted_strings_explicit_units_and_zero_ratio(self):
        a = book('a', reads='2.5万', words='100,000')
        b = book('b', reads='0', words='10')
        b['metrics'][1]['unit'] = '万字'
        report = build_insights(snapshot(books=[a, b]))
        self.assertEqual(report['sample']['total_reads'], 25000)
        self.assertEqual(report['sample']['total_words'], 200000)
        self.assertEqual(report['efficiency'][0]['reads_per_10k_words'], 2500)
        self.assertEqual(report['efficiency'][1]['reads_per_10k_words'], 0)

    def test_invalid_numeric_indicators_are_not_counted(self):
        values = [None, True, -1, float('nan'), float('inf'), '未知', '<1万', '2万月票']
        data = snapshot(books=[book(str(i), reads=v, words=v) for i, v in enumerate(values)])
        report = build_insights(data)
        self.assertEqual(report['sample']['read_count_books'], 0)
        self.assertEqual(report['sample']['word_count_books'], 0)
        self.assertIsNone(report['sample']['total_reads'])
        self.assertEqual(report['efficiency'], [])

    def test_unrelated_metrics_are_not_readership(self):
        data = snapshot(books=[{'id':'a','title':'某书','rank':1,'metrics':[{'name':'月票','value':100,'unit':'票'}]}])
        self.assertEqual(build_insights(data)['sample']['read_count_books'], 0)
        mixed = snapshot(books=[book('a', reads='10万字', words='10万人')])
        self.assertIsNone(build_insights(mixed)['sample']['total_reads'])
        self.assertIsNone(build_insights(mixed)['sample']['total_words'])

    def test_keywords_count_titles_once_and_exclude_unreadable_titles(self):
        titles = ['系统系统：重生后，我逆袭了', '重生归来', '\ue123重生', '书名暂未取得（原榜第4名）']
        report = build_insights(snapshot(books=[book(str(i), title=t) for i,t in enumerate(titles)]))
        counts = {item['word']: item for item in report['keywords']}
        self.assertEqual(report['sample']['title_count'], 2)
        self.assertEqual(counts['系统']['count'], 1)
        self.assertEqual(counts['重生']['count'], 2)
        self.assertEqual(counts['系统']['share'], 50)
        patterns = {item['pattern']: item['count'] for item in report['title_patterns']}
        self.assertEqual(patterns['冒号分段'], 1)
        self.assertEqual(patterns['“…后，…”结构'], 1)

    def test_no_source_date_never_uses_collection_date(self):
        for value in (None, '', '昨天', '2026-99-99'):
            report = build_insights(snapshot(data_date=value), [snapshot('old','2026-11-08')])
            self.assertFalse(report['changes']['available'])
            self.assertIsNone(report['changes']['baseline_snapshot_id'])
            self.assertEqual(report['changes']['items'], [])

    def test_comparison_scope_includes_platform_mode_channel_category_board_period(self):
        for key, value in {'platform':'qidian','mode':'short','channel':'男频',
                           'category':'豪门总裁','board':'阅读榜','period':'周榜'}.items():
            with self.subTest(key=key):
                report = build_insights(snapshot(), [snapshot('old','2026-11-08',**{key:value})])
                self.assertFalse(report['changes']['available'])

    def test_same_source_day_and_future_are_not_growth_baselines(self):
        current = snapshot(data_date='2026-11-09T15:00:00+08:00')
        history = [snapshot('same','2026-11-09T05:00:00+08:00'), snapshot('future','2026-11-10')]
        self.assertFalse(build_insights(current, history)['changes']['available'])

    def test_nearest_previous_source_day_not_seven_day_claim(self):
        current = snapshot(books=[book('a', rank=3, reads=160)])
        baseline = snapshot('nearest', '2026-11-07', books=[book('a', rank=5, reads=100)])
        older = snapshot('older','2026-11-01',books=[book('a',rank=10,reads=10)])
        report = build_insights(current, [older, baseline])
        changes = report['changes']
        self.assertTrue(changes['available'])
        self.assertEqual(changes['baseline_snapshot_id'], 'nearest')
        self.assertEqual(changes['interval_days'], 2)
        self.assertEqual(changes['items'][0]['read_delta'], 60)
        self.assertEqual(changes['items'][0]['read_delta_rate'], 60)
        self.assertEqual(changes['items'][0]['rank_delta'], 2)
        self.assertNotIn('growth7', changes)

    def test_identity_alignment_not_array_order_new_entries_do_not_get_fake_growth(self):
        current = snapshot(books=[book('b',rank=1,reads=200),book('a',rank=2,reads=100),book('new',rank=3,reads=999)])
        previous = snapshot('old','2026-11-08',books=[book('a',rank=1,reads=150),book('b',rank=2,reads=100)])
        changes = build_insights(current, [previous])['changes']
        self.assertEqual(changes['matched_books'], 2)
        rows = {row['book_id']: row for row in changes['items']}
        self.assertEqual(rows['a']['read_delta'], -50)
        self.assertEqual(rows['b']['read_delta'], 100)
        self.assertNotIn('new', rows)
        self.assertEqual(changes['items'][0]['book_id'], 'b')

    def test_zero_baseline_has_absolute_delta_but_no_growth_percentage(self):
        changes = build_insights(snapshot(books=[book('a', reads=50)]),
                                 [snapshot('old','2026-11-08',books=[book('a', reads=0)])])['changes']
        self.assertEqual(changes['items'][0]['read_delta'], 50)
        self.assertIsNone(changes['items'][0]['read_delta_rate'])

    def test_rank_only_comparison_does_not_invent_read_changes(self):
        current = snapshot(books=[book('a',rank=2,reads=None)])
        old = snapshot('old','2026-11-08',books=[book('a',rank=4,reads=None)])
        changes = build_insights(current, [old])['changes']
        self.assertEqual(changes['read_comparable_books'], 0)
        self.assertEqual(changes['rank_comparable_books'], 1)
        self.assertIsNone(changes['items'][0]['read_delta'])
        self.assertEqual(changes['items'][0]['rank_delta'], 2)

    def test_deduplication_bounds_and_non_mutating(self):
        books = [book(str(i), rank=i+1, reads=i*100) for i in range(30)]
        current = snapshot(books=books+[copy.deepcopy(books[0])])
        original = copy.deepcopy(current)
        report = build_insights(current)
        self.assertEqual(report['sample']['books'], 30)
        self.assertEqual(len(report['efficiency']), 20)
        self.assertEqual(current, original)


if __name__ == '__main__':
    unittest.main()
