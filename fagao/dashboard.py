"""Numerical views for the local ranking dashboard; no predicted success scores.

Each view uses current source records only. The optional historical index is
the supplied toolkit's transparent arithmetic, with missing history left null.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
import re

from .insights import TITLE_KEYWORDS, _books, _metric, _source_date, _source_id, _title

SCATTER_LIMIT = 240
WORD_BINS = (
    ('8万以下', 0, 80000), ('8–15万', 80000, 150000),
    ('15–30万', 150000, 300000), ('30–50万', 300000, 500000),
    ('50–100万', 500000, 1000000), ('100万以上', 1000000, None),
)
READ_BINS = (
    ('1千以下', 0, 1000), ('1千–1万', 1000, 10000),
    ('1–5万', 10000, 50000), ('5–10万', 50000, 100000),
    ('10–50万', 100000, 500000), ('50万以上', 500000, None),
)
TICKET_BINS = (
    ('100以下', 0, 100), ('100–1千', 100, 1000),
    ('1千–5千', 1000, 5000), ('5千–1万', 5000, 10000),
    ('1–5万', 10000, 50000), ('5万以上', 50000, None),
)


def quantile(values, fraction):
    """Linear-interpolated sample quantile; absent data stays absent."""
    values = sorted(value for value in values if _valid(value))
    if not values:
        return None
    index = (len(values) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return round(values[low] + (values[high] - values[low]) * (index - low), 2)


def _valid(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def _share(count, total):
    return round(count * 100 / total, 2) if total else 0


def _efficiency(ref):
    reads, words = ref.get('read_count'), ref.get('word_count')
    if not _valid(reads) or not _valid(words) or words <= 0:
        return None
    return round(reads * 10000 / words, 2)


def _histogram(refs, field, bins):
    valid = [ref for ref in refs if _valid(ref.get(field))]
    result = []
    for label, low, high in bins:
        group = [ref for ref in valid if ref[field] >= low
                 and (high is None or ref[field] < high)]
        result.append({
            'label': label, 'min': low, 'max': high, 'count': len(group),
            'share': _share(len(group), len(valid)),
            'primary_median': quantile([ref['primary_count'] for ref in group], .5),
            'efficiency_median': quantile([_efficiency(ref) for ref in group], .5),
            'metric_count': sum(_valid(ref['primary_count']) for ref in group),
        })
    return result


def _scatter(refs):
    valid = [ref for ref in refs if _valid(ref.get('word_count'))
             and _valid(ref.get('primary_count'))]
    by_x = sorted(valid, key=lambda ref: (ref['word_count'], ref['primary_count'], ref['book_id']))
    by_y = sorted(valid, key=lambda ref: (ref['primary_count'], ref['word_count'], ref['book_id']))
    selected = {}

    def add(ref):
        if len(selected) < SCATTER_LIMIT:
            selected[ref['book_id']] = ref

    if len(valid) <= SCATTER_LIMIT:
        for ref in by_x:
            add(ref)
    else:
        # Preserve both-axis extremes and efficiency outliers, then spread
        # samples across both axes; never show only the first source ranks.
        for ref in (by_x[0], by_x[-1], by_y[0], by_y[-1]):
            add(ref)
        for ref in sorted(valid, key=lambda ref: (-(_efficiency(ref) or 0), ref['book_id']))[:12]:
            add(ref)
        for index in range(100):
            position = round(index * (len(valid) - 1) / 99)
            add(by_x[position]); add(by_y[position])
        for ref in sorted(valid, key=lambda ref: hashlib.sha256(ref['book_id'].encode()).digest()):
            add(ref)
    fields = ('book_id', 'snapshot_id', 'selection', 'title', 'author', 'channel', 'category',
              'board', 'rank', 'data_date', 'word_count', 'primary_count', 'primary_label',
              'read_count', 'efficiency')
    points = [{key: ref.get(key) for key in fields}
              for ref in sorted(selected.values(), key=lambda ref: (ref['word_count'], ref['primary_count'], ref['book_id']))]
    return {'points': points, 'total': len(valid), 'sampled': len(points) < len(valid),
            'displayed': len(points), 'limit': SCATTER_LIMIT,
            'x_label': '字数', 'y_label': refs[0]['primary_label'] if refs else '',
            'selection_method': '保留两轴极值与效率高值，按两轴分位及稳定ID抽样。'}


def _references(ordered, platform, channel):
    from .overview import _reference
    label = '在读' if platform == 'fanqie' else '月票'
    unique, category_groups = {}, []
    for snapshot in ordered:
        if channel != '全部' and snapshot['channel'] != channel:
            continue
        group = []
        for book_id, book in _books(snapshot).items():
            ref = _reference(snapshot, book_id, book, platform, label)
            ref['status'] = str(book.get('status') or '')
            ref['efficiency'] = _efficiency(ref)
            group.append(ref)
            unique.setdefault(book_id, ref)
        category_groups.append((snapshot, group))
    return list(unique.values()), category_groups


def _history(refs, ordered, grouped, platform):
    """Use the selected book's exact scope, not a favourable other category."""
    from .overview import _historical_baseline, _primary, _scope_key
    snapshots = {snapshot['id']: snapshot for snapshot in ordered}
    baselines = {}
    result = {}
    for ref in refs:
        current = snapshots[ref['snapshot_id']]
        current_date = _source_date(current)
        if current_date is None:
            continue
        scope = _scope_key(current)
        source_scope = (scope, _source_id(current))
        if source_scope not in baselines:
            baseline = _historical_baseline(current, grouped.get(scope, []))
            baselines[source_scope] = (baseline, _books(baseline) if baseline else {})
        baseline, books = baselines[source_scope]
        previous = books.get(ref['book_id'])
        if baseline is None or previous is None:
            continue
        prior = _primary(previous, platform)
        if prior is None or ref['primary_count'] is None:
            continue
        days = (current_date - _source_date(baseline)).days
        result[ref['book_id']] = {
            'delta': ref['primary_count'] - prior, 'interval_days': days,
            'delta_rate': round((ref['primary_count'] - prior) * 100 / prior, 2) if prior > 0 else None,
            'baseline_snapshot_id': baseline['id'], 'baseline_source_id': _source_id(baseline),
            'baseline_data_date': baseline.get('data_date'),
        }
    return result


def _source_name(source_id):
    return '平台官网直采' if source_id == 'direct' else ('FanqieRankTracker 公开快照' if source_id == 'github:scoful/FanqieRankTracker' else source_id)


def _sources_and_growth(platform, grouped, channel):
    """A public pair can show change without replacing fuller direct chart data."""
    from .overview import _latest_key, _historical_baseline, _scope_key
    current_by_source, all_by_source = defaultdict(list), defaultdict(list)
    for records in grouped.values():
        by_source = defaultdict(list)
        for record in records:
            if channel == '全部' or record['channel'] == channel:
                by_source[_source_id(record)].append(record)
        for source_id, values in by_source.items():
            current_by_source[source_id].append(max(values, key=_latest_key))
            all_by_source[source_id].extend(values)
    sources, rows = [], {}
    for source_id, current_records in sorted(current_by_source.items()):
        current_records.sort(key=_latest_key, reverse=True)
        sample = current_records[0]
        provenance = sample.get('provenance') or {}
        public = source_id != 'direct'
        name = _source_name(source_id)
        source_url = provenance.get('repo_url') or sample.get('source_url') or ''
        sample_limit = provenance.get('sample_limit')
        used_records=list(current_records)
        for record in current_records:
            baseline=_historical_baseline(record,grouped.get(_scope_key(record),[]))
            if baseline: used_records.append(baseline)
        sources.append({'source_id': source_id, 'label': name, 'url': source_url,
                        'dates': sorted({str(record['data_date']) for record in used_records if record.get('data_date')}),
                        'snapshot_count': len(current_records),
                        'scope': ' / '.join(sorted({record['channel'] for record in current_records})) + ' · ' + sample['board'],
                        'sample_scope': f'每分类最多 {sample_limit} 本公开样本，非全榜' if public and sample_limit else ('公开来源样本' if public else '按实际采集范围'),
                        'metric_precision': provenance.get('metric_precision') or '来源原始指标'})
        refs, _ = _references(current_records, platform, channel)
        changes = _history(refs, current_records, grouped, platform)
        for ref in refs:
            change = changes.get(ref['book_id'])
            if change is None:
                continue
            item = {**ref, **change, 'source_id': source_id, 'source_name': name, 'source_url': source_url}
            key = (_source_date({'data_date': ref.get('data_date')}).toordinal(), source_id == 'direct', str(ref.get('data_date') or ''), source_id)
            if ref['book_id'] not in rows or key > rows[ref['book_id']][0]:
                rows[ref['book_id']] = (key, item)
    comparable = [entry[1] for entry in rows.values()]
    comparable.sort(key=lambda row: (-row['delta'], row['book_id']))
    return sources, {'available': bool(comparable), 'compared_count': len(comparable), 'items': comparable[:12],
                     'reason': '' if comparable else '暂无同来源、同榜范围、同一作品的可比较历史。',
                     'method': '优先同来源7天前记录，否则使用实际前次日期；原始在读增量按实际间隔展示，不混合不同来源。'}


def _blackhorse(refs, changes, platform):
    if platform != 'fanqie':
        items = sorted(refs, key=lambda ref: (ref['rank'] or 10**9, ref['book_id']))[:10]
        return {'enabled': False, 'label': '原榜前列', 'criteria': [], 'formula': '',
                'score_label': '', 'eligible_count': 0, 'scored_count': 0, 'items': items}
    candidates = [ref for ref in refs if _valid(ref.get('word_count'))
                  and 80000 <= ref['word_count'] <= 150000
                  and _valid(ref.get('read_count')) and ref['read_count'] >= 10000]
    candidates.sort(key=lambda ref: (-ref['efficiency'], -ref['read_count'], ref['book_id']))
    scored = 0
    items = []
    for ref in candidates:
        history = changes.get(ref['book_id'], {})
        delta = history.get('delta')
        score = (round(.6 * (ref['read_count'] * 10000 / ref['word_count']) + delta / 1000, 2)
                 if delta is not None else None)
        scored += score is not None
        if len(items) < 10:
            items.append({**ref, 'delta': delta, 'interval_days': history.get('interval_days'),
                          'baseline_data_date': history.get('baseline_data_date'),
                          'baseline_snapshot_id': history.get('baseline_snapshot_id'), 'score': score})
    return {
        'enabled': True, 'label': '黑马候选',
        'criteria': ['仅番茄新书榜', '字数8–15万（含端点）', '当前在读≥1万', '按每万字在读降序'],
        'min_words': 80000, 'max_words': 150000, 'min_reads': 10000,
        'formula': 'E = 在读 ÷（字数 ÷ 10,000）；S = 0.6 × E + Δ在读 ÷ 1,000',
        'score_label': '原包加权指数', 'eligible_count': len(candidates),
        'scored_count': scored, 'items': items,
        'method': '筛选窗口为本项目默认条件；指数沿用工具包的效率和实际在读增量加权。增量注明真实间隔，不冒称7日增长；缺少可比历史时不计算指数。指数是筛选算式，不是成功概率。',
    }


def _view(platform, ordered, grouped, channel):
    from .overview import FORMULAS
    refs, category_groups = _references(ordered, platform, channel)
    changes = _history(refs, ordered, grouped, platform)
    primary = [ref['primary_count'] for ref in refs if _valid(ref['primary_count'])]
    words = [ref['word_count'] for ref in refs if _valid(ref['word_count'])]
    efficiencies = [ref['efficiency'] for ref in refs if _valid(ref['efficiency'])]
    blackhorse = _blackhorse(refs, changes, platform)
    categories = []
    for snapshot, group in category_groups:
        values = [ref['primary_count'] for ref in group if _valid(ref['primary_count'])]
        example = sorted(group, key=lambda ref: (-(ref['primary_count'] or 0), ref['rank'] or 10**9, ref['book_id']))
        categories.append({
            'channel': snapshot['channel'], 'category': snapshot['category'], 'count': len(group),
            'snapshot_id': snapshot['id'], 'primary_median': quantile(values, .5),
            'primary_p90': quantile(values, .9), 'valid_primary_count': len(values),
            'example': example[0] if example else None,
        })
    categories.sort(key=lambda group: (group['primary_median'] is None, -(group['primary_median'] or 0),
                                       -group['count'], group['channel'], group['category']))
    titles = [ref for ref in refs if _title(ref)]
    patterns = []
    for label, _formula, expression in FORMULAS:
        matches = [ref for ref in titles if re.search(expression, ref['title'])]
        patterns.append({'label': label, 'count': len(matches), 'share': _share(len(matches), len(titles)),
                         'primary_median': quantile([ref['primary_count'] for ref in matches], .5),
                         'examples': sorted(matches, key=lambda ref: (-(ref['primary_count'] or 0), ref['book_id']))[:2]})
    patterns.sort(key=lambda pattern: (-pattern['count'], pattern['label']))
    keywords = []
    for word in TITLE_KEYWORDS:
        matches = [ref for ref in titles if word in ref['title']]
        if matches:
            keywords.append({'word': word, 'count': len(matches), 'share': _share(len(matches), len(titles)),
                             'primary_median': quantile([ref['primary_count'] for ref in matches], .5)})
    keywords.sort(key=lambda item: (-item['count'], item['word']))
    statuses = Counter(ref['status'] if ref['status'] in ('连载中', '已完结') else '未提供' for ref in refs)
    sources, growth = _sources_and_growth(platform, grouped, channel)
    title_lengths = [len(re.sub(r'\s', '', ref['title'])) for ref in titles]
    length_bins = []
    for label, low, high in (('6字以内', 0, 7), ('7–10字', 7, 11), ('11–14字', 11, 15),
                             ('15–18字', 15, 19), ('19–22字', 19, 23), ('23字以上', 23, None)):
        count = sum(length >= low and (high is None or length < high) for length in title_lengths)
        length_bins.append({'label': label, 'min': low, 'max': high, 'count': count,
                            'share': _share(count, len(title_lengths))})
    metric_label = '在读' if platform == 'fanqie' else '月票'
    return {
        'channel': channel, 'metric_label': metric_label, 'metric_unit': '人' if platform == 'fanqie' else '票',
        'kpis': {'books': len(refs), 'primary_median': quantile(primary, .5),
                 'primary_p90': quantile(primary, .9), 'words_median': quantile(words, .5),
                 'efficiency_median': quantile(efficiencies, .5),
                 'blackhorse_count': blackhorse['eligible_count'] if platform == 'fanqie' else None,
                 'valid_primary_count': len(primary), 'valid_words_count': len(words),
                 'valid_efficiency_count': len(efficiencies), 'valid_title_count': len(titles),
                 'title_length_median': quantile(title_lengths, .5),
                 'categories': len(categories)},
        'categories': categories, 'word_bins': _histogram(refs, 'word_count', WORD_BINS),
        'primary_bins': _histogram(refs, 'primary_count', READ_BINS if platform == 'fanqie' else TICKET_BINS),
        'scatter': _scatter(refs), 'blackhorse': blackhorse,
        'status': [{'label': name, 'count': statuses[name], 'share': _share(statuses[name], len(refs))}
                   for name in ('连载中', '已完结', '未提供')],
        'patterns': patterns, 'keywords': keywords[:14], 'title_length_bins': length_bins,
        'sources': sources, 'growth': growth,
        'methods': ['分类按各自原榜样本计数，可重叠；平台及频道总数按作品ID去重。',
                    '中位数、P90与分布仅使用对应指标有效的样本；未提供不计为0。',
                    '分布区间包含下界、不含上界；末档包含其下界及以上数值。',
                    '书名结构与关键词可重复命中，比例分母为当前频道的有效书名数。'],
    }


def build_dashboard(platform, ordered, grouped):
    """Build bounded chart payloads from the overview's eligible current scopes."""
    channels = ['全部', '男频', '女频'] if platform == 'fanqie' else ['全部']
    return {'channel_options': channels,
            'views': {channel: _view(platform, ordered, grouped, channel) for channel in channels}}
