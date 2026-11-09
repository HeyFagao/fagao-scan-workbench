"""Public catalogue collectors and isolated, local browser sessions.

Schemas/endpoints informed by Oh Story 7e26aa0708984ce81a41cd77ebfbbdf467c500b0
(MIT; see THIRD_PARTY_NOTICES.md). No search-result or sample-data fallback.
"""
from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
FANQIE = "https://fanqienovel.com"
HEIYAN = "https://manage.zhangwenpindu.cn/books/booklist"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
MOBILE_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"


class CollectorError(RuntimeError):
    def __init__(self, message, state="unavailable"):
        super().__init__(message)
        self.state = state


def _fetch(url, mobile=False):
    request = urllib.request.Request(url, headers={
        "User-Agent": MOBILE_AGENT if mobile else USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise CollectorError("平台响应过大，已停止读取。")
            return data.decode("utf-8")
    except urllib.error.HTTPError as e:
        state = "forbidden" if e.code in (401, 403) else "network_error"
        raise CollectorError(f"平台返回 HTTP {e.code}，请稍后重试。", state) from e
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as e:
        raise CollectorError("平台暂时无法连接，请检查网络后重试；上次结果仍保留。", "network_error") from e


def _state(html):
    marker = re.search(r"(?:window\.)?__INITIAL_STATE__\s*=\s*", html)
    if not marker:
        raise CollectorError("页面没有榜单数据，可能需要验证或平台页面已变化。")
    try:
        # Parse JSON only; never execute a script supplied by a platform.
        serialized = html[marker.end():]
        # The official serializer emits bare undefined for missing profile fields.
        # Tokenize quoted strings first so the same word inside prose is untouched.
        serialized = re.sub(r'"(?:\\.|[^"\\])*"|(?P<missing>\bundefined\b)',
                            lambda token: "null" if token.group("missing") else token.group(0), serialized)
        return json.JSONDecoder().raw_decode(serialized)[0]
    except (ValueError, TypeError) as e:
        raise CollectorError("平台数据格式已变化，当前结果未被覆盖。") from e


def _plain(value):
    return unescape(str(value or "")).strip()


def _clean_text(value):
    value = _plain(value)
    return "" if re.search(r"[\ue000-\uf8ff]", value) else value


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
        return result if result >= 0 else None
    except (ValueError, TypeError):
        return None


def _notify(progress, message):
    if progress:
        progress(message)


def _result(platform, mode, channel, category, board, source_url, books, **extra):
    if not books:
        raise CollectorError("没有取得有效书目；未用演示数据替代，上次结果仍保留。")
    return dict(platform=platform, mode=mode, channel=channel, category=category,
                board=board, source_url=source_url, data_date=None, books=books, **extra)


def _fanqie(options, progress):
    channel = options.get("channel") or "女频"
    channel = {"female": "女频", "male": "男频", "0": "女频", "1": "男频"}.get(channel, channel)
    if channel not in ("女频", "男频"):
        raise CollectorError("番茄请使用男频或女频。")
    board = options.get("board") or "新书榜"
    board = {"new": "新书榜", "read": "阅读榜"}.get(board, board)
    if board not in ("新书榜", "阅读榜"):
        raise CollectorError("番茄目前支持新书榜和阅读榜。")
    category = options.get("category") or ("古言脑洞" if channel == "女频" else "玄幻脑洞")
    c, t = ("0" if channel == "女频" else "1"), ("1" if board == "新书榜" else "2")
    starter = "253" if c == "0" else "257"
    _notify(progress, "正在读取番茄榜单和分类…")
    html = _fetch(f"{FANQIE}/rank/{c}_{t}_{starter}")
    categories = {}
    for href, name in re.findall(r'href="(/rank/\d+_\d+_\d+)"[^>]*>(.*?)</a>', html, re.S):
        if href.startswith(f"/rank/{c}_{t}_"):
            categories[_plain(re.sub(r"<[^>]+>", "", name))] = href
    path = categories.get(category)
    if path is None:
        raise CollectorError("当前频道没有这个分类，请选择平台实际分类：" + "、".join(categories.keys()))
    source = FANQIE + path
    if path != f"/rank/{c}_{t}_{starter}":
        html = _fetch(source)
    rank_data = _state(html).get("rank", {})
    rows = rank_data.get("book_list")
    if not isinstance(rows, list) or not rows:
        raise CollectorError("此榜单暂时没有取得书目，可能需要平台验证。")
    limit = max(1, min(100, int(options.get("limit") or 30)))
    warnings = []
    rank_version = str(rank_data.get("rankVersion") or "")
    # Same paginated public endpoint used by the platform's own rank page.
    # Keep the SSR version for each page, and require explicit currentPos fields.
    while len(rows) < min(limit, 30):
        parameters = urllib.parse.urlencode({"app_id":2503, "rank_list_type":3,
            "offset":len(rows), "limit":10, "category_id":path.rsplit("_",1)[1],
            "rank_version":rank_version, "gender":c, "rankMold":t})
        try:
            payload = json.loads(_fetch(FANQIE + "/api/rank/category/list?" + parameters))
            response_version = str(payload.get("data", {}).get("rankVersion") or "")
            if rank_version and response_version and response_version != rank_version:
                warnings.append("榜单版本在分页时发生变化，已停止合并，避免混用两个时点。")
                break
            more = payload.get("data", {}).get("book_list")
            if payload.get("code") != 0 or not isinstance(more, list) or not more:
                warnings.append("后续榜单页面未返回有效数据，仅保留已取得的原榜名次。")
                break
            seen = {str(row.get("bookId")) for row in rows}
            fresh = [row for row in more if str(row.get("bookId")) not in seen]
            if not fresh:
                warnings.append("平台分页返回重复书目，已停止。")
                break
            rows.extend(fresh)
        except (CollectorError, ValueError, TypeError):
            warnings.append("后续榜单分页连接失败，仅保留已取得的原榜名次。")
            break
    available = len(rows)
    rows = rows[:limit]
    books, failures, failed_rows = [], [], []

    def detail(row):
        book_id = str(row.get("bookId") or row.get("book_id") or "")
        rank = _int(row.get("currentPos"))
        if not re.fullmatch(r"\d{5,30}", book_id) or not rank:
            return None
        # Page fields are tied to this exact id, never to recommendation objects.
        page = _state(_fetch(f"{FANQIE}/page/{book_id}")).get("page", {})
        if str(page.get("bookId")) != book_id:
            raise CollectorError("详情页作品身份不匹配。")
        title = _clean_text(page.get("bookName"))
        if not title:
            raise CollectorError("详情页书名不可读。")
        try:
            tags_data = page.get("categoryV2") or "[]"
            if isinstance(tags_data, str):
                tags_data = json.loads(tags_data)
            tags = [_plain(item.get("Name")) for item in tags_data if isinstance(item, dict) and item.get("Name")]
        except (ValueError, TypeError):
            tags = []
        metrics = []
        reads = _int(row.get("read_count"))
        words = _int(page.get("wordNumber"))
        if reads is not None:
            metrics.append({"name": "在读", "value": reads, "unit": "人"})
        if words is not None:
            metrics.append({"name": "字数", "value": words, "unit": "字"})
        return {"id": book_id, "title": title, "author": _clean_text(page.get("author")),
                "summary": _clean_text(page.get("abstract")), "rank": rank,
                "url": f"{FANQIE}/page/{book_id}", "tags": tags, "metrics": metrics,
                "status": "连载中" if str(page.get("creationStatus")) == "1" else "已完结"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(detail, row): row for row in rows}
        for done, future in enumerate(as_completed(futures), 1):
            row = futures[future]
            try:
                book = future.result()
                if book:
                    books.append(book)
                else:
                    failed_rows.append(row)
            except Exception:
                failed_rows.append(row)
            _notify(progress, f"正在读取简介，已处理 {done}/{len(rows)} 本…")
    # One bounded, sequential retry only for the failed identities. Keep this exact
    # rank snapshot; do not re-scan or substitute a fresh book at the same rank.
    for retry_index, row in enumerate(failed_rows, 1):
        _notify(progress, f"正在补读缺失简介 {retry_index}/{len(failed_rows)}…")
        try:
            book = detail(row)
            if book:
                books.append(book)
            else:
                failures.append(str(row.get("currentPos", "未知")))
        except Exception:
            failures.append(str(row.get("currentPos", "未知")))
    books.sort(key=lambda book: book["rank"])
    if failures:
        warnings.append("以下原榜名次的详情读取失败：" + "、".join(failures))
    if limit > available:
        warnings.append(f"本次实际覆盖前 {available} 个源条目；不代表请求数量或完整榜单。")
    result = _result("fanqie", "long", channel, category, board, source, books,
                   warnings=warnings, requested_count=limit, available_count=available,
                   complete=not failures and available >= limit, categories=list(categories), rank_version=rank_version)
    if rank_version.isdigit() and len(rank_version) == 10:
        result["data_date"] = datetime.fromtimestamp(int(rank_version), timezone.utc).isoformat()
    return result


QIDIAN_BOARDS = {"月票榜": "yuepiao", "畅销榜": "hotsales", "签约新书榜": "sign", "公众新书榜": "newbook", "新人新书榜": "newauthor", "推荐榜": "rec"}


def parse_qidian(html, board):
    match = re.search(r'<script[^>]+id=[\"\x27]vite-plugin-ssr_pageContext[\"\x27][^>]*>([\s\S]*?)</script>', html)
    if not match:
        raise CollectorError("起点页面未返回榜单资料，请稍后重试或在平台完成验证。")
    try:
        data = json.loads(match.group(1))["pageContext"]["pageProps"]["pageData"]
    except (KeyError, ValueError, TypeError) as e:
        raise CollectorError("起点榜单结构发生变化。") from e
    if data.get("gender") not in (None, "male"):
        raise CollectorError("起点返回的频道与请求不一致。")
    books = []
    for row in data.get("records", []):
        book_id = str(row.get("bid") or row.get("bookId") or "")
        rank = _int(row.get("rankNum"))
        title = _clean_text(row.get("bName") or row.get("bookName"))
        if not re.fullmatch(r"\d{5,30}", book_id) or not title or not rank:
            continue
        metrics = []
        if row.get("rankCnt"):
            metrics.append({"name": board, "value": str(row["rankCnt"]), "unit": ""})
        if row.get("cnt"):
            metrics.append({"name": "字数", "value": str(row["cnt"]), "unit": ""})
        books.append({"id": book_id, "title": title, "author": _plain(row.get("bAuth") or row.get("author")),
                      "summary": _plain(row.get("desc")), "rank": rank, "url": f"https://www.qidian.com/book/{book_id}/",
                      "tags": [str(row[k]) for k in ("cat", "subCat") if row.get(k)], "metrics": metrics})
    return books, data.get("total")


def _qidian(options, progress):
    channel = options.get("channel") or "男频"
    category = options.get("category") or "全部"
    board = options.get("board") or "月票榜"
    if channel not in ("男频", "male") or category != "全部":
        raise CollectorError("起点当前已接入男频全分类榜，请选择男频、全部。")
    if board not in QIDIAN_BOARDS:
        raise CollectorError("起点当前支持：" + "、".join(QIDIAN_BOARDS))
    source = f"https://m.qidian.com/rank/{QIDIAN_BOARDS[board]}/"
    _notify(progress, "正在读取起点原始榜单…")
    books, total = parse_qidian(_fetch(source, mobile=True), board)
    limit = max(1, min(100, int(options.get("limit") or 30)))
    # Only the actually returned first page is available. Do not invent 100 entries.
    return _result("qidian", "long", "男频", "全部", board, source, books[:limit],
                   available_count=len(books), source_total=total, requested_count=limit,
                   warnings=[f"当前读取首屏 {len(books)} 本；不代表完整榜单。"], complete=limit <= len(books))


class _Node:
    def __init__(self, tag="root", attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
    @property
    def text(self):
        return " ".join(c if isinstance(c, str) else c.text for c in self.children).strip()
    def walk(self, tag=None):
        for child in self.children:
            if isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.walk(tag)


class _Tree(HTMLParser):
    VOID = {"img", "br", "hr", "input", "meta", "link", "source", "wbr", "area", "base", "embed", "param", "track", "col"}
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = _Node()
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node
    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent
    def handle_data(self, text):
        if self.current.tag not in ("script", "style"):
            self.current.children.append(text)


def parse_ciweimao(html, board):
    tree = _Tree(html).root
    # Current deep rank pages expose a dedicated ol rather than a heading per
    # rank. Require both a matching page title and explicit per-item source ids.
    title_node = next(tree.walk("title"), None)
    deep_lists = [n for n in tree.walk("ol") if "rank-book-list" in n.attrs.get("class", "").split()]
    if deep_lists and title_node and board in title_node.text:
        books, seen = [], set()
        for item in deep_lists[0].walk("li"):
            book_id = item.attrs.get("data-book-id", "")
            rank_node = next((n for n in item.children if isinstance(n, _Node) and n.tag == "i" and re.fullmatch(r"\d+", n.text)), None)
            heading = next((n for n in item.walk("h3") if "tit" in n.attrs.get("class", "").split()), None)
            link = next(heading.walk("a"), None) if heading else None
            if not re.fullmatch(r"\d+", book_id) or book_id in seen or rank_node is None or not link:
                continue
            if not re.fullmatch(r"\d+", rank_node.text) or not re.search(r"/book/" + re.escape(book_id) + r"(?:/|$)", link.attrs.get("href", "")):
                continue
            author_node = next((p for p in item.walk("p") if p.text.startswith("小说作者：")), None)
            summary_node = next((p for p in item.walk("p") if "desc" in p.attrs.get("class", "").split()), None)
            seen.add(book_id)
            books.append({"id": book_id, "title": link.text, "rank": int(rank_node.text),
                          "author": author_node.text.removeprefix("小说作者：").strip() if author_node else "",
                          "summary": summary_node.text if summary_node else "", "url": f"https://www.ciweimao.com/book/{book_id}",
                          "tags": [], "metrics": []})
        return sorted(books, key=lambda b: b["rank"])
    headings = [n for n in tree.walk() if n.tag in ("h2", "h3", "h4") and board in n.text]
    if not headings:
        raise CollectorError("刺猬猫未返回所选榜单，可能需要验证或页面结构已变化。")
    candidates = []
    for heading in headings:
        section = heading.parent
        for _ in range(4):
            if section is None:
                break
            items = list(section.walk("li"))
            if sum(bool(re.search(r"/book/\d+", a.attrs.get("href", ""))) for a in section.walk("a")) >= 10:
                candidates.append(section)
                break
            section = section.parent
    if not candidates:
        raise CollectorError("刺猬猫当前页面无法可靠识别榜单区块。")
    books, seen = [], set()
    section = candidates[0]
    for item in section.walk("li"):
        links = [a for a in item.walk("a") if re.search(r"/book/\d+", a.attrs.get("href", "")) and a.text.strip()]
        if not links:
            continue
        link = max(links, key=lambda a: len(a.text))
        book_id = re.search(r"/book/(\d+)", link.attrs["href"])[1]
        if book_id in seen:
            continue
        rank_node = next((n for n in item.walk() if "rank" in n.attrs.get("class", "") and re.fullmatch(r"(?:NO\.)?\s*\d{1,3}", n.text, re.I)), None)
        if rank_node is None:
            # Only accept explicit displayed positions, not traversal order.
            match = re.match(r"\s*(?:NO\.)?\s*(\d{1,2})\s", item.text, re.I)
            rank = int(match[1]) if match else None
        else:
            rank = int(re.search(r"\d+", rank_node.text)[0])
        if rank is None or rank > 10:
            continue
        seen.add(book_id)
        books.append({"id": book_id, "title": link.text.strip(), "author": "", "summary": "",
                      "rank": rank, "url": f"https://www.ciweimao.com/book/{book_id}", "tags": [], "metrics": []})
    return sorted(books, key=lambda b: b["rank"])


def _ciweimao(options, progress):
    board = options.get("board") or "月票榜"
    if options.get("channel", "综合") not in ("综合", "全部") or options.get("category", "全部") != "全部":
        raise CollectorError("刺猬猫当前支持综合频道、全部分类。")
    if board not in ("月票榜", "点击榜", "收藏榜", "推荐榜", "订阅榜", "新书榜", "刀片榜", "更新榜", "吐槽榜"):
        raise CollectorError("暂不支持此刺猬猫榜单。")
    # Verified link from the public homepage and rank page. Use an explicit
    # month URL; the bare /yp redirect defaults to a different period.
    source = "https://www.ciweimao.com/rank-index/yp-month" if board == "月票榜" else "https://www.ciweimao.com/rank-index"
    _notify(progress, "正在读取刺猬猫榜单…")
    books = parse_ciweimao(_fetch(source), board)
    requested = max(1, min(100, int(options.get("limit") or 10)))
    limit = min(10, requested)
    return _result("ciweimao", "long", "综合", "全部", board, source, books[:limit],
                   warnings=["月票榜采用月榜周期，本次只读取首屏前 10 本；源页面未提供票数。" if board == "月票榜" else "刺猬猫本次只读取前 10 本；部分简介和作者未返回。"],
                   complete=len(books) >= requested, period="月榜" if board == "月票榜" else None,
                   requested_count=requested, available_count=len(books))


class _BrowserWorker:
    def __init__(self, data_dir):
        node = shutil.which("node") or os.environ.get("CODEX_PRIMARY_RUNTIME_NODE")
        if not node:
            raise CollectorError("黑岩登录需要 Node.js 20+ 和 Playwright，请让 WorkBuddy 完成浏览器环境准备。")
        env = dict(os.environ)
        env["FAGAO_DATA_DIR"] = str(Path(data_dir).resolve())
        self.process = subprocess.Popen([node, str(ROOT / "scripts" / "browser-worker.mjs")], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                        encoding="utf-8", env=env, bufsize=1)
        self.lines = queue.Queue()
        self.lock = threading.Lock()
        def read():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)
        threading.Thread(target=read, daemon=True).start()
    def call(self, action, options=None):
        with self.lock:
            if self.process.poll() is not None:
                raise CollectorError("浏览器服务已经退出，请重新连接黑岩。")
            self.process.stdin.write(json.dumps({"action": action, "options": options or {}}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            try:
                line = self.lines.get(timeout=180)
            except queue.Empty as e:
                self.process.terminate()
                raise CollectorError("浏览器操作超时，请检查登录窗口后重试。", "network_error") from e
            if line is None:
                raise CollectorError("浏览器服务未启动，请运行 npm install 和 npx playwright install chromium。")
            result = json.loads(line)
            if result.get("error"):
                raise CollectorError(result["error"], result.get("state", "unavailable"))
            return result
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()


_workers = {}
_workers_lock = threading.Lock()


def _worker(data_dir):
    key = str(Path(data_dir).resolve())
    with _workers_lock:
        current = _workers.get(key)
        if current is None or current.process.poll() is not None:
            current = _workers[key] = _BrowserWorker(key)
        return current


@atexit.register
def close_browsers():
    for worker in list(_workers.values()):
        worker.close()


def auth(platform, action, data_dir):
    """Open/check the same private session. Never return credentials."""
    if platform != "heiyan":
        return {"state": "unavailable", "message": "此平台暂未提供账号连接入口。"}
    if action not in ("login", "check"):
        raise CollectorError("未知的登录操作。")
    if action == "check" and str(Path(data_dir).resolve()) not in _workers and not (Path(data_dir) / "browser" / "heiyan").exists():
        return {"state": "logged_out", "message": "请连接能够访问黑岩书库的账号。"}
    try:
        return _worker(data_dir).call(action)
    except CollectorError as e:
        return {"state": e.state, "message": str(e)}


def collect(platform, options, data_dir, progress=None):
    """Collect one precisely scoped list; failures never fabricate a snapshot."""
    if platform == "fanqie":
        return _fanqie(options, progress)
    if platform == "qidian":
        return _qidian(options, progress)
    if platform == "ciweimao":
        return _ciweimao(options, progress)
    if platform == "heiyan":
        _notify(progress, "正在检查黑岩书库访问权限…")
        result = _worker(data_dir).call("collect", options)
        if result.get("state") != "connected":
            raise CollectorError(result.get("message", "请先登录黑岩书库。"), result.get("state", "logged_out"))
        return _result("heiyan", "short", result.get("channel", "全部"), "书库", "书库", HEIYAN,
                       result.get("books", []), warnings=result.get("warnings", []), complete=result.get("complete", False))
    if platform == "fanqie_short":
        raise CollectorError("番茄短故事热门入口需要作者账号；当前采集适配尚未验证，可先导入已有书目。")
    raise CollectorError("暂不支持这个平台。")


def fetch_text(platform, book_id, data_dir, max_chapters=3):
    """Only normal publicly readable chapters; never report encoded data as text.

    No font substitution, private decryption service or paywall workarounds.
    Always reports the actual partial scope, including failure after earlier chapters.
    """
    if platform != "fanqie":
        raise CollectorError("此平台正文自动获取尚未接入，请导入可用原文。")
    book_id = str(book_id)
    if not re.fullmatch(r"\d{5,30}", book_id):
        raise CollectorError("无效的番茄作品 ID。")
    max_chapters = max(1, min(20, int(max_chapters)))
    source = f"{FANQIE}/page/{book_id}"
    page = _state(_fetch(source)).get("page", {})
    if str(page.get("bookId")) != book_id:
        raise CollectorError("作品详情身份不匹配，未取得正文。")
    catalog = [chapter for volume in page.get("chapterListWithVolume", []) if isinstance(volume, list)
               for chapter in volume if isinstance(chapter, dict)]
    chapters, texts, missing = [], [], []
    for item in catalog[:max_chapters]:
        item_id = str(item.get("itemId") or "")
        title = _plain(item.get("title"))
        if item.get("needPay") or item.get("isChapterLock") or item.get("isPaidPublication") or item.get("isPaidStory"):
            missing.append({"chapter": title, "reason": "需要相应正文权限"})
            break
        if not re.fullmatch(r"\d{5,30}", item_id):
            missing.append({"chapter": title, "reason": "章节 ID 缺失"})
            break
        url = f"{FANQIE}/reader/{item_id}"
        try:
            chapter = _state(_fetch(url)).get("reader", {}).get("chapterData", {})
            if str(chapter.get("bookId")) != book_id or str(chapter.get("itemId")) != item_id:
                raise CollectorError("章节身份不匹配")
            if chapter.get("needPay") or chapter.get("isChapterLock"):
                raise CollectorError("章节需要相应正文权限")
            raw = str(chapter.get("content") or "")
            if re.search(r"[\ue000-\uf8ff]", raw):
                raise CollectorError("章节正文采用字体编码，当前未取得可靠可读文本；请导入原文")
            text = _plain(re.sub(r"<[^>]+>", "", re.sub(r"</p\s*>", "\n\n", raw)))
            if len(text) < 100 or re.search(r"验证码|访问过于频繁|登录后阅读|购买本章", text[:500]):
                raise CollectorError("章节正文为空、不完整或返回验证页面")
            title = _clean_text(chapter.get("title")) or title
            chapters.append({"id": item_id, "title": title, "url": url, "characters": len(text)})
            texts.append(title + "\n\n" + text)
        except CollectorError as e:
            missing.append({"chapter": title, "reason": str(e)})
            break
    if not texts:
        reason = missing[0]["reason"] if missing else "公开目录未返回可读取章节，请导入原文"
        raise CollectorError(reason)
    return {"platform": platform, "book_id": book_id, "text": "\n\n".join(texts),
            "scope": f"公开阅读页前 {len(chapters)} 章", "complete": False, "source": source,
            "chapters": chapters, "missing": missing, "catalog_chapters": len(catalog)}
