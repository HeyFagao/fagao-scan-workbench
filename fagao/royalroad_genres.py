# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""RR Tracker's publicly observed Royal Road Rising Stars by genre."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re

from .collectors import CollectorError, _cover_url, _fetch, _notify, _notify_snapshot, _page_options

RR_GENRES = {
    '动作': 'action', '冒险': 'adventure', '喜剧': 'comedy', '当代': 'contemporary',
    '剧情': 'drama', '奇幻': 'fantasy', '历史': 'historical', '恐怖': 'horror',
    '悬疑': 'mystery', '心理': 'psychological', '言情': 'romance', '讽刺': 'satire',
    '科幻': 'sci_fi', '短篇': 'short_story', '悲剧': 'tragedy',
}
SOURCE = "https://rrtrack.app/?category="


class GenreTable(HTMLParser):
    """Read the public page's serialized table as JSON, without executing a script."""
    def __init__(self, genre):
        super().__init__()
        self.genre = genre
        self.script = None
        self.tables = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and 'src' not in dict(attrs):
            self.script = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag != 'script' or self.script is None:
            return
        call = re.fullmatch(r'self\.__next_f\.push\((\[1,[\s\S]+\])\);?', ''.join(self.script).strip())
        self.script = None
        if not call:
            return
        chunk = json.loads(call[1])
        if len(chunk) != 2 or not isinstance(chunk[1], str):
            return
        for line in chunk[1].split('\n'):
            if '"rankSortCategory"' not in line or '"rankings"' not in line:
                continue
            record = re.fullmatch(r'[a-fA-F0-9]+:(\["\$",.*\])', line)
            if record:
                props = json.loads(record[1])
                if len(props) > 3 and isinstance(props[3], dict) and props[3].get('rankSortCategory') == self.genre:
                    self.tables.append(props[3].get('rankings'))


def _display(value):
    if isinstance(value, str) and not re.fullmatch(r'\$(?:[\da-fA-F]+|undefined)', value):
        return value.strip()
    return ''


def parse_genre(html, category):
    parser = GenreTable(RR_GENRES[category])
    try:
        parser.feed(html)
        parser.close()
        if len(parser.tables) != 1 or not isinstance(parser.tables[0], list) or len(parser.tables[0]) != 50:
            raise ValueError('incomplete table')
        stamps, ids, ranks, books = set(), set(), set(), []
        for row in parser.tables[0]:
            placement = row['categoryRanks'][parser.genre]
            rank = placement['rank']
            stamp = placement['observedAt'].removeprefix('$D')
            if placement['source'] != 'observed' or type(rank) is not int or not 1 <= rank <= 50 or rank in ranks:
                raise ValueError('invalid observed rank')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', stamp):
                raise ValueError('missing source date')
            datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            stamps.add(stamp)
            ranks.add(rank)
            story = row['story']
            ident, title = story['royalRoadId'], _display(story['title'])
            if type(ident) is not int or not 0 < ident <= 2**53 - 1 or ident in ids or not title:
                raise ValueError('invalid work identity')
            ids.add(ident)
            follows = row.get('followers')
            metrics = [{'name': '关注', 'value': follows, 'unit': '人'}] if type(follows) is int and follows >= 0 else []
            cover = _display(story.get('coverUrl'))
            books.append({'id': str(ident), 'title': title, 'author': _display(story.get('authorName')),
                          'summary': _display(story.get('description')), 'rank': rank, 'source_position': rank,
                          'rank_semantics': 'original_rank', 'url': 'https://www.royalroad.com/fiction/' + str(ident),
                          'cover_url': _cover_url(cover), 'tags': [category], 'metrics': metrics})
        if len(stamps) != 1:
            raise ValueError('different observation times')
        return sorted(books, key=lambda book: book['rank']), stamps.pop()
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise CollectorError('RR Tracker 未提供同一时间、本题材可核实的完整前 50 名。', 'source_changed') from exc


def collect_genre(options, progress=None):
    category = options['category']
    if options.get('period') not in (None, '', '当前'):
        raise CollectorError('题材新星榜只提供当前快照。')
    url = SOURCE + RR_GENRES[category]
    max_pages, limit = _page_options(options, 50)
    _notify(progress, '正在读取 RR Tracker 的' + category + '新星榜…')
    books, observed_at = parse_genre(_fetch(url), category)
    selected = books[:limit]
    fetched_at = datetime.now(timezone.utc).isoformat()
    provenance = {'source_id': 'rrtracker:genre', 'source_name': 'RR Tracker · 题材新星榜快照',
                  'source_url': url, 'source_date': observed_at, 'fetched_at': fetched_at,
                  'scope': category + ' · 新星榜前 50 名', 'sample_limit': 50, 'rank_semantics': 'observed_genre_rank'}
    result = {'platform': 'royalroad', 'mode': 'overseas', 'channel': '全部', 'category': category,
              'board': '新星榜', 'period': '当前', 'categories': ['全部', *RR_GENRES],
              'source_id': provenance['source_id'], 'source_url': url, 'data_date': observed_at, 'collected_at': fetched_at,
              'source_revision': RR_GENRES[category] + '/' + observed_at, 'provenance': provenance,
              'books': selected, 'warnings': [], 'complete': True, 'requested_count': limit,
              'available_count': len(selected), 'source_total': 50, 'pagination': {'pages_fetched': 1,
              'max_pages': max_pages, 'page_size': 50, 'stop_reason': 'requested_limit' if limit < 50 else 'source_end'}}
    _notify_snapshot(progress, result, False)
    return result
