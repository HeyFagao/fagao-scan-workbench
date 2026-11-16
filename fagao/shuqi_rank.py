# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Shuqi's official, publicly rendered rank lists and metadata."""
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from . import network
from .collectors import CollectorError, _Tree, _cover_url, _notify, _notify_snapshot, _page_options
from .public_rank_page import fetch_page

ORIGIN = 'https://www.shuqi.com'
READER_AGENT = 'Fagao-Workbench/0.7 (public ranking reader)'
CHANNELS = {'男频': 'boy', '女频': 'girl'}
BOARDS = {'点击榜': 'Click', '收藏榜': 'Store', '订阅榜': 'Order', '人气榜': 'hot',
          '新书榜': 'New', '完结榜': 'End', '更新榜': 'Update'}
PLATFORM = {
    'id': 'shuqi', 'name': '书旗小说', 'mode': 'long', 'status': 'available',
    'source_url': ORIGIN + '/rank', 'message': '书旗官网当前榜单，按来源列表顺序展示',
    'channels': list(CHANNELS), 'categories': ['全部'], 'boards': list(BOARDS), 'periods': ['当前榜'],
    'default_options': {'channel': '男频', 'category': '全部', 'board': '点击榜', 'period': '当前榜'},
}


def _class(node, name):
    return name in node.attrs.get('class', '').split()


def _one(root, tag, name):
    return next((node for node in root.walk(tag) if _class(node, name)), None)


def _text(root, tag, name):
    node = _one(root, tag, name)
    return node.text if node else ''


def _pause(progress, page):
    _notify(progress, f'正在读取书旗第 {page} 页…')
    time.sleep(15)
    network.check_authorization()


def parse_page(html, channel, board, page, source):
    root = _Tree(html).root
    active = [node for node in root.walk('div') if _class(node, 'nav') and _class(node, 'active')]
    if len(active) != 1 or active[0].text != channel + board:
        raise CollectorError('书旗页面没有确认所选频道和榜型。', 'source_changed')
    pagination = _one(root, 'div', 'comp-web-pages')
    selected = [node.text for node in pagination.walk('span') if _class(node, 'active') and node.text.isdecimal()] if pagination else []
    if selected != [str(page)]:
        raise CollectorError('书旗返回的榜单页码与请求不一致。', 'source_changed')
    listing = _one(root, 'ul', 'ranklist-ul')
    cards = [node for node in listing.children if not isinstance(node, str) and node.tag == 'li'] if listing else []
    if not 1 <= len(cards) <= 10:
        raise CollectorError('书旗没有返回可识别的榜单书目。', 'source_changed')
    books = []
    for index, card in enumerate(cards, 1):
        title = next(card.walk('h3'), None)
        link = next((a for a in card.walk('a') if re.fullmatch(r'/book/[1-9][0-9]{0,18}\.html', a.attrs.get('href', ''))), None)
        if title is None or not title.text or link is None:
            raise CollectorError('书旗书名或官方作品链接缺失。', 'source_changed')
        image = next(card.walk('img'), None)
        author_node = _one(card, 'span', 'bkuser-icon')
        category = _text(card, 'span', 'bkcate-icon')
        tags = [node.text for tagbox in card.walk('p') if _class(tagbox, 'ranklist-tag')
                for node in tagbox.walk('a') if node.text]
        ident = re.search(r'/book/([1-9][0-9]*)\.html', link.attrs['href'])[1]
        books.append({'id': ident, 'title': title.text, 'rank': (page - 1) * 10 + index,
                      'rank_semantics': 'source_list_position', 'url': ORIGIN + link.attrs['href'],
                      'author': author_node.text if author_node else '',
                      'cover_url': _cover_url(image.attrs.get('src')) if image else '',
                      'summary': _text(card, 'p', 'ranklist-des'),
                      'tags': list(dict.fromkeys(([category] if category else []) + tags)), 'metrics': []})
    next_links = [a for a in pagination.walk('a') if a.text == '下一页']
    next_url = None
    if next_links:
        if len(next_links) != 1:
            raise CollectorError('书旗出现多个下一页入口。', 'source_changed')
        next_url = urljoin(source, next_links[0].attrs.get('href', ''))
        parsed = urlsplit(next_url)
        expected_rank = CHANNELS[channel] + BOARDS[board]
        if (parsed.scheme != 'https' or parsed.netloc != 'www.shuqi.com' or parsed.path != '/ranklist'
                or parse_qs(parsed.query) != {'rank': [expected_rank], 'page': [str(page + 1)]}):
            raise CollectorError('书旗的下一页与所选榜单不一致。', 'source_changed')
    return books, next_url


def collect(options, progress=None):
    channel = options.get('channel') or '男频'
    board = options.get('board') or '点击榜'
    if (channel not in CHANNELS or board not in BOARDS or options.get('category', '全部') != '全部'
            or options.get('period', '当前榜') != '当前榜'):
        raise CollectorError('书旗不支持所选频道、分类、榜型或周期。')
    max_pages, limit = _page_options(options, 10)
    max_pages, limit = min(10, max_pages), min(100, limit)
    source = ORIGIN + '/ranklist?' + urlencode({'rank': CHANNELS[channel] + BOARDS[board]})
    url, books, seen = source, [], set()
    for page in range(1, max_pages + 1):
        if page > 1:
            _pause(progress, page)
        batch, next_url = parse_page(fetch_page(url, headers={'User-Agent': READER_AGENT}), channel, board, page, source)
        ids = {book['id'] for book in batch}
        if len(ids) != len(batch) or seen.intersection(ids):
            raise CollectorError('书旗翻页出现重复作品。', 'source_changed')
        seen.update(ids)
        books.extend(batch[:limit - len(books)])
        complete = next_url is None or len(books) >= limit
        more = not complete and page < max_pages
        stop = 'last_page' if next_url is None else 'requested_limit' if complete else 'loading' if more else 'max_pages'
        snapshot = {'platform': 'shuqi', 'mode': 'long', 'channel': channel, 'category': '全部',
                    'board': board, 'period': '当前榜', 'books': list(books), 'source_url': source,
                    'data_date': None, 'requested_count': limit, 'available_count': len(books),
                    'complete': complete, 'rank_semantics': 'source_list_position',
                    'pagination': {'pages_fetched': page, 'max_pages': max_pages, 'stop_reason': stop, 'page_size': 10}}
        _notify_snapshot(progress, snapshot, more)
        if not more:
            return snapshot
        url = next_url
