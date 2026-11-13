# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Read the visible ranked table on Jinjiang's official public ranking page."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .collectors import CollectorError, _Tree, _notify, _notify_snapshot, _page_options, _result
from .public_rank_page import fetch_page

ORIGIN = 'https://www.jjwxc.net/'
CHANNELS = {'言情': 0, '纯爱': 1, '原创站': 2}
BOARDS = {'总分榜': 7, '月榜': 5, '字数榜': 8, '季榜': 4, '半年榜': 6, '新晋作者榜': 3}
DEFAULT = {'channel': '言情', 'category': '全部', 'board': '总分榜'}
HEADERS = ['序号', '作者', '作品', '类型', '进度', '字数', '作品积分', '发表时间']
PLATFORM = {
    'id': 'jjwxc', 'name': '晋江文学城', 'mode': 'long', 'status': 'available',
    'message': '按原站分站排名；月榜统计发文第 11 至 40 天的作品积分。',
    'source_url': ORIGIN + 'topten.php?orderstr=7&t=0', 'channels': list(CHANNELS),
    'channel_labels': {'言情': '言情站', '纯爱': '非言情站', '原创站': '原创站'},
    'categories': ['全部'], 'boards': list(BOARDS), 'default_options': DEFAULT,
}


def _cells(row):
    return [child for child in row.children if getattr(child, 'tag', None) in ('td', 'th')]


def _parse_row(cells):
    rank, author, work, category, status, words, points, published = cells
    anchors = list(work.walk('a'))
    if len(anchors) != 1:
        raise CollectorError('晋江作品链接不明确，已保留原榜单。')
    link = anchors[0]
    url = urljoin(ORIGIN, link.attrs.get('href') or '')
    ident = re.fullmatch(re.escape(ORIGIN) + r'onebook\.php\?novelid=([1-9][0-9]*)', url)
    title = link.text.strip()
    if not ident or not title or not re.fullmatch(r'[1-9][0-9]*', rank.text):
        raise CollectorError('晋江作品标识、书名或原榜名次不完整，已保留原榜单。')
    if not re.fullmatch(r'[0-9]+', words.text) or not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', points.text):
        raise CollectorError('晋江榜单的字数或积分格式变化，已保留原榜单。')
    # The list puts the publisher's short blurb in its ordinary hover tooltip.
    blurb = link.attrs.get('rel') or ''
    return {
        'id': ident[1], 'rank': int(rank.text), 'title': title, 'author': author.text,
        'url': url, 'tags': category.text.split('-') if category.text else [],
        'status': status.text, 'summary': ' '.join(_Tree(blurb).root.text.split()) if blurb else '', 'cover_url': '',
        'metrics': [{'name': '字数', 'value': words.text, 'unit': '字'},
                    {'name': '作品积分', 'value': points.text.replace(',', ''), 'unit': ''},
                    {'name': '发表时间', 'value': published.text, 'unit': ''}],
    }


def parse_page(html, url, board, limit):
    root = _Tree(html).root
    current = [node for node in root.walk('span') if 'current' in (node.attrs.get('class') or '').split()
               and node.parent.tag == 'a']
    expected_tab = '新晋作者' if board == '新晋作者榜' else board
    if len(current) != 1 or current[0].text != expected_tab or urljoin(ORIGIN, current[0].parent.attrs.get('href') or '') != url:
        raise CollectorError('晋江未返回所选分站和榜单，已保留原榜单。')
    headers = [row for row in root.walk('tr') if [c.text for c in _cells(row)] == HEADERS]
    if len(headers) != 1:
        raise CollectorError('晋江页面缺少可核对的排名表，已保留原榜单。')
    table = headers[0].parent
    while table.parent and table.tag != 'table':
        table = table.parent
    rows = list(table.walk('tr'))
    if not rows or rows[0] is not headers[0] or len(rows) < 2:
        raise CollectorError('晋江排名表暂时没有可读取的作品。')
    books, seen_ids = [], set()
    for row in rows[1:limit + 1]:
        cells = _cells(row)
        if len(cells) != len(HEADERS) or any('\ufffd' in cell.text for cell in cells):
            raise CollectorError('晋江排名表字段不全，已保留原榜单。')
        book = _parse_row(cells)
        if '\ufffd' in book['summary']:
            raise CollectorError('晋江作品简介编码有误，已保留原榜单。')
        if book['id'] in seen_ids or (books and book['rank'] <= books[-1]['rank']):
            raise CollectorError('晋江榜单作品或名次重复、乱序，已保留原榜单。')
        books.append(book)
        seen_ids.add(book['id'])
    return books, len(rows) - 1


def collect(options, progress=None):
    channel = options.get('channel', DEFAULT['channel'])
    board = options.get('board', DEFAULT['board'])
    if channel not in CHANNELS or board not in BOARDS or options.get('category', '全部') != '全部':
        raise CollectorError('请选择晋江支持的分站和榜单。')
    _, requested = _page_options(options, 100)
    limit = min(requested, 100)
    url = ORIGIN + f'topten.php?orderstr={BOARDS[board]}&t={CHANNELS[channel]}'
    _notify(progress, f'正在读取晋江{channel}分站{board}…')
    # This chart can exceed 10 MB and contain malformed GB18030 far below the requested rows.
    # Decode it tolerantly, then verify the selected cells and blurb before accepting any record.
    fetch_options = {'max_bytes': 16 * 1024 * 1024, 'timeout': 60, 'errors': 'replace'} if board == '新晋作者榜' else {'timeout': 25}
    books, total = parse_page(fetch_page(url, encoding='gb18030', **fetch_options), url, board, limit)
    complete = len(books) == total
    result = _result('jjwxc', 'long', channel, '全部', board, url, books, period='',
                     complete=complete, requested_count=limit, available_count=len(books), source_total=total,
                     pagination={'pages_fetched': 1, 'max_pages': 1, 'source_total': total,
                                 'stop_reason': 'source_end' if complete else 'requested_limit'})
    _notify_snapshot(progress, result, False)
    return result
