# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Faloo's two public book charts, as printed in the selected desktop ranking tab."""
import re
from urllib.parse import urljoin

from .collectors import CollectorError, _notify, _notify_snapshot, _page_options, _result, _Tree
from .public_rank_page import fetch_page


ORIGIN = 'https://b.faloo.com/'
BOARDS = {
    '24小时热销榜': ('SR_1.html', '小说销售排行榜-天榜', '24小时'),
    '订阅人数天榜': ('SR_2.html', '小说订阅人数排行榜-天榜', '天榜'),
}
PLATFORM = {
    'id': 'faloo', 'name': '飞卢小说网', 'mode': 'long', 'status': 'available',
    'message': '公开天榜', 'source_url': ORIGIN + 'Rank_1.html',
    'channels': ['全部'], 'categories': ['全部'], 'boards': list(BOARDS),
    'board_periods': {board: spec[2] for board, spec in BOARDS.items()},
    'default_options': {'channel': '全部', 'category': '全部', 'board': '24小时热销榜', 'period': '24小时'},
}
BOOK_URL = re.compile(r'https://b\.faloo\.com/([1-9][0-9]{0,17})\.html')
COVER_URL = re.compile(r'(?:https?:)?//img\.faloo\.com(/Novel/[A-Za-z0-9_./-]+\.(?:jpg|png|webp))', re.I)


def _nodes(root, css_class):
    return [n for n in root.walk() if css_class in n.attrs.get('class', '').split()]


def parse_page(html, board):
    path, expected_tab, _ = BOARDS[board]
    source = ORIGIN + path
    root = _Tree(html).root
    active = [node for nav in _nodes(root, 'c_tab') for node in nav.walk('a')
              if 'hover' in node.attrs.get('class', '').split()]
    tables = _nodes(root, 'c_tabdetail')
    if (len(active) != 1 or active[0].text.strip() != expected_tab
            or urljoin(source, active[0].attrs.get('href', '')) != source or len(tables) != 1):
        raise CollectorError('飞卢页面未显示所选天榜，已保留上次结果。')

    books = []
    ids = set()
    previous_rank = 0
    rows = _nodes(tables[0], 'c_td_d_data')
    for row in rows[:100]:
        numbers = _nodes(row, 'c_td_d_d_number1') + _nodes(row, 'c_td_d_d_number2')
        links = [a for title in _nodes(row, 'c_td_d_d_title') for a in title.walk('a')]
        if len(numbers) != 1 or not re.fullmatch(r'[0-9]{1,5}', numbers[0].text) or len(links) != 1:
            raise CollectorError('飞卢榜单缺少可核对的原始名次或书名。')
        rank = int(numbers[0].text)
        link = links[0]
        url = urljoin(source, link.attrs.get('href', ''))
        match = BOOK_URL.fullmatch(url)
        title = (link.attrs.get('title') or link.text).strip()
        if not match or not title or match[1] in ids or rank <= previous_rank:
            raise CollectorError('飞卢榜单的书目链接、名次或顺序异常。')
        ids.add(match[1])
        previous_rank = rank
        authors = _nodes(row, 'c_td_d_d_author')
        categories = _nodes(row, 'c_td_d_d_class')
        topics = [a for box in _nodes(row, 'c_td_d_d_tag') for a in box.walk('a')]
        tags = list(dict.fromkeys(v for a in categories + topics if (v := (a.attrs.get('title') or a.text).strip())))
        image = next((img for box in _nodes(row, 'c_td_d_d_img') for img in box.walk('img')), None)
        cover = COVER_URL.fullmatch(image.attrs.get('src', '')) if image else None
        books.append({'id': match[1], 'rank': rank, 'title': title, 'url': url,
                      'author': authors[0].text if authors else '', 'tags': tags,
                      'cover_url': 'https://img.faloo.com' + cover[1] if cover else '',
                      'summary': '', 'metrics': []})
    if not books:
        raise CollectorError('飞卢没有显示可核对的榜单记录。')
    return books, len(rows)


def collect(options, progress=None):
    board = options.get('board') or PLATFORM['default_options']['board']
    if board not in BOARDS or any(options.get(k) not in (None, '', '全部') for k in ('channel', 'category')):
        raise CollectorError('请选择飞卢支持的天榜及分类。')
    path, _, period = BOARDS[board]
    if options.get('period') not in (None, '', period):
        raise CollectorError('飞卢榜期与所选榜型不一致。')
    _, limit = _page_options(options, 100)
    limit = min(limit, 100)
    source = ORIGIN + path
    _notify(progress, f'正在读取飞卢{board}…')
    html = fetch_page(source, encoding='gb18030')
    books, visible_count = parse_page(html, board)
    selected = books[:limit]
    # This desktop chart prints its entire list on one page. Reading a smaller
    # requested prefix also fulfils the request; keep its size distinct from the board size.
    stop = 'source_end' if len(selected) == visible_count else 'requested_limit'
    result = _result('faloo', 'long', '全部', '全部', board, source, selected, period=period,
                     complete=True, requested_count=limit, available_count=len(selected), source_total=visible_count,
                     pagination={'pages_fetched': 1, 'max_pages': 1, 'source_total': visible_count,
                                 'stop_reason': stop})
    _notify_snapshot(progress, result, False)
    return result
