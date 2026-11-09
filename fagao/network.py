"""Application-owned HTTP routing, independent of an agent's shell session.

``system`` reads macOS System Configuration or Windows Internet Settings on
each request. Linux and absent manual system settings use direct HTTP(S).
Desktop-specific Linux settings and PAC scripts are not interpreted here.
Python's default ProxyHandler also consults environment-based bypass rules;
the small handler below deliberately uses only the selected application policy.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import errno
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from functools import wraps


POLICY = "isolated-system-proxy-v1"
_PROXY_ENV = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"}
_LOGGER = logging.getLogger("fagao.network")
_lock = threading.RLock()
_directory = None
_last_request = None
_authorization_check = ContextVar("fagao_authorization_check", default=None)


def check_authorization():
    """Check the account captured by the current request/task, if any."""
    check = _authorization_check.get()
    if check is not None:
        return check()
    return None


def has_authorization():
    """Whether this call belongs to an account-bound official app operation."""
    return _authorization_check.get() is not None


@contextmanager
def authorized_requests(check):
    """Bind an account check to a request/task without changing other threads."""
    if check is not None and not callable(check):
        raise TypeError("authorization check must be callable")
    token = _authorization_check.set(check)
    try:
        yield
    finally:
        _authorization_check.reset(token)


@contextmanager
def without_authorization():
    """Internal escape for the authorization transport itself, avoiding recursion."""
    with authorized_requests(None):
        yield


def authorized_callable(function):
    """Capture the caller's account when queueing work on another thread.

    The check happens when the work actually starts, so a pending task cannot
    start another request after logout, an account switch or permit expiry.
    """
    check = _authorization_check.get()

    @wraps(function)
    def run(*args, **kwargs):
        with authorized_requests(check):
            check_authorization()
            return function(*args, **kwargs)
    return run


def is_authorization_error(error):
    return str(getattr(error, "code", "")).startswith("membership_")


class _AuthorizationHandler(urllib.request.BaseHandler):
    # urllib calls request processors for redirects too. Checking only the
    # outer open() would permit a redirected request after the permit ended.
    handler_order = 90

    def http_request(self, request):
        check_authorization()
        return request

    https_request = http_request


class NetworkError(urllib.error.URLError):
    """A safe diagnostic; never includes raw proxy credentials or request URLs."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return self.message


def sanitize_environment(environment):
    return {key: value for key, value in environment.items() if key.lower() not in _PROXY_ENV}


def configure(data_dir):
    """Call once before starting workers, including direct CLI/server entrypoints."""
    global _directory
    with _lock:
        _directory = Path(data_dir).expanduser().resolve()
    for key in tuple(os.environ):
        if key.lower() in _PROXY_ENV:
            os.environ.pop(key, None)
    return status()


def _data_directory():
    with _lock:
        if _directory is not None:
            return _directory
    if os.environ.get("FAGAO_DATA_DIR"):
        return Path(os.environ["FAGAO_DATA_DIR"]).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FagaoWorkbench"
    return Path.home() / ".fagao-workbench"


def _proxy_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise NetworkError("代理地址无效，请配置 http://主机:端口。")
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or not parsed.port
                or not 1 <= parsed.port <= 65535):
            raise ValueError("invalid proxy")
        host = parsed.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
            host = "[" + host + "]"
        elif not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            raise ValueError("invalid host")
        return f"http://{host}:{parsed.port}"
    except (ValueError, TypeError):
        raise NetworkError("代理地址须为 http://主机:端口，不能包含账号、密码、路径或查询参数。") from None


def _validated_settings(value):
    if not isinstance(value, dict) or value.get("mode") not in ("system", "direct", "proxy"):
        raise NetworkError("网络配置无效，请将模式设为 system、direct 或 proxy。")
    mode = value["mode"]
    proxy = value.get("proxy_url")
    if mode == "proxy":
        proxy = _proxy_url(proxy)
    elif proxy not in (None, ""):
        raise NetworkError("只有 proxy 模式可以设置代理地址。")
    return {"version": 1, "mode": mode, "proxy_url": proxy if mode == "proxy" else None}


def _settings():
    path = _data_directory() / "network.json"
    try:
        if not path.exists():
            return {"version": 1, "mode": "system", "proxy_url": None}
        if path.stat().st_size > 8192:
            raise ValueError("oversize")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("version", 1) != 1:
            raise ValueError("version")
        return _validated_settings(value)
    except NetworkError:
        raise
    except (OSError, ValueError, AttributeError, TypeError):
        raise NetworkError("network.json 无法读取或格式无效，请让维护助手修复网络配置。") from None


def save_settings(data_dir, mode="system", proxy_url=None):
    settings = _validated_settings({"mode": mode, "proxy_url": proxy_url})
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".network-", suffix=".tmp", dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, directory / "network.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _safe_settings(settings)


def _endpoint(proxy):
    if not proxy:
        return None
    try:
        parsed = urllib.parse.urlsplit(proxy)
        host = parsed.hostname or "unknown"
        if ":" in host:
            host = "[" + host + "]"
        return f"{host}:{parsed.port or 80}"
    except ValueError:
        return "invalid"


def _safe_settings(settings):
    return {"policy": POLICY, "mode": settings["mode"], "configuration": "valid", "proxy": _endpoint(settings["proxy_url"])}


def status():
    try:
        result = _safe_settings(_settings())
    except NetworkError as exc:
        result = {"policy": POLICY, "mode": None, "configuration": "invalid", "message": str(exc), "proxy": None}
    with _lock:
        result["last_request"] = dict(_last_request) if _last_request else None
    return result


def _system_proxies():
    name = {"darwin": "getproxies_macosx_sysconf", "win32": "getproxies_registry"}.get(sys.platform)
    resolver = getattr(urllib.request, name, None) if name else None
    try:
        return dict(resolver()) if resolver else {}
    except (OSError, ValueError, TypeError):
        raise NetworkError("无法读取系统代理设置，请检查系统网络设置或为工作台选择 direct 模式。") from None


def _system_bypass(host):
    name = {"darwin": "proxy_bypass_macosx_sysconf", "win32": "proxy_bypass_registry"}.get(sys.platform)
    resolver = getattr(urllib.request, name, None) if name else None
    try:
        return bool(resolver(host)) if resolver else False
    except (OSError, ValueError, TypeError):
        raise NetworkError("无法读取系统代理排除设置，请检查系统网络设置。") from None


class _ControlledProxyHandler(urllib.request.ProxyHandler):
    def __init__(self, proxies, system=False, route_state=None):
        self.system = system
        self.route_state = route_state if route_state is not None else {}
        super().__init__({})
        self.proxies = proxies

    def http_open(self, request):
        proxy = self.proxies.get(request.type)
        if self.system and request.host and _system_bypass(request.host):
            proxy = None
        self.route_state.update(proxy=proxy, target=urllib.parse.urlsplit(request.full_url).hostname)
        if not proxy:
            return None
        parsed = urllib.parse.urlsplit(_proxy_url(proxy))
        # HTTP forward proxies also tunnel HTTPS through CONNECT. Request.set_proxy
        # retains the original HTTPS target; the standard HTTPSHandler verifies TLS.
        request.set_proxy(parsed.netloc, "http")
        return None

    https_open = http_open


def _clone_request(value):
    if isinstance(value, str):
        return urllib.request.Request(value)
    headers = {key: val for key, val in value.header_items() if key.lower() != "proxy-authorization"}
    return urllib.request.Request(value.full_url, data=value.data, headers=headers,
                                  origin_req_host=value.origin_req_host,
                                  unverifiable=value.unverifiable, method=value.get_method())


def _loopback(proxy):
    if not proxy:
        return False
    try:
        host = urllib.parse.urlsplit(proxy).hostname
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _error_info(error):
    if isinstance(error, (urllib.error.HTTPError, NetworkError)):
        return type(error).__name__, getattr(error, "errno", None)
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return type(reason).__name__, getattr(reason, "errno", None)


def _record(target, mode, route, proxy, outcome, error=None, fallback=False):
    global _last_request
    result = {"target": target, "mode": mode, "route": route, "proxy": _endpoint(proxy),
              "outcome": outcome, "fallback": fallback,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    if fallback:
        result["failed_proxy"], result["proxy"] = result["proxy"], None
    if error is not None:
        result["error_type"], result["errno"] = _error_info(error)
        if isinstance(error, urllib.error.HTTPError):
            result["http_status"] = error.code
        _LOGGER.warning("HTTP route failed target=%s mode=%s route=%s proxy=%s error=%s errno=%s status=%s",
                        target, mode, route, result["proxy"], result["error_type"], result["errno"], result.get("http_status"))
    elif fallback:
        _LOGGER.warning("HTTP route recovered target=%s mode=%s route=direct failed_proxy=%s", target, mode, result["failed_proxy"])
    with _lock:
        _last_request = result


class _Opener:
    def __init__(self, handlers):
        self.handlers = handlers

    def _open(self, request, timeout, proxies, system, route_state):
        check_authorization()
        # Each opener owns its handlers. A copied CookieProcessor keeps the same
        # CookieJar, preserving the normal anonymous website session across pages.
        handlers = [copy.copy(handler) for handler in self.handlers]
        opener = urllib.request.build_opener(_AuthorizationHandler(), _ControlledProxyHandler(proxies, system, route_state), *handlers)
        return opener.open(_clone_request(request), timeout=timeout)

    def open(self, request, timeout=20):
        original = _clone_request(request)
        parsed = urllib.parse.urlsplit(original.full_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise NetworkError("网络请求须使用有效的 HTTP 或 HTTPS 地址。")
        target = parsed.hostname
        try:
            settings = _settings()
            mode = settings["mode"]
            proxies = ({"http": settings["proxy_url"], "https": settings["proxy_url"]} if mode == "proxy"
                       else _system_proxies() if mode == "system" else {})
            # Keep both protocols so redirects obey the same policy; discard
            # ftp/all/no entries instead of letting urllib reinterpret them.
            proxies = {scheme: _proxy_url(value) for scheme, value in proxies.items()
                       if scheme in ("http", "https") and value}
            proxy = proxies.get(parsed.scheme)
            if proxy and mode == "system" and _system_bypass(original.host):
                proxy = None
        except NetworkError as exc:
            _record(target, "invalid", "unavailable", None, "failed", exc)
            raise
        route = "proxy" if proxy else "direct"
        route_state = {"proxy": proxy, "target": target}
        try:
            response = self._open(original, timeout, proxies, mode == "system", route_state)
        except urllib.error.HTTPError as exc:
            proxy, target = route_state["proxy"], route_state["target"]
            route = "proxy" if proxy else "direct"
            _record(target, mode, route, proxy, "http_error", exc)
            raise
        except (urllib.error.URLError, OSError) as exc:
            proxy = route_state["proxy"]
            route = "proxy" if proxy else "direct"
            target = route_state["target"]
            _record(target, mode, route, proxy, "failed", exc)
            _, error_number = _error_info(exc)
            may_retry = (mode == "system" and _loopback(proxy) and error_number in (errno.ECONNREFUSED, 10061)
                         and original.get_method() in ("GET", "HEAD"))
            if not may_retry:
                message = ("工作台配置的代理无法连接，请检查网络配置；已有结果仍保留。" if mode == "proxy" else
                           "平台网络请求失败，诊断中已记录连接方式和错误类型；已有结果仍保留。")
                raise NetworkError(message) from exc
            direct_state = {"proxy": None, "target": parsed.hostname}
            try:
                response = self._open(original, timeout, {}, False, direct_state)
            except urllib.error.HTTPError as direct_error:
                _record(direct_state["target"], mode, "direct", proxy, "http_error", direct_error, True)
                raise
            except (urllib.error.URLError, OSError) as direct_error:
                _record(direct_state["target"], mode, "direct", proxy, "failed", direct_error, True)
                raise NetworkError("系统本机代理连接被拒绝，直连重试也未成功；已有结果仍保留，请查看网络诊断。") from direct_error
            _record(direct_state["target"], mode, "direct", proxy, "connected", fallback=True)
            return response
        proxy, target = route_state["proxy"], route_state["target"]
        route = "proxy" if proxy else "direct"
        _record(target, mode, route, proxy, "connected")
        return response


def build_opener(*handlers):
    return _Opener(handlers)
