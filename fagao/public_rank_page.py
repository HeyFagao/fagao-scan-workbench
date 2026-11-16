# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Read the public list pages used by the domestic novel ranking adapters."""
import http.client
import time
from urllib import error, parse, request

from . import network
from .collectors import CollectorError, USER_AGENT, _notify


HOSTS = {'b.faloo.com', 'wap.faloo.com', 'www.qimao.com', 'www.jjwxc.net', 'book.zongheng.com',
         'hongguoduanju.com', 'www.ishugui.com', 'www.shuqi.com'}
MAX_BYTES = 6 * 1024 * 1024


def pause_before_page(progress, message):
    """Pace sequential pagination and propagate task cancellation before the request."""
    _notify(progress, message)
    time.sleep(2)
    network.check_authorization()


class _RankRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise CollectorError('榜单地址发生跳转，请在官网确认当前入口。', 'source_changed')


def fetch_page(url, *, encoding='utf-8', timeout=15, headers=None, max_bytes=None, errors='strict'):
    """URLs are constructed by the adapters from their registered board options."""
    network.check_authorization()
    parsed = parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in HOSTS:
        raise CollectorError('榜单地址不属于已接入的平台。', 'invalid_source')
    limit = MAX_BYTES if max_bytes is None else max_bytes
    fields = {'User-Agent': USER_AGENT, 'Accept': 'text/html,application/json',
              'Accept-Language': 'zh-CN,zh;q=0.9', 'Accept-Encoding': 'identity'}
    if headers:
        fields.update(headers)
    try:
        deadline = time.monotonic() + timeout
        with network.build_opener(_RankRedirect()).open(request.Request(url, headers=fields), timeout=timeout) as res:
            if res.status != 200 or res.geturl() != url:
                raise CollectorError('官网未返回所选榜单。', 'source_changed')
            size = res.headers.get('Content-Length', '')
            if size.isdigit() and int(size) > limit:
                raise CollectorError('榜单页面超过读取上限。', 'source_changed')
            body = bytearray()
            read = getattr(res, 'read1', res.read)
            while len(body) <= limit:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                chunk = read(min(65536, limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > limit:
                raise CollectorError('榜单页面超过读取上限。', 'source_changed')
        network.check_authorization()
        return body.decode(encoding, errors=errors)
    except error.HTTPError as exc:
        exc.close()
        state = 'forbidden' if exc.code in (401, 403) else ('not_found' if exc.code == 404 else 'network_error')
        raise CollectorError(f'平台官网返回 HTTP {exc.code}，请稍后重试。', state) from exc
    except UnicodeError as exc:
        raise CollectorError('榜单页面编码已变化，暂时无法读取。', 'source_changed') from exc
    except (OSError, error.URLError, http.client.HTTPException) as exc:
        raise CollectorError('暂时无法连接平台官网，请稍后重试。', 'network_error') from exc
