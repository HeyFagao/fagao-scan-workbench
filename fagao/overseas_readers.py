# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Anonymous official ranking pages, parsed without executing site scripts.

Titles and synopses keep the source language. Only the navigation vocabulary
and numeric metric labels are localized. No model service is called here.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlencode, urljoin
from urllib.request import BaseHandler
from .network import build_opener

from .collectors import (CollectorError, _cover_url, _fetch, _notify,
                         _notify_snapshot, _page_options)


class _EnglishLanguage(BaseHandler):
    """Webnovel negotiates catalogue language using this request header."""
    def http_request(self, request):
        request.add_header("Accept-Language", "en-US,en;q=0.9")
        return request

    https_request = http_request


_ENGLISH_OPENER = build_opener(_EnglishLanguage())


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, name):
        return name in self.attrs.get("class", "").split()

    def all(self, predicate):
        result = []
        for child in self.children:
            if isinstance(child, _Node):
                if predicate(child):
                    result.append(child)
                result.extend(child.all(predicate))
        return result

    def first(self, predicate):
        matches = self.all(predicate)
        return matches[0] if matches else _Node()

    def text(self, skip=()):
        if self.tag in ("script", "style") or any(self.has(name) for name in skip):
            return ""
        result = "".join(child.text(skip) if isinstance(child, _Node) else child
                         for child in self.children)
        if self.tag in ("p", "div", "br", "li"):
            result += "\n"
        return result


class _Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _text(node, skip=()):
    return node.text(skip).strip()


def _metric(name, value, unit):
    """Keep rounded source strings such as 3.81M instead of inventing precision."""
    value = str(value).strip()
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?(?:[kKmMbB])?", value):
        return {"name": name, "value": value, "unit": unit}
    return None


def _tags(values):
    from .overseas import translated_tags
    return translated_tags(values)


def parse_webnovel(html):
    root = _Document(html).root
    wrapper = root.first(lambda n: n.has("j_rank_wrapper"))
    books = []
    for row in wrapper.all(lambda n: n.tag == "section" and n.has("g_hr")):
        title_link = row.first(lambda n: n.tag == "h3").first(lambda n: n.tag == "a")
        link = title_link.attrs.get("href", "")
        match = re.fullmatch(r"/book/[^/?#]*_(\d+)", link)
        # Some official links are numeric-only book URLs.
        if not match:
            match = re.fullmatch(r"/book/(\d+)", link)
        rank = _text(row.first(lambda n: n.tag == "i" and n.has("ff_number")))
        if not match or not rank.isdigit() or int(rank) < 1 or not _text(title_link):
            continue
        image = row.first(lambda n: n.tag == "img")
        amount_node = row.first(lambda n: n.tag == "strong" and n.has("ff_number"))
        amount = _text(amount_node.first(lambda n: n.tag == "span"))
        metric = _metric("能量石", amount, "票")
        genres = [_text(n).lstrip("# ") for n in row.all(
            lambda n: n.tag == "a" and n.attrs.get("href", "").startswith(("/tags/", "/stories/")))]
        books.append({"id": match.group(1), "title": _text(title_link),
                      "author": _text(row.first(lambda n: n.tag == "strong" and n.has("c_l"))),
                      "summary": _text(row.first(lambda n: n.tag == "p" and n.has("c_s"))),
                      "url": urljoin("https://www.webnovel.com", link),
                      "cover_url": _cover_url(image.attrs.get("data-original") or image.attrs.get("src")),
                      "rank": int(rank), "rank_semantics": "original_rank",
                      "tags": _tags(genres), "source_tags": genres,
                      "metrics": [metric] if metric else [], "status": ""})
    # This is the same end indicator the official infinite-scroll script reads.
    more = bool(root.first(lambda n: n.has("j_rank_loading") and n.has("_on")).tag)
    return books, more


def parse_royalroad(html):
    root = _Document(html).root
    books = []
    for position, row in enumerate(root.all(lambda n: n.has("fiction-list-item")), 1):
        title_link = row.first(lambda n: n.has("fiction-title")).first(lambda n: n.tag == "a")
        link = title_link.attrs.get("href", "")
        match = re.match(r"^/fiction/(\d+)(?:/|$)", link)
        if not match or not _text(title_link):
            continue
        ident = match.group(1)
        image = row.first(lambda n: n.tag == "img" and n.attrs.get("data-type") == "cover")
        stats = row.first(lambda n: n.has("stats"))
        stats_text = " ".join(_text(n) for n in stats.children if isinstance(n, _Node)
                              and not n.attrs.get("id", "").startswith("description-"))
        metrics = []
        for source, name, unit in (("Followers", "关注", "人"), ("Views", "浏览", "次"),
                                   ("Pages", "页数", "页"), ("Chapters", "章节", "章")):
            found = re.search(r"([\d,.]+[kKmM]?)\s+" + source + r"\b", stats_text)
            metric = _metric(name, found.group(1), unit) if found else None
            if metric:
                metrics.append(metric)
        rating = stats.first(lambda n: n.attrs.get("aria-label", "").startswith("Rating:"))
        found = re.search(r"Rating:\s*([\d.]+) out of 5", rating.attrs.get("aria-label", ""))
        if found:
            metrics.append({"name": "评分", "value": found.group(1), "unit": "/5"})
        status_labels = [_text(n).upper() for n in row.all(lambda n: n.tag == "span" and n.has("label"))]
        status = next(({"ONGOING": "连载中", "COMPLETED": "已完结", "HIATUS": "暂停更新", "STUB": "部分章节已移出"}[s]
                       for s in status_labels if s in ("ONGOING", "COMPLETED", "HIATUS", "STUB")), "")
        source_tags = [_text(n) for n in row.all(lambda n: n.has("fiction-tag"))]
        books.append({"id": ident, "title": _text(title_link), "author": "",
                      "summary": _text(row.first(lambda n: n.attrs.get("id") == "description-" + ident)),
                      "url": urljoin("https://www.royalroad.com", link),
                      "cover_url": _cover_url(image.attrs.get("src")),
                      "rank": None, "source_position": position, "rank_semantics": "source_list_position",
                      "tags": _tags(source_tags), "source_tags": source_tags,
                      "metrics": metrics, "status": status})
    return books, False


def parse_scribblehub(html):
    root = _Document(html).root
    books = []
    for row in root.all(lambda n: n.has("search_main_box")):
        title = row.first(lambda n: n.has("search_title"))
        link = title.first(lambda n: n.tag == "a")
        url = link.attrs.get("href", "")
        match = re.fullmatch(r"https://www\.scribblehub\.com/series/(\d+)/[^?#]*/?", url)
        rank = _text(title.first(lambda n: n.has("genre_rank"))).lstrip("#")
        if not match or not rank.isdigit() or int(rank) < 1 or not _text(link):
            continue
        body = row.first(lambda n: n.has("search_body"))
        summary = _text(body, ("search_title", "search_stats", "search_genre", "dots", "morelink"))
        metrics = []
        for stat in row.all(lambda n: n.has("nl_stat")):
            value = _text(stat)
            for source, name, unit in (("Views", "浏览", "次"), ("Favorites", "收藏", "次"),
                                      ("Chapters", "章节", "章"), ("Chapters/Week", "周更新", "章/周"),
                                      ("Readers", "书架读者", "人"), ("Reviews", "书评", "条"),
                                      ("Words", "英文词数", "词")):
                found = re.fullmatch(r"([\d,.]+[kKmMbB]?)\s+" + source, value)
                metric = _metric(name, found.group(1), unit) if found else None
                if metric:
                    metrics.append(metric)
        rating = _text(row.first(lambda n: n.has("search_ratings"))).strip("()")
        metric = _metric("评分", rating, "/5")
        if metric:
            metrics.append(metric)
        source_tags = [_text(n) for n in row.all(lambda n: n.has("fic_genre"))]
        books.append({"id": match.group(1), "title": _text(link),
                      "author": _text(row.first(lambda n: n.has("a_un_st"))), "summary": summary,
                      "url": url, "cover_url": _cover_url(row.first(lambda n: n.tag == "img").attrs.get("src")),
                      "rank": int(rank), "rank_semantics": "original_rank",
                      "tags": _tags(source_tags), "source_tags": source_tags,
                      "metrics": metrics, "status": ""})
    # Official pagination uses items = number of pages, itemsOnPage = 1.
    pager = re.search(r"\.pagination\(\{\s*items:\s*(\d+).*?currentPage:\s*['\"]?(\d+)", html, re.S)
    more = int(pager.group(2)) < int(pager.group(1)) if pager else False
    return books, more


SOURCES = {
    "webnovel": ("新书能量榜", "https://www.webnovel.com/ranking/novel/monthly/power_rank", 20, parse_webnovel),
    "royalroad": ("新星榜", "https://www.royalroad.com/fictions/rising-stars", 50, parse_royalroad),
    "scribblehub": ("周收藏榜", "https://www.scribblehub.com/series-ranking/?order=2&sort=2", 25, parse_scribblehub),
}


def collect_readers(platform, options, data_dir=None, progress=None):
    if platform not in SOURCES:
        raise CollectorError("海外平台尚未接入。")
    board, base, page_size, parse = SOURCES[platform]
    channel = options.get("channel") or ("男频" if platform == "webnovel" else "全部")
    if (options.get("category") or "全部") != "全部" or (options.get("board") or board) != board:
        raise CollectorError("请选择此海外平台已接入的榜单与全部分类。")
    if channel not in (("男频", "女频") if platform == "webnovel" else ("全部",)):
        raise CollectorError("请选择此平台支持的频道。")
    if options.get("period") not in (None, "", "周榜" if platform in ("webnovel", "scribblehub") else "当前"):
        raise CollectorError("此榜单的周期与来源不一致。")
    if platform == "webnovel":
        # Pin all filters; the site's defaults must not silently change our scope.
        base += "?" + urlencode({"sourceType": 2, "sex": 2 if channel == "女频" else 1,
                                  "signStatus": 1, "timeType": 3})
    max_pages, limit = _page_options(options, page_size)
    books, warnings = [], []
    pages, stop, more = 0, "source_end", False
    source_failure = None
    source_scope = {"webnovel": f"{channel} · 30 天内发布的签约原创 · 本周能量石",
                    "royalroad": "官网新星榜当前列表 · 按来源列表顺序展示",
                    "scribblehub": "官网周收藏榜 · 单项指标沿用来源显示精度"}[platform]

    def snapshot(has_more=False):
        return {"platform": platform, "mode": "overseas", "channel": channel,
                "category": "全部", "board": board, "source_url": base,
                "period": "周榜" if platform in ("webnovel", "scribblehub") else "当前",
                "data_date": None, "books": list(books), "categories": ["全部"],
                "warnings": list(warnings), "complete": not has_more and stop in ("source_end", "requested_limit"),
                "requested_count": limit, "available_count": len(books),
                "pagination": {"pages_fetched": pages, "max_pages": max_pages,
                               "page_size": page_size, "stop_reason": "awaiting_demand" if has_more else stop},
                "source_id": "direct", "provenance": {"source_id": "direct", "source_url": base,
                    "source_name": {"webnovel": "Webnovel 官网", "royalroad": "Royal Road 官网", "scribblehub": "Scribble Hub 官网"}[platform],
                    "scope": source_scope, "metric_precision": "source_display_rounded",
                    "rank_semantics": "source_list_position" if platform == "royalroad" else "original_rank"},
                **({"source_state": source_failure.state, "source_message": str(source_failure)}
                   if source_failure else {})}

    seen, ranks = {}, {}
    for page in range(1, max_pages + 1):
        _notify(progress, f"正在读取海外榜单第 {page} 页…")
        url = base if page == 1 else base + ("&" if "?" in base else "?") + urlencode({"pageIndex" if platform == "webnovel" else "pg": page})
        try:
            raw, more = parse(_fetch(url, opener=_ENGLISH_OPENER))
        except CollectorError as error:
            denied = None
            if platform == 'scribblehub' and error.state == 'forbidden':
                denied = CollectorError(
                    'Scribble Hub 拒绝了工作台的读取请求（HTTP 403/401）。'
                    '可打开官网榜单，或导入浏览器保存的榜单 HTML；已有记录仍保留。',
                    'source_access_denied')
            if not books:
                if platform == 'royalroad' and error.state in ('forbidden', 'network_error'):
                    from .royalroad_fallback import collect_royalroad_fallback
                    return collect_royalroad_fallback(options, progress=progress, reason=error)
                if denied:
                    raise denied from error
                raise
            # Publish the retained page while preserving the access refusal so
            # the job does not become a fresh success that restarts automatically.
            source_failure = denied
            warnings.append(str(denied or error))
            stop = "page_error"
            break
        pages += 1
        if not raw:
            if not books:
                raise CollectorError("海外榜单没有有效书目，来源页面结构已变化或要求验证。")
            stop = "empty_page"
            warnings.append("后续页面没有可识别的作品，已保留取得的书目。")
            break
        fresh, page_ids, page_ranks = [], {}, {}
        conflict = False
        for book in raw:
            if book["id"] in seen:
                if seen[book["id"]] != book.get("rank"):
                    conflict = True
                    break
                continue
            rank = book.get("rank")
            if book["id"] in page_ids:
                if page_ids[book["id"]] != rank:
                    conflict = True
                    break
                continue
            if rank is not None and ((rank in ranks and ranks[rank] != book["id"])
                                     or (rank in page_ranks and page_ranks[rank] != book["id"])):
                conflict = True
                break
            page_ids[book["id"]] = rank
            if rank is not None:
                page_ranks[rank] = book["id"]
            fresh.append(book)
        if conflict:
            stop = "rank_conflict"
            warnings.append("榜单名次在翻页时变化，已停止拼接并保留已取得页面。")
            break
        if not fresh:
            stop = "repeated_page"
            break
        for book in fresh[:max(0, limit - len(books))]:
            books.append(book)
            seen[book["id"]] = book.get("rank")
            if book.get("rank") is not None:
                ranks[book["rank"]] = book["id"]
        if len(books) >= limit:
            stop = "requested_limit" if options.get("limit") else ("max_pages" if more else "source_end")
        elif not more:
            stop = "source_end"
        elif page == max_pages:
            stop = "max_pages"
        else:
            stop = "awaiting_demand"
        has_more = more and len(books) < limit and page < max_pages
        _notify_snapshot(progress, snapshot(has_more), has_more)
        if not has_more:
            break
    return snapshot()
