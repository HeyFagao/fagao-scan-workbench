# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Read Hongguo's ordinary public ranking pages, including their displayed posters."""
import json
import re
import time
from urllib.parse import urlsplit

from . import network
from .collectors import CollectorError, _notify, _notify_snapshot, _page_options
from .public_rank_page import fetch_page


ORIGIN = 'https://hongguoduanju.com'
BOARDS = {
    '综合热播榜': 'hot-drama',
    '真人热播榜': 'hot-real-drama',
    '漫剧热播榜': 'hot-comic-drama',
    'AI热播榜': 'hot-ai-drama',
}
PLATFORM = {
    'id': 'hongguo_official', 'name': '红果短剧', 'mode': 'short', 'group': 'drama',
    'status': 'available', 'message': '红果官网公开热播榜',
    'source_url': ORIGIN + '/rank/hot-drama', 'channels': ['综合'],
    'categories': ['全部'], 'boards': list(BOARDS), 'max_limit': 100,
    'default_options': {'channel': '综合', 'category': '全部', 'board': '综合热播榜',
                        'limit': 100, 'max_pages': 5},
}
PAGE_SIZE = 20
PAGE_INTERVAL = 15
PUBLIC_AGENT = 'Fagao-Workbench/0.7 (public ranking metadata)'
_STATE_MARKER = re.compile(r'(?<![\w.])(?:window\.)?_ROUTER_DATA\s*=\s*')


def _page_url(slug, page):
    base = ORIGIN + '/rank/' + slug
    return base if page == 1 else base + '?page=' + str(page)


def _positive(value):
    return type(value) is int and value > 0


def _metric(value, label, suffix):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([万亿]?)' + suffix, value.strip())
    if match:
        return {'name': label, 'value': match[1], 'unit': match[2]}
    return None


def _image(value):
    if not isinstance(value, str) or len(value) > 4096:
        return ''
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme == 'https' and parsed.username is None and parsed.password is None
            and (host.endswith('.byteimg.com') or host == 'byteimg.com')
            and parsed.port is None):
        return value
    return ''


def _book(row):
    if not isinstance(row, dict):
        raise CollectorError('红果榜单返回了无效的作品记录。', 'source_changed')
    ident = row.get('seriesId')
    title = row.get('title')
    rank = row.get('rank')
    if (not isinstance(ident, str) or not re.fullmatch(r'[1-9][0-9]{0,29}', ident)
            or row.get('id') != ident or not _positive(rank)
            or not isinstance(title, str) or not title.strip()
            or row.get('href') != '/detail?series_id=' + ident):
        raise CollectorError('红果作品编号、原名次或链接无法核对。', 'source_changed')
    metrics = []
    for field, label, suffix in (('heatText', '热度', '热度'), ('scoreText', '评分', '分'),
                                  ('favoriteText', '收藏', '收藏'), ('likeText', '点赞', '点赞')):
        item = _metric(row.get(field), label, suffix)
        if item is not None:
            metrics.append(item)
    episodes = row.get('episodeCount')
    if _positive(episodes):
        metrics.append({'name': '集数', 'value': episodes, 'unit': '集'})
    tags = row.get('tags', [])
    status = row.get('statusTags', [])
    summary = row.get('description', '')
    return {
        'id': ident, 'title': title.strip(), 'rank': rank,
        'rank_semantics': 'original_rank', 'url': ORIGIN + row['href'],
        'cover_url': _image(row.get('cover')), 'summary': summary if isinstance(summary, str) else '',
        'author': '', 'tags': [s for s in tags if isinstance(s, str)] if isinstance(tags, list) else [],
        'status': ' · '.join(s for s in status if isinstance(s, str)) if isinstance(status, list) else '',
        'metrics': metrics,
    }


def parse_page(html, slug, page):
    """Parse JSON supplied to this exact public route without evaluating page scripts."""
    marker = _STATE_MARKER.search(html)
    if not marker:
        raise CollectorError('红果页面未提供可识别的榜单，请稍后重试。', 'source_changed')
    try:
        state = json.JSONDecoder().raw_decode(html[marker.end():])[0]
        data = state['loaderData']['rank_' + slug + '/page']
        content = data['content']
        pagination = content['pagination']
        rows = content['rankList']
        total_pages = pagination['totalPages']
        if (data['canonicalUrl'] != _page_url(slug, page)
                or data['pageNum'] != page or pagination['pageNum'] != page
                or content.get('isSuccess') is not True
                or not _positive(total_pages) or total_pages > 5 or page > total_pages
                or not isinstance(rows, list) or not 1 <= len(rows) <= PAGE_SIZE):
            raise ValueError('wrong ranking scope')
        has_more = page < total_pages
        if has_more and (len(rows) != PAGE_SIZE or pagination.get('nextUrl') != _page_url(slug, page + 1)):
            raise ValueError('wrong next page')
        if not has_more and pagination.get('nextUrl'):
            raise ValueError('unexpected continuation')
        books = [_book(row) for row in rows]
        if ([b['rank'] for b in books] != list(range((page - 1) * PAGE_SIZE + 1,
                                                   (page - 1) * PAGE_SIZE + len(books) + 1))
                or len({b['id'] for b in books}) != len(books)):
            raise ValueError('invalid original ranks')
        label = data.get('updatedText')
        label = label if isinstance(label, str) else ''
        return books, total_pages, label
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise CollectorError('红果返回的榜型、页码或原始排名无法核对。', 'source_changed') from exc


def _pause(progress, message):
    _notify(progress, message)
    # Short slices recheck membership/cancellation during an intentional source pause.
    for _ in range(PAGE_INTERVAL):
        time.sleep(1)
        network.check_authorization()


def collect(options, progress=None):
    board = options.get('board') or '综合热播榜'
    if (board not in BOARDS or options.get('channel', '综合') != '综合'
            or options.get('category', '全部') != '全部'):
        raise CollectorError('请选择红果官网提供的热播榜。', 'invalid_options')
    max_pages, limit = _page_options({**PLATFORM['default_options'], **options}, PAGE_SIZE)
    max_pages, limit = min(5, max_pages), min(100, limit)
    slug = BOARDS[board]
    books, seen = [], set()
    expected_pages, expected_label = None, None
    for page in range(1, max_pages + 1):
        if page > 1:
            _pause(progress, f'正在读取红果{board}第 {page} 页…')
        html = fetch_page(_page_url(slug, page), headers={'User-Agent': PUBLIC_AGENT})
        batch, total_pages, label = parse_page(html, slug, page)
        if page == 1:
            expected_pages, expected_label = total_pages, label
        elif total_pages != expected_pages or label != expected_label:
            raise CollectorError('翻页期间红果榜单范围发生变化，请稍后重试。', 'source_changed')
        ids = {book['id'] for book in batch}
        if seen.intersection(ids):
            raise CollectorError('红果分页重复返回作品，已保留先前结果。', 'source_changed')
        seen.update(ids)
        books.extend(batch[:limit - len(books)])
        end = page == total_pages
        complete = len(books) >= limit or end
        more = not complete and page < max_pages
        stop = 'last_page' if end else 'requested_limit' if complete else 'loading' if more else 'max_pages'
        snapshot = {
            'platform': PLATFORM['id'], 'mode': PLATFORM['mode'], 'channel': '综合',
            'category': '全部', 'board': board, 'books': list(books),
            'source_url': _page_url(slug, 1), 'source_id': 'hongguo:' + slug,
            # updatedText is a display label; the page has no reliable data-generation timestamp.
            'data_date': None, 'source_label_date': label,
            'available_count': len(books), 'requested_count': limit, 'complete': complete,
            'pagination': {'pages_fetched': page, 'max_pages': max_pages, 'page_size': PAGE_SIZE,
                           'source_pages': total_pages, 'stop_reason': stop},
        }
        _notify_snapshot(progress, snapshot, more)
        if not more:
            return snapshot
