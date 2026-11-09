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
from http.cookiejar import CookieJar
import inspect
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .fanqie_codec import decode_text, has_unmapped_pua
from . import network


ROOT = Path(__file__).resolve().parents[1]
FANQIE = "https://fanqienovel.com"
HEIYAN = "https://manage.zhangwenpindu.cn/books/booklist"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
MOBILE_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"


class CollectorError(RuntimeError):
    def __init__(self, message, state="unavailable"):
        super().__init__(message)
        self.state = state


def _fetch(url, mobile=False, opener=None, referer=None, timeout=20, read_body=True):
    request = urllib.request.Request(url, headers={
        "User-Agent": MOBILE_AGENT if mobile else USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "identity",
    })
    if referer:
        request.add_header("Referer", referer)
    try:
        open_url = (opener or network.build_opener()).open
        with open_url(request, timeout=timeout) as response:
            if not read_body:
                return ""
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise CollectorError("平台响应过大，已停止读取。")
            return data.decode("utf-8")
    except urllib.error.HTTPError as e:
        state = "forbidden" if e.code in (401, 403) else "network_error"
        raise CollectorError(f"平台返回 HTTP {e.code}，请稍后重试。", state) from e
    except network.NetworkError as e:
        raise CollectorError(str(e), "network_error") from e
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as e:
        raise CollectorError("平台暂时无法连接，请检查网络后重试；上次结果仍保留。", "network_error") from e


def probe_network():
    """Check response headers through the real transport, without reading a book."""
    result = {"target": FANQIE + "/rank/0_1_253", "checked_at": datetime.now(timezone.utc).isoformat()}
    started = time.monotonic()
    try:
        _fetch(result["target"], timeout=5, read_body=False)
        result.update(status="reachable", message="番茄榜单页面连接成功；其他来源以各自采集结果为准。")
    except CollectorError as exc:
        result.update(status="failed", code=exc.state, message=str(exc))
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


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


def _fanqie_text(value):
    text = _plain(decode_text(value))
    return "" if has_unmapped_pua(text) else text


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


def _notify_snapshot(progress, snapshot, has_more):
    """Publish a usable page before another request; the consumer may wait here.

    Legacy one-argument progress handlers still receive a status message. The
    richer callback may persist an immutable snapshot and block until a user
    requests more rows. No source session/cookie is included in this payload.
    Cancellation exceptions from the callback deliberately propagate.
    """
    if not progress or not snapshot.get("books"):
        return
    message = f"已加载 {len(snapshot['books'])} 本，可先浏览" + ("，后续书目按需继续。" if has_more else "。")
    # Do not catch TypeError from inside the callback: it can signal a genuine
    # consumer failure, which must not be retried as a second invocation.
    try:
        inspect.signature(progress).bind(message, snapshot=snapshot, has_more=has_more)
    except (TypeError, ValueError):
        progress(message)
    else:
        progress(message, snapshot=snapshot, has_more=has_more)


def _fanqie_preview_books(rows, category):
    """Decode the actual list fields without waiting for slow detail requests."""
    books = []
    for row in rows:
        book_id = str(row.get("bookId") or row.get("book_id") or "")
        rank = _int(row.get("currentPos"))
        title = _fanqie_text(row.get("bookName"))
        if not re.fullmatch(r"\d{5,30}", book_id) or not rank or not title:
            continue
        metrics = []
        for name, value, unit in (("在读", row.get("read_count"), "人"), ("字数", row.get("wordNumber"), "字")):
            number = _int(value)
            if number is not None:
                metrics.append({"name": name, "value": number, "unit": unit})
        books.append({"id": book_id, "title": title, "author": _fanqie_text(row.get("author")),
                      "summary": _fanqie_text(row.get("abstract")), "rank": rank,
                      "url": f"{FANQIE}/page/{book_id}", "cover_url": _cover_url(row.get("thumbUri")),
                      "tags": [category], "metrics": metrics,
                      "status": {"1": "连载中", "0": "已完结"}.get(str(row.get("creationStatus")), ""),
                      "metadata_status": "list_preview"})
    return sorted(books, key=lambda book: book["rank"])


def _page_options(options, page_size):
    try:
        max_pages = max(1, min(100, int(options.get("max_pages") or 100)))
        raw_limit = options.get("limit")
        # Preserve an explicit requested count even when the page budget is
        # smaller; reaching that budget must not masquerade as fulfilling it.
        limit = max(1, int(raw_limit)) if raw_limit not in (None, "", 0) else page_size * max_pages
    except (ValueError, TypeError) as e:
        raise CollectorError("采集页数与数量必须是正整数。") from e
    return max_pages, limit


def _page_pause(progress, message):
    _notify(progress, message)  # cancellation exceptions deliberately propagate
    time.sleep(0.35)


def _cover_url(value):
    value = _plain(value)
    if value.startswith("//"):
        value = "https:" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if "transparent" in parsed.path.lower() or "placeholder" in parsed.path.lower():
        return ""
    return value


def _merge_page(existing, new_books):
    """Append exact source identities; never let a later page reassign a rank."""
    ids = {str(book.get("id", book.get("bookId", ""))): book.get("rank", book.get("currentPos")) for book in existing}
    ranks = {rank: ident for ident, rank in ids.items() if rank is not None}
    fresh = []
    for book in new_books:
        ident = str(book.get("id", book.get("bookId", "")))
        rank = book.get("rank", book.get("currentPos"))
        if not ident or rank is None:
            continue
        if ident in ids:
            if ids[ident] != rank:
                return [], "rank_conflict"
            continue
        if rank in ranks and ranks[rank] != ident:
            return [], "rank_conflict"
        ids[ident], ranks[rank] = rank, ident
        fresh.append(book)
    return fresh, None


def _cache_read(data_dir, platform, book_id, source_update):
    if data_dir is None:
        return None
    path = Path(data_dir) / "cache" / "metadata" / platform / (book_id + ".json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(payload["cached_at"])
        if 0 <= age < 86400 and str(payload.get("source_update", "")) == str(source_update or "") and payload.get("book", {}).get("id") == book_id:
            return payload["book"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _cache_write(data_dir, platform, book_id, source_update, book):
    if data_dir is None:
        return
    folder = Path(data_dir) / "cache" / "metadata" / platform
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".metadata-", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"schema": 1, "cached_at": time.time(), "source_update": str(source_update or ""), "book": book}, output, ensure_ascii=False)
        os.replace(temporary, folder / (book_id + ".json"))
    except OSError:
        try:
            if "temporary" in locals():
                os.unlink(temporary)
        except OSError:
            pass


def _result(platform, mode, channel, category, board, source_url, books, **extra):
    if not books:
        raise CollectorError("没有取得有效书目；未用演示数据替代，上次结果仍保留。")
    return dict(platform=platform, mode=mode, channel=channel, category=category,
                board=board, source_url=source_url, data_date=None, books=books, **extra)


def _fanqie(options, progress, data_dir=None):
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
    # The public endpoint accepts batches of 100. The initial SSR page remains
    # a separate, smaller anchor for this exact category and rank version.
    max_pages, limit = _page_options(options, 100)
    initial_page_size = len(rows)
    warnings = []
    rank_version = str(rank_data.get("rankVersion") or "")
    pages_fetched, stop_reason = 1, "max_pages"
    offset = len(rows)

    def publish_page():
        if not progress:
            return
        preview = _fanqie_preview_books(rows[:limit], category)
        if not preview:
            return
        raw = _result("fanqie", "long", channel, category, board, source, preview,
                      warnings=list(warnings), complete=False, categories=list(categories), rank_version=rank_version,
                      requested_count=limit, available_count=len(rows),
                      pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": "loading",
                                  "source_total": rank_data.get("total_num"), "page_size": 100,
                                  "initial_page_size": initial_page_size})
        if rank_version.isdigit() and len(rank_version) == 10:
            raw["data_date"] = datetime.fromtimestamp(int(rank_version), timezone.utc).isoformat()
        _notify_snapshot(progress, raw, len(rows) < limit and pages_fetched < max_pages)

    publish_page()
    # Same paginated public endpoint used by the platform's own rank page.
    # Keep the SSR version for each page, and require explicit currentPos fields.
    while len(rows) < limit and pages_fetched < max_pages:
        _page_pause(progress, f"正在读取番茄第 {pages_fetched + 1} 页，已取得 {len(rows)} 个榜单条目…")
        parameters = urllib.parse.urlencode({"app_id":2503, "rank_list_type":3,
            "offset":offset, "limit":min(100, limit - len(rows)), "category_id":path.rsplit("_",1)[1],
            "rank_version":rank_version, "gender":c, "rankMold":t})
        try:
            payload = json.loads(_fetch(FANQIE + "/api/rank/category/list?" + parameters))
            pages_fetched += 1
            response_version = str(payload.get("data", {}).get("rankVersion") or "")
            more = payload.get("data", {}).get("book_list")
            if payload.get("code") != 0 or not isinstance(more, list):
                warnings.append("后续榜单页面未返回有效数据，仅保留已取得的原榜名次。")
                stop_reason = "invalid_response"
                break
            # The official empty response omits rankVersion entirely. No books
            # are merged in this case, so its omission is not a version change.
            if not more and not response_version:
                stop_reason = "empty_page"
                break
            if rank_version and response_version != rank_version:
                warnings.append("榜单版本在分页时发生变化，已停止合并，避免混用两个时点。")
                stop_reason = "version_changed"
                break
            if not more:
                stop_reason = "empty_page"
                break
            fresh, conflict = _merge_page(rows, more)
            if conflict:
                warnings.append("榜单分页的原始名次与作品身份冲突，已停止合并。")
                stop_reason = conflict
                break
            if not fresh:
                warnings.append("平台分页返回重复书目，已停止。")
                stop_reason = "repeated_page"
                break
            rows.extend(fresh)
            offset += len(more)
        except (CollectorError, ValueError, TypeError):
            warnings.append("后续榜单分页连接失败，仅保留已取得的原榜名次。")
            stop_reason = "request_failed"
            break
        publish_page()
    if len(rows) >= limit:
        stop_reason = "requested_limit" if options.get("limit") else "max_pages"
    available = len(rows)
    rows = rows[:limit]
    books, failures, failed_rows = [], [], []

    def detail(row):
        book_id = str(row.get("bookId") or row.get("book_id") or "")
        rank = _int(row.get("currentPos"))
        if not re.fullmatch(r"\d{5,30}", book_id) or not rank:
            return None
        source_update = row.get("lastChapterUpdateTime") or row.get("last_chapter_update_time") or ""
        cached = _cache_read(data_dir, "fanqie", book_id, source_update)
        fields = {key: decode_text(row.get(key)) for key in ("bookName", "author", "abstract")}
        # Decode the public list's actual fields directly. A new/unmapped glyph
        # or missing field falls back to the existing ID-checked detail path.
        from_rank = (all(key in row for key in fields) and bool(_plain(fields["bookName"]))
                     and not any(has_unmapped_pua(value) for value in fields.values()))
        if from_rank:
            page = {**row, **fields}
            if cached and not page.get("categoryV2"):
                page["categoryV2"] = cached.get("page", {}).get("categoryV2")
            metadata_source = "rank"
        else:
            # Page fields are tied to this exact id, never recommendation objects.
            page = cached.get("page", {}) if cached else _state(_fetch(f"{FANQIE}/page/{book_id}")).get("page", {})
            metadata_source = "cache" if cached else "detail"
        if str(page.get("bookId")) != book_id:
            raise CollectorError("详情页作品身份不匹配。")
        if any(has_unmapped_pua(decode_text(page.get(key))) for key in ("bookName", "author", "abstract")):
            raise CollectorError("作品资料包含当前字表尚未覆盖的字符。")
        title = _fanqie_text(page.get("bookName"))
        if not title:
            raise CollectorError("详情页书名不可读。")
        try:
            tags_data = page.get("categoryV2") or "[]"
            if isinstance(tags_data, str):
                tags_data = json.loads(tags_data)
            tags = [_fanqie_text(item.get("Name")) for item in tags_data if isinstance(item, dict) and item.get("Name")]
            tags = [tag for tag in tags if tag]
        except (ValueError, TypeError):
            tags = []
        if not tags:
            tags = [category]
        metrics = []
        reads = _int(row.get("read_count"))
        words = _int(row.get("wordNumber") if row.get("wordNumber") is not None else page.get("wordNumber"))
        if reads is not None:
            metrics.append({"name": "在读", "value": reads, "unit": "人"})
        if words is not None:
            metrics.append({"name": "字数", "value": words, "unit": "字"})
        result = {"id": book_id, "title": title, "author": _fanqie_text(page.get("author")),
                "summary": _fanqie_text(page.get("abstract")), "rank": rank,
                "url": f"{FANQIE}/page/{book_id}", "tags": tags, "metrics": metrics,
                "cover_url": _cover_url(row.get("thumbUri") or page.get("thumbUri")),
                "status": {"1": "连载中", "0": "已完结"}.get(str(page.get("creationStatus")), ""),
                "_metadata_source": metadata_source}
        if not cached:
            # Cache metadata only, excluding chapter catalogues, cookies and text.
            keep = {key: page.get(key) for key in ("bookId", "bookName", "author", "abstract", "categoryV2", "wordNumber", "thumbUri", "creationStatus")}
            _cache_write(data_dir, "fanqie", book_id, source_update, {"id": book_id, "page": keep})
        return result

    # Submit bounded batches, not thousands of queued requests. Cancellation
    # stops before the next batch and waits for at most four in-flight requests.
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(rows), 4):
            _notify(progress, f"正在整理书名和简介，已处理 {done}/{len(rows)} 本…")
            futures = {pool.submit(detail, row): row for row in rows[start:start + 4]}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    book = future.result()
                    if book:
                        books.append(book)
                    else:
                        failed_rows.append(row)
                except Exception:
                    failed_rows.append(row)
                done += 1
                _notify(progress, f"正在整理书名和简介，已处理 {done}/{len(rows)} 本…")
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
        for row in failed_rows:
            rank = _int(row.get("currentPos"))
            book_id = str(row.get("bookId") or row.get("book_id") or "")
            if str(rank) not in failures or not re.fullmatch(r"\d{5,30}", book_id) or not rank:
                continue
            # Preserve the real source identity and cover when detail requests
            # fail. The title is explicitly unavailable, never a fabricated book.
            title = _fanqie_text(row.get("bookName")) or f"书名暂未取得（原榜第 {rank} 名）"
            metrics = []
            for name, value, unit in (("在读", row.get("read_count"), "人"), ("字数", row.get("wordNumber"), "字")):
                number = _int(value)
                if number is not None:
                    metrics.append({"name": name, "value": number, "unit": unit})
            books.append({"id": book_id, "title": title, "author": _fanqie_text(row.get("author")),
                          "summary": _fanqie_text(row.get("abstract")), "rank": rank,
                          "url": f"{FANQIE}/page/{book_id}", "cover_url": _cover_url(row.get("thumbUri")),
                          "tags": [], "metrics": metrics, "status": "详情暂未取得", "metadata_status": "missing"})
        books.sort(key=lambda book: book["rank"])
    if stop_reason not in ("empty_page", "requested_limit"):
        warnings.append(f"本次读取 {pages_fetched} 页、{available} 个源条目，停止原因：{stop_reason}。")
    metadata_statistics = {"rank": 0, "cache": 0, "detail": 0, "missing": 0}
    for book in books:
        metadata_statistics[book.pop("_metadata_source", "missing")] += 1
    result = _result("fanqie", "long", channel, category, board, source, books,
                   warnings=warnings, requested_count=limit, available_count=available,
                   complete=not failures and stop_reason in ("empty_page", "requested_limit"), categories=list(categories), rank_version=rank_version,
                   metadata_statistics=metadata_statistics,
                   pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": stop_reason,
                               "source_total": rank_data.get("total_num"), "page_size": 100, "initial_page_size": initial_page_size})
    if rank_version.isdigit() and len(rank_version) == 10:
        result["data_date"] = datetime.fromtimestamp(int(rank_version), timezone.utc).isoformat()
    return result


QIDIAN_BOARDS = {"月票榜": "yuepiao", "畅销榜": "hotsales", "签约新书榜": "sign", "公众新书榜": "newbook", "新人新书榜": "newauthor", "推荐榜": "rec"}


def _qidian_data(html):
    match = re.search(r'<script[^>]+id=[\"\x27]vite-plugin-ssr_pageContext[\"\x27][^>]*>([\s\S]*?)</script>', html)
    if not match:
        raise CollectorError("起点页面未返回榜单资料，请稍后重试或在平台完成验证。")
    try:
        data = json.loads(match.group(1))["pageContext"]["pageProps"]["pageData"]
    except (KeyError, ValueError, TypeError) as e:
        raise CollectorError("起点榜单结构发生变化。") from e
    if data.get("gender") not in (None, "male"):
        raise CollectorError("起点返回的频道与请求不一致。")
    return data


def _qidian_books(data, board, covers=None):
    covers = covers or {}
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
                      "tags": [str(row[k]) for k in ("cat", "subCat") if row.get(k)], "metrics": metrics,
                      # Official mobile bookCoverUrl uses c_<bid>/180; SSR may
                      # supply an equivalent URL without the c_ prefix.
                      "cover_url": _cover_url(covers.get(book_id) or row.get("coverUrl") or row.get("cover") or f"https://bookcover.yuewen.com/qdbimg/349573/c_{book_id}/180")})
    return books


def parse_qidian(html, board):
    data = _qidian_data(html)
    covers = {}
    for image in _Tree(html).root.walk("img"):
        cover = _cover_url(image.attrs.get("data-src") or image.attrs.get("src"))
        match = re.search(r"bookcover\.yuewen\.com/qdbimg/\d+/(?:c_)?(\d+)/", cover)
        if match:
            covers[match[1]] = cover
    return _qidian_books(data, board, covers), data.get("total")


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
    # Normal anonymous website session. Keep the CSRF cookie server-side and use
    # only the token issued by this exact page session; no signature generation.
    cookies = CookieJar()
    opener = network.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    html = _fetch(source, mobile=True, opener=opener)
    data = _qidian_data(html)
    books, total = parse_qidian(html, board)
    max_pages, limit = _page_options(options, 20)
    pages_fetched, stop_reason, warnings = 1, "max_pages", []
    missing_records = max(0, len(data.get("records", [])) - len(books))
    selected_filters = {}
    for item in data.get("filters", []):
        try:
            key = str(item["key"])
            value = item["items"][int(item["selectedIndex"])]["value"]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", key) and key not in ("gender", "pageNum") and isinstance(value, (str, int)):
                selected_filters[key] = value
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    csrf = next((cookie.value for cookie in cookies if cookie.name == "_csrfToken" and (cookie.domain == "m.qidian.com" or cookie.domain.endswith(".qidian.com"))), None)
    # Official rank component maps its verified route id to id+'list'.
    endpoint = QIDIAN_BOARDS[board] + "list"

    def publish_page():
        if not progress or not books:
            return
        raw = _result("qidian", "long", "男频", "全部", board, source, [dict(book) for book in books[:limit]],
                      warnings=list(warnings), complete=False, requested_count=limit,
                      available_count=len(books), source_total=total, source_filters=dict(selected_filters),
                      pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": "loading",
                                  "source_total": total, "page_size": 20})
        has_more = bool(len(books) < limit and pages_fetched < max_pages and csrf and endpoint
                        and data.get("isLast") not in (1, True, "1"))
        _notify_snapshot(progress, raw, has_more)

    publish_page()
    while len(books) < limit and pages_fetched < max_pages:
        if data.get("isLast") in (1, True, "1"):
            stop_reason = "last_page"
            break
        if not csrf or not endpoint:
            stop_reason = "session_required" if not csrf else "pagination_unverified"
            warnings.append("已保存当前有效书目；后续分页需要有效网页会话。" if not csrf else "此榜型的后续分页尚未验证，保留首屏结果。")
            break
        page_number = pages_fetched + 1
        _page_pause(progress, f"正在读取起点第 {page_number} 页，已取得 {len(books)} 本…")
        params = {"gender": "male", "pageNum": page_number, **selected_filters, "_csrfToken": csrf}
        try:
            response = json.loads(_fetch("https://m.qidian.com/webcommon/rank/" + endpoint + "?" + urllib.parse.urlencode(params),
                                         mobile=True, opener=opener, referer=source))
            if response.get("code") != 0 or not isinstance(response.get("data"), dict):
                stop_reason = "session_required" if response.get("code") in (1403, 401, 403) else "invalid_response"
                warnings.append("起点分页暂时未通过网页会话检查，已保留此前结果。")
                break
            new_data = response["data"]
            if int(new_data.get("pageNum", page_number)) != page_number or new_data.get("gender") not in (None, "male"):
                stop_reason = "wrong_page"
                warnings.append("起点返回的页码或频道与请求不符，已停止。")
                break
            for item in new_data.get("filters", []):
                key = item.get("key")
                if key in selected_filters:
                    value = item.get("items", [])[int(item.get("selectedIndex", 0))].get("value")
                    if str(value) != str(selected_filters[key]):
                        raise CollectorError("起点榜单筛选或月份在翻页时发生变化。")
            new_books = _qidian_books(new_data, board)
            missing_records += max(0, len(new_data.get("records", [])) - len(new_books))
            pages_fetched += 1
        except (CollectorError, ValueError, TypeError, KeyError, IndexError) as e:
            stop_reason = "request_failed"
            warnings.append("起点后续分页暂时无法读取，已保留此前结果。")
            break
        if not new_books:
            stop_reason = "invalid_records" if new_data.get("records") else "empty_page"
            break
        fresh, conflict = _merge_page(books, new_books)
        if conflict or not fresh:
            stop_reason = conflict or "repeated_page"
            warnings.append("起点分页重复或原始名次发生冲突，已停止合并。")
            break
        books.extend(fresh)
        data = new_data
        publish_page()
    if len(books) >= limit and data.get("isLast") not in (1, True, "1"):
        stop_reason = "requested_limit" if options.get("limit") else "max_pages"
    elif data.get("isLast") in (1, True, "1"):
        stop_reason = "last_page"
    if missing_records:
        warnings.append(f"有 {missing_records} 个源条目的书名、身份或名次不可读，已保留其余有效书目。")
    return _result("qidian", "long", "男频", "全部", board, source, books[:limit],
                   available_count=len(books), source_total=total, requested_count=limit, warnings=warnings,
                   complete=not missing_records and stop_reason in ("last_page", "empty_page", "requested_limit"),
                   pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": stop_reason,
                               "source_total": total, "page_size": 20}, source_filters=selected_filters)


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
            cover_link = next((a for a in item.walk("a") if "cover" in a.attrs.get("class", "").split()), None)
            cover_image = next(cover_link.walk("img"), None) if cover_link else None
            cover = _cover_url(cover_image.attrs.get("data-original") or cover_image.attrs.get("data-src") or cover_image.attrs.get("src")) if cover_image else ""
            seen.add(book_id)
            books.append({"id": book_id, "title": link.text, "rank": int(rank_node.text),
                          "author": author_node.text.removeprefix("小说作者：").strip() if author_node else "",
                          "summary": summary_node.text if summary_node else "", "url": f"https://www.ciweimao.com/book/{book_id}",
                          "tags": [], "metrics": [], "cover_url": cover})
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
    max_pages, limit = _page_options(options, 10)
    pages_fetched, stop_reason, next_url = 0, "max_pages", source
    books, warnings = [], []
    started_at = datetime.now(timezone.utc).isoformat()

    def publish_page(has_next):
        if not progress or not books:
            return
        raw = _result("ciweimao", "long", "综合", "全部", board, source, [dict(book) for book in books[:limit]],
                      warnings=list(warnings), complete=False, period="月榜" if board == "月票榜" else None,
                      requested_count=limit, available_count=len(books),
                      pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": "loading",
                                  "source_total": None, "page_size": 10, "started_at": started_at})
        _notify_snapshot(progress, raw, bool(has_next and pages_fetched < max_pages and len(books) < limit))

    while next_url and pages_fetched < max_pages and len(books) < limit:
        _page_pause(progress, f"正在读取刺猬猫第 {pages_fetched + 1} 页，已取得 {len(books)} 本…")
        try:
            html = _fetch(next_url)
            new_books = parse_ciweimao(html, board)
            pages_fetched += 1
        except CollectorError as e:
            if not books:
                raise
            warnings.append(f"第 {pages_fetched + 1} 页未完成：{e}")
            stop_reason = "request_failed"
            break
        if not new_books:
            stop_reason = "empty_page"
            break
        fresh, conflict = _merge_page(books, new_books)
        if conflict:
            warnings.append("实时榜单跨页名次发生冲突，已保留此前结果；请稍后重新更新。")
            stop_reason = conflict
            break
        if not fresh:
            stop_reason = "repeated_page"
            warnings.append("平台下一页重复返回已取得的书目，已停止。")
            break
        books.extend(fresh)
        tree = _Tree(html).root
        next_link = next((a for a in tree.walk("a") if "next" in a.attrs.get("rel", "").split()), None)
        if not next_link:
            stop_reason = "last_page"
            next_url = None
            publish_page(False)
            break
        candidate = urllib.parse.urljoin(next_url, next_link.attrs.get("href", ""))
        parsed = urllib.parse.urlsplit(candidate)
        prefix = urllib.parse.urlsplit(source).path.rstrip("/")
        page_match = re.fullmatch(re.escape(prefix) + r"/(\d+)", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != "www.ciweimao.com" or not page_match or int(page_match[1]) != pages_fetched + 1 or parsed.query:
            stop_reason = "invalid_next_page"
            warnings.append("平台下一页没有指向同一个榜单周期，已停止，避免混榜。")
            publish_page(False)
            break
        next_url = candidate
        publish_page(True)
    if len(books) >= limit and stop_reason not in ("last_page", "empty_page"):
        stop_reason = "requested_limit" if options.get("limit") else "max_pages"
    if board == "月票榜":
        warnings.append("月票榜采用月榜周期；源页面未提供票数。")
    if stop_reason in ("max_pages", "requested_limit"):
        warnings.append(f"本次按设置最多读取 {max_pages} 页，已取得 {min(len(books), limit)} 本。")
    return _result("ciweimao", "long", "综合", "全部", board, source, books[:limit],
                   warnings=warnings, complete=stop_reason in ("last_page", "empty_page", "requested_limit"),
                   period="月榜" if board == "月票榜" else None, requested_count=limit, available_count=len(books),
                   pagination={"pages_fetched": pages_fetched, "max_pages": max_pages, "stop_reason": stop_reason,
                               "source_total": None, "page_size": 10, "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat()})


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
    def call(self, action, options=None, progress=None):
        with self.lock:
            if self.process.poll() is not None:
                raise CollectorError("浏览器服务已经退出，请重新连接黑岩。")
            self.process.stdin.write(json.dumps({"action": action, "options": options or {}}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + (1860 if action == "collect" else 180)
            latest = "正在读取黑岩书库…"
            while time.monotonic() < deadline:
                try:
                    line = self.lines.get(timeout=3)
                except queue.Empty:
                    if progress:
                        try:
                            progress(latest)
                        except Exception:
                            self.process.terminate()
                            raise
                    continue
                if line is None:
                    raise CollectorError("浏览器服务未启动，请运行 npm install 和 npx playwright install chromium。")
                result = json.loads(line)
                if result.get("event") == "progress":
                    latest = str(result.get("message") or latest)
                    if progress:
                        try:
                            progress(latest)
                        except Exception:
                            self.process.terminate()
                            raise
                    continue
                if result.get("error"):
                    raise CollectorError(result["error"], result.get("state", "unavailable"))
                return result
            self.process.terminate()
            raise CollectorError("浏览器操作超时，请检查登录窗口后重试。", "network_error")
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
    """Collect one precisely scoped list; failures never fabricate a snapshot.

    Public long-list collectors emit ``progress(message, snapshot=raw,
    has_more=bool)`` after each accepted source page. A callback may suspend
    collection there until the consumer requests another batch. Ordinary status
    messages still call ``progress(message)``. One-argument callbacks remain
    compatible. The final return is the fully enriched result, not the preview.
    """
    from .overseas import OVERSEAS_PLATFORMS, collect_overseas
    if platform in {item['id'] for item in OVERSEAS_PLATFORMS}:
        return collect_overseas(platform, options, data_dir, progress)
    if platform == "fanqie":
        return _fanqie(options, progress, data_dir)
    if platform == "qidian":
        return _qidian(options, progress)
    if platform == "ciweimao":
        return _ciweimao(options, progress)
    if platform == "heiyan":
        _notify(progress, "正在检查黑岩书库访问权限…")
        result = _worker(data_dir).call("collect", options, progress)
        if result.get("state") != "connected":
            raise CollectorError(result.get("message", "请先登录黑岩书库。"), result.get("state", "logged_out"))
        return _result("heiyan", "short", result.get("channel", "全部"), "书库", "书库", HEIYAN,
                       result.get("books", []), warnings=result.get("warnings", []), complete=result.get("complete", False),
                       requested_count=result.get("requested_count"), available_count=len(result.get("books", [])),
                       pagination=result.get("pagination"), source_total=result.get("source_total"),
                       auth_state=result.get("auth_state", "connected"), auth_message=result.get("auth_message", ""))
    if platform == "fanqie_short":
        raise CollectorError("番茄短故事热门入口需要作者账号；当前采集适配尚未验证，可先导入已有书目。")
    raise CollectorError("暂不支持这个平台。")


def fetch_text(platform, book_id, data_dir, max_chapters=3, start_chapter=1, progress=None):
    """Read a fixed range of public chapters through the shared content adapter."""
    if platform != "fanqie":
        raise CollectorError("此平台正文自动获取尚未接入，请导入可用原文。")
    from .fanqie_content import fetch_book_text
    return fetch_book_text(book_id, data_dir, max_chapters=max_chapters,
                           start_chapter=start_chapter, progress=progress)
