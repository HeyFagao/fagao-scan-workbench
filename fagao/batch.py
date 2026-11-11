# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Sequential category collection and searches over local, immutable snapshots.

Every rank remains attached to its original category snapshot. These helpers do
not select a work, combine ranks, start a server, or call a language model.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .collectors import collect
from . import network
from .store import AppError


class _ProgressInterrupted(Exception):
    def __init__(self, original):
        self.original = original


def _options(options):
    if not isinstance(options, dict):
        raise AppError("invalid_options", "批量采集条件必须是 JSON 对象。")
    result = deepcopy(options)
    if result.pop("platform", "fanqie") != "fanqie" or result.get("mode", "long") != "long":
        raise AppError("invalid_platform", "批量分类采集目前仅支持番茄长篇。")
    channel = result.get("channel") or "女频"
    if not isinstance(channel, str):
        raise AppError("invalid_channel", "番茄频道应为男频或女频。")
    channel = {"female": "女频", "male": "男频", "0": "女频", "1": "男频"}.get(channel, channel)
    if channel not in ("女频", "男频"):
        raise AppError("invalid_channel", "番茄频道应为男频或女频。")
    board = result.get("board") or "新书榜"
    if not isinstance(board, str):
        raise AppError("invalid_board", "番茄榜单应为新书榜或阅读榜。")
    board = {"new": "新书榜", "read": "阅读榜", "reading": "阅读榜"}.get(board, board)
    if board not in ("新书榜", "阅读榜"):
        raise AppError("invalid_board", "番茄榜单应为新书榜或阅读榜。")
    category = result.get("category") or ("古言脑洞" if channel == "女频" else "玄幻脑洞")
    if not isinstance(category, str) or not category.strip() or len(category) > 100:
        raise AppError("invalid_category", "首个分类应是当前频道的真实分类名称。")
    result.update(channel=channel, board=board, category=category.strip())
    result.setdefault("max_pages", 100)
    # A missing limit stays missing: the collector stops at the source boundary
    # or page cap instead of silently limiting every category to 100 works.
    return result


def collect_all_categories(store, options, progress=None, *, load_category=None, on_discovery=None):
    """Collect one Fanqie channel/board, saving a separate snapshot per category.

    Discovery uses only the first successful category's current navigation.
    A failure to discover categories returns an incomplete result; it never
    borrows another channel's old categories. Individual collection/save errors
    are reported while later categories continue. Progress cancellation escapes.
    """
    options = _options(options)
    channel, board, first = options["channel"], options["board"], options["category"]
    result = {
        "platform": "fanqie", "channel": channel, "board": board,
        "categories": [first], "snapshots": [], "failed_categories": [],
        "incomplete_categories": [], "discovery_complete": False,
        "complete": False, "warnings": [],
    }

    def notify(message, *args, **kwargs):
        if progress is not None:
            try:
                progress(message, *args, **kwargs)
            except Exception as exc:
                raise _ProgressInterrupted(exc) from exc

    def one(category):
        try:
            network.check_authorization()
            notify(f"正在采集番茄{channel}／{board}／{category}；每个分类独立保存。")
            requested = {**deepcopy(options), "category": category}
            # A scheduler may reuse an existing/fresh job and return its saved
            # snapshot. CLI callers keep the direct sequential collection path.
            raw = (load_category(requested) if load_category else
                   collect("fanqie", requested, store.directory, notify if progress else None))
            if (not isinstance(raw, dict) or raw.get("platform") != "fanqie"
                    or raw.get("mode", "long") != "long"
                    or raw.get("channel") != channel or raw.get("board") != board
                    or raw.get("category") != category):
                raise AppError("batch_scope_mismatch", "来源返回的频道、分类或榜型与请求不符，未保存混合结果。")
            snapshot = raw if load_category else store.save_snapshot(raw)
            if not snapshot.get("id"):
                raise AppError("batch_snapshot_missing", "分类任务未返回已保存的榜单记录。")
        except _ProgressInterrupted as exc:
            raise exc.original
        except Exception as exc:
            if getattr(exc, "code", None) == "cancelled" or network.is_authorization_error(exc):
                raise
            result["failed_categories"].append({
                "category": category,
                "code": str(getattr(exc, "code", "collection_failed")),
                "message": str(getattr(exc, "message", None) or exc)[:1200],
            })
            return None
        result["snapshots"].append(deepcopy({key: value for key, value in snapshot.items() if key != "books"}))
        if snapshot.get("complete") is not True:
            result["incomplete_categories"].append(category)
        return snapshot

    initial = one(first)
    if initial is None:
        result["warnings"].append("首个分类未完成，未取得当前频道的分类列表；已有快照保持不变。")
        return result
    categories = initial.get("categories")
    discovered = []
    if isinstance(categories, list):
        for value in categories:
            if isinstance(value, str) and value.strip() and len(value) <= 100:
                value = value.strip()
                if value not in discovered:
                    discovered.append(value)
    if not discovered:
        result["warnings"].append("已保存首个分类，但来源没有提供有效分类列表；未宣称完成全分类采集。")
        return result
    result["discovery_complete"] = True
    result["categories"] = [first] + [category for category in discovered if category != first]
    if on_discovery:
        on_discovery(list(result["categories"]))
    for category in result["categories"][1:]:
        one(category)
    result["complete"] = not result["failed_categories"] and not result["incomplete_categories"]
    return result


def _time_key(snapshot):
    for field in ("collected_at", "created_at"):
        try:
            value = datetime.fromisoformat(str(snapshot.get(field)).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    return float("-inf")


def search_snapshots(snapshots, query, limit=100):
    """Search full local snapshots without modifying them or inventing ranks.

    Titles/authors match before summaries/tags/categories. Results are unique by
    platform plus work ID, with every snapshot occurrence retained even when only
    an older title matches. A suggested selection always includes a snapshot ID.
    """
    if not isinstance(query, str):
        raise AppError("invalid_query", "检索词应为文字。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise AppError("invalid_limit", "检索数量应为 1 到 1000 的整数。")
    query = query.strip()
    needle = query.casefold()
    records = {}
    indexed = [(index, snapshot) for index, snapshot in enumerate(snapshots) if isinstance(snapshot, dict)]
    indexed.sort(key=lambda item: (_time_key(item[1]), -item[0]), reverse=True)
    for _, snapshot in indexed:
        platform, sid = snapshot.get("platform"), snapshot.get("id")
        if not isinstance(platform, str) or not platform or not isinstance(sid, str) or not sid:
            continue
        books = snapshot.get("books")
        if not isinstance(books, list):
            continue
        for book in books:
            if not isinstance(book, dict):
                continue
            bid = book.get("id")
            if isinstance(bid, bool) or not isinstance(bid, (str, int)) or not str(bid).strip():
                continue
            bid = str(bid).strip()
            identity = (platform, bid)
            if identity not in records:
                item = deepcopy({key: book.get(key) for key in (
                    "title", "author", "summary", "tags", "metrics", "cover_url", "url", "status")})
                item.update(platform=platform, book_id=bid, snapshot_id=sid,
                            selection={"snapshot_id": sid, "book_id": bid},
                            occurrences=[], matched_snapshot_ids=[])
                records[identity] = {"item": item, "match": 2, "seen": set()}
            entry = records[identity]
            if sid in entry["seen"]:
                continue
            entry["seen"].add(sid)
            occurrence = {key: deepcopy(snapshot.get(key)) for key in (
                "channel", "category", "board", "period", "data_date", "collected_at", "created_at")}
            occurrence.update(snapshot_id=sid, book_id=bid, rank=book.get("rank"))
            entry["item"]["occurrences"].append(occurrence)
            tags = book.get("tags") or []
            if not isinstance(tags, list):
                tags = [tags]
            primary = [book.get("title"), book.get("author")]
            secondary = [book.get("summary"), snapshot.get("category"), *tags]
            match = 0 if not needle or any(needle in str(value or "").casefold() for value in primary) else (
                1 if any(needle in str(value or "").casefold() for value in secondary) else 2)
            if match < 2:
                entry["match"] = min(entry["match"], match)
                entry["item"]["matched_snapshot_ids"].append(sid)
    matches = [entry for entry in records.values() if entry["match"] < 2]
    matches.sort(key=lambda entry: entry["match"])
    return {"query": query, "total": len(matches), "limit": limit,
            "items": [entry["item"] for entry in matches[:limit]]}
