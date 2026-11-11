# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Deterministic statistics over saved source snapshots; no model or network calls.

Inspired by the user-provided ranking toolkit's keyword/pattern and readership
views. Comparisons deliberately use one exact ranking scope and source dates.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
import re

SCOPE_FIELDS = ('platform', 'mode', 'channel', 'category', 'board', 'period')
TITLE_KEYWORDS = (
    '重生', '穿越', '穿书', '快穿', '系统', '开局', '末世', '修仙', '高武',
    '玄幻', '直播', '综武', '规则怪谈', '年代', '种田', '团宠', '萌宝', '读心',
    '女扮男装', '逆袭', '复仇', '打脸', '无敌', '反派', '反杀', '暴富', '封神',
    '养崽', '经营', '空间', '真假千金', '豪门', '总裁', '先婚后爱', '追妻',
    '离婚', '退婚', '闪婚', '替嫁', '白月光', '破镜重圆', '宫斗', '宅斗',
    '王妃', '嫡女', '皇后', '太子', '将军', '权臣', '师尊', '师妹', '师兄',
    '恶毒女配', '炮灰', '锦鲤', '神豪', '学霸', '娱乐圈', '校园', '悬疑',
    '诡异', '游戏', '副本', '无限流', '求生', '星际', '兽世', '吃瓜',
)
TITLE_PATTERNS = (
    ('冒号分段', r'^[^：:]{1,16}[：:].+'),
    ('“重生／穿越／开局／回到”开头', r'^(?:重生|穿越|开局|回到)'),
    ('“我在…当／做／成…”结构', r'^我在.{1,16}(?:当|做|成)'),
    ('“…后，…”结构', r'.+后[，,].+'),
    ('“从…开始”结构', r'从.{1,16}开始'),
    ('包含疑问号', r'[？?]'),
)
_PUA = re.compile(r'[\ue000-\uf8ff]')
_NUMBER = re.compile(r'^(\d+(?:\.\d+)?)(万|亿)?(?:人|字)?$')


def _number(value, unit=''):
    """Parse explicit numeric units, never interpret unavailable values as zero."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number, suffix = float(value), None
    elif isinstance(value, str):
        match = _NUMBER.fullmatch(re.sub(r'[\s,，]', '', value))
        if not match:
            return None
        number, suffix = float(match[1]), match[2]
    else:
        return None
    scale = {'万': 10000, '亿': 100000000}
    if suffix:
        number *= scale[suffix]
    elif str(unit).startswith('万'):
        number *= 10000
    elif str(unit).startswith('亿'):
        number *= 100000000
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _metric(book, name):
    for item in book.get('metrics', []) if isinstance(book.get('metrics'), list) else []:
        if not isinstance(item, dict) or item.get('name') != name:
            continue
        unit = str(item.get('unit') or '')
        allowed = ('', '人', '万', '万人', '亿', '亿人') if name == '在读' else ('', '字', '万', '万字', '亿', '亿字')
        if unit not in allowed:
            continue
        raw_value = item.get('value')
        if isinstance(raw_value, str):
            suffix = raw_value.strip()[-1:]
            if (name == '在读' and suffix == '字') or (name == '字数' and suffix == '人'):
                continue
        value = _number(raw_value, unit)
        if value is not None:
            return value
    return None


def _books(snapshot):
    # Store already enforces unique identities. This also makes standalone calls
    # robust without inflating sample size from duplicate input rows.
    result = {}
    for book in snapshot.get('books', []) if isinstance(snapshot.get('books'), list) else []:
        if isinstance(book, dict) and book.get('id') is not None and not isinstance(book['id'], bool):
            ident = str(book['id']).strip()
            if ident and ident not in result:
                result[ident] = book
    return result


def _source_date(snapshot):
    value = snapshot.get('data_date')
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Respect the calendar date explicitly supplied by the source, rather
        # than treating a later local fetch as a new daily observation.
        return stamp.date()
    except (ValueError, TypeError):
        return None


def _collected_order(snapshot):
    try:
        stamp = datetime.fromisoformat(str(snapshot.get('collected_at') or snapshot.get('created_at') or '').replace('Z', '+00:00'))
        return stamp.replace(tzinfo=timezone.utc).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def _scope(snapshot):
    return {name: str(snapshot.get(name) or '') for name in SCOPE_FIELDS}


def _source_id(snapshot):
    """Older locally collected records predate explicit source provenance."""
    return str(snapshot.get('source_id') or 'direct')


def _title(book):
    title = book.get('title')
    if not isinstance(title, str) or _PUA.search(title) or title.startswith(('书名暂未取得', '书名待加载')):
        return ''
    return title.strip()


def _rank(book):
    value = book.get('rank')
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _reference(ident, book):
    return {'book_id': ident, 'title': str(book.get('title') or ''), 'rank': _rank(book)}


def _changes(snapshot, history_snapshots, current):
    current_date = _source_date(snapshot)
    empty = {'available': False, 'reason': '', 'baseline_snapshot_id': None,
             'baseline_data_date': None, 'current_data_date': snapshot.get('data_date'),
             'interval_days': None, 'matched_books': 0, 'read_comparable_books': 0,
             'rank_comparable_books': 0, 'items': []}
    if current_date is None:
        return {**empty, 'reason': '当前榜单没有有效的来源数据日期，暂不计算涨跌。'}
    eligible = []
    for previous in history_snapshots or []:
        if (not isinstance(previous, dict) or previous.get('id') == snapshot.get('id')
                or _scope(previous) != _scope(snapshot) or _source_id(previous) != _source_id(snapshot)):
            continue
        previous_date = _source_date(previous)
        if previous_date is not None and previous_date < current_date and _books(previous):
            eligible.append((previous_date, _collected_order(previous), str(previous.get('id') or ''), previous))
    if not eligible:
        return {**empty, 'reason': '暂无同平台、频道、分类、榜型及周期的前一来源日期记录；持续保存后可比较。'}
    previous_date, _, _, baseline = max(eligible, key=lambda entry: entry[:3])
    previous_books = _books(baseline)
    items, read_count, rank_count = [], 0, 0
    common = set(current).intersection(previous_books)
    for ident in sorted(common):
        book, prior = current[ident], previous_books[ident]
        reads, old_reads = _metric(book, '在读'), _metric(prior, '在读')
        rank, old_rank = _rank(book), _rank(prior)
        comparable_reads = reads is not None and old_reads is not None
        comparable_rank = rank is not None and old_rank is not None
        if not comparable_reads and not comparable_rank:
            continue
        read_count += comparable_reads
        rank_count += comparable_rank
        delta = reads - old_reads if comparable_reads else None
        items.append({**_reference(ident, book), 'previous_rank': old_rank,
                      'rank_delta': old_rank - rank if comparable_rank else None,
                      'read_count': reads, 'previous_read_count': old_reads, 'read_delta': delta,
                      'read_delta_rate': round(delta * 100 / old_reads, 2) if comparable_reads and old_reads > 0 else None})
    items.sort(key=lambda item: (item['read_delta'] is not None, item['read_delta'] or 0,
                                 item['rank_delta'] is not None, item['rank_delta'] or 0), reverse=True)
    return {**empty, 'available': bool(items),
            'reason': '' if items else '前次记录存在，但没有可对齐的同一作品指标。',
            'baseline_snapshot_id': baseline.get('id'), 'baseline_data_date': baseline.get('data_date'),
            'interval_days': (current_date - previous_date).days, 'matched_books': len(common),
            'read_comparable_books': read_count, 'rank_comparable_books': rank_count,
            'items': items[:20]}


def build_insights(snapshot, history_snapshots=()):
    """Return source-bounded statistics. Inputs are never changed."""
    if not isinstance(snapshot, dict):
        raise ValueError('snapshot must be a dictionary')
    books = _books(snapshot)
    titles = [title for book in books.values() if (title := _title(book))]
    reads = [value for book in books.values() if (value := _metric(book, '在读')) is not None]
    words = [value for book in books.values() if (value := _metric(book, '字数')) is not None]
    share = lambda count: round(count * 100 / len(titles), 2) if titles else 0
    keywords = []
    for word in TITLE_KEYWORDS:
        matches = [title for title in titles if word in title]
        if matches:
            keywords.append({'word': word, 'count': len(matches), 'share': share(len(matches)), 'examples': matches[:3]})
    keywords.sort(key=lambda item: (-item['count'], item['word']))
    patterns = []
    for label, rule in TITLE_PATTERNS:
        matches = [title for title in titles if re.search(rule, title)]
        if matches:
            patterns.append({'pattern': label, 'count': len(matches), 'share': share(len(matches)), 'examples': matches[:3]})
    patterns.sort(key=lambda item: -item['count'])
    efficiency = []
    for ident, book in books.items():
        read_count, word_count = _metric(book, '在读'), _metric(book, '字数')
        if read_count is not None and word_count is not None and word_count > 0:
            efficiency.append({**_reference(ident, book), 'read_count': read_count, 'word_count': word_count,
                               'reads_per_10k_words': round(read_count * 10000 / word_count, 2)})
    efficiency.sort(key=lambda item: (-item['reads_per_10k_words'], item['rank'] or 10**9, item['book_id']))
    return {
        'snapshot_id': snapshot.get('id'), 'scope': _scope(snapshot), 'data_date': snapshot.get('data_date'),
        'sample': {'books': len(books), 'title_count': len(titles), 'read_count_books': len(reads),
                   'word_count_books': len(words), 'total_reads': sum(reads) if reads else None,
                   'total_words': sum(words) if words else None,
                   'average_title_length': round(sum(len(re.sub(r'\s', '', title)) for title in titles) / len(titles), 2) if titles else None},
        'keywords': keywords[:20], 'title_patterns': patterns, 'efficiency': efficiency[:20],
        'changes': _changes(snapshot, history_snapshots, books),
        'methods': [
            '仅统计当前快照实际保存的作品；缺失指标不当作零，在读合计不是去重读者人数。',
            '标题关键词使用固定词表，按含该词的书名数量计数；句式使用明确文字规则，一本书可以命中多项，占比之和不必等于100%。',
            '每万字在读=来源在读数÷（来源字数÷10000）；只作样本排序参考，不表示收入、转化率或写作因果效果。',
            '变化只比较同平台、频道、分类、榜型与周期、不同来源数据日期的同一作品；显示真实间隔，不将同日重复采集当作增长。',
            '变化列表按在读增量降序展示最多20本；每万字在读列表同样最多20本。没有相同比较对象时不推算涨跌。',
            '这里是可复核的统计，不是AI拆书或市场建议。',
        ],
    }
