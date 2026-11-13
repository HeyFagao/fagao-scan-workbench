# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Faloo's desktop charts and the first page of its named mobile rankings."""
import re
from urllib.parse import urljoin

from .collectors import CollectorError, _notify, _notify_snapshot, _page_options, _result, _Tree
from .public_rank_page import fetch_page


ORIGIN = 'https://b.faloo.com/'
MOBILE = 'https://wap.faloo.com/'
BOARDS = {
    '24小时热销榜': ('SR_1.html', '小说销售排行榜-天榜', '24小时'),
    '订阅人数天榜': ('SR_2.html', '小说订阅人数排行榜-天榜', '天榜'),
}
# Use the labels and targets paired by the mobile rank navigation, including
# 军事榜 at bookrank_lishi.html. The female section links a weekly sorted shelf.
MOBILE_RANKS = {
    '新书PK榜': 'newbookpk', '月票榜': 'yuepiao', '打赏榜': 'dashang',
    '女生榜': 'nvsheng', '同人榜': 'danmei', '都市榜': 'dushi', '玄幻榜': 'xuanhuan',
    '军事榜': 'lishi', '网游榜': 'wangyou', '灵异榜': 'kongbu', '武侠榜': 'wuxia',
    '校园榜': 'xiaoyuan', '轻小说榜': 'qingxiaoshuo',
}
GIRLS_WEEKLY = {
    '女生周点击 · ' + label: code for code, label in (
        (91, '穿越附身'), (92, '都市言情'), (93, '女尊天下'), (94, '架空历史'),
        (95, '豪门恩怨'), (96, '重生异能'), (48, '古典言情'),
    )
}
MOBILE_URLS = {board: MOBILE + 'bookrank_' + slug + '.html' for board, slug in MOBILE_RANKS.items()}
MOBILE_URLS.update({board: MOBILE + f'y_54_{code}_0_0_0_1.html' for board, code in GIRLS_WEEKLY.items()})
BOARD_PERIODS = {board: spec[2] for board, spec in BOARDS.items()}
BOARD_PERIODS.update({board: '周榜' if board in GIRLS_WEEKLY else '' for board in MOBILE_URLS})
PLATFORM = {
    'id': 'faloo', 'name': '飞卢小说网', 'mode': 'long', 'status': 'available',
    'message': '热销、订阅、分类与女生榜', 'source_url': ORIGIN + 'Rank_1.html',
    'channels': ['全部'], 'categories': ['全部'], 'boards': list(BOARD_PERIODS),
    'board_periods': BOARD_PERIODS,
    'default_options': {'channel': '全部', 'category': '全部', 'board': '24小时热销榜', 'period': '24小时'},
}
BOOK_URL = re.compile(r'https://b\.faloo\.com/([1-9][0-9]{0,17})\.html')
COVER_URL = re.compile(r'(?:https?:)?//img\.faloo\.com(/Novel/[A-Za-z0-9_./-]+\.(?:jpg|png|webp))', re.I)


def _nodes(root, css_class):
    return [n for n in root.walk() if css_class in n.attrs.get('class', '').split()]


def _links_in(root, css_class):
    return [link for container in _nodes(root, css_class) for link in container.walk('a')]


def _mobile_books(html, board):
    source = MOBILE_URLS[board]
    root = _Tree(html).root
    expected = '小说书库' if board in GIRLS_WEEKLY else '新书pk' if board == '新书PK榜' else board
    headers = _nodes(root, 'page_title')
    if len(headers) != 1 or headers[0].text != expected:
        raise CollectorError('飞卢未返回所选分类榜，已保留上次结果。')
    if board in GIRLS_WEEKLY:
        active = [a for a in _nodes(root, 'select') if a.tag == 'a' and a.text == '周点击']
        if len(active) != 1 or not active[0].attrs.get('href') or urljoin(source, active[0].attrs['href']) != source:
            raise CollectorError('飞卢未返回所选女生题材的周点击榜。')
    lists = [node for node in _nodes(root, 'novelList') if node.tag == 'ul']
    rows = [r for r in lists[0].walk('li') if r.parent is lists[0]] if len(lists) == 1 else []
    if not 0 < len(rows) <= (30 if board in GIRLS_WEEKLY else 15):
        raise CollectorError('飞卢分类榜首页没有可核实的作品列表。')
    books, seen = [], set()
    for position, row in enumerate(rows, 1):
        links = _links_in(row, 'bl_r1_tit')
        if len(links) != 1:
            raise CollectorError('飞卢分类榜缺少可核对的作品。')
        link = links[0]
        url = urljoin(source, link.attrs.get('href', ''))
        match = re.fullmatch(r'https://wap\.faloo\.com/([1-9][0-9]{0,17})\.html', url)
        title = (link.attrs.get('title') or link.text).strip()
        if not match or match[1] in seen or not title:
            raise CollectorError('飞卢分类榜作品地址、顺序或书名异常。')
        seen.add(match[1])
        credits = _links_in(row, 'nl_r1_author')
        author = (credits[0].attrs.get('title') or credits[0].text).strip() if credits else ''
        labels = [a.attrs.get('title') or a.text for a in credits[1:2] + _links_in(row, 'nl_tags')]
        summaries = _links_in(row, 'bl_r1_into')
        summary = next((a.text for a in summaries if urljoin(source, a.attrs.get('href', '')) == url), '')
        cover = ''
        for image in _nodes(row, 'cover'):
            if image.tag != 'img' or not image.parent or image.parent.tag != 'a' or urljoin(source, image.parent.attrs.get('href', '')) != url:
                continue
            image_match = re.fullmatch(r'(?:https?:)?//img\.faloo\.com(/Novel/332x470/\d+/\d+/\d+\.jpg)', image.attrs.get('src', ''), re.I)
            if image_match:
                cover = 'https://img.faloo.com' + image_match[1]
                break
        books.append({'id': match[1], 'rank': position, 'source_position': position, 'rank_semantics': 'source_list_position',
                      'title': title, 'url': url, 'author': author, 'cover_url': cover, 'summary': summary,
                      'tags': list(dict.fromkeys(tag.strip() for tag in labels if tag.strip())), 'metrics': []})
    return books


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
    if board not in BOARD_PERIODS or any(options.get(k) not in (None, '', '全部') for k in ('channel', 'category')):
        raise CollectorError('请选择飞卢支持的榜单及分类。')
    period = BOARD_PERIODS[board]
    if options.get('period') not in (None, '', period):
        raise CollectorError('飞卢榜期与所选榜型不一致。')
    _, limit = _page_options(options, 100)
    limit = min(limit, 100)
    mobile = board in MOBILE_URLS
    source = MOBILE_URLS[board] if mobile else ORIGIN + BOARDS[board][0]
    _notify(progress, f'正在读取飞卢{board}…')
    html = fetch_page(source, encoding='gb18030')
    if mobile:
        books = _mobile_books(html, board)
        count = min(len(books), limit)
        result = _result('faloo', 'long', '全部', '全部', board, source, books[:count], period=period,
                         requested_count=limit, available_count=count, complete=count == limit,
                         warnings=['此榜仅收录官网首页。'] if count < limit else [],
                         pagination={'pages_fetched': 1, 'max_pages': 1,
                                     'stop_reason': 'requested_limit' if count == limit else 'first_page_only'})
        _notify_snapshot(progress, result, False)
        return result
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
