import copy
import unittest

from fagao.overview import build_overview, relevant_snapshot_ids


def book(ident, title='重生后，我在仙界当师尊', rank=1, reads=100, words=10000, **fields):
    metrics = []
    if reads is not None:
        metrics.append({'name': '在读', 'value': reads, 'unit': '人'})
    if words is not None:
        metrics.append({'name': '字数', 'value': words, 'unit': '字'})
    return {'id': ident, 'title': title, 'author': '作者', 'rank': rank,
            'metrics': metrics, 'cover_url': 'https://example.com/cover.jpg', **fields}


def snapshot(ident='now', data_date='2026-11-09', books=None, **fields):
    return {'id': ident, 'platform': 'fanqie', 'mode': 'long', 'channel': '女频',
            'category': '古言脑洞', 'board': '新书榜', 'period': None,
            'data_date': data_date, 'collected_at': '2026-11-09T10:00:00Z',
            'complete': True, 'categories': ['古言脑洞'],
            'books': [book('a')] if books is None else books, **fields}


def panel(snapshots, platform='fanqie'):
    return next(p for p in build_overview(snapshots)['platforms'] if p['platform'] == platform)


class OverviewTests(unittest.TestCase):
    def test_reading_board_never_contributes_to_any_overview_output(self):
        current = snapshot()
        reading = snapshot('reading', books=[book(f'read-{i}', '阅读榜污染独占词', reads=10**9)
                                            for i in range(400)], board='阅读榜')
        male_reading = snapshot('male-reading', channel='男频', board='阅读榜',
                                books=[book('male-read', '阅读榜污染词')])
        self.assertEqual(build_overview([current, reading, male_reading]), build_overview([current]))

    def test_wrong_platform_mode_scope_and_category_are_excluded(self):
        ignored = [snapshot('short', mode='short'), snapshot('unknown', channel='未知'),
                   snapshot('qidian-new', platform='qidian', channel='男频', category='全部'),
                   snapshot('ciwei-daily', platform='ciweimao', channel='综合', category='全部', board='月票榜', period='日榜'),
                   snapshot('qidian-female', platform='qidian', channel='女频', category='全部', board='月票榜')]
        self.assertEqual(build_overview(ignored)['summary']['books'], 0)

    def test_latest_source_date_wins_over_late_collection_of_old_data(self):
        latest = snapshot('latest', books=[book('a', reads=300)])
        delayed = snapshot('delayed', '2026-11-08', books=[book('old', reads=999)],
                           collected_at='2026-11-10T20:00:00Z')
        result = panel([latest, delayed])
        self.assertEqual(result['summary']['books'], 1)
        self.assertEqual(result['candidates'][0]['book_id'], 'a')
        self.assertEqual(result['summary']['total_metric'], 300)

    def test_latest_snapshot_same_date_uses_collected_time(self):
        latest = snapshot('latest', books=[book('a', reads=300)], collected_at='2026-11-09T12:00:00Z')
        first = snapshot('first', books=[book('a', reads=50)])
        self.assertEqual(panel([latest, first])['summary']['total_metric'], 300)
        self.assertFalse(panel([latest, first])['growth']['available'])

    def test_source_version_time_wins_over_later_fetch_on_same_day(self):
        newer = snapshot('newer', '2026-11-09T08:00:00Z', books=[book('new')])
        later_fetch = snapshot('later-fetch', '2026-11-09T07:00:00Z', books=[book('old')],
                               collected_at='2026-11-09T15:00:00Z')
        report = panel([newer, later_fetch])
        self.assertEqual(report['candidates'][0]['book_id'], 'new')

    def test_same_version_preview_does_not_degrade_fuller_result(self):
        full = snapshot('full', books=[book(str(i)) for i in range(20)])
        preview = snapshot('preview', books=[book('0')], complete=False,
                           collected_at='2026-11-09T15:00:00Z')
        report = panel([full, preview])
        self.assertEqual(report['summary']['books'], 20)
        self.assertTrue(all(c['snapshot_id'] == 'full' for c in report['candidates']))
        # A genuinely newer source version may be displayed progressively.
        preview['data_date'] = '2026-11-10'
        self.assertEqual(panel([full, preview])['summary']['books'], 1)

    def test_dedup_across_categories_preserves_original_references(self):
        a = snapshot('one', books=[book('a', reads=100, rank=14)])
        b = snapshot('two', category='现言脑洞', books=[book('a', reads=200, rank=2), book('b', reads=50)],
                     collected_at='2026-11-09T12:00:00Z')
        report = panel([a, b])
        self.assertEqual(report['summary']['books'], 2)
        self.assertEqual(report['summary']['total_metric'], 250)
        self.assertEqual(sum(c['books'] for c in report['categories']), 3)
        chosen = next(c for c in report['candidates'] if c['book_id'] == 'a')
        self.assertEqual(chosen['selection'], {'snapshot_id': 'two', 'book_id': 'a'})
        self.assertEqual(chosen['rank'], 2)
        self.assertEqual(chosen['category'], '现言脑洞')
        self.assertTrue(chosen['cover_url'])

    def test_formula_counts_unique_readable_titles_and_examples_are_grounded(self):
        source = snapshot(books=[book('a', '重生后，我吃瓜'), book('b', '系统：开局送能力'),
                                 book('c', '\ue123重生后，我开局'), book('d', '书名暂未取得（原榜第4名）')])
        duplicate = snapshot('duplicate', category='现言脑洞', books=[book('a', '重生后，我吃瓜')])
        report = panel([source, duplicate])
        self.assertEqual(report['summary']['title_count'], 2)
        formula = next(f for f in report['formulas'] if f['title'] == '处境转折')
        self.assertEqual(formula['count'], 1)
        self.assertEqual(formula['share'], 50)
        self.assertEqual(formula['examples'][0]['book_id'], 'a')
        self.assertIn(formula['examples'][0]['snapshot_id'], ('now', 'duplicate'))
        keyword = next(k for k in report['keywords'] if k['word'] == '系统')
        self.assertEqual(keyword['count'], 1)

    def test_missing_metrics_are_missing_not_zero(self):
        report = panel([snapshot(books=[book('a', reads=None, words=None)])])
        self.assertIsNone(report['summary']['total_metric'])
        self.assertEqual(report['summary']['metric_count_books'], 0)
        self.assertIsNone(report['categories'][0]['average_metric'])
        self.assertIsNone(report['candidates'][0]['reads_per_10k_words'])

    def test_first_day_candidates_exist_without_invented_growth(self):
        report = panel([snapshot(books=[book('a', reads=200, words=10000),
                                       book('b', reads=500, words=100000),
                                       book('tiny', reads=9999, words=10)])])
        self.assertFalse(report['growth']['available'])
        self.assertEqual(report['growth']['items'], [])
        self.assertEqual([b['book_id'] for b in report['candidates']], ['a', 'b'])
        self.assertEqual(report['candidates'][0]['reads_per_10k_words'], 200)
        self.assertIn('至少1万字', report['candidate_basis'])
        self.assertNotIn('metric_delta', report['candidates'][0])

    def test_true_growth_requires_exact_scope_and_different_source_day(self):
        current = snapshot(books=[book('a', reads=300, rank=3)])
        baseline = snapshot('baseline', '2026-11-07', books=[book('a', reads=100, rank=5)])
        wrong_scope = snapshot('wrong', '2026-11-08', category='玄幻脑洞', books=[book('a', reads=1, rank=99)])
        reading = snapshot('reading', '2026-11-08', board='阅读榜', books=[book('a', reads=1, rank=99)])
        report = panel([current, baseline, wrong_scope, reading])
        growth = report['growth']['items'][0]
        self.assertEqual(growth['baseline_snapshot_id'], 'baseline')
        self.assertEqual(growth['interval_days'], 2)
        self.assertEqual(growth['metric_delta'], 200)
        self.assertEqual(growth['rank_delta'], 2)
        self.assertEqual(growth['metric_delta_rate'], 200)

    def test_missing_date_same_day_and_future_never_fake_growth(self):
        current = snapshot(data_date=None)
        old = snapshot('old', '2026-11-08', books=[book('a', reads=0)])
        self.assertFalse(panel([current, old])['growth']['available'])
        current = snapshot(data_date='2026-11-09T15:00:00+08:00')
        same = snapshot('same', '2026-11-09T04:00:00+08:00', books=[book('a', reads=0)])
        self.assertFalse(panel([current, same])['growth']['available'])

    def test_zero_baseline_has_delta_not_infinite_percentage(self):
        report = panel([snapshot(), snapshot('old', '2026-11-08', books=[book('a', reads=0)])])
        self.assertEqual(report['growth']['items'][0]['metric_delta'], 100)
        self.assertIsNone(report['growth']['items'][0]['metric_delta_rate'])

    def test_no_growing_work_does_not_label_negative_changes_as_dark_horses(self):
        report = panel([snapshot(books=[book('a', reads=10, rank=2)]),
                        snapshot('old', '2026-11-08', books=[book('a', reads=100, rank=1)])])
        self.assertFalse(report['growth']['available'])
        self.assertEqual(report['growth']['items'], [])
        self.assertEqual(len(report['candidates']), 1)

    def test_qidian_tickets_and_ciwei_rank_only_keep_platform_metrics(self):
        qidian = snapshot('qidian', platform='qidian', channel='男频', category='全部', board='月票榜',
                          books=[book('a', metrics=[{'name':'月票榜','value':'2万月票','unit':''}])])
        ciwei = snapshot('ciwei', platform='ciweimao', channel='综合', category='全部', board='月票榜', period='月榜',
                         books=[book('a', metrics=[], rank=14)])
        result = build_overview([snapshot(), qidian, ciwei])
        self.assertEqual(result['summary']['books'], 3)  # IDs are platform-local.
        q = next(p for p in result['platforms'] if p['platform'] == 'qidian')
        c = next(p for p in result['platforms'] if p['platform'] == 'ciweimao')
        self.assertEqual(q['summary']['total_metric'], 20000)
        self.assertEqual(q['candidates'][0]['primary_label'], '月票')
        self.assertIsNone(q['candidates'][0]['read_count'])
        self.assertIsNone(q['candidates'][0]['reads_per_10k_words'])
        self.assertIsNone(c['summary']['total_metric'])
        self.assertEqual(c['candidates'][0]['rank'], 14)

    def test_qidian_growth_uses_tickets_not_readership(self):
        q = {'platform':'qidian', 'channel':'男频', 'category':'全部', 'board':'月票榜'}
        current = snapshot('q-new', books=[book('a', metrics=[{'name':'月票榜','value':'2万月票'}])], **q)
        old = snapshot('q-old', '2026-11-08', books=[book('a', metrics=[{'name':'月票榜','value':'1万月票'}])], **q)
        growth = panel([current, old], 'qidian')['growth']['items'][0]
        self.assertEqual(growth['metric_delta'], 10000)
        self.assertIsNone(growth['read_count'])
        self.assertEqual(growth['primary_label'], '月票')

    def test_coverage_requires_both_channels_and_all_discovered_categories(self):
        female = snapshot(categories=['古言脑洞','现言脑洞'])
        male = snapshot('male', channel='男频', category='玄幻脑洞', categories=['玄幻脑洞'])
        partial = panel([female, male])
        self.assertEqual(partial['coverage']['expected_category_count'], 3)
        self.assertEqual(partial['coverage']['category_count'], 2)
        self.assertFalse(partial['coverage']['complete'])
        self.assertEqual(next(c for c in partial['channels'] if c['channel']=='女频')['missing_categories'], ['现言脑洞'])
        female2 = snapshot('female2', category='现言脑洞', categories=['古言脑洞','现言脑洞'])
        self.assertTrue(panel([female, female2, male])['coverage']['complete'])
        female2['complete'] = False
        self.assertFalse(panel([female, female2, male])['coverage']['complete'])

    def test_unknown_navigation_is_not_complete_and_no_data_is_not_mocked(self):
        empty = build_overview([])
        self.assertEqual(empty['summary'], {'books':0, 'platforms_with_data':0})
        self.assertEqual(len(empty['platforms']), 3)
        for p in empty['platforms']:
            self.assertFalse(p['coverage']['complete'])
            self.assertEqual(p['formulas'], [])
            self.assertEqual(p['candidates'], [])
        no_navigation = panel([snapshot(categories=[])])
        self.assertIsNone(no_navigation['coverage']['expected_category_count'])
        self.assertFalse(no_navigation['coverage']['complete'])

    def test_metadata_selector_preserves_full_overview_with_only_latest_and_baseline(self):
        records = [
            snapshot('current', '2026-11-09T08:00:00Z', books=[book('a', reads=500), book('b', reads=200)]),
            snapshot('current-preview', '2026-11-09T08:00:00Z', books=[book('a')], complete=False,
                     collected_at='2026-11-09T20:00:00Z'),
            snapshot('same-day-old-version', '2026-11-09T07:00:00Z', books=[book('a', reads=300)]),
            snapshot('baseline-full', '2026-11-08T08:00:00Z', books=[book('a', reads=100), book('b', reads=50)]),
            snapshot('baseline-preview', '2026-11-08T08:00:00Z', books=[book('a', reads=50)], complete=False,
                     collected_at='2026-11-08T23:00:00Z'),
            snapshot('older', '2026-11-07', books=[book('a', reads=1)]),
            snapshot('reading', board='阅读榜', books=[book('ignore', reads=10**9)]),
            snapshot('male-current', '2026-11-09', channel='男频', category='玄幻脑洞', categories=['玄幻脑洞'],
                     books=[book('m', reads=700)]),
            snapshot('male-old', '2026-11-08', channel='男频', category='玄幻脑洞', categories=['玄幻脑洞'],
                     books=[book('m', reads=200)]),
        ]
        qscope = {'platform':'qidian', 'channel':'男频', 'category':'全部', 'board':'月票榜'}
        records += [snapshot('q-current', None, collected_at='2026-11-09T08:00:00Z', **qscope),
                    snapshot('q-older', None, collected_at='2026-11-08T08:00:00Z', **qscope)]
        metadata = [{**{key:value for key,value in row.items() if key not in ('books','categories')},
                     'books_count':len(row['books'])} for row in records]
        selected_ids = relevant_snapshot_ids(metadata)
        self.assertEqual(set(selected_ids), {'current','baseline-full','male-current','male-old','q-current'})
        selected = [row for row in records if row['id'] in selected_ids]
        self.assertEqual(build_overview(records), build_overview(selected))
        self.assertEqual(relevant_snapshot_ids(records), selected_ids)

    def test_metadata_selector_handles_empty_prior_records_and_unknown_source_dates(self):
        records = [snapshot('current', books=[book('a')]),
                   snapshot('empty-prior', '2026-11-08', books=[]),
                   snapshot('valid-prior', '2026-11-07', books=[book('a', reads=10)])]
        metadata = [{**{key:value for key,value in row.items() if key not in ('books','categories')},
                     'books_count':len(row['books'])} for row in records]
        self.assertEqual(set(relevant_snapshot_ids(metadata)), {'current','valid-prior'})
        current_empty = snapshot('current-empty', '2026-11-10', books=[])
        metadata.append({**{key:value for key,value in current_empty.items() if key not in ('books','categories')},
                         'books_count':0})
        self.assertEqual(set(relevant_snapshot_ids(metadata)), {'current-empty','current'})
        self.assertEqual(relevant_snapshot_ids([None, {}, {'id':'invalid'}]), [])

    def test_input_is_unchanged_and_lists_are_bounded(self):
        data = [snapshot(books=[book(str(i), reads=i+1) for i in range(100)])]
        before = copy.deepcopy(data)
        result = build_overview(data)
        self.assertEqual(data, before)
        self.assertEqual(len(result['platforms'][0]['candidates']), 12)
        self.assertTrue(all(len(f['examples']) <= 3 for f in result['platforms'][0]['formulas']))
        result['platforms'][0]['candidates'][0]['title'] = 'changed'
        self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
