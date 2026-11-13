# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Read Zongheng's public illustrated ranking pages and their displayed editions."""
from datetime import date
import re

from .collectors import CollectorError, _Tree, _notify, _notify_snapshot, _page_options
from .public_rank_page import fetch_page, pause_before_page


ROOT = 'https://book.zongheng.com'
BOARD_IDS = {
    '新书榜': 4, '月票榜': 1, '24小时畅销榜': 3, '点击榜': 5, '推荐榜': 6,
    '捧场榜': 7, '完结榜': 8, '新书订阅榜': 9, '24小时更新榜': 10,
}
BOARD_URLS = {name: f'{ROOT}/rank/details.html?rt={board_id}&d=1' for name, board_id in BOARD_IDS.items()}
BOARD_URLS['月票榜'] += '&i=2'
PERIOD_IDS = {'周榜': 0, '日榜': 1, '月榜': 2}
METRICS = {'月票榜': ('月票', '票'), '新书榜': ('人气', ''), '点击榜': ('点击数', ''), '推荐榜': ('推荐票', '票')}
GENRE_BOARDS = ('新书榜', '点击榜', '推荐榜')
PERIODS_BY_BOARD = {name: list(PERIOD_IDS) for name in ('点击榜', '推荐榜')}
PLATFORM = {
    'id': 'zongheng', 'name': '纵横中文网', 'mode': 'long', 'status': 'available',
    'source_url': ROOT + '/rank.html', 'message': '纵横官网作品榜',
    'channels': ['全站'], 'categories': ['全部'], 'boards': list(BOARD_URLS),
    'periods_by_board': PERIODS_BY_BOARD,
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
    elif board == '新书榜':
        picker = _one(root, 'data-date')
        selected = _one(picker, 'drop_tit').text.strip()
        try:
            valid = date.fromisoformat(selected).isoformat() == selected
        except ValueError:
            valid = False
        if valid and selected.replace('-', '') == picker.attrs.get('data-val'):
            return selected
    else:
        return None
    raise CollectorError('纵横榜单没有可确认的期次。', 'source_changed')


def _book(row, board):
    book_id = row.attrs.get('bookid', '')
    name = _one(row, 'rank_d_b_name')
    links = list(name.walk('a'))
    expected_url = f'{ROOT}/book/{book_id}.html'
    rank = _one(row, 'rank_d_b_num').text
    if (not re.fullmatch(r'[0-9]{1,20}', book_id) or len(links) != 1
            or links[0].attrs.get('href') != expected_url or not links[0].text
            or not re.fullmatch(r'[1-9][0-9]{0,7}', rank)):
        raise CollectorError('纵横书目链接或原榜名次无法核对。', 'source_changed')
    info = [a.text.strip() for a in _one(row, 'rank_d_b_cate').walk('a')]
    if len(info) != 3:
        raise CollectorError('纵横书目作者、分类或状态有变化。', 'source_changed')
    metric = _one(row, 'rank_d_b_ticket').text.replace(' ', '').replace(',', '')
    metrics = []
    if board in METRICS:
        label, unit = METRICS[board]
        value = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?(?:万|亿)?)' + label, metric)
        if not value:
            raise CollectorError('纵横榜单指标与所选榜型不一致。', 'source_changed')
        metrics.append({'name': label, 'value': value[1], 'unit': unit})
    image = next(_one(row, 'rank_d_book_img').walk('img'), None)
    cover = image.attrs.get('src', '') if image else ''
    if not cover.startswith('https://static.zongheng.com/upload/'):
        cover = ''
    return {'id': book_id, 'rank': int(rank), 'rank_semantics': 'original_rank',
            'title': links[0].text, 'author': info[0], 'tags': [info[1]] if info[1] else [],
            'status': info[2], 'summary': _one(row, 'rank_d_b_info').text,
            'url': expected_url, 'cover_url': cover,
            'metrics': metrics}


def parse_page(html, board, page, period=None):
    tree = _Tree(html).root
    main = _one(tree, 'rank_main')
    if _one(_one(main, 'rank_i_title_name'), 'active').text != board:
        raise CollectorError('纵横返回了不同榜型。', 'source_changed')
    if board in GENRE_BOARDS:
        chosen = _one(_one(main, 'rankclass_nav'), 'on')
        if chosen.text != '全部分类' or chosen.attrs.get('catepid', '0') != '0':
            raise CollectorError('纵横返回了不同分类。', 'source_changed')
    if board in PERIODS_BY_BOARD:
        selected = [node for node in _nodes(main, 'on') if 'period' in node.attrs]
        if len(selected) != 1 or selected[0].text != period or selected[0].attrs['period'] != str(PERIOD_IDS.get(period)):
            raise CollectorError('纵横返回了不同周期。', 'source_changed')
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
    if not rows or len(rows) > min(PAGE_SIZE, total - offset):
        raise CollectorError('纵横该页作品数量无法核对。', 'source_changed')
    books = [_book(row, board) for row in rows]
    # The official recommendation/update pages sometimes omit ranks (e.g. 79/80).
    # Preserve the explicit slots; never fill a gap by renumbering later works.
    ranks = [book['rank'] for book in books]
    if ranks != sorted(set(ranks)) or ranks[0] <= offset or ranks[-1] > min(offset + PAGE_SIZE, total):
        raise CollectorError('纵横原榜名次与当前页不符。', 'source_changed')
    return _edition(main, board), total, books


def collect(options, progress=None):
    board = options.get('board') or '新书榜'
    category = options.get('category') or '全部'
    available_periods = PERIODS_BY_BOARD.get(board) or [PLATFORM['board_periods'].get(board)]
    period = options.get('period') or available_periods[0]
    if (board not in BOARD_URLS or options.get('channel') not in (None, '', '全站')
            or category != '全部' or period not in available_periods):
        raise CollectorError('请选择纵横中文网提供的榜单范围。')
    max_pages, limit = _page_options(options, PAGE_SIZE)
    limit = min(100, limit)
    source = BOARD_URLS[board]
    if board in PERIODS_BY_BOARD:
        source += f'&i={PERIOD_IDS[period]}'
    books, seen = [], set()
    edition = source_total = None
    for page in range(1, max_pages + 1):
        if page == 1:
            _notify(progress, f'正在读取纵横{board}…')
        else:
            pause_before_page(progress, f'正在读取纵横{board}第 {page} 页…')
        html = fetch_page(source if page == 1 else f'{source}&p={page}')
        current_edition, total, rows = parse_page(html, board, page, period)
        if source_total is not None and (edition != current_edition or source_total != total):
            raise CollectorError('纵横榜单在翻页期间已更新，请重新获取。', 'source_changed')
        edition, source_total = current_edition, total
        for book in rows:
            if book['rank'] > limit:
                break
            if book['id'] in seen:
                raise CollectorError('纵横在不同名次重复返回同一作品。', 'source_changed')
            books.append(book)
            seen.add(book['id'])
        target = min(limit, total)
        scanned = min(page * PAGE_SIZE, target)
        complete = len(books) == target
        more = scanned < target and page < max_pages
        if len(books) < scanned and scanned == target:
            stop = 'source_gaps'
        elif complete and target == total:
            stop = 'last_page'
        else:
            stop = 'requested_limit' if complete else 'loading' if more else 'max_pages'
        snapshot = {'platform': 'zongheng', 'mode': 'long', 'channel': '全站', 'category': category,
                    'board': board, 'period': period, 'source_url': source, 'books': list(books),
                    'data_date': edition, 'source_total': total, 'requested_count': limit,
                    'available_count': len(books), 'complete': complete,
                    'warnings': [f'来源在前 {scanned} 个名次中显示了 {len(books)} 部作品，已保留原榜名次。'] if len(books) < scanned else [],
                    'pagination': {'page_size': PAGE_SIZE, 'pages_fetched': page, 'max_pages': max_pages,
                                   'stop_reason': stop, 'source_total': total}}
        _notify_snapshot(progress, snapshot, more)
        if not more:
            return snapshot
