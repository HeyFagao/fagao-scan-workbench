# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Read Zongheng's public illustrated ranking pages and their displayed editions."""
from datetime import date
import re

from .collectors import CollectorError, _Tree, _notify, _notify_snapshot, _page_options
from .public_rank_page import fetch_page, pause_before_page


ROOT = 'https://book.zongheng.com'
BOARD_URLS = {'新书榜': ROOT + '/rank/details.html?rt=4&d=1',
              '月票榜': ROOT + '/rank/details.html?rt=1&d=1&i=2'}
PLATFORM = {
    'id': 'zongheng', 'name': '纵横中文网', 'mode': 'long', 'status': 'available',
    'source_url': ROOT + '/rank.html', 'message': '纵横官方新书榜和月票榜',
    'channels': ['全站'], 'categories': ['全部'], 'boards': list(BOARD_URLS),
    'board_periods': {'月票榜': '月榜'},
    'default_options': {'channel': '全站', 'category': '全部', 'board': '新书榜'},
}
PAGE_SIZE = 20


def _nodes(node, class_name):
    return [child for child in node.walk() if class_name in child.attrs.get('class', '').split()]


def _one(node, class_name):
    matches = _nodes(node, class_name)
    if len(matches) != 1:
        raise CollectorError('纵横榜单页面结构已变化。', 'source_changed')
    return matches[0]


def _edition(root, board):
    if board == '月票榜':
        year = _one(root, 'data-year').attrs.get('data-val', '')
        month = _one(root, 'data-month').attrs.get('data-val', '')
        if re.fullmatch(r'[0-9]{4}', year) and re.fullmatch(r'[0-9]{1,2}', month) and 1 <= int(month) <= 12:
            return f'{year}-{int(month):02d}'
    else:
        picker = _one(root, 'data-date')
        selected = _one(picker, 'drop_tit').text.strip()
        try:
            valid = date.fromisoformat(selected).isoformat() == selected
        except ValueError:
            valid = False
        if valid and selected.replace('-', '') == picker.attrs.get('data-val'):
            return selected
    raise CollectorError('纵横榜单没有可确认的期次。', 'source_changed')


def _book(row, board, rank):
    book_id = row.attrs.get('bookid', '')
    name = _one(row, 'rank_d_b_name')
    links = list(name.walk('a'))
    expected_url = f'{ROOT}/book/{book_id}.html'
    if (not re.fullmatch(r'[0-9]{1,20}', book_id) or len(links) != 1
            or links[0].attrs.get('href') != expected_url or not links[0].text
            or _one(row, 'rank_d_b_num').text != str(rank)):
        raise CollectorError('纵横书目链接或原榜名次无法核对。', 'source_changed')
    info = [a.text.strip() for a in _one(row, 'rank_d_b_cate').walk('a')]
    if len(info) != 3:
        raise CollectorError('纵横书目作者、分类或状态有变化。', 'source_changed')
    metric = _one(row, 'rank_d_b_ticket').text.replace(' ', '').replace(',', '')
    label = '月票' if board == '月票榜' else '人气'
    value = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?(?:万|亿)?)' + label, metric)
    if not value:
        raise CollectorError('纵横榜单指标与所选榜型不一致。', 'source_changed')
    image = next(_one(row, 'rank_d_book_img').walk('img'), None)
    cover = image.attrs.get('src', '') if image else ''
    if not cover.startswith('https://static.zongheng.com/upload/'):
        cover = ''
    return {'id': book_id, 'rank': rank, 'rank_semantics': 'original_rank',
            'title': links[0].text, 'author': info[0], 'tags': [info[1]] if info[1] else [],
            'status': info[2], 'summary': _one(row, 'rank_d_b_info').text,
            'url': expected_url, 'cover_url': cover,
            'metrics': [{'name': label, 'value': value[1], 'unit': '票' if label == '月票' else ''}]}


def parse_page(html, board, page):
    tree = _Tree(html).root
    main = _one(tree, 'rank_main')
    if (_one(_one(main, 'rank_i_title_name'), 'active').text != board
            or (board == '新书榜' and _one(_one(main, 'rankclass_nav'), 'on').text != '全部分类')):
        raise CollectorError('纵横当前页面与所选榜型或分类不一致。', 'source_changed')
    listing = _one(main, 'rankpage_box')
    pager = _one(listing, 'pagebar').attrs
    try:
        total = int(pager['total'])
        pages = int(pager['count'])
    except (ValueError, TypeError, KeyError) as exc:
        raise CollectorError('纵横的分页数量无法核对。', 'source_changed') from exc
    if (pager.get('page') != str(page) or total <= 0 or pages != (total + PAGE_SIZE - 1) // PAGE_SIZE
            or not 1 <= page <= pages):
        raise CollectorError('纵横返回的榜单页码或总数不一致。', 'source_changed')
    rows = _nodes(listing, 'rank_d_list')
    offset = (page - 1) * PAGE_SIZE
    if len(rows) != min(PAGE_SIZE, total - offset):
        raise CollectorError('纵横该页返回的作品数量不完整。', 'source_changed')
    books = [_book(row, board, offset + position) for position, row in enumerate(rows, 1)]
    return _edition(main, board), total, books


def collect(options, progress=None):
    board = options.get('board') or '新书榜'
    period = PLATFORM['board_periods'].get(board)
    if (board not in BOARD_URLS or options.get('channel') not in (None, '', '全站')
            or options.get('category') not in (None, '', '全部') or options.get('period') not in (None, '', period)):
        raise CollectorError('请选择纵横中文网提供的榜单范围。')
    max_pages, limit = _page_options(options, PAGE_SIZE)
    limit = min(100, limit)
    source = BOARD_URLS[board]
    books, seen = [], set()
    edition = source_total = None
    for page in range(1, max_pages + 1):
        if page == 1:
            _notify(progress, f'正在读取纵横{board}…')
        else:
            pause_before_page(progress, f'正在读取纵横{board}第 {page} 页…')
        html = fetch_page(source if page == 1 else f'{source}&p={page}')
        current_edition, total, rows = parse_page(html, board, page)
        if edition is not None and (edition != current_edition or source_total != total):
            raise CollectorError('纵横榜单在翻页期间已更新，请重新获取。', 'source_changed')
        edition, source_total = current_edition, total
        for book in rows[:limit - len(books)]:
            if book['id'] in seen:
                raise CollectorError('纵横在不同名次重复返回同一作品。', 'source_changed')
            books.append(book)
            seen.add(book['id'])
        complete = len(books) == min(limit, total)
        more = not complete and page < max_pages
        stop = 'last_page' if len(books) == total else 'requested_limit' if complete else 'loading' if more else 'max_pages'
        snapshot = {'platform': 'zongheng', 'mode': 'long', 'channel': '全站', 'category': '全部',
                    'board': board, 'period': period, 'source_url': source, 'books': list(books),
                    'data_date': edition, 'source_total': total, 'requested_count': limit,
                    'available_count': len(books), 'complete': complete,
                    'pagination': {'page_size': PAGE_SIZE, 'pages_fetched': page, 'max_pages': max_pages,
                                   'stop_reason': stop, 'source_total': total}}
        _notify_snapshot(progress, snapshot, more)
        if not more:
            return snapshot
