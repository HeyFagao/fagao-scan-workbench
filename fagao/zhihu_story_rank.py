# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Independently read Zhihu's anonymous story billboard metadata, once per collection.

The surrounding feed's pagination describes modules, not subsequent billboard
entries. Only the first billboard is used. LongSpace entries are omitted from
this short-story source while their positions remain visible as gaps.
"""
from datetime import datetime, timezone
import http.client
import json
import re
import time
from urllib import error, parse, request

from . import network
from .collectors import CollectorError, _Tree, _notify_snapshot


ORIGIN = 'https://api.zhihu.com'
PATH = '/km-vip-zhihu-web/vip_tab/svip_story'
BOARDS = {'热度榜': 'hot', '口碑榜': 'reputation', '推荐榜': 'recommend'}
CATEGORY_FILTERS = {
    '全部': '0', '言情': '1', '虐恋': '32', '甜宠': '31', '现实情感': '2',
    '悬疑': '3', '爽文': '45', '脑洞': '12', '大女主': '15', '校园': '41',
    '追妻火葬场': '36', '重生': '48', '家庭': '40', '复仇': '46', '恐怖': '4',
    '病娇': '18', '娱乐圈': '43', '沙雕文': '44', '治愈': '318',
}
CATEGORIES_BY_BOARD = {
    '热度榜': ['全部', '言情', '虐恋', '甜宠', '现实情感', '悬疑', '爽文', '脑洞', '大女主', '校园', '追妻火葬场', '重生'],
    '口碑榜': ['全部', '言情', '现实情感', '爽文', '大女主', '家庭', '复仇', '恐怖', '追妻火葬场', '脑洞', '悬疑', '病娇', '娱乐圈', '沙雕文', '重生'],
    '推荐榜': ['全部', '言情', '脑洞', '现实情感', '甜宠', '爽文', '治愈', '虐恋'],
}
PLATFORM = {
    'id': 'zhihu_story', 'name': '知乎盐选', 'mode': 'short', 'status': 'available',
    'message': '知乎公开故事榜首批中的短篇', 'source_url': ORIGIN + PATH,
    'channels': ['女频'], 'boards': list(BOARDS), 'categories': list(CATEGORY_FILTERS),
    'categories_by_board': CATEGORIES_BY_BOARD, 'max_limit': 100,
    'default_options': {'channel': '女频', 'board': '热度榜', 'category': '全部', 'limit': 100, 'max_pages': 1},
}
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
PUBLIC_AGENT = 'Fagao-Workbench/0.7 (public story ranking metadata)'
SHORT_LINK = re.compile(r'https://www\.zhihu\.com/market/paid_column/([1-9][0-9]{0,29})/section/([1-9][0-9]{0,29})\Z')


def source_url(board, category):
    if (not isinstance(board, str) or not isinstance(category, str)
            or board not in BOARDS or category not in CATEGORIES_BY_BOARD[board]):
        raise CollectorError('请选择知乎盐选当前榜型提供的题材。', 'invalid_options')
    return ORIGIN + PATH + '?' + parse.urlencode({
        'modules': 'billboard', 'billboard_type': BOARDS[board],
        'billboard_filter': CATEGORY_FILTERS[category], 'offset': 0, 'limit': 10,
    })


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise CollectorError('知乎故事榜入口发生跳转，请稍后确认来源。', 'source_changed')


def _fetch(board, category):
    network.check_authorization()
    url = source_url(board, category)
    headers = {'User-Agent': PUBLIC_AGENT, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with network.build_opener(_NoRedirect()).open(request.Request(url, headers=headers), timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200 or response.geturl() != url:
                raise CollectorError('知乎没有返回所选故事榜。', 'source_changed')
            size = response.headers.get('Content-Length', '')
            if size.isdigit() and int(size) > MAX_BYTES:
                raise CollectorError('知乎故事榜响应超过读取上限。', 'source_changed')
            content = bytearray()
            read = getattr(response, 'read1', response.read)
            while len(content) <= MAX_BYTES:
                network.check_authorization()
                if time.monotonic() >= deadline:
                    raise TimeoutError
                chunk = read(min(65536, MAX_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise CollectorError('知乎故事榜响应超过读取上限。', 'source_changed')
        network.check_authorization()
        return json.loads(content.decode('utf-8'))
    except error.HTTPError as exc:
        exc.close()
        state = 'forbidden' if exc.code in (401, 403) else 'rate_limited' if exc.code == 429 else 'not_found' if exc.code == 404 else 'network_error'
        raise CollectorError(f'知乎故事榜返回 HTTP {exc.code}，请稍后重试。', state) from exc
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CollectorError('知乎故事榜响应格式已变化。', 'source_changed') from exc
    except (OSError, error.URLError, http.client.HTTPException, network.NetworkError) as exc:
        raise CollectorError('暂时无法连接知乎故事榜，请稍后重试。', 'network_error') from exc


def _text(value, limit=4000):
    if not isinstance(value, str):
        return ''
    return _Tree(value[:20000]).root.text.strip()[:limit]


def _image(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        return ''
    try:
        url = parse.urlsplit(value)
        host = url.hostname or ''
        if (url.scheme == 'https' and (host == 'zhimg.com' or host.endswith('.zhimg.com'))
                and not url.username and not url.password and url.port is None):
            return value
    except ValueError:
        pass
    return ''


def _metrics(subtitle):
    label = _text(subtitle, 100)
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*([万亿]?)\s*(热度|赞)', label)
    if not match:
        return []
    return [{'name': '热度' if match[3] == '热度' else '点赞', 'value': match[1], 'unit': match[2]}]


def _billboard(payload, board, category):
    if not isinstance(payload, dict):
        raise CollectorError('知乎故事榜响应不是有效对象。', 'source_changed')
    if payload.get('error'):
        info = payload['error'] if isinstance(payload['error'], dict) else {}
        code = info.get('code')
        state = 'forbidden' if code in (401, 403) else 'rate_limited' if code == 429 else 'source_changed'
        raise CollectorError('知乎暂未允许读取故事榜，请稍后重试。', state)
    modules = payload.get('data')
    if not isinstance(modules, list):
        raise CollectorError('知乎故事榜模块格式已变化。', 'source_changed')
    matches = []
    for module in modules:
        if not isinstance(module, dict) or module.get('module_type') != 'billboard':
            continue
        body = module.get('module_data')
        body = body.get('data') if isinstance(body, dict) else None
        data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise CollectorError('知乎故事榜列表结构已变化。', 'source_changed')
        matches.extend(row for row in data if isinstance(row, dict) and isinstance(row.get('head'), dict)
                       and row['head'].get('type') == BOARDS[board])
    if len(matches) != 1:
        raise CollectorError('知乎响应没有唯一的所选故事榜。', 'source_changed')
    head = matches[0]['head']
    filters = head.get('filters')
    try:
        target = parse.urlsplit(head.get('button_url', ''))
        query = parse.parse_qs(target.query)
        scope_valid = (target.scheme == 'https' and target.netloc == 'story.zhihu.com'
                       and target.path == '/vip-ranking' and query.get('channel') == ['female']
                       and query.get('tab') == [BOARDS[board]]
                       and query.get('filter_key', ['0']) == [CATEGORY_FILTERS[category]])
        advertised = {f['key']: _text(f['name'], 100) for f in filters} if isinstance(filters, list) else {}
    except (TypeError, KeyError, ValueError):
        scope_valid, advertised = False, {}
    if head.get('title') != board or not scope_valid or advertised.get(CATEGORY_FILTERS[category]) != category:
        raise CollectorError('知乎故事榜没有确认所选榜型或题材。', 'source_changed')
    rows = matches[0].get('content_list')
    if not isinstance(rows, list):
        raise CollectorError('知乎故事榜条目格式已变化。', 'source_changed')
    return rows


def parse_result(payload, board, category, limit=100):
    """Use the billboard's verified scope, never the surrounding feed's total/next."""
    if type(limit) is not int or limit < 1:
        raise CollectorError('采集数量应为正整数。', 'invalid_options')
    limit = min(limit, 100)
    source = source_url(board, category)
    rows = _billboard(payload, board, category)
    books, seen = [], set()
    for position, row in enumerate(rows[:100], 1):
        if not isinstance(row, dict) or row.get('space_type') not in ('ShortSpace', 'LongSpace'):
            raise CollectorError('知乎故事篇幅类型无法核对。', 'source_changed')
        if row['space_type'] == 'LongSpace':
            continue
        match = SHORT_LINK.fullmatch(row.get('url', '')) if isinstance(row.get('url'), str) else None
        title = _text(row.get('title'), 500)
        if (not match or row.get('business_id') != match[2] or row.get('business_type') != 'PaidColumn'
                or not title or match[0] in seen):
            raise CollectorError('知乎短篇作品身份或来源链接无法核对。', 'source_changed')
        seen.add(match[0])
        labels = [_text(x, 100) for x in _text(row.get('label_text'), 500).split('·') if x.strip()]
        books.append({'id': match[1] + '/' + match[2], 'title': title, 'url': match[0],
                      'author': _text(row.get('producer_name'), 500), 'cover_url': _image(row.get('artwork')),
                      'summary': _text(row.get('description')), 'tags': labels, 'status': '',
                      'metrics': _metrics(row.get('subtitle')), 'rank': position, 'source_position': position,
                      'rank_semantics': 'source_list_position'})
    if not books:
        raise CollectorError('所选故事榜首批暂未提供可展示的短篇。', 'empty_source')
    books = books[:limit]
    return {'platform': PLATFORM['id'], 'mode': 'short', 'channel': '女频', 'category': category,
            'board': board, 'books': books, 'source_url': source, 'data_date': None,
            'source_id': 'zhihu:story-billboard:' + BOARDS[board],
            'rank_semantics': 'source_list_position', 'requested_count': limit,
            'available_count': len(books), 'source_rows_seen': min(len(rows), 100), 'source_total': None,
            'complete': len(books) >= limit, 'collected_at': datetime.now(timezone.utc).isoformat(),
            'warnings': ['仅收录官网故事榜首批中的短篇，位置沿用来源列表；来源未提供榜期和全榜总数。'],
            'pagination': {'pages_fetched': 1, 'max_pages': 1, 'source_total': None,
                           'stop_reason': 'requested_limit' if len(books) >= limit else 'source_first_batch'}}


def collect(options, progress=None):
    if not isinstance(options, dict):
        raise CollectorError('请选择知乎盐选的榜型和题材。', 'invalid_options')
    board = options.get('board', '热度榜')
    category = options.get('category', '全部')
    if options.get('channel', '女频') != '女频':
        raise CollectorError('知乎公开故事榜当前只接入已核验的女频范围。', 'invalid_options')
    limit = options.get('limit', 100)
    if type(limit) is not int or limit < 1:
        raise CollectorError('采集数量应为正整数。', 'invalid_options')
    limit = min(limit, 100)
    source_url(board, category)
    result = parse_result(_fetch(board, category), board, category, limit)
    network.check_authorization()
    _notify_snapshot(progress, result, False)
    return result
