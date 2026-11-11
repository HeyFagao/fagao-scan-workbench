# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Public opening chapters, fixed to positions 1–10 of the official directory.

Only anonymous official pages are used. A paid, challenged or unreadable page
stops the request without replacing its position with a later chapter. These
adapters are independent of the ranking-only DSH plugin in the supplied bundle.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import network
from .fanqie_content import _content_integrity

PLATFORMS = {"qidian": ("起点", "https://m.qidian.com"),
             "ciweimao": ("刺猬猫", "https://www.ciweimao.com")}
DOWNLOAD_LIMIT = 10
REQUEST_INTERVAL = 0.5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARACTERS = 1_000_000
CACHE_TTL = 30 * 60
CACHE_MAX_FILES = 32
CACHE_MAX_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"[0-9]{5,30}\Z")
_request_lock = threading.Lock()
_cache_lock = threading.Lock()
_last_request = 0.0


def _error(message, state="unavailable"):
    from .collectors import CollectorError
    return CollectorError(message, state)


def _identity(value):
    value = str(value)
    if not _ID.fullmatch(value):
        raise _error("作品或章节编号无效。", "invalid_identity")
    return value


def validate_range(platform, max_chapters=3, start_chapter=1):
    if platform not in PLATFORMS:
        raise _error("此平台尚未接入开篇正文读取。", "unsupported_text_source")
    if (isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or start_chapter != 1
            or isinstance(max_chapters, bool) or not isinstance(max_chapters, int)
            or not 1 <= max_chapters <= DOWNLOAD_LIMIT):
        raise _error("起点、刺猬猫仅能从目录第 1 项开始，读取前 1 到 10 章；不支持全文或其他起始位置。", "invalid_range")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and int(value) > 0:
        return int(value)
    return None


def _active(value):
    return value not in (None, False, 0, "", "0", "false")


def _checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    paths = {
        "m.qidian.com": r"/(?:book/[0-9]{5,30}/catalog|chapter/[0-9]{5,30}/[0-9]{5,30})/?",
        "www.ciweimao.com": r"/(?:chapter-list/[0-9]{5,30}/book_detail|chapter/[0-9]{5,30}|chapter/(?:ajax_get_session_code|get_book_chapter_detail_info))/?",
    }
    if (parsed.scheme != "https" or parsed.netloc not in paths or parsed.query or parsed.fragment
            or not re.fullmatch(paths[parsed.netloc], parsed.path)):
        raise _error("正文来源地址不符合官方作品路径，已停止读取。", "invalid_source")
    return parsed


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        original, target = _checked_url(request.full_url), _checked_url(newurl)
        if request.get_method() != "GET" or (original.netloc, original.path.rstrip("/")) != (target.netloc, target.path.rstrip("/")):
            raise _error("原站跳转到其他页面，未读取正文；请在原站确认作品状态。", "forbidden")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _fetch(url, *, opener=None, form=None, referer=None):
    """Bound anonymous reads to the exact official resource, including redirects."""
    global _last_request
    from .collectors import MOBILE_AGENT, USER_AGENT
    expected = _checked_url(url)
    payload = None
    if form is not None:
        allowed = {"/chapter/ajax_get_session_code": {"chapter_id"},
                   "/chapter/get_book_chapter_detail_info": {"chapter_id", "chapter_access_key"}}
        if (expected.netloc != "www.ciweimao.com" or expected.path not in allowed
                or not isinstance(form, dict) or set(form) != allowed[expected.path]):
            raise _error("公开阅读请求格式不正确，已停止。", "invalid_source")
        _identity(form.get("chapter_id"))
        payload = urllib.parse.urlencode(form).encode("ascii")
    network.check_authorization()
    with _request_lock:
        wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        network.check_authorization()
        _last_request = time.monotonic()
        request = urllib.request.Request(url, data=payload, headers={
            "User-Agent": MOBILE_AGENT if expected.netloc == "m.qidian.com" else USER_AGENT,
            "Accept": "text/html,application/json;q=0.9", "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        })
        if form is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
            request.add_header("X-Requested-With", "XMLHttpRequest")
        if referer is not None:
            referring = _checked_url(referer)
            if referring.netloc != expected.netloc or not re.fullmatch(r"/chapter/[0-9]{5,30}/?", referring.path):
                raise _error("公开阅读来源页不正确，已停止。", "invalid_source")
            request.add_header("Referer", referer)
        try:
            with (opener or network.build_opener(_OfficialRedirect())).open(request, timeout=20) as response:
                actual = _checked_url(response.geturl())
                if (expected.netloc, expected.path.rstrip("/")) != (actual.netloc, actual.path.rstrip("/")):
                    raise _error("原站返回了其他作品页面，未读取正文。", "invalid_identity")
                if response.status != 200:
                    raise _error(f"原站返回 HTTP {response.status}，本次读取已停止。", "forbidden")
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise _error("原站响应过大，本次读取已停止。")
                network.check_authorization()
                return data.decode("utf-8")
        except urllib.error.HTTPError as exc:
            # HTTPError owns the error response stream even though open()
            # raised before entering the response context manager above.
            try:
                exc.close()
            except OSError:
                pass
            raise _error(f"原站返回 HTTP {exc.code}，请稍后重试或打开原站查看。",
                         "forbidden" if exc.code in (401, 403, 429) else "network_error") from exc
        except (urllib.error.URLError, network.NetworkError, TimeoutError, OSError, UnicodeError) as exc:
            raise _error("暂时无法读取原站，已取得的内容会保留，请稍后重试。", "network_error") from exc


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, name):
        return name in self.attrs.get("class", "").split()

    def nodes(self, tag=None, id=None, cls=None):
        for child in self.children:
            if isinstance(child, _Node):
                if ((tag is None or child.tag == tag) and (id is None or child.attrs.get("id") == id)
                        and (cls is None or child.has(cls))):
                    yield child
                yield from child.nodes(tag, id, cls)

    def text(self):
        return "".join(child.text() if isinstance(child, _Node) else child for child in self.children)


class _Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root, self.stack = _Node(), []
        self.stack.append(self.root)
        self.count = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        # Qidian publishes consecutive opening <p> tags, without closing tags.
        if tag == "p" and self.stack[-1].tag == "p":
            self.stack.pop()
        self.count += 1
        if len(self.stack) > 200 or self.count > 150000:
            raise _error("原站页面结构异常，未读取正文。")
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _one(nodes, message):
    nodes = list(nodes)
    if len(nodes) != 1:
        raise _error(message)
    return nodes[0]


def _plain_content(html):
    """Keep readable body paragraphs; omit UI, scripts, ads and hidden payloads."""
    if not isinstance(html, (str, _Node)):
        raise _error("原站没有提供可直接读取的正文，请打开原站查看或导入原文。", "content_unavailable")
    root = html if isinstance(html, _Node) else _Document(html).root
    parts = []

    def walk(node):
        if (node.tag in {"script", "style", "noscript", "svg", "button", "iframe", "form"}
                or node.has("watermark") or node.has("chapter-say") or node.has("author-say") or node.has("J_Num")
                or node.attrs.get("aria-hidden") == "true" or "hidden" in node.attrs
                or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.attrs.get("style", ""), re.I)):
            return
        if node.tag in {"p", "div", "br", "li"}:
            parts.append("\n\n")
        for child in node.children:
            if isinstance(child, _Node):
                walk(child)
            else:
                parts.append(child)
        if node.tag in {"p", "div", "li"}:
            parts.append("\n\n")

    walk(root)
    text = "\n\n".join(line.strip() for line in "".join(parts).splitlines() if line.strip())
    if not text or len(text) > MAX_TEXT_CHARACTERS:
        raise _error("原站没有提供可直接读取的正文，请打开原站查看或导入原文。", "content_unavailable")
    if any(0xE000 <= ord(char) <= 0xF8FF or 0xF0000 <= ord(char) <= 0x10FFFD or char == "\ufffd" for char in text):
        raise _error("原站正文含暂不支持的字体编码，未当作有效正文保存。", "content_unavailable")
    if len(text) < 500 and re.search(r"(?:请先登录|登录后(?:阅读|查看)|请(?:完成|通过).*验证|访问过于频繁|本章为VIP|请先订阅|购买本章)", text, re.I):
        raise _error("原站要求登录、订阅或验证，本次读取已停止。", "forbidden")
    return text


def _qidian_data(html, book_id, chapter_id=None):
    root = _Document(html).root
    state = _one(root.nodes("script", id="vite-plugin-ssr_pageContext"), "起点页面没有提供公开数据，可能需要验证，请稍后重试。")
    try:
        context = json.loads(state.text())["pageContext"]
        data = context["pageProps"]["pageData"]
        params = context["routeParams"]
        expected_path = f"/chapter/{book_id}/{chapter_id}/" if chapter_id else f"/book/{book_id}/catalog/"
        if (context.get("hostname") != "m.qidian.com" or context.get("urlPathname") != expected_path
                or str(params.get("bookId")) != book_id
                or (chapter_id and str(params.get("chapterId")) != chapter_id)):
            raise _error("起点页面作品或章节身份不一致，未采用。", "invalid_identity")
        if not isinstance(data, dict):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("起点公开页面格式发生变化，未取得正文。") from exc
    risk = data.get("riskInfo", {})
    if not isinstance(risk, dict) or any(_active(value) for value in risk.values()):
        raise _error("起点要求验证或限制访问，本次读取已停止。", "forbidden")
    if isinstance(data.get("riskbe"), dict) and _active(data["riskbe"].get("be")):
        raise _error("起点要求验证或限制访问，本次读取已停止。", "forbidden")
    return data


def _qidian_catalog(book_id, html):
    data = _qidian_data(html, book_id)
    if str(data.get("bookId")) != book_id:
        raise _error("起点目录作品身份不一致，未采用。", "invalid_identity")
    volumes = data.get("vs")
    if not isinstance(volumes, list) or not volumes:
        raise _error("起点没有提供可用的公开目录。")
    rows, seen = [], set()
    for volume in volumes:
        if not isinstance(volume, dict) or not isinstance(volume.get("cs"), list):
            raise _error("起点目录结构不完整，未采用。")
        for entry in volume["cs"]:
            if not isinstance(entry, dict):
                raise _error("起点目录章节格式无效。")
            chapter_id = _identity(entry.get("id"))
            if chapter_id in seen:
                raise _error("起点目录含重复章节，未采用。")
            seen.add(chapter_id)
            if len(seen) > 50000:
                raise _error("原站目录过大，已停止读取。")
            if len(rows) < DOWNLOAD_LIMIT:
                rows.append({"id": chapter_id, "item_id": chapter_id, "index": len(seen),
                             "title": str(entry.get("cN") or "未命名章节").strip(),
                             "volume": str(volume.get("vN") or ""),
                             "url": f"https://m.qidian.com/chapter/{book_id}/{chapter_id}/",
                             "locked": str(entry.get("sS")) != "1", "declared_word_count": _positive_int(entry.get("cnt"))})
    total = _positive_int(data.get("chapterTotalCnt"))
    if not rows or total != len(seen):
        raise _error("起点目录数量不一致，未采用不完整目录。")
    return {"title": str(data.get("bookName") or ""), "chapters": rows,
            "catalog_chapters": total, "catalog_complete": True, "status": ""}


def _ciweimao_identity(root, book_id, chapter_id=None):
    matches = []
    for script in root.nodes("script"):
        matches.extend(re.findall(r"HB\.book\s*=\s*\{([^{}]+)\}", script.text()))
    if len(matches) != 1:
        raise _error("刺猬猫没有提供可核对的作品身份，未采用。", "invalid_identity")
    values = matches[0]
    book = re.search(r"\bbook_id\s*:\s*['\"]?([0-9]+)['\"]?(?=\s*[,}])", values + "}")
    chapter = re.search(r"\bchapter_id\s*:\s*['\"]?([0-9]+)['\"]?(?=\s*[,}])", values + "}")
    if not book or book.group(1) != book_id or (chapter_id and (not chapter or chapter.group(1) != chapter_id)):
        raise _error("刺猬猫作品或章节身份不一致，未采用。", "invalid_identity")


def _ciweimao_catalog(book_id, html):
    root = _Document(html).root
    _ciweimao_identity(root, book_id)
    container = _one(root.nodes(cls="book-catalog"), "刺猬猫没有提供可用的公开目录。")
    rows, seen = [], set()
    for volume in container.nodes(cls="book-chapter-box"):
        headings = list(volume.nodes("h4", cls="sub-tit"))
        volume_name = headings[0].text().strip() if headings else ""
        for listing in volume.nodes("ul", cls="book-chapter-list"):
            for item in (child for child in listing.children if isinstance(child, _Node) and child.tag == "li"):
                links = list(item.nodes("a"))
                if len(links) != 1:
                    raise _error("刺猬猫目录章节链接不完整，未采用。")
                link = links[0]
                url = urllib.parse.urljoin("https://www.ciweimao.com", link.attrs.get("href", ""))
                parsed = _checked_url(url)
                match = re.fullmatch(r"/chapter/([0-9]{5,30})/?", parsed.path)
                if parsed.netloc != "www.ciweimao.com" or not match or match.group(1) in seen:
                    raise _error("刺猬猫目录含无效或重复章节，未采用。")
                chapter_id = match.group(1)
                seen.add(chapter_id)
                if len(seen) > 50000:
                    raise _error("原站目录过大，已停止读取。")
                if len(rows) < DOWNLOAD_LIMIT:
                    rows.append({"id": chapter_id, "item_id": chapter_id, "index": len(seen),
                                 "title": link.text().strip() or "未命名章节", "volume": volume_name,
                                 "url": f"https://www.ciweimao.com/chapter/{chapter_id}",
                                 "locked": bool(list(item.nodes(cls="icon-lock"))), "declared_word_count": None})
    if not rows:
        raise _error("刺猬猫目录没有公开章节。")
    title = next(container.nodes("h3"), None)
    return {"title": title.text().strip() if title else "", "chapters": rows,
            "catalog_chapters": len(seen), "catalog_complete": True, "status": ""}


def _cache_path(data_dir, platform, book_id):
    return Path(data_dir) / "cache" / "opening-content" / f"{platform}-{book_id}.json" if data_dir else None


def _cache_read(data_dir, platform, book_id):
    path = _cache_path(data_dir, platform, book_id)
    if path is None:
        return None
    try:
        if path.stat().st_size > 256 * 1024 or path.is_symlink():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        result = record["value"]
        if 0 <= time.time() - record["saved_at"] < CACHE_TTL and record.get("version") == 1:
            return _validate_catalog(result, platform, book_id)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError):
        pass
    return None


def _cache_write(data_dir, platform, book_id, value):
    path = _cache_path(data_dir, platform, book_id)
    if path is None:
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with _cache_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "saved_at": time.time(), "value": value}, stream, ensure_ascii=False)
            os.replace(temporary, path)
            # Only this adapter's disposable directory records are pruned.
            records = sorted((p for p in path.parent.glob("*.json") if p.is_file() and not p.is_symlink()),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            total = 0
            for index, record in enumerate(records):
                total += record.stat().st_size
                if index >= CACHE_MAX_FILES or total > CACHE_MAX_BYTES or time.time() - record.stat().st_mtime > CACHE_TTL:
                    record.unlink(missing_ok=True)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_catalog(value, platform, book_id):
    if (not isinstance(value, dict) or value.get("platform") != platform or value.get("book_id") != book_id
            or value.get("download_limit") != DOWNLOAD_LIMIT or value.get("download_start") != 1
            or value.get("catalog_complete") is not True):
        raise _error("目录作品身份或开篇范围不一致，未采用。", "invalid_identity")
    total, rows = value.get("catalog_chapters"), value.get("chapters")
    if (isinstance(total, bool) or not isinstance(total, int) or not 1 <= total <= 50000
            or not isinstance(rows, list) or len(rows) != min(total, DOWNLOAD_LIMIT)):
        raise _error("公开目录范围不完整，未采用。")
    ids = set()
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict) or item.get("index") != index or isinstance(item.get("index"), bool):
            raise _error("公开目录顺序不完整，未采用。")
        chapter_id = _identity(item.get("item_id"))
        expected = (f"https://m.qidian.com/chapter/{book_id}/{chapter_id}/" if platform == "qidian"
                    else f"https://www.ciweimao.com/chapter/{chapter_id}")
        if item.get("url") != expected or item.get("id") != chapter_id or chapter_id in ids or not isinstance(item.get("locked"), bool):
            raise _error("公开目录章节身份不一致，未采用。", "invalid_identity")
        ids.add(chapter_id)
    return value


def catalog(platform, book_id, data_dir=None, *, refresh=False):
    validate_range(platform)
    book_id = _identity(book_id)
    network.check_authorization()
    cached = None if refresh else _cache_read(data_dir, platform, book_id)
    if cached:
        return cached
    source = (f"https://m.qidian.com/book/{book_id}/catalog/" if platform == "qidian"
              else f"https://www.ciweimao.com/chapter-list/{book_id}/book_detail")
    html = _fetch(source)
    result = (_qidian_catalog if platform == "qidian" else _ciweimao_catalog)(book_id, html)
    result.update(platform=platform, book_id=book_id, source=source, collected_at=_now(),
                  download_limit=DOWNLOAD_LIMIT, download_start=1, integrity_version=1)
    _validate_catalog(result, platform, book_id)
    _cache_write(data_dir, platform, book_id, result)
    return result


def _ciweimao_json(url, opener, form, referer, book_id, chapter_id):
    try:
        response = json.loads(_fetch(url, opener=opener, form=form, referer=referer))
    except (ValueError, TypeError) as exc:
        raise _error("刺猬猫公开阅读接口未返回有效数据，请稍后重试。", "content_unavailable") from exc
    if not isinstance(response, dict) or response.get("code") != 100000:
        raise _error("刺猬猫暂未允许读取此章；可能需要登录、订阅或验证，请打开原站查看。", "forbidden")
    if any(_active(response.get(key)) for key in ("needPay", "isChapterLock", "is_paid", "vipStatus", "price", "need_login", "captcha", "captcha_url")):
        raise _error("此章需要原站阅读权限或验证，已停止读取。", "forbidden")
    for key, expected in (("book_id", book_id), ("bookId", book_id), ("chapter_id", chapter_id), ("chapterId", chapter_id)):
        if key in response and str(response[key]) != expected:
            raise _error("刺猬猫公开正文的作品或章节身份不一致，未采用。", "invalid_identity")
    return response


def _ciweimao_decode(content, keys, access_key):
    """Normal anonymous reader transport, matching the official two AES layers.

    The server supplies all keys after it grants this public chapter request.
    The established AES implementation is bundled separately; no downloaded
    JavaScript, guessed keys, login session or subscription request is used.
    """
    from .reader_crypto import decrypt_cbc
    if (not isinstance(content, str) or not 1 <= len(content) <= 2 * MAX_TEXT_CHARACTERS
            or not isinstance(keys, list) or not 1 <= len(keys) <= 64
            or not isinstance(access_key, str) or not 1 <= len(access_key) <= 256 or not access_key.isascii()):
        raise _error("刺猬猫公开正文格式发生变化，未保存无效内容。", "content_unavailable")
    try:
        # The official reader selects last-character first, then first-character.
        selected = [keys[ord(access_key[-1]) % len(keys)], keys[ord(access_key[0]) % len(keys)]]
        current = content.encode("ascii")
        for encoded_key in selected:
            if not isinstance(encoded_key, str) or len(encoded_key) > 128:
                raise ValueError()
            key = base64.b64decode(encoded_key, validate=True)
            packet = base64.b64decode(current, validate=True)
            if len(packet) <= 16:
                raise ValueError()
            current = decrypt_cbc(packet[16:], key, packet[:16])
        return current.decode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise _error("刺猬猫公开正文未能正确还原，已停止；已有内容会保留。", "content_unavailable") from exc


def _ciweimao_public_body(root, body, opener, item, book_id):
    # A page which already contains ordinary public text needs no extra request.
    try:
        return _plain_content(body)
    except Exception as exc:
        from .collectors import CollectorError
        if not isinstance(exc, CollectorError) or exc.state != "content_unavailable":
            raise
    scripts = "\n".join(node.text() for node in root.nodes("script"))
    use_image = re.findall(r"HB\.config\.bookread\.useImage\s*=\s*([0-9]+)", scripts)
    readers = re.findall(r"HB\.userinfo\s*=\s*\{[^{}]*\breader_id\s*:\s*([0-9]+)", scripts)
    if use_image != ["0"] or readers != ["0"]:
        raise _error("此章暂未提供可读取的匿名公开正文，请打开原站查看或导入原文。", "content_unavailable")
    chapter_id, referer = item["item_id"], item["url"]
    session = _ciweimao_json("https://www.ciweimao.com/chapter/ajax_get_session_code", opener,
                            {"chapter_id": chapter_id}, referer, book_id, chapter_id)
    access_key = session.get("chapter_access_key")
    if not isinstance(access_key, str) or not 1 <= len(access_key) <= 256 or not access_key.isascii():
        raise _error("刺猬猫没有提供有效的匿名阅读凭据，本次读取已停止。", "forbidden")
    response = _ciweimao_json("https://www.ciweimao.com/chapter/get_book_chapter_detail_info", opener,
                             {"chapter_id": chapter_id, "chapter_access_key": access_key}, referer, book_id, chapter_id)
    return _plain_content(_ciweimao_decode(response.get("chapter_content"), response.get("encryt_keys"), access_key))


def _chapter(platform, book_id, item):
    if item["locked"]:
        raise _error("此章需要在原站订阅或使用其指定阅读方式，已停止读取。", "forbidden")
    if platform == "qidian":
        html = _fetch(item["url"])
        data = _qidian_data(html, book_id, item["item_id"])
        chapter, book = data.get("chapterInfo"), data.get("bookInfo")
        if (not isinstance(chapter, dict) or not isinstance(book, dict)
                or str(book.get("bookId")) != book_id or str(chapter.get("chapterId")) != item["item_id"]):
            raise _error("起点正文作品或章节身份不一致，未采用。", "invalid_identity")
        if (str(chapter.get("vipStatus")) != "0" or str(chapter.get("price")) != "0"
                or any(_active(chapter.get(key)) for key in ("randomFont", "fixedFontTtf", "fixedFontWoff2", "css", "fEnS", "eFW"))
                or (isinstance(chapter.get("riskbe"), dict) and _active(chapter["riskbe"].get("be")))):
            raise _error("此章不是可直接读取的公开正文，已停止读取。", "forbidden")
        text = _plain_content(chapter.get("content"))
        declared = _positive_int(chapter.get("wordsCount")) or item.get("declared_word_count")
        title = str(chapter.get("chapterName") or item["title"]).strip()
    else:
        # A new in-memory anonymous visitor jar is scoped to this one chapter.
        # It is never populated from the browser, persisted or shared with users.
        opener = network.build_opener(_OfficialRedirect(), urllib.request.HTTPCookieProcessor(CookieJar()))
        html = _fetch(item["url"], opener=opener)
        root = _Document(html).root
        _ciweimao_identity(root, book_id, item["item_id"])
        container = _one(root.nodes(id="J_BookCnt"), "刺猬猫未提供可读取的章节内容。")
        if container.attrs.get("data-id") != item["item_id"]:
            raise _error("刺猬猫章节身份不一致，未采用。", "invalid_identity")
        body = _one(container.nodes(id="J_BookRead"), "刺猬猫未提供可直接读取的正文，请打开原站查看或导入原文。")
        # Reusing the structural walker avoids pulling navigation or comments
        # into a body that the official server left empty.
        text = _ciweimao_public_body(root, body, opener, item, book_id)
        heading = next(container.nodes("h1", cls="chapter"), None)
        title = heading.text().strip() if heading else item["title"]
        header = next(container.nodes(cls="read-hd"), None)
        word_count = re.search(r"字数\s*[：:]\s*([0-9]+)", header.text()) if header else None
        declared = int(word_count.group(1)) if word_count else None
    return {"title": title, "text": text, "integrity": _content_integrity(text, declared)}


def fetch_book_text(platform, book_id, data_dir=None, max_chapters=3, start_chapter=1, progress=None):
    """Read a prefix of the official opening window; never continue past it."""
    validate_range(platform, max_chapters, start_chapter)
    book_id = _identity(book_id)
    cancelled, interruption = False, None

    def notify(message):
        nonlocal cancelled, interruption
        try:
            network.check_authorization()
            if progress:
                progress(message)
        except Exception as exc:
            if network.is_authorization_error(exc):
                interruption = exc
                return False
            if getattr(exc, "code", None) == "cancelled" or getattr(exc, "state", None) == "cancelled":
                cancelled = True
                return False
            raise
        return True

    if not notify("正在读取这本书的公开目录…"):
        if interruption is not None:
            raise interruption
        return {"platform": platform, "book_id": book_id, "text": "", "chapters": [], "cancelled": True,
                "complete": False, "coverage_complete": False}
    # Display caches are not authority for chapter positions: re-read the
    # official directory before every text request, even with a valid cache.
    info = _validate_catalog(catalog(platform, book_id, data_dir, refresh=True), platform, book_id)
    wanted = info["chapters"][:max_chapters]
    chapters, texts, missing = [], [], []
    for item in wanted:
        if not notify(f"正在读取第 {item['index']} 章，已取得 {len(chapters)}/{len(wanted)} 章…"):
            break
        try:
            result = _chapter(platform, book_id, item)
            piece = result["title"] + "\n\n" + result["text"]
            if sum(map(len, texts)) + len(piece) > MAX_TEXT_CHARACTERS:
                raise _error("本次开篇正文达到大小上限，已停止读取。")
            texts.append(piece)
            chapters.append({"id": item["item_id"], "item_id": item["item_id"], "index": item["index"],
                             "title": result["title"], "url": item["url"], "characters": len(result["text"]),
                             "integrity": result["integrity"]})
            # A source-count shortfall marks this chapter as a fragment, but
            # does not prevent reading other public chapters in this same
            # fixed window. Permission, identity and network failures do stop.
        except Exception as exc:
            if network.is_authorization_error(exc):
                interruption = exc
                break
            from .collectors import CollectorError
            if not isinstance(exc, CollectorError):
                raise
            missing.append({"index": item["index"], "id": item["item_id"], "chapter": item["title"], "reason": str(exc)})
            break
    if not texts and interruption is not None:
        raise interruption
    if not texts and not cancelled:
        raise _error(missing[0]["reason"] if missing else "当前没有取得可读正文。", "content_unavailable")
    problem = next((c["integrity"] for c in chapters if c["integrity"]["status"] == "partial"), None)
    if problem is None:
        problem = next((c["integrity"] for c in chapters if c["integrity"]["status"] != "verified"), None)
    integrity = {"version": 1, "status": problem["status"] if problem else ("verified" if chapters else "unverified"),
                 "declared_book_word_count": None,
                 "actual_characters": sum(c["integrity"]["actual_characters"] for c in chapters),
                 "reason": problem["reason"] if problem else "已逐章核对公开正文身份与来源字数；仅包含开篇"}
    if not cancelled and interruption is None:
        notify(f"已取得 {len(chapters)}/{len(wanted)} 章，已保留实际读取范围。")
    coverage = len(chapters) == len(wanted) and not missing and not cancelled and interruption is None and integrity["status"] == "verified"
    scope = f"开篇公开目录第 1—{chapters[-1]['index']} 项，共 {len(chapters)} 章" if chapters else "尚未取得正文"
    if integrity["status"] != "verified":
        scope += "；" + integrity["reason"]
    if missing or cancelled or interruption:
        scope += "；读取已停止，已取得内容保留"
    return {"platform": platform, "book_id": book_id, "title": info["title"], "text": "\n\n".join(texts),
            "scope": scope, "complete": False, "completeness_basis": "仅提供目录前 10 项以内的公开开篇，不代表整本小说",
            "integrity": integrity, "source": info["source"], "chapters": chapters, "missing": missing,
            "catalog_chapters": info["catalog_chapters"], "catalog_complete": info["catalog_complete"],
            "requested_chapters": len(wanted), "requested_start": 1, "coverage_complete": coverage,
            "cancelled": cancelled, "collected_at": _now(), "status": info["status"],
            "download_limit": DOWNLOAD_LIMIT, "download_start": 1,
            **({"interrupted": True, "interrupted_reason": interruption.code,
                "interrupted_message": str(getattr(interruption, "message", "会员状态变化，读取已暂停。"))[:1200]}
               if interruption is not None else {})}
