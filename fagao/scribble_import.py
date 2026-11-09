"""Read a user-saved Scribble Hub ranking page without requesting the site.

Only the page's book metadata is returned. Scripts are parsed as inert text,
and raw HTML (which may include a signed-in navigation bar) is never stored.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from .collectors import CollectorError


BASE_URL = "https://www.scribblehub.com/series-ranking/"
MAX_HTML_BYTES = 8 * 1024 * 1024


def _source_parts(value, *, require_scope):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in ("www.scribblehub.com", "scribblehub.com")
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.path != "/series-ranking/"
                or parsed.fragment):
            raise ValueError("not a ranking URL")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {"order", "sort", "pg"}:
            raise ValueError("unrecognized filters")
        if any(len(values) != 1 for values in query.values()):
            raise ValueError("ambiguous filters")
        if require_scope or "order" in query or "sort" in query:
            if query.get("order") != ["2"] or query.get("sort") != ["2"]:
                raise ValueError("wrong scope")
        page = query.get("pg", [None])[0]
        if page is not None and (not re.fullmatch(r"[1-9][0-9]{0,4}", page)):
            raise ValueError("invalid page")
        return int(page) if page else None
    except (TypeError, ValueError, AttributeError):
        raise CollectorError("请导入 Scribble Hub 的 Weekly / Favorites 周收藏榜页面，当前文件的来源或榜型不匹配。") from None


def parse_saved_ranking(html: str, source_url: str | None = None):
    """Return one explicitly imported page in the normal snapshot schema.

The canonical URL proves the source identity; active controls prove the
ranking scope because the site's canonical URL omits its query string.
An optional externally supplied URL must agree, never replace that evidence.
The page's saving date is unknown and must not become a current source date.
"""
    if not isinstance(html, str) or not html.strip():
        raise CollectorError("请选择已保存的 Scribble Hub 榜单 HTML 文件。")
    try:
        size = len(html.encode("utf-8"))
    except UnicodeError:
        raise CollectorError("榜单 HTML 含有无效字符，请使用 UTF-8 格式重新保存。") from None
    if size > MAX_HTML_BYTES:
        raise CollectorError("榜单 HTML 超过 8 MB，请只保存当前榜单页面。")

    # Lazy imports keep the collector free to reuse this importer in future.
    from .overseas_readers import _Document, _text, parse_scribblehub

    try:
        root = _Document(html).root
        canonical = root.all(lambda node: node.tag == "link" and "canonical" in node.attrs.get("rel", "").lower().split())
        if len(canonical) != 1:
            raise CollectorError("文件缺少唯一的官网来源标记，请保存完整的榜单 HTML 页面。")
        canonical_page = _source_parts(canonical[0].attrs.get("href", ""), require_scope=False)
        supplied_page = _source_parts(source_url, require_scope=True) if source_url is not None else None

        active = root.all(lambda node: node.tag == "a" and node.has("linkactive"))
        selected = {}
        for node in active:
            label = " ".join(_text(node).split()).casefold()
            if label in ("favorites", "weekly"):
                if label in selected:
                    raise CollectorError("页面的榜单选项重复，未导入含糊的数据。")
                selected[label] = _source_parts(urljoin(BASE_URL, node.attrs.get("href", "")), require_scope=True)
            elif label in ("activity", "popularity", "readers", "rising", "daily", "monthly", "all time"):
                raise CollectorError("这份页面不是 Weekly / Favorites 周收藏榜，请切换到对应榜单再保存。")
        if set(selected) != {"favorites", "weekly"}:
            raise CollectorError("页面没有可核验的 Weekly / Favorites 已选标记，请保存完整的周收藏榜页面。")
        if any(node.attrs.get("value", "0") != "0"
               for node in root.all(lambda node: node.has("sfc_genre"))):
            raise CollectorError("页面设置了额外题材筛选，请清除筛选后导入全部分类的周收藏榜。")

        pagers = re.findall(r"\.pagination\(\{\s*items:\s*(\d+)[\s\S]{0,1500}?currentPage:\s*['\"]?(\d+)", html)
        if len(pagers) != 1:
            raise CollectorError("文件缺少可核验的分页信息，请保存完整的榜单 HTML 页面。")
        page_total, page = (int(value) for value in pagers[0])
        if not (1 <= page <= page_total <= 99999):
            raise CollectorError("页面的分页信息无效，未导入。")
        if any(value is not None and value != page for value in (canonical_page, supplied_page, *selected.values())):
            raise CollectorError("页面地址与实际页码不一致，未导入混合榜单。")

        books, _ = parse_scribblehub(html)
    except RecursionError:
        raise CollectorError("页面结构过深，无法读取；请重新保存官网榜单页面。") from None

    if not books:
        raise CollectorError("文件中没有可识别的榜单作品，请确认保存的是书目页面，而非错误或验证页面。")
    if len(books) > 25 or len({book["id"] for book in books}) != len(books):
        raise CollectorError("页面含有重复作品或超过一页的书目，未导入。")
    expected = list(range((page - 1) * 25 + 1, (page - 1) * 25 + 1 + len(books)))
    if [book["rank"] for book in books] != expected:
        raise CollectorError("书目名次与页码不一致，未导入混合或缺损的榜单。")

    url = BASE_URL + "?" + urlencode({"order": 2, "sort": 2, "pg": page})
    warning = "来自用户保存的官网页面；页面保存时间未知，本次导入时间不代表榜单更新时间。仅导入这一页，未合并其它历史页面。"
    warnings = [warning]
    if any(not book.get("cover_url") for book in books):
        warnings.append("部分封面在保存时变为本地文件；请保存“仅 HTML”格式以保留官网图片地址。")
    return {
        "platform": "scribblehub", "mode": "overseas", "channel": "全部",
        "category": "全部", "board": "周收藏榜", "period": "周榜",
        "source_id": "browser-import", "source_url": url, "data_date": None,
        "books": books, "categories": ["全部"], "warnings": warnings,
        "complete": False, "available_count": len(books),
        "pagination": {"pages_fetched": 0, "pages_imported": 1, "source_page": page,
                       "source_pages": page_total, "max_pages": 1, "page_size": 25,
                       "stop_reason": "imported_page"},
        "provenance": {"source_id": "browser-import", "source_url": url,
                       "source_name": "Scribble Hub 官网 · 用户保存页面",
                       "scope": f"周收藏榜 · 全部分类 · 保存的第 {page} 页 · 保存时间未知",
                       "metric_precision": "source_display_rounded", "rank_semantics": "original_rank"},
    }
