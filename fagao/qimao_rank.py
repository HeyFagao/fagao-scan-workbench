# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Qimao's public chart and the same-site pagination used by its Next button."""
import json
import re
from urllib.parse import urlencode, urlsplit

from .collectors import CollectorError, _Tree, _notify_snapshot, _page_options
from .public_rank_page import fetch_page, pause_before_page

ORIGIN = 'https://www.qimao.com'
CHANNELS = {'男频': ('boy', '0'), '女频': ('girl', '1')}
BOARDS = {'大热榜': ('hot', '1'), '新书榜': ('new', '2'), '完结榜': ('over', '3')}
PERIODS = {'日榜': ('date', '1'), '月榜': ('month', '2')}
PLATFORM = {
    'id': 'qimao', 'name': '七猫中文网', 'mode': 'long', 'status': 'available',
    'message': '七猫公开日榜和月榜', 'source_url': ORIGIN + '/paihang/',
    'channels': list(CHANNELS), 'categories': ['全部'], 'boards': list(BOARDS),
    'periods': list(PERIODS),
    'default_options': {'channel': '男频', 'category': '全部', 'board': '大热榜', 'period': '日榜'},
}
BOOK_URL = re.compile(r'https://www\.qimao\.com/shuku/([1-9][0-9]{0,19})/')


def _opening_ids(html, source, channel, board, period):
    """Bind API records to the actual selected chart and visible first-page ranks."""
    root = _Tree(html).root
    title = next(root.walk('title'), None)
    active = [n for n in root.walk() if 'active' in n.attrs.get('class', '').split()]
    path = urlsplit(source).path
    channel_label = '男生榜' if channel == '男频' else '女生榜'
    if (not title or board not in title.text or not any(n.text == channel_label for n in active)
            or not any(n.tag == 'a' and n.text == period and n.attrs.get('href') == path for n in active)):
        raise CollectorError('七猫网页没有确认所选频道、榜型和周期。')
    ids = []
    for card in root.walk('li'):
        if 'rank-list-item' not in card.attrs.get('class', '').split():
            continue
        rank = next((n.text for n in card.walk('span') if 'rank-number' in n.attrs.get('class', '').split()), '')
        link = next((n for n in card.walk('a') if 's-book-title' in n.attrs.get('class', '').split()), None)
        ident = BOOK_URL.fullmatch(link.attrs.get('href', '')) if link else None
        if not ident or not link.text or rank != str(len(ids) + 1):
            raise CollectorError('七猫榜单首屏缺少可核对的作品或名次。')
        ids.append(ident[1])
    if not ids:
        raise CollectorError('七猫榜单首屏未显示作品。')
    return ids


def _positive_int(value):
    if not re.fullmatch(r'[1-9][0-9]{0,8}', str(value)):
        raise CollectorError('七猫分页返回的数量或页码无效。')
    return int(value)


def _book(row, rank):
    if not isinstance(row, dict):
        raise CollectorError('七猫返回了无效的作品记录。')
    ident = str(row.get('book_id', ''))
    url = row.get('book_url')
    title = row.get('title')
    if (not re.fullmatch(r'[1-9][0-9]{0,19}', ident) or not isinstance(title, str) or not title.strip()
            or url != f'{ORIGIN}/shuku/{ident}/'):
        raise CollectorError('七猫作品编号、书名或官方链接无法核对。')
    metrics = []
    heat = str(row.get('number', ''))
    unit = row.get('unit', '')
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', heat) and unit in ('', '万', '亿'):
        metrics.append({'name': '热度', 'value': heat, 'unit': unit})
    words = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([万亿]?字)', str(row.get('words_num', '')))
    if words:
        metrics.append({'name': '字数', 'value': words[1], 'unit': words[2]})
    cover = row.get('image_link')
    return {'id': ident, 'rank': rank, 'rank_semantics': 'source_list_position',
            'title': title.strip(), 'url': url, 'author': row.get('author', ''), 'summary': row.get('intro', ''),
            'cover_url': cover if isinstance(cover, str) and cover.startswith('https://cdn.qimao.com/') else '',
            'tags': [row[k] for k in ('category1_name', 'category2_name') if isinstance(row.get(k), str) and row[k]],
            'status': {'0': '连载中', '1': '已完结'}.get(str(row.get('is_over')), ''), 'metrics': metrics}


def collect(options, progress=None):
    channel = options.get('channel') or '男频'
    board = options.get('board') or '大热榜'
    period = options.get('period') or '日榜'
    if (channel not in CHANNELS or board not in BOARDS or period not in PERIODS
            or options.get('category', '全部') != '全部'):
        raise CollectorError('七猫榜单不支持所选频道、分类、榜型或周期。')
    channel_slug, gender = CHANNELS[channel]
    board_slug, board_id = BOARDS[board]
    period_slug, period_id = PERIODS[period]
    source = f'{ORIGIN}/paihang/{channel_slug}/{board_slug}/{period_slug}/'
    max_pages, limit = _page_options(options, 20)
    max_pages, limit = min(5, max_pages), min(100, limit)
    opening = _opening_ids(fetch_page(source), source, channel, board, period)
    params = {'is_girl': gender, 'rank_type': board_id, 'date_type': period_id}
    books, seen = [], set()
    source_total, source_month = None, None
    for page in range(1, max_pages + 1):
        pause_before_page(progress, f'正在读取七猫{board}第 {page} 页…')
        url = ORIGIN + '/qimaoapi/api/rank/book-list?' + urlencode({**params, 'page': page})
        try:
            body = json.loads(fetch_page(url, headers={'Referer': source}))
            rows = body['data']['table_data']
            meta = body['data']['page_data']
            returned_page = _positive_int(meta['page'])
            page_size = _positive_int(meta['page_size'])
            total = _positive_int(meta['count'])
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise CollectorError('七猫返回的内容不是可识别的榜单页。') from exc
        if (not isinstance(rows, list) or returned_page != page or page_size != 20
                or len(rows) != min(page_size, total - (page - 1) * page_size)):
            raise CollectorError('七猫的页码、行数或总数不一致。')
        month = meta.get('data_month')
        if period == '月榜' and not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', str(month)):
            raise CollectorError('七猫未标明所选月榜的月份。')
        if page == 1:
            source_total, source_month = total, month
            if period == '月榜':
                params['date'] = month.replace('-', '')
        elif total != source_total or (period == '月榜' and month != source_month):
            raise CollectorError('翻页期间七猫榜单的范围发生变化，请稍后重试。')
        batch = [_book(row, (page - 1) * page_size + i) for i, row in enumerate(rows, 1)]
        ids = [b['id'] for b in batch]
        if len(set(ids)) != len(ids) or seen.intersection(ids) or (page == 1 and ids != opening):
            raise CollectorError('七猫榜单与公开首屏不符或分页作品重复，请稍后重试。')
        seen.update(ids)
        books.extend(batch[:limit - len(books)])
        complete = len(books) == min(total, limit)
        more = not complete and page < max_pages
        if len(books) == total:
            stop = 'last_page'
        elif complete:
            stop = 'requested_limit'
        else:
            stop = 'loading' if more else 'max_pages'
        snapshot = {'platform': 'qimao', 'mode': 'long', 'channel': channel, 'category': '全部',
                    'board': board, 'period': period, 'books': list(books), 'source_url': source,
                    'source_total': total, 'available_count': len(books), 'requested_count': limit,
                    'data_date': month if period == '月榜' else None, 'complete': complete,
                    'pagination': {'pages_fetched': page, 'max_pages': max_pages, 'stop_reason': stop,
                                   'page_size': page_size, 'source_total': total}}
        _notify_snapshot(progress, snapshot, more)
        if not more:
            return snapshot
