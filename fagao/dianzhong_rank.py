# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Dianzhong's public rankings, filtered to the site's 精品故事 category."""
import json
import re
import time

from . import network
from .collectors import CollectorError, _Tree, _cover_url, _notify, _notify_snapshot, _page_options
from .public_rank_page import fetch_page

ORIGIN = 'https://www.ishugui.com'
CHANNELS = {'男频': 1, '女频': 2}
BOARDS = {'畅销榜': (1, 2), '完本榜': (3, 4), '新书榜': (5, 6),
          '热读榜': (7, 8), '好评榜': (11, 12), '经典榜': (20, 21)}
PERIODS = {'日榜': 1, '月榜': 2}
BOARD_PERIODS = {name: ['日榜'] if name in ('新书榜', '好评榜', '经典榜') else ['日榜', '月榜']
                 for name in BOARDS}
PLATFORM = {
    'id': 'dianzhong', 'name': '点众短篇', 'mode': 'short', 'status': 'available',
    'source_url': ORIGIN + '/ranking', 'message': '点众原榜中的精品故事，保留原榜名次',
    'channels': list(CHANNELS), 'categories': ['精品故事'], 'boards': list(BOARDS),
    'periods': list(PERIODS), 'periods_by_board': BOARD_PERIODS,
    'default_options': {'channel': '男频', 'category': '精品故事', 'board': '畅销榜', 'period': '日榜'},
}


def _pause(progress, page):
    _notify(progress, f'正在读取点众第 {page} 页…')
    time.sleep(15)
    network.check_authorization()


def _positive(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(value)):
        raise CollectorError('点众返回的作品编号或页码无法核对。', 'source_changed')
    return int(value)


def parse_page(html, channel, board, period, page):
    marker = re.search(r'<script\b[^>]*\bid=["\x27]__NEXT_DATA__["\x27][^>]*>(.*?)</script>', html, re.S)
    if not marker:
        raise CollectorError('点众页面没有返回可核对的榜单。', 'source_changed')
    try:
        data = json.loads(marker[1])['props']['pageProps']
        rows = data['rankBook']
        pages = _positive(data['pages'])
        channel_id = CHANNELS[channel]
        board_id = BOARDS[board][channel_id - 1]
        if (int(data['page']) != page or int(data['rankId']) != board_id
                or int(data['rankType']) != channel_id or int(data['rankStyle']) != PERIODS[period]
                or data['rankPathName']['rankIdName'] != board or data['rankPathName']['styleName'] != period
                or not isinstance(rows, list) or not 1 <= len(rows) <= 10 or page > pages
                or (page < pages and len(rows) != 10)):
            raise ValueError
    except (KeyError, ValueError, TypeError, RecursionError) as exc:
        raise CollectorError('点众返回的频道、榜型或页码与请求不一致。', 'source_changed') from exc

    root = _Tree(html).root
    visible = []
    for card in root.walk('div'):
        if not any(name.startswith('RankBookList_rankItem__') for name in card.attrs.get('class', '').split()):
            continue
        rank = next((node for node in card.walk('a') if any(
            name.startswith('RankBookList_rankIndex__') for name in node.attrs.get('class', '').split())), None)
        if rank is None:
            raise CollectorError('点众原始名次缺失。', 'source_changed')
        visible.append((rank.text, rank.attrs.get('href')))
    expected = [(str((page - 1) * 10 + index), '/book/' + str(row.get('bookId')))
                for index, row in enumerate(rows, 1) if isinstance(row, dict)]
    if visible != expected or len(expected) != len(rows):
        raise CollectorError('点众网页名次与书目数据不一致。', 'source_changed')

    books = []
    for index, row in enumerate(rows, 1):
        ident = str(_positive(row['bookId']))
        title = row.get('bookName')
        tags = row.get('bookTypeThree')
        if not isinstance(title, str) or not title.strip() or not isinstance(tags, list):
            raise CollectorError('点众作品标题或分类缺失。', 'source_changed')
        if '精品故事' not in tags:
            continue
        if any(not isinstance(row.get(field, ''), str) for field in ('author', 'introduction', 'coverWap')):
            raise CollectorError('点众作品资料的字段格式已变化。', 'source_changed')
        metrics = []
        heat = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([万亿]?)热度', str(row.get('num', '')))
        if heat:
            metrics.append({'name': '热度', 'value': heat[1], 'unit': heat[2]})
        words = str(row.get('totalWordSize', ''))
        if re.fullmatch(r'[0-9]+', words):
            metrics.append({'name': '字数', 'value': words, 'unit': '字'})
        books.append({'id': ident, 'title': title.strip(), 'rank': (page - 1) * 10 + index,
                      'url': ORIGIN + '/book/' + ident, 'author': row.get('author', ''),
                      'summary': row.get('introduction', ''), 'cover_url': _cover_url(row.get('coverWap')),
                      'tags': [tag for tag in tags if isinstance(tag, str)], 'metrics': metrics})
    return books, len(rows), pages


def collect(options, progress=None):
    channel = options.get('channel') or '男频'
    board = options.get('board') or '畅销榜'
    period = options.get('period') or '日榜'
    if (channel not in CHANNELS or period not in BOARD_PERIODS.get(board, ())
            or options.get('category', '精品故事') != '精品故事'):
        raise CollectorError('点众不支持所选频道、分类、榜型或周期。')
    max_pages, limit = _page_options(options, 10)
    max_pages, limit = min(10, max_pages), min(100, limit)
    rank_id = BOARDS[board][CHANNELS[channel] - 1]
    source = f'{ORIGIN}/ranking/{CHANNELS[channel]}-{rank_id}-{PERIODS[period]}'
    books, seen = [], set()
    source_rows = 0
    expected_pages = None
    for page in range(1, max_pages + 1):
        if page > 1:
            _pause(progress, page)
        url = source if page == 1 else source + '/' + str(page)
        batch, row_count, pages = parse_page(fetch_page(url), channel, board, period, page)
        if expected_pages is not None and pages != expected_pages:
            raise CollectorError('点众翻页期间榜单页数发生变化。', 'source_changed')
        expected_pages = pages
        bound = min(limit, source_rows + row_count)
        batch = [book for book in batch if book['rank'] <= limit]
        ids = {book['id'] for book in batch}
        if len(ids) != len(batch) or ids.intersection(seen):
            raise CollectorError('点众翻页出现重复作品。', 'source_changed')
        seen.update(ids)
        books.extend(batch)
        source_rows = bound
        complete = page == pages or source_rows == limit
        more = not complete and page < max_pages
        stop = 'last_page' if page == pages else 'requested_limit' if complete else 'loading' if more else 'max_pages'
        snapshot = {'platform': 'dianzhong', 'mode': 'short', 'channel': channel,
                    'category': '精品故事', 'board': board, 'period': period, 'books': list(books),
                    'source_url': source, 'data_date': None, 'requested_count': limit,
                    'available_count': len(books), 'source_rows_seen': source_rows,
                    'scope': f'点众{channel}{board}{period} · 原榜前{limit}项中的精品故事',
                    'complete': complete, 'warnings': ['仅收录原榜中的精品故事，名次可能有空缺。'],
                    'pagination': {'pages_fetched': page, 'max_pages': max_pages, 'page_size': 10,
                                   'stop_reason': stop, 'source_pages': pages, 'source_rows_seen': source_rows}}
        _notify_snapshot(progress, snapshot, more)
        if not more:
            if not books:
                raise CollectorError('点众本次榜单范围内没有精品故事，已有资料仍保留。', 'empty')
            return snapshot
