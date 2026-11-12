# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Read the public list pages used by the domestic novel ranking adapters."""
import http.client
import time
from urllib import error, parse, request

from . import network
from .collectors import CollectorError, USER_AGENT, _notify


HOSTS = {'b.faloo.com', 'www.qimao.com', 'www.jjwxc.net', 'book.zongheng.com'}
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


def fetch_page(url, *, encoding='utf-8', timeout=15, headers=None):
    """URLs are constructed by the adapters from their registered board options."""
    network.check_authorization()
    parsed = parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in HOSTS:
        raise CollectorError('榜单地址不属于已接入的平台。', 'invalid_source')
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
            if size.isdigit() and int(size) > MAX_BYTES:
                raise CollectorError('榜单页面超过读取上限。', 'source_changed')
            body = bytearray()
            read = getattr(res, 'read1', res.read)
            while len(body) <= MAX_BYTES:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                chunk = read(min(65536, MAX_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise CollectorError('榜单页面超过读取上限。', 'source_changed')
        network.check_authorization()
        return body.decode(encoding)
    except error.HTTPError as exc:
        exc.close()
        state = 'forbidden' if exc.code in (401, 403) else ('not_found' if exc.code == 404 else 'network_error')
        raise CollectorError(f'平台官网返回 HTTP {exc.code}，请稍后重试。', state) from exc
    except UnicodeError as exc:
        raise CollectorError('榜单页面编码已变化，暂时无法读取。', 'source_changed') from exc
    except (OSError, error.URLError, http.client.HTTPException) as exc:
        raise CollectorError('暂时无法连接平台官网，请稍后重试。', 'network_error') from exc
