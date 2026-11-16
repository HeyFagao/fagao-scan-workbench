# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Read the public short-story recommendation page through its normal browser UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit

from . import network
from .collectors import CollectorError, _notify, _notify_snapshot


ORIGIN = 'https://wen1701.github.io'
PAGE = ORIGIN + '/FanqieRankTracker/shorts.html'
TAGS = [
    '爽文', '打脸逆袭', '现代', '男生生活', '婚姻家庭', '女生生活', '虐文', '现实情感', '救赎',
    '玄幻仙侠', '霸总', '职场', '先虐后甜', '追妻火葬场', '现言甜宠', '励志', '系统', '校园',
    '豪门世家', '男生情感', '古代言情', '架空', '青春虐恋', '惊悚', '推理', '复仇', '暗恋',
    '金手指', '女配', '沙雕搞笑', '豪门总裁', '悬疑灵异', '纯爱', '宫斗宅斗', '婆媳', '虐心婚恋',
    '先婚后爱', '替身', '年代', '破镜重圆', '真假千金', '规则怪谈', '追妻', '直播', '病娇', '萌宝',
    '无限流', '科幻', '追夫火葬场', '男频衍生', '女性成长', '历史古代', '犯罪', '娱乐圈', '科幻末世',
    '游戏动漫', '万人迷', '末日求生', '悬疑', '搞笑轻松', '姐弟恋', '赘婿', '女频衍生', '同人',
    '养崽文', '团宠', '都市日常', '都市异能', '现实生活', '民国', '影视', '奇妙物语', '星际',
    '反转', '玄幻', '校霸', '狼人', '追夫', '升级流', '特种兵', '明星', '神医', '虐恋情深',
    '历史武侠', '古言虐恋', '外卖', '古言甜宠', '幻想言情', '奶爸', '历史', '都市脑洞', '武侠',
    '鉴宝', '热血', '现言复仇', '权谋', '基建', '十日衍生', '仕途',
]
PLATFORM = {
    'id': 'fanqie_recommend', 'name': '番茄短篇推荐', 'mode': 'short', 'status': 'available',
    'message': '按题材浏览公开推荐，保留来源展示顺序', 'source_url': PAGE,
    'channels': ['短篇'], 'categories': TAGS, 'boards': ['题材推荐'], 'max_limit': 100,
    'default_options': {'channel': '短篇', 'category': '男生生活', 'board': '题材推荐',
                        'limit': 100, 'max_pages': 1},
}
WORKER = Path(__file__).resolve().parents[1] / 'scripts' / 'recommendation-worker.mjs'
BOOK_LINK = re.compile(r'https://fanqienovel\.com/page/([1-9][0-9]{4,29})')


def _text(value, maximum=4000):
    return value.strip()[:maximum] if isinstance(value, str) else ''


def _cover(value):
    value = _text(value, 4096)
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and (host.endswith('.fqnovelpic.com') or host.endswith('.byteimg.com')
                 or host.endswith('.fqnovel.com'))):
        return value
    return ''


def _metric(name, label):
    label = _text(label, 80).replace(',', '')
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([万亿]?)', label)
    if name in ('浏览', '点赞', '评论') and match:
        return {'name': name, 'value': match[1], 'unit': match[2]}
    return None


def _reading_metrics(label):
    result = []
    for part in _text(label, 300).split('/'):
        part = part.strip().replace(',', '')
        for pattern, name, suffix in (
                (r'([0-9]+(?:\.[0-9]+)?)([万亿]?)人在读', '在读', '人'),
                (r'([0-9]+(?:\.[0-9]+)?)([万亿]?)字', '字数', '字'),
                (r'([0-9]+(?:\.[0-9]+)?)分钟读完', '阅读时长', '分钟')):
            match = re.fullmatch(pattern, part)
            if match:
                unit = (match[2] if len(match.groups()) > 1 else '') + suffix
                result.append({'name': name, 'value': match[1], 'unit': unit})
                break
    return result


def parse_result(payload, category, limit):
    """Validate only visible card fields, without reading browser session storage."""
    source = PAGE + '?' + urlencode({'tag': category})
    if (not isinstance(payload, dict) or payload.get('source_url') != source
            or payload.get('category') != category):
        raise CollectorError('短篇推荐页面没有确认所选题材。', 'source_changed')
    rows = payload.get('items')
    if not isinstance(rows, list) or len(rows) > 100:
        raise CollectorError('短篇推荐页面的列表格式发生变化。', 'source_changed')
    if not rows:
        raise CollectorError('这个题材暂时没有公开推荐，已保存的资料仍可查看。', 'empty_source')
    books, identities, positions = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise CollectorError('短篇推荐包含无法核对的作品。', 'source_changed')
        url = _text(row.get('url'), 1000)
        identity = BOOK_LINK.fullmatch(url)
        title = _text(row.get('title'), 500)
        raw_position = _text(row.get('position'), 4)
        if (not identity or not title or not re.fullmatch(r'0*[1-9][0-9]{0,2}', raw_position)
                or identity[1] in identities or int(raw_position) in positions):
            raise CollectorError('短篇推荐的作品链接、标题或展示顺序无法核对。', 'source_changed')
        position = int(raw_position)
        if positions and position <= max(positions):
            raise CollectorError('短篇推荐的展示顺序发生变化，请稍后重试。', 'source_changed')
        identities.add(identity[1])
        positions.add(position)
        topics = row.get('topics')
        if not isinstance(topics, list) or not all(isinstance(v, str) for v in topics):
            raise CollectorError('短篇推荐的题材标签格式发生变化。', 'source_changed')
        metrics = row.get('metrics')
        if not isinstance(metrics, list):
            raise CollectorError('短篇推荐的互动指标格式发生变化。', 'source_changed')
        observed = []
        for item in metrics:
            if isinstance(item, dict):
                metric = _metric(item.get('name'), item.get('value'))
                if metric:
                    observed.append(metric)
        observed.extend(_reading_metrics(row.get('reading_meta')))
        books.append({'id': identity[1], 'rank': position, 'source_position': position,
                      'rank_semantics': 'source_list_position', 'title': title, 'url': url,
                      'author': _text(row.get('author'), 500), 'summary': _text(row.get('summary')),
                      'cover_url': _cover(row.get('cover_url')), 'status': '',
                      'tags': list(dict.fromkeys([category, *[_text(v, 100) for v in topics if v.strip()]])),
                      'metrics': observed})
    books = books[:limit]
    return {'platform': PLATFORM['id'], 'mode': 'short', 'channel': '短篇', 'category': category,
            'board': '题材推荐', 'books': books, 'source_url': source,
            'source_id': 'public:wen1701:short-recommendation', 'data_date': None,
            'rank_semantics': 'source_list_position', 'requested_count': limit,
            'available_count': len(books), 'source_total': None, 'complete': True,
            'warnings': ['按来源当前推荐顺序展示；来源未提供榜期。'],
            'pagination': {'pages_fetched': 1, 'max_pages': 1,
                           'stop_reason': 'recommendation_feed', 'page_size': len(rows), 'source_total': None}}


def _stop_browser_process(process):
    """Stop only this worker's browser tree, allowing normal browser cleanup first."""
    if process.poll() is not None:
        return

    def windows_tree(force):
        command = str(Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32' / 'taskkill.exe')
        arguments = [command, '/PID', str(process.pid), '/T']
        if force:
            arguments.append('/F')
        try:
            subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        except (OSError, subprocess.TimeoutExpired):
            pass

    try:
        if sys.platform == 'win32':
            windows_tree(False)
        else:
            process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=8)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if sys.platform == 'win32':
            windows_tree(True)
        elif os.name == 'posix':
            # Popen creates a separate session for this one worker. Its Chrome
            # children share the group, independently of the user's own browser.
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _read_page(category, limit, data_dir, progress):
    from .recommendation_runtime import ensure_runtime

    runtime = ensure_runtime(data_dir or network._data_directory(), progress)
    env = network.sanitize_environment(os.environ)
    env.update(runtime.get('env') or {})
    env['FAGAO_PLAYWRIGHT_MODULE'] = str(runtime['module_path'])
    env['FAGAO_BROWSER_PROXY_JSON'] = json.dumps(runtime.get('proxy') or {})
    if runtime.get('executable_path'):
        env['FAGAO_BROWSER_EXECUTABLE'] = str(runtime['executable_path'])
    launch_options = {'start_new_session': True} if os.name == 'posix' else {}
    if sys.platform == 'win32':
        launch_options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen([str(runtime['node']), str(WORKER)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   encoding='utf-8', env=env, **launch_options)
    except OSError as exc:
        raise CollectorError('推荐浏览器未能启动，请让 WorkBuddy 检查浏览器环境。',
                             'browser_setup_required') from exc
    messages = queue.Queue()
    def read():
        for line in process.stdout:
            messages.put(line)
        messages.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.stdin.write(json.dumps({'category': category, 'limit': limit}, ensure_ascii=False) + '\n')
        process.stdin.flush()
        process.stdin.close()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            network.check_authorization()
            _notify(progress, '正在读取公开短篇推荐…')
            try:
                line = messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                raise CollectorError('推荐浏览器未返回完整内容，请稍后重试。', 'network_error')
            if len(line) > 2 * 1024 * 1024:
                raise CollectorError('短篇推荐页面超过读取上限。', 'source_changed')
            try:
                result = json.loads(line)
            except (ValueError, TypeError) as exc:
                raise CollectorError('推荐浏览器返回了无法识别的内容。', 'source_changed') from exc
            if not isinstance(result, dict):
                raise CollectorError('推荐浏览器返回了无法识别的内容。', 'source_changed')
            if result.get('event') == 'progress':
                continue
            if result.get('error'):
                raise CollectorError(_text(result['error'], 500), result.get('state', 'network_error'))
            return result
        raise CollectorError('推荐页面读取超时，请稍后重试。', 'network_error')
    finally:
        _stop_browser_process(process)
        reader.join(timeout=1)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def collect(options, progress=None, data_dir=None):
    category = options.get('category') or '男生生活'
    limit = options.get('limit', 100)
    if (options.get('channel', '短篇') != '短篇' or options.get('board', '题材推荐') != '题材推荐'
            or category not in TAGS or type(limit) is not int or limit < 1):
        raise CollectorError('请选择短篇推荐提供的题材。', 'invalid_options')
    limit = min(100, limit)
    network.check_authorization()
    payload = _read_page(category, limit, data_dir, progress)
    network.check_authorization()
    snapshot = parse_result(payload, category, limit)
    _notify_snapshot(progress, snapshot, False)
    return snapshot
