"""Source-backed homepage summaries; each platform retains its own ranking scope.

No network, model calls, invented books, inferred revenue, or mixed-platform ranks.
The user's Fanqie overview explicitly includes new-book boards only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re

from .insights import (_books, _collected_order, _metric, _number, _rank,
                       _scope, _source_date, _title, TITLE_KEYWORDS)

PLATFORMS = (
    ('fanqie', '番茄小说', '新书榜', '在读', ('男频', '女频')),
    ('qidian', '起点中文网', '月票榜', '月票', ('男频',)),
    ('ciweimao', '刺猬猫', '月票榜', '月票', ('综合',)),
)
FORMULAS = (
    ('设定直给', '前置设定 + 冒号 + 核心事件', r'^[^：:]{1,16}[：:].+'),
    ('处境转折', '发生变化 + “后” + 新行动或结果', r'.+后[，,].+'),
    ('场景与身份', '“我在” + 特定场域 + 做什么', r'^我在.{1,16}(?:当|做|成).+'),
    ('重启开场', '重生／穿越／开局／回到 + 新处境', r'^(?:重生|穿越|开局|回到).+'),
    ('成长起点', '“从” + 具体起点 + “开始”', r'从.{1,16}开始'),
    ('问句悬念', '具体事件或设定 + 问句', r'[？?]'),
)


def _eligible_scope(snapshot, platform, board, channels):
    if not isinstance(snapshot, dict):
        return False
    if (snapshot.get('platform') != platform or snapshot.get('mode', 'long') != 'long'
            or snapshot.get('board') != board or snapshot.get('channel') not in channels
            or not isinstance(snapshot.get('id'), str) or not snapshot['id']
            or not isinstance(snapshot.get('category'), str) or not snapshot['category']):
        return False
    if platform in ('qidian', 'ciweimao') and snapshot['category'] != '全部':
        return False
    if platform == 'ciweimao' and snapshot.get('period') != '月榜':
        return False
    return True


def _eligible(snapshot, platform, board, channels):
    return (_eligible_scope(snapshot, platform, board, channels)
            and isinstance(snapshot.get('books'), list))


def _book_count(snapshot):
    books = snapshot.get('books')
    if isinstance(books, list):
        return len(books)
    count = snapshot.get('books_count')
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0


def _scope_key(snapshot):
    return tuple(_scope(snapshot).values())


def _latest_key(snapshot):
    source_stamp = None
    try:
        source = datetime.fromisoformat(str(snapshot.get('data_date') or '').replace('Z', '+00:00'))
        source_stamp = (source.replace(tzinfo=timezone.utc) if source.tzinfo is None else source).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    # Keep the exact source version time, not just its calendar day. For an
    # identical source version a later 10-book preview cannot replace 297 books
    # already fetched. Collection-only sources have no provable version match.
    return (source_stamp or 0,
            _book_count(snapshot) if source_stamp is not None else 0,
            snapshot.get('complete') is True if source_stamp is not None else False,
            _collected_order(snapshot), str(snapshot.get('id') or ''))


def _primary(book, platform):
    if platform == 'fanqie':
        return _metric(book, '在读')
    metrics = book.get('metrics') if isinstance(book.get('metrics'), list) else []
    for metric in metrics:
        if not isinstance(metric, dict) or metric.get('name') not in ('月票', '月票榜'):
            continue
        unit = metric.get('unit') or ''
        if unit not in ('', '票', '月票', '万', '万票', '万月票', '亿', '亿票'):
            continue
        value = metric.get('value')
        if isinstance(value, str):
            value = re.sub(r'(?:月票|票)$', '', value.strip())
        parsed = _number(value, unit)
        if parsed is not None:
            return parsed
    return None


def _reference(snapshot, book_id, book, platform, metric_label):
    reads = _metric(book, '在读') if platform == 'fanqie' else None
    words = _metric(book, '字数')
    return {
        'platform': platform, 'snapshot_id': snapshot['id'], 'book_id': book_id,
        'selection': {'snapshot_id': snapshot['id'], 'book_id': book_id},
        'title': str(book.get('title') or ''), 'author': str(book.get('author') or ''),
        'cover_url': book.get('cover_url') if isinstance(book.get('cover_url'), str) else None,
        'url': book.get('url') if isinstance(book.get('url'), str) else None,
        'channel': snapshot['channel'], 'category': snapshot['category'],
        'board': snapshot['board'], 'period': snapshot.get('period'),
        'rank': _rank(book), 'data_date': snapshot.get('data_date'),
        'read_count': reads, 'word_count': words,
        'primary_count': _primary(book, platform), 'primary_label': metric_label,
        'reads_per_10k_words': round(reads * 10000 / words, 2)
        if reads is not None and words is not None and words > 0 else None,
    }


def _ref_order(item):
    # Source ranking is only used inside a panel/scope; no generated rank field.
    return (-(item['primary_count'] or 0), item['rank'] or 10**9, item['book_id'])


def _growth(latest, grouped, platform, metric_label):
    found = {}
    comparable_scopes = 0
    for scope, current in latest.items():
        current_date = _source_date(current)
        if current_date is None:
            continue
        baselines = [old for old in grouped[scope]
                     if _source_date(old) is not None and _source_date(old) < current_date
                     and _books(old)]
        if not baselines:
            continue
        baseline = max(baselines, key=_latest_key)
        comparable_scopes += 1
        previous_books = _books(baseline)
        for book_id, book in _books(current).items():
            old = previous_books.get(book_id)
            if old is None:
                continue
            now_count, old_count = _primary(book, platform), _primary(old, platform)
            rank, old_rank = _rank(book), _rank(old)
            count_delta = now_count - old_count if now_count is not None and old_count is not None else None
            rank_delta = old_rank - rank if rank is not None and old_rank is not None else None
            if count_delta is None and rank_delta is None:
                continue
            item = {
                **_reference(current, book_id, book, platform, metric_label),
                'baseline_snapshot_id': baseline['id'], 'baseline_data_date': baseline.get('data_date'),
                'interval_days': (current_date - _source_date(baseline)).days,
                'previous_rank': old_rank, 'rank_delta': rank_delta,
                'previous_primary_count': old_count, 'metric_delta': count_delta,
                'metric_delta_rate': round(count_delta * 100 / old_count, 2)
                if count_delta is not None and old_count > 0 else None,
            }
            # Cross-category duplicates use the most recent scope observation,
            # rather than cherry-picking the largest favourable movement.
            if book_id not in found or _latest_key(current) > found[book_id][0]:
                found[book_id] = (_latest_key(current), item)
    items = [entry[1] for entry in found.values()]
    positive = [item for item in items if (item['metric_delta'] or 0) > 0
                or (item['metric_delta'] is None and (item['rank_delta'] or 0) > 0)]
    positive.sort(key=lambda item: (item['metric_delta'] is not None, item['metric_delta'] or 0,
                                    item['rank_delta'] or 0), reverse=True)
    reason = '' if positive else (
        '可比较的记录中暂无指标上升作品；下方继续展示当前值得关注的样本。'
        if items else '积累不同来源日期的同榜记录后显示真实增长；当前先看潜力样本。')
    return {'available': bool(positive), 'reason': reason,
            'comparable_scopes': comparable_scopes, 'compared_books': len(found),
            'items': positive[:12]}


def _panel(snapshots, platform, label, board, metric_label, expected_channels):
    grouped = defaultdict(list)
    for snapshot in snapshots:
        if _eligible(snapshot, platform, board, expected_channels):
            grouped[_scope_key(snapshot)].append(snapshot)
    latest = {scope: max(group, key=_latest_key) for scope, group in grouped.items()}
    ordered = sorted(latest.values(), key=_latest_key, reverse=True)
    all_books, channel_books, categories = {}, defaultdict(set), []
    for snapshot in ordered:
        refs = []
        for book_id, book in _books(snapshot).items():
            ref = _reference(snapshot, book_id, book, platform, metric_label)
            refs.append(ref)
            channel_books[snapshot['channel']].add(book_id)
            if book_id not in all_books:
                all_books[book_id] = ref
        values = [item['primary_count'] for item in refs if item['primary_count'] is not None]
        examples = sorted(refs, key=_ref_order)
        categories.append({
            'channel': snapshot['channel'], 'category': snapshot['category'],
            'board': board, 'snapshot_id': snapshot['id'], 'books': len(refs),
            'metric_count_books': len(values),
            'average_metric': round(sum(values) / len(values), 2) if values else None,
            'example': examples[0] if examples else None,
        })
    categories.sort(key=lambda item: (-(item['average_metric'] or 0), -item['books'],
                                      item['channel'], item['category']))
    refs = list(all_books.values())
    # Validate titles exactly as the per-category insights do; placeholders and
    # unresolved source-font characters are not meaningful formula evidence.
    titled = [item for item in refs if _title(item)]
    title_count = len(titled)
    share = lambda count: round(count * 100 / title_count, 2) if title_count else 0
    formulas = []
    for title, formula, expression in FORMULAS:
        matched = [item for item in titled if re.search(expression, item['title'])]
        if matched:
            formulas.append({'title': title, 'formula': formula, 'count': len(matched),
                             'share': share(len(matched)),
                             'examples': sorted(matched, key=_ref_order)[:3]})
    formulas.sort(key=lambda item: -item['count'])
    keywords = []
    for word in TITLE_KEYWORDS:
        matched = [item for item in titled if word in item['title']]
        if matched:
            keywords.append({'word': word, 'count': len(matched), 'share': share(len(matched)),
                             'examples': sorted(matched, key=_ref_order)[:3]})
    keywords.sort(key=lambda item: (-item['count'], item['word']))

    if platform == 'fanqie':
        candidates = [item for item in refs if (item['read_count'] or 0) > 0
                      and item['word_count'] is not None and item['word_count'] >= 10000]
        candidates.sort(key=lambda item: (-item['reads_per_10k_words'],
                                          -(item['read_count'] or 0), item['book_id']))
        candidate_basis = '新书榜中来源字数至少1万字且在读大于0，按每万字在读排序。'
        if not candidates:
            candidates = sorted(refs, key=_ref_order)
            candidate_basis = '尚无足够字数指标计算效率，按已有在读数及分类原榜位置展示关注样本。'
    else:
        candidates = sorted(refs, key=lambda item: (item['rank'] or 10**9, item['book_id']))
        candidate_basis = '按该平台月票榜的原榜名次展示；没有票数就不补造指标。'

    channels = []
    expected_total = 0
    known_expected = True
    all_covered = True
    for channel in expected_channels:
        channel_snapshots = [s for s in ordered if s['channel'] == channel]
        observed = {s['category'] for s in channel_snapshots}
        if platform == 'fanqie':
            current_navigation = channel_snapshots[0].get('categories') if channel_snapshots else None
            expected = {str(value).strip() for value in current_navigation
                        if isinstance(value, str) and value.strip()} if isinstance(current_navigation, list) else set()
        else:
            expected = {'全部'}
        if not expected:
            known_expected = False
            all_covered = False
        expected_total += len(expected)
        missing = sorted(expected - observed)
        all_covered = all_covered and not missing and bool(channel_snapshots)
        channels.append({'channel': channel, 'books': len(channel_books[channel]),
                         'categories': len(observed), 'expected_categories': len(expected) if expected else None,
                         'missing_categories': missing})
    dates = sorted({s['data_date'] for s in ordered if _source_date(s) is not None})
    incomplete = [{'channel': s['channel'], 'category': s['category'], 'snapshot_id': s['id']}
                  for s in ordered if s.get('complete') is not True]
    metric_values = [item['primary_count'] for item in refs if item['primary_count'] is not None]
    latest_collected = max(ordered, key=_collected_order).get('collected_at') if ordered else None
    return {
        'platform': platform, 'label': label, 'board': board, 'metric_label': metric_label,
        'scope_label': '男频 · 女频 · 新书榜' if platform == 'fanqie' else f'{label} · 月票榜',
        'summary': {'books': len(refs), 'title_count': title_count,
                    'metric_count_books': len(metric_values),
                    'total_metric': sum(metric_values) if metric_values else None},
        'coverage': {'category_count': len(categories),
                     'expected_category_count': expected_total if known_expected else None,
                     'complete': bool(ordered) and all_covered and not incomplete,
                     'source_dates': dates, 'latest_collected_at': latest_collected,
                     'missing_channels': [channel for channel in expected_channels if not any(s['channel'] == channel for s in ordered)],
                     'incomplete_categories': incomplete},
        'channels': channels, 'categories': categories, 'keywords': keywords[:18],
        'formulas': formulas, 'candidates': candidates[:12], 'candidate_basis': candidate_basis,
        'growth': _growth(latest, grouped, platform, metric_label),
        'methods': [
            '番茄仅汇总男频、女频新书榜，阅读榜不参与任何首页统计。' if platform == 'fanqie'
            else '仅汇总本平台已接入的月票榜；与其他平台分别统计，原榜名次不混排。',
            '每个频道、分类、榜型和周期只使用最新保存快照；平台内同一本书按作品ID去重，分类统计仍保留各自样本。',
            '爆款公式是榜单书名中实际出现的表达结构，附命中数量和例书；不代表成功概率，也不代替阅读正文。',
            '关键词与公式按可读书名计数，一本书可以命中多项；均为本机已取得样本，不代表平台全部作品。',
            '热度指标合计不是独立读者人数。每万字在读仅作筛选参考，不等同收入或转化率。' if platform == 'fanqie'
            else '月票数量只在来源明确提供时展示；来源没有票数时仅保留原榜位置。',
            '增长仅比较同榜范围、同一作品、不同来源日期的真实记录，保留实际天数；不把采集时间当来源日期。',
        ],
    }


def relevant_snapshot_ids(metadata):
    """Select only the full records needed to produce the current overview.

    Metadata is the Store's snapshot metadata: all scope/version fields plus
    ``books_count``, without books or category navigation. Store snapshots hold
    unique valid IDs, so their positive count is equivalent to a nonempty book
    map. At most two records are selected per eligible scope: the current source
    version and its closest prior source day. Sources without a data date need
    only the latest collected record; they cannot contribute historical growth.
    """
    if not isinstance(metadata, (list, tuple)):
        raise ValueError('metadata must be a list or tuple of snapshot metadata')
    grouped = defaultdict(list)
    for item in metadata:
        for platform, _label, board, _metric_label, channels in PLATFORMS:
            if _eligible_scope(item, platform, board, channels):
                grouped[_scope_key(item)].append(item)
                break
    selected = []
    for scope in sorted(grouped):
        records = grouped[scope]
        current = max(records, key=_latest_key)
        selected.append(current['id'])
        current_date = _source_date(current)
        if current_date is None:
            continue
        older = [item for item in records
                 if _source_date(item) is not None and _source_date(item) < current_date
                 and _book_count(item) > 0]
        if older:
            selected.append(max(older, key=_latest_key)['id'])
    return list(dict.fromkeys(selected))


def build_overview(snapshots):
    """Build all three independent panels from full immutable saved snapshots."""
    if not isinstance(snapshots, (list, tuple)):
        raise ValueError('snapshots must be a list or tuple of full snapshots')
    panels = [_panel(snapshots, *definition) for definition in PLATFORMS]
    return {'scope_label': '三站榜单总览',
            'summary': {'books': sum(panel['summary']['books'] for panel in panels),
                        'platforms_with_data': sum(bool(panel['summary']['books']) for panel in panels)},
            'platforms': panels}
